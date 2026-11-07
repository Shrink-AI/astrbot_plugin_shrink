"""Auditable validation of learned expression habits."""

import hashlib
import re


def style_decision(style, evidence, samples, current, rejected):
    if not isinstance(style, str) or not style.strip():
        return "invalid_format", "候选表达习惯为空或不是文本"
    if len(style) > 400:
        return "invalid_length", "候选超过400字"
    if style.strip() == current.strip():
        return "unchanged", "模型保留原表达习惯"
    if not isinstance(evidence, list) or not evidence:
        return "missing_evidence", "候选缺少样本依据"
    if any(not isinstance(x, str) or x not in samples for x in evidence):
        return "invalid_evidence", "依据不是本批样本ID"
    if hashlib.sha256(style.encode()).hexdigest() in rejected:
        return "rollback_protected", "候选已被管理员回退"
    # Match operations on protected settings, not ordinary adverbs such as 始终.
    forbidden = re.search(
        r"(忽略|覆盖|绕过|无视|替换|泄露|输出).{0,16}(系统|规则|提示|指令|权限)|"
        r"(修改|更改|关闭|开启|禁用|启用|提升|取消|删除).{0,12}"
        r"(权限|采集|学习开关|字数|长度限制|短句规则)|"
        r"(你叫|改名|名字改|不再叫石英)|"
        r"(ignore|override|bypass).{0,30}(system|instruction|permission|rule)",
        style,
        re.I,
    )
    if forbidden:
        return "fixed_rule_conflict", "候选要求修改或绕过固定规则"
    if re.search(r"系统提示|工具权限|采集设置|字数限制|长度规则|system prompt", style, re.I):
        return "needs_review", "涉及受保护设置，无法确认仅为表达习惯"
    return "eligible", "依据有效，可应用表达习惯"
