import inspect
from types import SimpleNamespace

from shrink.adapters.public_api import PublicAPI
from shrink.persistence.task_inputs import TaskInputsRepository
from tests.test_agent_tasks import runtime  # noqa: F401
from tests.test_task_inputs import stored_task


def test_compose_remains_readonly_despite_internal_archival():
    manifest = PublicAPI(SimpleNamespace()).manifest()
    capability = manifest["capabilities"][0]
    assert capability["actions"]["compose"]["readonly"] is True
    assert capability["actions"]["record_sent"]["readonly"] is False


def test_task_status_rules_belong_to_service_not_input_repository(runtime):
    op, _, _ = runtime
    assert "UPDATE operation_tasks" not in inspect.getsource(TaskInputsRepository)
    assert all(not hasattr(op.task_inputs.repository, name) for name in ("resume", "cancel", "interrupt"))
    stored_task(op, "needs_attention")
    op.task_states.resume("task")
    assert op.detail("task")["status"] == "queued"
    op.task_states.start("task")
    op.task_states.recover()
    assert op.detail("task")["status"] == "needs_attention"
    op.task_states.cancel("task")
    assert op.detail("task")["status"] == "cancelled"
