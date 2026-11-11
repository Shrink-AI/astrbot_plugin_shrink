"""Task result policy; durable sending belongs to conversation.notifications."""
from ..services.task_reporting import delivery_owner, execution_report
from ..persistence.task_reports import TaskReports


async def deliver_task_result(operations, task_id, event):
    row = operations.owned_task(event, task_id)
    if row['parent'] or row['status'] in {'cancelled', 'blocked', 'awaiting_deployment'}:
        return
    if row['delivery'] in {'platform_accepted', 'sending', 'unknown'}:
        return
    reports = TaskReports(operations.database)
    if delivery_owner(row, operations.list()):
        reports.delivery(task_id, 'delegated')
        return
    key = 'task-result:' + task_id
    await operations.notifications.send_once(
        event, key, execution_report(row), request=row['request'],
        kind='question' if row['status'] == 'awaiting_input' else 'task_result')
    receipt = reports.receipt(row, key)
    if receipt:
        reports.delivery(task_id, receipt)
