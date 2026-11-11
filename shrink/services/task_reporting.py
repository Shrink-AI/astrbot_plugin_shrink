"""Facts used by foreground correction and durable background reporting."""
import json


def execution_report(task):
    """Return audited records, never promote model prose to execution evidence."""
    records = []
    for event in task.get('events', []):
        if event.get('action', '').startswith(('tool:', 'tool_end:')) or event.get('action') in {
                'write', 'check', 'test', 'deploy', 'rollback'}:
            records.append({k: event.get(k, '') for k in ('action', 'plugin', 'path', 'result')})
    return {'task_id': task['id'], 'status': task['status'],
            'tool_records': records, 'result_summary_unverified': task.get('result', '')}


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
        if candidate['status'] not in {'queued', 'running', 'waiting'}:
            continue
        if task['id'] in json.loads(candidate.get('dependencies') or '[]'):
            return candidate['id']
    return None
