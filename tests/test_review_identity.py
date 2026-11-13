import pytest

from shrink.services.task_context import CURRENT_TASK
from tests.test_agent_tasks import runtime  # noqa: F401
from tests.test_task_inputs import message, stored_task


@pytest.mark.parametrize("target", ["self", "other"])
async def test_background_task_cannot_append_as_original_creator(runtime, target):
    op, event, _ = runtime
    stored_task(op, task_id="self")
    stored_task(op, task_id="other")
    token = CURRENT_TASK.set("self")
    try:
        with pytest.raises(ValueError, match="后台|同构体|原用户"):
            await op.orchestrate(message(event), "append_input", {
                "task_id": target, "text": "model-generated instruction"})
    finally:
        CURRENT_TASK.reset(token)
    assert not op.detail(target)["inputs"]
