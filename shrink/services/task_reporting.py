"""Facts used by foreground correction and durable background reporting."""
import json
import re
from .tool_projection import receipt, brief
from .tool_evidence import desktop_tool_evidence, json_value

DESKTOP_REQUEST = re.compile(
    r"windows\s*(?:本机|电脑|上|桌面|后台)|(?:在|用|交给|发到)(?:我的|那台|这台)?(?:本机|电脑|桌面端)"
    r"|shrinkpc|shrink_computer|computer\.tasks", re.I)
DESKTOP_WORK = re.compile(r"做|跑|执行|运行|启动|训练|微调|安装|产出|生成|部署|后台|交给|派给|派工|委派")


def desktop_requested(text):
    value = str(text or "")
    return bool(DESKTOP_REQUEST.search(value) and DESKTOP_WORK.search(value))


def desktop_execution(task):
    confirmed = {}
    for event in task.get("events", []):
        prefix, sep, name = event.get("action", "").partition(":")
        if not sep or prefix not in {"tool", "tool_end"}:
            continue
        envelope = json_value(event.get("result"))
        if not isinstance(envelope, dict) or envelope.get("error"):
            continue
        evidence = desktop_tool_evidence(name, envelope.get("arguments"), envelope.get("result"))
        if evidence and evidence.get("receipt_id"):
            identity = evidence["receipt_id"]
            if (identity not in confirmed or evidence["status"] not in {"awaiting_receipt", "unconfirmed"}
                    or confirmed[identity]["status"] in {"awaiting_receipt", "unconfirmed"}):
                confirmed[identity] = evidence
    return next(reversed(confirmed.values()), None) or (
        {"status": "unconfirmed"} if desktop_requested(task.get("request")) else None)


def execution_report(task):
    """Return audited records, never promote model prose to execution evidence."""
    records = []
    for event in task.get('events', []):
        if event.get('action', '').startswith(('tool:', 'tool_end:')) or event.get('action') in {
                'write', 'check', 'test', 'deploy', 'rollback'}:
            records.append({"action": event["action"], "record_ref": f"task-event:{event.get('id', '')}",
                            "result": json.dumps(receipt(event.get("result", ""),
                                f"task-event:{event.get('id', '')}", 240), ensure_ascii=False)})
    desktop = desktop_execution(task)
    return {'task_id': task['id'], 'status': task['status'],
            'tool_records': records[-4:], 'tool_record_count': len(records),
            'result_ref': "task:" + task["id"],
            **({"desktop_execution": desktop} if desktop else {}),
            'result_summary_unverified': brief(task.get('result', ''), 2500)}


def delivery_owner(task, candidates):
    """Delegate successful work only to a live, same-owner downstream aggregator.

    Failure is reported by its origin. A blocked dependent has no independent
    failure to announce. Historical, cancelled and foreign tasks cannot swallow
    this task's result.
    """
    if task['status'] != 'completed':
        return None
    for candidate in candidates:
        if candidate.get('kind') != 'agent' or candidate.get('parent'):
            continue
        if (candidate['session'], str(candidate['sender'])) != (task['session'], str(task['sender'])):
            continue
        if candidate['status'] not in {'queued', 'running', 'waiting_external', 'retry_wait'}:
            continue
        if task['id'] in json.loads(candidate.get('dependencies') or '[]'):
            return candidate['id']
    return None
