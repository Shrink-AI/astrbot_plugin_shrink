import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from shrink.services.task_context import CURRENT_TASK
from tests.test_agent_tasks import create, runtime  # noqa: F401
from tests.test_task_inputs import stored_task


@pytest.mark.parametrize("kind", ["agent", "development"])
async def test_interrupted_resume_enforces_existing_capacity(runtime, kind):
    op, event, _ = runtime
    await create(op)
    stored_task(op, "needs_attention")
    with op.database.transaction() as con:
        con.execute("UPDATE operation_tasks SET kind=? WHERE id='task'", (kind,))
    count = 12 if kind == "agent" else 1
    for i in range(count):
        stored_task(op, "running", f"busy-{i}")
    if kind == "development":
        with op.database.transaction() as con:
            con.execute("UPDATE operation_tasks SET kind='development' WHERE id='busy-0'")
        op.running["busy-0"] = asyncio.create_task(asyncio.Event().wait())
    op.invoke_agent = AsyncMock(return_value="unexpected execution")
    try:
        with pytest.raises(ValueError, match="12|开发任务"):
            await op.orchestrate(event, "resume", {"task_id": "task"})
        assert op.detail("task")["status"] == "needs_attention"
    finally:
        for task in op.running.values():
            task.cancel()
        await asyncio.gather(*op.running.values(), return_exceptions=True)


async def test_resume_reconciles_only_unmatched_tool_starts(runtime):
    op, event, _ = runtime
    await create(op)
    stored_task(op, "needs_attention")
    op.task_inputs.repository.checkpoint("task", [{"role": "user", "content": "original"}])
    token = CURRENT_TASK.set("task")
    try:
        op.record("tool_start:write", result=json.dumps({"id": "done"}))
        op.record("tool_end:write", result=json.dumps({"arguments": {"id": "done"}, "result": "ok"}))
        op.record("tool_start:write", result=json.dumps({"id": "unknown"}))
        op.record("tool_start:read", result=json.dumps({"id": "read"}))
        op.record("tool_end:read", result=json.dumps({"arguments": {"id": "read"}, "result": "ok"}))
    finally:
        CURRENT_TASK.reset(token)
    seen = []
    async def invoke(*args):
        seen.extend(op.task_inputs.repository.context("task"))
        return "done"
    op.invoke_agent = invoke
    await op.orchestrate(event, "resume", {"task_id": "task"})
    await op.running["task"]
    text = json.dumps(seen, ensure_ascii=False)
    assert "结果未知" in text and "先核对" in text and "不重复写入" in text
    assert "unknown" in text and '"done"' not in text


async def test_developer_resume_respects_still_live_development_coroutine(runtime):
    op, event, _ = runtime
    await create(op)
    stored_task(op, "needs_attention")
    stored_task(op, "completed", "busy")
    with op.database.transaction() as con:
        con.execute("UPDATE operation_tasks SET kind='development'")
    op.running["busy"] = asyncio.create_task(asyncio.Event().wait())
    op.invoke_agent = AsyncMock(return_value="unexpected")
    try:
        with pytest.raises(ValueError, match="开发任务"):
            await op.orchestrate(event, "resume", {"task_id": "task"})
    finally:
        for task in op.running.values():
            task.cancel()
        await asyncio.gather(*op.running.values(), return_exceptions=True)
