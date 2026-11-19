"""Optional v1 tool result contract; no plugin names or prose-based success inference."""
import json


def json_value(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None
    return value


def execution_evidence(value):
    value = json_value(value)
    if not isinstance(value, dict):
        return None
    evidence = value.get('execution')
    if not isinstance(evidence, dict) or evidence.get('version') != 1:
        return None
    desktop = evidence.get("domain") == "desktop"
    statuses = {'submitted', 'running', 'completed', 'failed', 'unknown'}
    if desktop:
        statuses |= {"not_sent", "awaiting_receipt", "unconfirmed"}
    status = evidence.get("status")
    if not isinstance(status, str) or status not in statuses:
        return None
    result = {'version': 1, 'status': status}
    if desktop:
        result["domain"] = "desktop"
        if evidence.get("reason") == "device_offline":
            result["reason"] = "device_offline"
    for key in ('task_id', 'receipt_id'):
        if isinstance(evidence.get(key), str):
            result[key] = evidence[key][:200]
    return result


def desktop_tool_evidence(name, args, value):
    if not isinstance(args, dict):
        return None
    if name == "shrink_plugin_capability":
        if (args.get("action") != "call" or args.get("capability") not in ("computer.control", "computer.tasks")
                or args.get("provider") not in (None, "", "astrbot_plugin_shrink_desktop")):
            return None
        action = args.get("operation")
    elif name == "shrink_computer":
        action = args.get("action")
    else:
        return None
    if action not in ("start", "submit", "resume", "send", "result"):
        return None
    evidence = execution_evidence(value)
    return evidence if evidence and evidence.get("domain") == "desktop" else None
