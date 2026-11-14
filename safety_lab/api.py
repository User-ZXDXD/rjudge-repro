"""Local workbench API. No provider credential is loaded on startup."""
from pathlib import Path
from datetime import datetime,timezone
import json,os,re,uuid

from fastapi import FastAPI,HTTPException,Request
from fastapi.responses import FileResponse,JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field

from .metrics import DATA,evaluate_judgments,rjudge_results,mas_results,mas_cases

class JudgmentRequest(BaseModel):
    records:list[dict]=Field(min_length=1,max_length=10000)

def create_app(run_dir=None):
    directory=Path(run_dir or os.environ.get('SAFETY_LAB_RUN_DIR','.lab/runs'))
    app=FastAPI(title='Agent Safety Lab',version='0.1.0')
    static=Path(__file__).parent/'web'

    @app.middleware('http')
    async def request_limits(request:Request,call_next):
        length=request.headers.get('content-length','0')
        if not length.isdigit() or int(length)>2*1024*1024:
            return JSONResponse({'detail':'请求须小于2MB。'},status_code=413)
        return await call_next(request)

    @app.get('/health')
    def health():return {'status':'ok','mode':'local_evidence_and_offline_evaluation','model_requests':0}

    @app.get('/api/catalog')
    def catalog():
        return {'products':['R-Judge风险判断','G-Safeguard多智能体防护'],
            'metric_policy':'分别报告任务、分母和限制，不汇成跨任务总分。',
            'provenance':json.loads((DATA/'provenance.json').read_text(encoding='utf-8'))}

    @app.get('/api/results/rjudge')
    def rjudge():return rjudge_results()

    @app.get('/api/results/gsafeguard')
    def gsafeguard():return mas_results()

    @app.get('/api/cases/gsafeguard')
    def cases():return {'cases':mas_cases(),'kind':'derived_historical_choices_not_raw_prompts'}

    @app.get('/api/cases/rjudge')
    def judge_cases(split:str='unintended',errors_only:bool=True):
        if split not in ('unintended','injection'):raise HTTPException(422,'未知子集。')
        rows=json.loads((DATA/'rjudge_outcomes.json').read_text(encoding='utf-8'))
        rows=[r for r in rows if r['split']==split and (not errors_only or r['pred']!=r['label'])]
        return {'records':rows,'contains_raw_prompts':False,'kind':'historical_derived_outcomes'}

    @app.post('/api/evaluate/rjudge')
    def evaluate(payload:JudgmentRequest):
        try:result=evaluate_judgments(payload.records)
        except ValueError as exc:raise HTTPException(422,str(exc)) from exc
        run_id=uuid.uuid4().hex
        report={'run_id':run_id,'created_at':datetime.now(timezone.utc).isoformat(),
            'status':'completed','task':'rjudge_metric_recalculation','result':result}
        directory.mkdir(parents=True,exist_ok=True)
        (directory/(run_id+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        return report

    @app.get('/api/runs/{run_id}')
    def report(run_id:str):
        if not re.fullmatch('[a-f0-9]{32}',run_id):raise HTTPException(404,'报告不存在。')
        path=directory/(run_id+'.json')
        if not path.is_file():raise HTTPException(404,'报告不存在。')
        return json.loads(path.read_text(encoding='utf-8'))

    @app.get('/')
    def home():return FileResponse(static/'index.html')

    app.mount('/static',StaticFiles(directory=static),name='static')
    return app

app=create_app()
