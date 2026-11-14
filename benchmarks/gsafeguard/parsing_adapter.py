"""G-Safeguard 选择题回复适配器；不修改官方仓库，不调用模型 API。

只从显式 ANSWER 字段读答案，不从解释中的零散 A/B/C/D 猜测答案。
原始回复始终放在 raw_response；reason 只移除明确的字段包装。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from types import MethodType
from typing import Iterable


@dataclass(frozen=True)
class ParsedAnswer:
    raw_response: str
    answer: str | None
    reason: str
    valid: bool
    status: str
    candidates: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)


_REASON_CLOSED = re.compile(
    r'<(?P<label>REASON|UPDATED_REASON)\s*>\s*:?(?P<body>.*?)</(?P=label)\s*>',
    re.IGNORECASE | re.DOTALL,
)
_FIELD_OPEN = re.compile(r'<[A-Z_]+\s*>', re.IGNORECASE)
_ANSWER_OPEN = re.compile(r'<ANSWER\s*>', re.IGNORECASE)
_ANSWER_CLOSE = re.compile(r'</ANSWER\s*>', re.IGNORECASE)
_REASON_OPEN = re.compile(r'<(?:REASON|UPDATED_REASON)\s*>\s*:?', re.IGNORECASE)


def parse_answer(response: str, allowed_options: Iterable[str] = ('A', 'B', 'C', 'D')) -> ParsedAnswer:
    """接受 XML 字段及冒号字段；缺失、歧义、越界或非单字母答案无效。

    重复且相同的显式答案可以接受；任一显式答案字段格式无效，整条回复
    即无效。该规则避免在 "A or B"、多个冲突字段中任意挑选一个答案。
    """
    if not isinstance(response, str):
        raise TypeError('Model response must be a string; preserve the API error separately.')
    options = tuple(str(x).strip().upper() for x in allowed_options)
    if not options or len(set(options)) != len(options) or any(not re.fullmatch('[A-Z]', x) for x in options):
        raise ValueError('allowed_options must be distinct single letters.')

    # 闭合的 REASON 里面即使引用 ANSWER 字段，也不能冒充最终答案。
    search_chars = list(response)
    closed_reasons = list(_REASON_CLOSED.finditer(response))
    for match in closed_reasons:
        for i in range(match.start(), match.end()):
            if search_chars[i] not in '\r\n':
                search_chars[i] = ' '
    # 示例代码块也不属于最终答案字段。
    for match in re.finditer(r'```.*?```|~~~.*?~~~', response, re.DOTALL):
        for i in range(match.start(), match.end()):
            if search_chars[i] not in '\r\n':
                search_chars[i] = ' '
    searchable = ''.join(search_chars)

    fields = list(_ANSWER_OPEN.finditer(searchable))
    candidates = []
    field_spans = []
    errors = []
    for opening in fields:
        next_field = _FIELD_OPEN.search(searchable, opening.end())
        closing = _ANSWER_CLOSE.search(searchable, opening.end())
        boundary = next_field.start() if next_field else len(searchable)
        # 已闭合REASON被遮罩以免误读其中引用的ANSWER，但其起点仍是答案字段的结束边界。
        following_reasons = [m.start() for m in closed_reasons if m.start() >= opening.end()]
        if following_reasons:
            boundary = min(boundary, min(following_reasons))
        is_closed = closing is not None and closing.start() <= boundary
        content_end = closing.start() if is_closed else boundary
        payload = response[opening.end():content_end].strip()
        field_spans.append((opening.start(), closing.end() if is_closed else content_end))
        has_colon = payload.startswith(':')
        if has_colon:
            payload = payload[1:].strip()
        if not is_closed and not has_colon:
            errors.append('unclosed_answer_field')
        elif not re.fullmatch('[A-Za-z]', payload):
            errors.append('not_single_letter')
        elif payload.upper() not in options:
            errors.append('outside_allowed_options')
        else:
            candidates.append(payload.upper())

    if closed_reasons:
        reason = '\n'.join(m.group('body').strip() for m in closed_reasons).strip()
    else:
        # 兼容 <REASON>: ... / <UPDATED_REASON>: ... 这种不闭合的官方格式。
        reason_chars = list(response)
        for start, end in field_spans:
            reason_chars[start:end] = ' ' * (end - start)
        reason = _REASON_OPEN.sub('', ''.join(reason_chars)).strip()

    if not fields:
        status = 'missing_answer_field'
    elif errors:
        status = errors[0]
    elif len(set(candidates)) != 1:
        status = 'conflicting_answers'
    else:
        status = 'ok'
    valid = status == 'ok'
    return ParsedAnswer(response, candidates[0] if valid else None,
                        reason if valid else response, valid, status, tuple(candidates))


def bind_answer_parser(agent, allowed_options: Iterable[str] = ('A', 'B', 'C', 'D')):
    """给一个官方 Agent 实例安装适配器，不改模块全局类或磁盘源码。

    Agent.chat()/achat() 会自动调用 self.parser(response)，随后原样把
    response 放进 memory。下一轮 AgentGraph 从 last_response 读取这里的
    answer/reason；parse_record 与 parse_history 留下可审计的解析结果。
    调用者应在付费生成下一轮之前检查 parse_record['valid']，无效则停止。
    """
    options = tuple(allowed_options)

    def adapted_parser(self, response):
        parsed = parse_answer(response, options)
        self.last_response = {'answer': parsed.answer, 'reason': parsed.reason}
        self.parse_record = parsed.to_dict()
        if not hasattr(self, 'parse_history'):
            self.parse_history = []
        self.parse_history.append(self.parse_record)
        return parsed

    agent.parser = MethodType(adapted_parser, agent)
    return agent


def require_valid_answers(agent_graph) -> None:
    """轮次闸门：全部节点解析通过后才允许进入下一轮或正式构图。"""
    invalid = [i for i, a in enumerate(agent_graph.agents)
               if not getattr(a, 'parse_record', {}).get('valid', False)]
    if invalid:
        raise ValueError(f'Invalid or missing answer fields at nodes {invalid}; do not propagate them.')
