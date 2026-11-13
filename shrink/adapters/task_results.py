"""Task result policy; durable sending belongs to conversation.notifications."""
from ..services.task_reporting import delivery_owner, execution_report
from ..persistence.task_reports import TaskReports


async def deliver_task_result(operations, task_id, event, *, retry=False):
    row = operations.owned_task(event, task_id)
    if row['parent'] or row['status'] in {'cancelled', 'queued', 'running', 'waiting_external', 'retry_wait', 'cancelling', 'reconciling'}:
        return
    if row['delivery'] in {'platform_accepted', 'sending', 'unknown', 'silent'}:
        return
    reports = TaskReports(operations.database)
    if delivery_owner(row, operations.list()):
        reports.delivery(task_id, 'delegated')
        return
    attempts = row.get('attempts', [])
    key = 'task-result:' + task_id
    if row['status'] == 'waiting_user':
        question = operations.clarifications.get(task_id)
        key = 'task-question:' + task_id + ':' + str(question['created'])
    elif len(attempts) > 1:
        key += ':' + attempts[-1]['id']
    try:
        await operations.notifications.send_once(
            event, key, execution_report(row), request=row['request'],
            kind='question' if row['status'] == 'waiting_user' else 'task_result', retry=retry)
    finally:
        receipt = reports.receipt(row, key)
        if receipt:
            reports.delivery(task_id, receipt)
