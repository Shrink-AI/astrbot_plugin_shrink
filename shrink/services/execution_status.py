"""Report only execution facts recorded by the tool hook."""

from ..prompts import render
from .tool_evidence import execution_evidence


def timeout_reply(steps, deadline):
    steps = steps or []
    for step in reversed(steps):
        evidence = execution_evidence({'execution': step.get('execution')})
        if evidence:
            return render('timeout.execution', status=evidence['status'],
                          task_id=evidence.get('task_id', ''), receipt_id=evidence.get('receipt_id', ''))
    if steps:
        return render("timeout.partial", deadline=deadline)
    return render("timeout.nothing", deadline=deadline)


