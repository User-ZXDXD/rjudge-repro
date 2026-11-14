"""G-Safeguard 单样本：默认离线检查；--run 才会调用 DeepSeek。"""
from __future__ import annotations

import argparse
import asyncio
import csv
from datetime import datetime, timezone, timedelta
import getpass
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import subprocess
import shutil
import sys
import time
import uuid
import warnings

PROJECT = Path(__file__).resolve().parent
REPO = Path(os.environ.get('GSAFEGUARD_UPSTREAM', str(Path(__file__).resolve().parents[2]/'vendor/G-Safeguard')))
MODEL = 'deepseek-flash'
BASE_URL = 'https://api.deepseek.com'
MAX_CALLS = 32
MAX_OUTPUT_TOKENS = 1024
PEAK_BUDGET_CNY = 2.0
MAX_INPUT_BYTES = 65536
MAX_SECONDS = 600
BEIJING = timezone(timedelta(hours=8))
PRICE_SOURCE = 'https://api-docs.deepseek.com/zh-cn/quick_start/pricing/'


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git_info():
    git = shutil.which('git')
    if not git:raise RuntimeError('Git is required for source verification')
    commit = subprocess.check_output([git, '-C', str(REPO), 'rev-parse', 'HEAD'], text=True).strip()
    status = subprocess.check_output([git, '-C', str(REPO), 'status', '--porcelain'], text=True).strip()
    return {'commit': commit, 'clean': not bool(status)}


class PilotStop(Exception):
    """达到限制或真实请求失败时明确停止，不在后台继续发请求。"""


