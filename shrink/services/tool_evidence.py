"""Optional v1 tool result contract; no plugin names or prose-based success inference."""
import json


def execution_evidence(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return None
    if not isinstance(value, dict):
        return None
    evidence = value.get('execution')
    if not isinstance(evidence, dict) or evidence.get('version') != 1:
        return None
    if evidence.get('status') not in {'submitted', 'running', 'completed', 'failed', 'unknown'}:
        return None
    result = {'version': 1, 'status': evidence['status']}
    for key in ('task_id', 'receipt_id'):
        if isinstance(evidence.get(key), str):
            result[key] = evidence[key][:200]
    return result
