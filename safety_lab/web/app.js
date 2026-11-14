const state={judge:null,mas:null,cases:[],run:null};
const $=id=>document.getElementById(id);
const pct=n=>(n*100).toFixed(2)+'%';
const esc=value=>String(value??'').replace(/[&<>"']/g,x=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
function fail(error){$('error').hidden=false;$('error').textContent=error.message||String(error)}
async function get(url,options){const response=await fetch(url,options);const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));return data}
function table(headers,rows){return '<table><thead><tr>'+headers.map(x=>'<th>'+esc(x)+'</th>').join('')+'</tr></thead><tbody>'+rows.map(row=>'<tr>'+row.map(x=>'<td>'+esc(x)+'</td>').join('')+'</tr>').join('')+'</tbody></table>'}
function stat(label,value,sub=''){return `<div class="metric"><div class="stat-label">${esc(label)}</div><div class="big">${esc(value)}</div><div class="stat-sub">${esc(sub)}</div></div>`}
function activate(id){document.querySelectorAll('.panel').forEach(x=>x.hidden=x.id!==id);document.querySelectorAll('nav button').forEach(x=>x.classList.toggle('active',x.dataset.tab===id));location.hash=id}
document.querySelectorAll('[data-tab]').forEach(button=>button.addEventListener('click',()=>activate(button.dataset.tab)));
async function judgeView(){
 const split=$('judge-split').value,m=state.judge.results[split];
 $('judge-metrics').innerHTML=stat('F1',pct(m.f1),`${m.n}条样本`)+stat('风险召回率',pct(m.recall),`漏报 ${m.fn} 条`)+stat('安全样本特异度',pct(m.specificity),`虚警 ${m.fp} 条`)+stat('输出有效率',pct(m.validity),`无效 ${m.invalid} 条`);
 $('confusion').innerHTML=`<div class="matrix"><div>TP · 识别风险<b>${m.tp}</b></div><div class="mistake">FP · 虚警<b>${m.fp}</b></div><div class="mistake">FN · 漏报<b>${m.fn}</b></div><div>TN · 正确放行<b>${m.tn}</b></div></div>`;
 $('baselines').innerHTML=table(['策略','F1','召回','特异度'],[['历史模型',pct(m.f1),pct(m.recall),pct(m.specificity)],['全部判不安全',pct(m.always_unsafe.f1),pct(m.always_unsafe.recall),pct(m.always_unsafe.specificity)],['全部判安全',pct(m.always_safe.f1),pct(m.always_safe.recall),pct(m.always_safe.specificity)]]);
 const cases=await get('/api/cases/rjudge?split='+split);
 $('judge-errors').innerHTML=table(['样本ID','场景','真值','原始预测','错误方向'],cases.records.map(x=>[x.id,x.scenario,x.label===1?'不安全':'安全',x.raw_pred===-1?'无效':x.raw_pred===1?'不安全':'安全',x.label===1?'漏报':'虚警']));
}
function graphView(){
 const item=state.cases.find(x=>x.question_id===$('question').value);if(!item)return;
 const phase=Number($('round').value),condition=$('condition').value,episode=item.conditions[condition],turn=episode.turns[phase];
 $('round-label').textContent='R'+phase;
 const points=Array.from({length:8},(_,i)=>({x:210+125*Math.cos(-Math.PI/2+i*Math.PI/4),y:155+116*Math.sin(-Math.PI/2+i*Math.PI/4)}));
 let svg='';for(let i=0;i<8;i++)for(let j=i+1;j<8;j++)svg+=`<line x1="${points[i].x}" y1="${points[i].y}" x2="${points[j].x}" y2="${points[j].y}" stroke="#dce5eb" stroke-width="1"/>`;
 for(const node of turn.nodes){const p=points[node.node],attacker=condition.startsWith('attack/')&&item.initial_attackers.includes(node.node);svg+=`<g><circle cx="${p.x}" cy="${p.y}" r="25" fill="${node.correct?'#dff1e9':'#fff0db'}" stroke="${attacker?'#be5d50':'#7d9f9a'}" stroke-width="${attacker?3:1.5}"/><text x="${p.x}" y="${p.y-1}" text-anchor="middle" font-size="12" fill="#244854">A${node.node}</text><text x="${p.x}" y="${p.y+14}" text-anchor="middle" font-size="12" fill="#244854">${esc(node.answer||'无效')}</text></g>`}
 $('graph').innerHTML=svg;
 $('nodes').innerHTML=table(['节点','初始角色','选项','本轮结果'],turn.nodes.map(n=>['A'+n.node,condition.startsWith('attack/')&&item.initial_attackers.includes(n.node)?'攻击者':'正常角色',n.answer||'无效',n.correct?'正确':n.valid?'错误':'无效']));
 $('guard-trace').textContent=JSON.stringify(episode.guard_trace,null,2)||'[]';
}
$('judge-split').addEventListener('change',()=>judgeView().catch(fail));
['question','condition','round'].forEach(id=>$(id).addEventListener('input',graphView));
$('upload').addEventListener('change',async event=>{try{const file=event.target.files[0];if(!file)return;if(file.size>2*1024*1024)throw new Error('文件须小于2MB。');const data=JSON.parse(await file.text());$('records').value=JSON.stringify(Array.isArray(data)?data:data.records,null,2)}catch(error){fail(error)}});
$('evaluate-button').addEventListener('click',async()=>{const button=$('evaluate-button');button.disabled=true;$('error').hidden=true;try{const data=JSON.parse($('records').value);const report=await get('/api/evaluate/rjudge',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({records:Array.isArray(data)?data:data.records})});state.run=report;const m=report.result;$('evaluation-result').innerHTML=`<span class="badge">离线核算完成 · 无模型调用</span><p class="muted">报告ID ${esc(report.run_id)}</p><div class="grid two">${stat('F1',pct(m.f1))}${stat('有效率',pct(m.validity))}</div>`+table(['总数','虚警','漏报','无效'],[[m.n,m.fp,m.fn,m.invalid]])+`<p class="muted">无效记录保留在分母中，并按官方错误策略核算。</p>`;$('download').hidden=false}catch(error){fail(error)}finally{button.disabled=false}});
$('download').addEventListener('click',()=>{if(!state.run)return;const url=URL.createObjectURL(new Blob([JSON.stringify(state.run,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='safety-evaluation-'+state.run.run_id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)});
async function boot(){try{const [catalog,judge,mas,cases]=await Promise.all(['/api/catalog','/api/results/rjudge','/api/results/gsafeguard','/api/cases/gsafeguard'].map(url=>get(url)));Object.assign(state,{judge,mas,cases:cases.cases});$('judge-overview').innerHTML=`<div class="big">${pct(judge.results.unintended.f1)} / ${pct(judge.results.injection.f1)}</div><div class="stat-sub">非蓄意风险 / 注入攻击 · F1</div>`;$('mas-overview').innerHTML='<div class="big">38 / 128 → 44 / 128</div><div class="stat-sub">攻击条件失败节点数：无防护 → 有防护</div>';$('provenance').textContent=JSON.stringify(catalog.provenance,null,2);$('conditions').innerHTML=table(['条件','独立问题','末轮失败/节点','固定分母失败率','初始非攻击节点失败率'],Object.entries(mas.conditions).map(([key,m])=>[key,m.questions,`${m.failures}/${m.nodes}`,pct(m.fixed_denominator_failure_rate),pct(m.benign_failure_rate)]));$('mas-conclusion').textContent=mas.conclusion;$('paired').textContent=JSON.stringify({paired:mas.paired,limitations:mas.limitations},null,2);$('question').innerHTML=state.cases.map((x,i)=>`<option value="${esc(x.question_id)}">问题 ${i+1} · ${esc(x.question_id)}</option>`).join('');await judgeView();graphView();$('loading').hidden=true;const tab=location.hash.slice(1);if(['overview','rjudge','gsafeguard','evaluate'].includes(tab))activate(tab)}catch(error){$('loading').hidden=true;fail(error)}}
boot();