class Gateway:
    def __init__(self, folder, live=False, client=None):
        self.folder, self.live, self.client = folder, live, client
        self.lock = asyncio.Lock()  # 一次只发一个请求，方便记录并限制费用。
        self.attempted, self.successful = 0, 0
        self.submitted = 0
        self.prompt_tokens, self.output_tokens = 0, 0
        self.hit_tokens, self.miss_tokens = 0, 0
        self.peak_cost = 0.0
        self.phase = 0
        self.halted = False
        self.started = time.monotonic()
        self.records = []

    def journal(self, record):
        with (self.folder / 'requests.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')

    def reject_sync(self, *args, **kwargs):
        # 该试跑只允许官方异步路径，防止误用未加费用限制的同步入口。
        raise PilotStop('SYNCHRONOUS_PATH_DISABLED')

    async def invoke(self, prompt, model_type):
        async with self.lock:
            try:
                return await self._invoke_locked(prompt, model_type)
            except BaseException as exc:
                # 包括写日志、序列化等准备动作失败，也必须先停止所有排队请求。
                self.halted = True
                try:
                    self.journal({'event': 'gateway_halted', 'call': self.attempted,
                                  'error_type': type(exc).__name__,
                                  'http_status': getattr(exc, 'status_code', None),
                                  'stop_reason': str(exc) if isinstance(exc, PilotStop) else None})
                except Exception:
                    pass  # 日志设备故障不影响停发请求，也不替换原始错误。
                raise

    async def _invoke_locked(self, prompt, model_type):
        # 此辅助函数只从持有锁的 invoke 调用。
        if self.halted:
            raise PilotStop('ALREADY_HALTED')
        input_bytes = len(json.dumps(prompt, ensure_ascii=False).encode('utf-8'))
        # 这是本地保守估算，不是平台的硬扣费上限；实际用量在响应后记录。
        next_estimate = ((input_bytes + 512) * 2 + MAX_OUTPUT_TOKENS * 8) / 1_000_000
        if (model_type != MODEL or self.attempted >= MAX_CALLS
                or input_bytes > MAX_INPUT_BYTES
                or self.peak_cost + next_estimate > PEAK_BUDGET_CNY
                or time.monotonic() - self.started > MAX_SECONDS):
            self.halted = True
            raise PilotStop('REQUEST_OR_BUDGET_OR_TIME_LIMIT')
        self.attempted += 1
        node_match = re.search(r'agent_(\d+)', prompt[0]['content'])
        node = int(node_match.group(1)) if node_match else None
        body = {'model': model_type, 'messages': prompt, 'temperature': 0,
                'max_tokens': MAX_OUTPUT_TOKENS,
                'extra_body': {'thinking': {'type': 'disabled'}}}
        # 不保存请求头、环境变量或密钥，仅保存公开题目与模型交流内容。
        self.journal({'event': 'request_started', 'call': self.attempted,
                      'phase': self.phase, 'node': node, 'live': self.live,
                      'input_utf8_bytes': input_bytes, 'request': body})
        try:
            if self.live:
                self.journal({'event': 'request_submitted', 'call': self.attempted})
                self.submitted += 1
                reply = await self.client.chat.completions.create(**body)
                raw = reply.model_dump(mode='json')
                usage = raw.get('usage')
                if not usage or 'prompt_tokens' not in usage or 'completion_tokens' not in usage:
                    self.journal({'event': 'response_without_usage', 'call': self.attempted, 'response': raw})
                    raise PilotStop('MISSING_USAGE_UNKNOWN_COST')
                prompt_tokens, completion_tokens = usage['prompt_tokens'], usage['completion_tokens']
                details = usage.get('prompt_tokens_details') or {}
                hit = usage.get('prompt_cache_hit_tokens', details.get('cached_tokens', 0)) or 0
                miss = usage.get('prompt_cache_miss_tokens', prompt_tokens - hit)
                self.prompt_tokens += prompt_tokens
                self.output_tokens += completion_tokens
                self.hit_tokens += hit
                self.miss_tokens += miss
                self.peak_cost += (hit * 0.04 + miss * 2 + completion_tokens * 8) / 1_000_000
                self.journal({'event': 'response_received', 'call': self.attempted,
                              'phase': self.phase, 'node': node, 'response': raw})
                text = reply.choices[0].message.content
                finish = reply.choices[0].finish_reason
                if getattr(reply.choices[0].message, 'reasoning_content', None):
                    raise PilotStop('UNEXPECTED_THINKING_OUTPUT')
                if not isinstance(text, str) or not text.strip():
                    raise PilotStop('EMPTY_RESPONSE')
            else:
                # 明确标记人造回答，只验证原始交流代码，不伪装真实 API 结果。
                reason_tag = 'REASON' if self.phase == 0 else 'UPDATED_REASON'
                text = f'<{reason_tag}>: OFFLINE_STUB; no model call was made.\n<ANSWER>: A'
                finish = 'offline_stub'
                self.journal({'event': 'offline_stub', 'call': self.attempted,
                              'phase': self.phase, 'node': node, 'text': text})
            self.successful += 1
            self.records.append({'call': self.attempted, 'phase': self.phase,
                                 'node': node, 'finish_reason': finish})
            print(f'[{self.successful}/{MAX_CALLS}] 完成第{self.phase}轮 agent_{node}', flush=True)
            return text
        except BaseException as exc:
            self.halted = True
            # 异常原文可能包含认证信息，因此只记录异常类型和状态码。
            try:
                self.journal({'event': 'request_failed', 'call': self.attempted,
                              'error_type': type(exc).__name__,
                              'http_status': getattr(exc, 'status_code', None),
                              'stop_reason': str(exc) if isinstance(exc, PilotStop) else None,
                              'billing_may_have_occurred': self.live and self.submitted > 0})
            except Exception:
                pass
            raise


def prepare_sample():
    # 只导入本地官方代码；这里不加载句向量模型或下载权重。
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    sys.path.insert(0, str(REPO / 'PI'))
    import random
    import numpy as np
    import pandas as pd
    import agents
    import gen_graph
    from gen_mmlu import format_example

    random.seed(20261103)
    np.random.seed(20261103)
    csv_path = REPO / 'PI/datasets/MMLU/dev/computer_security_dev.csv'
    frame = pd.read_csv(csv_path, header=None)
    qa = format_example(frame, 0)
    source_graph = gen_graph.generate_agent_graph_dataset(8, 1.0, 1, 0)[0]
    data = gen_graph.generate_initial_data(source_graph, qa)
    return agents, data, qa, csv_path


def write_reader(folder, data, live):
    text = ['# 单样本智能体交流记录', '',
            '真实 DeepSeek 请求；只检查交流流程，尚未训练或评测防护器。' if live else
            '**离线人造回答，不是真实模型结果，不消耗 API。**', '', '## 题目', '',
            data['question'], '', f"标准答案：{data['correct_answer']}", '']
    for phase, responses in enumerate(data['communication_data']):
        text += [f'## 第 {phase} 轮（初答）' if phase == 0 else f'## 第 {phase} 轮（讨论后更新）', '']
        for node, answer in responses:
            text += [f'### Agent {node}', '', str(answer), '']
    (folder / '交流记录.md').write_text('\n'.join(text), encoding='utf-8')


async def execute(live=False):
    stamp = datetime.now(BEIJING).strftime('%Y%m%d_%H%M%S')
    folder = PROJECT / 'runs' / f"{'live' if live else 'offline'}_{stamp}_{uuid.uuid4().hex[:6]}"
    folder.mkdir(parents=True, exist_ok=False)
    try:
        return await execute_in_folder(live, folder)
    except BaseException as exc:
        # 初始化或收尾出错也保留失败摘要；不把未知用量当成零。
        summary_path = folder / 'run_summary.json'
        summary = {'status': 'STARTUP_OR_FINALIZATION_FAILED', 'mode': 'live' if live else 'offline_stub',
                   'error_type': type(exc).__name__, 'http_status': getattr(exc, 'status_code', None),
                   'stop_reason': str(exc) if isinstance(exc, PilotStop) else None,
                   'paid_request_attempts': ('unknown; inspect requests.jsonl' if (folder / 'requests.jsonl').exists() else 0) if live else 0,
                   'output_folder': str(folder), 'paper_reproduction_complete': False}
        if not summary_path.exists():
            dump(summary_path, summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        return 1


async def execute_in_folder(live, folder):
    agents, data, qa, csv_path = prepare_sample()
    source_files = [REPO / 'PI' / n for n in ['agents.py', 'gen_graph.py', 'gen_mmlu.py', 'model.py', 'gat_with_attr_conv.py']]
    hashes = {str(p): sha(p) for p in source_files}
    metadata = {
        'mode': 'live' if live else 'offline_stub', 'seed': 20261103,
        'scope': 'single clean communication sample; no guard training or attack evaluation',
        'official_repo_before': git_info(), 'source_sha256': hashes,
        'dataset_file': str(csv_path), 'dataset_sha256': sha(csv_path), 'row_index_zero_based': 0,
        'model': MODEL, 'model_alias_version_from_docs': 'DeepSeek-V4.1-Flash-1017',
        'version_note': 'Alias version from documentation, not an independently pinned model revision.',
        'nodes': 8, 'attackers': 0, 'additional_rounds': 3, 'expected_requests': MAX_CALLS,
        'adaptations': ['DeepSeek replaces the original GPT-4o mini',
                        'Fixed MMLU dev subject/row instead of random sampling',
                        'Non-thinking mode; SDK retries disabled; calls serialized',
                        'Official agent prompts and parser used unchanged'],
        'price_snapshot_date': '2026-11-03', 'price_source': PRICE_SOURCE,
        'local_estimated_budget_cny': PEAK_BUDGET_CNY,
        'budget_note': 'Local estimate only, not a platform-enforced billing cap; errors may incur charges.',
        'limits': {'calls': MAX_CALLS, 'output_tokens_per_call': MAX_OUTPUT_TOKENS,
                   'input_utf8_bytes_per_call': MAX_INPUT_BYTES, 'wall_seconds': MAX_SECONDS},
        'packages': {n: importlib.metadata.version(n) for n in ['openai', 'torch', 'torch-geometric', 'numpy', 'pandas', 'sentence-transformers']},
    }
    dump(folder / 'run_config.json', metadata)
    client = None
    if live:
        connection = json.loads((PROJECT / 'reports/deepseek_connection_result.json').read_text(encoding='utf-8-sig'))
        if not connection.get('chat_verified'):
            raise PilotStop('CHAT_TEST_NOT_VERIFIED')
        print('本轮最多32次模型请求；不自动重试。密钥不写入文件。', flush=True)
        # 无法隐藏输入时直接停止，不允许 getpass 回退为明文回显。
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            key = getpass.getpass('请在本机输入 DeepSeek API Key（隐藏输入）：')
        if not key.strip():
            raise PilotStop('EMPTY_KEY')
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=key.strip(), base_url=BASE_URL, max_retries=0, timeout=60.0)
        key = None
    gate = Gateway(folder, live=live, client=client)
    original_async, original_sync = agents.allm_invoke, agents.llm_invoke
    agents.allm_invoke, agents.llm_invoke = gate.invoke, gate.reject_sync
    try:
        graph = agents.AgentGraph(data['adj_matrix'], data['system_prompts'], [], model_type=MODEL)
    except BaseException:
        gate.halted = True
        agents.allm_invoke, agents.llm_invoke = original_async, original_sync
        if client is not None:
            try:
                await client.close()
            except BaseException:
                pass
        raise
    data['communication_data'] = []
    status, error = 'FAILED', None
    try:
        for phase in range(4):
            gate.phase = phase
            responses = await (graph.afirst_generate((qa[0], qa[1], qa[2][0])) if phase == 0 else graph.are_generate())
            if len(responses) != 8 or sorted(node for node, _ in responses) != list(range(8)):
                raise PilotStop('INCOMPLETE_ROUND')
            data['communication_data'].append(responses)
        if gate.successful != MAX_CALLS:
            raise PilotStop('INCOMPLETE_REQUEST_COUNT')
        status = 'COMMUNICATION_COMPLETE' if live else 'OFFLINE_CHECK_PASS'
    except BaseException as exc:
        error = {'error_type': type(exc).__name__, 'http_status': getattr(exc, 'status_code', None),
                 'stop_reason': str(exc) if isinstance(exc, PilotStop) else None}
    finally:
        gate.halted = True
        agents.allm_invoke, agents.llm_invoke = original_async, original_sync
        cleanup_error = None
        if client is not None:
            try:
                await client.close()
            except BaseException as exc:
                cleanup_error = {'error_type': type(exc).__name__}
                if status in ['COMMUNICATION_COMPLETE', 'OFFLINE_CHECK_PASS']:
                    status = 'FAILED_CLEANUP'
        data['adj_matrix'] = data['adj_matrix'].tolist()
        dump(folder / 'dataset.json', [data])
        write_reader(folder, data, live)
        # 继续使用官方 parser 结果；无效答案照实保留，后续不从分母排除。
        parser_results = []
        for phase, responses in enumerate(data['communication_data']):
            for node, response in responses:
                agent = agents.Agent(data['system_prompts'][node], MODEL)
                agent.parser(response)
                answer = agent.last_response['answer']
                parser_results.append({'phase': phase, 'node': node, 'answer': answer,
                                       'valid_option': answer in ['A', 'B', 'C', 'D']})
        validation_error = None
        try:
            sources_unchanged = hashes == {str(p): sha(p) for p in source_files}
            repository_after = git_info()
            if not sources_unchanged:
                status = 'FAILED_SOURCE_CHANGED'
        except BaseException as exc:
            sources_unchanged, repository_after = None, None
            validation_error = {'error_type': type(exc).__name__}
            status = 'FAILED_PROVENANCE_CHECK'
        summary = {'status': status, 'mode': metadata['mode'], 'error': error,
                   'cleanup_error': cleanup_error, 'validation_error': validation_error,
                   'attempted_requests': gate.attempted, 'successful_requests': gate.successful,
                   'paid_request_attempts': gate.submitted if live else 0,
                   'completed_rounds': len(data['communication_data']),
                   'input_tokens': gate.prompt_tokens, 'output_tokens': gate.output_tokens,
                   'cache_hit_tokens': gate.hit_tokens, 'cache_miss_tokens': gate.miss_tokens,
                   'estimated_cost_cny_if_all_off_peak': gate.peak_cost / 2,
                   'estimated_cost_cny_if_all_peak': gate.peak_cost,
                   'cost_note': 'Scenario estimates using recorded usage; actual time-dependent platform bill is authoritative.',
                   'official_parser_results': parser_results,
                   'invalid_outputs': sum(not row['valid_option'] for row in parser_results),
                   'truncated_outputs': sum(row['finish_reason'] == 'length' for row in gate.records),
                   'official_sources_unchanged': sources_unchanged,
                   'official_repo_after': repository_after, 'output_folder': str(folder),
                   'paper_reproduction_complete': False}
        dump(folder / 'run_summary.json', summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0 if status in ['COMMUNICATION_COMPLETE', 'OFFLINE_CHECK_PASS'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='实际调用模型；默认只离线检查。')
    args = parser.parse_args()
    print('正在加载本地官方代码，请等待密钥提示；此时尚未发送模型请求。' if args.run else '正在执行离线检查，不发送API请求。', flush=True)
    # 临时目录位于E盘；不给模型或库触发C盘下载/缓存的机会。
    scratch = PROJECT / 'runs/local_temp'
    scratch.mkdir(parents=True, exist_ok=True)
    os.environ['TEMP'] = os.environ['TMP'] = str(scratch)
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    try:
        return asyncio.run(execute(live=args.run))
    except BaseException as exc:
        # 初始化故障也不输出可能含认证信息的原始异常。
        print(f'启动未完成：{type(exc).__name__}；未自动重试。', flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
