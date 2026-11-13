import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from shrink.adapters.operations import Operations
from shrink.persistence.database import Database
from tests.test_agent_tasks import Event, create, runtime  # noqa: F401


def stored_task(op, status="running", task_id="task"):
    with op.database.transaction() as con:
        con.execute("INSERT INTO operation_tasks("
                    "id,request,session,sender,status,started,provider,kind,agent)"
                    " VALUES(?,?,?,?,?,0,?,'agent','researcher')",
                    (task_id, "原任务", Event.unified_msg_origin, "123", status, "provider"))
    return task_id


def message(event, identity="original-message"):
    event.message_obj = SimpleNamespace(message_id=identity)
    return event


async def test_append_returns_durable_receipt_without_waiting_or_starting_work(runtime):
    op, event, _ = runtime
    await create(op)
    gate = asyncio.Event()
    op.invoke_agent = AsyncMock(side_effect=lambda *a: None)

    async def waiting(*args):
        await gate.wait()
        return "done"
    op.invoke_agent.side_effect = waiting
    task = await op.submit_agent(event, "researcher", "original")
    message(event)
    first = await asyncio.wait_for(op.orchestrate(event, "append_input", {
        "task_id": task["task_id"], "text": "补充限制", "message_id": "forged"}), 1)
    second = await op.orchestrate(event, "append_input", {
        "task_id": task["task_id"], "text": "重复的不同文字", "message_id": "another"})
    assert first == second and first["status"] == "received"
    rows = op.detail(task["task_id"])["inputs"]
    assert len(rows) == 1 and rows[0]["message_id"] == "original-message"
    assert rows[0]["text"] == "补充限制" and rows[0]["sender"] == "123"
    assert not op.running[task["task_id"]].done() and len(op.list()) == 1
    gate.set()
    await op.running[task["task_id"]]
    op.invoke_agent.assert_awaited_once()


@pytest.mark.parametrize("wrong", ["sender", "session", "admin"])
async def test_append_rejects_other_authority(runtime, wrong):
    op, event, _ = runtime
    stored_task(op)
    message(event)
    if wrong == "sender":
        event.get_sender_id = lambda: "456"
    elif wrong == "session":
        event.unified_msg_origin = "qq:GroupMessage:other"
    else:
        event.is_admin = lambda: False
    with pytest.raises(ValueError):
        await op.orchestrate(event, "append_input", {"task_id": "task", "text": "new"})
    assert op.detail("task")["inputs"] == []


@pytest.mark.parametrize("status", [
    "completed", "cancelled", "failed", "needs_attention", "reconciling",
    "cancelling", "waiting_user",
])
async def test_closed_task_reports_state_without_creating_a_replacement(runtime, status):
    op, event, _ = runtime
    stored_task(op, status)
    with pytest.raises(ValueError, match=status):
        await op.orchestrate(message(event), "append_input", {"task_id": "task", "text": "new"})
    assert len(op.list()) == 1 and not op.detail("task")["inputs"]


@pytest.mark.parametrize("identity,text", [(None, "text"), ("", "text"), ("id", ""), ("id", " " * 4)])
async def test_append_requires_real_message_and_nonempty_text(runtime, identity, text):
    op, event, _ = runtime
    stored_task(op)
    with pytest.raises(ValueError):
        await op.orchestrate(message(event, identity), "append_input", {"task_id": "task", "text": text})
    assert not op.detail("task")["inputs"]


async def test_restart_preserves_positions_and_resumes_same_task(runtime):
    op, event, _ = runtime
    await create(op)
    stored_task(op)
    service = op.task_inputs
    for identity in ("processed", "entered", "received"):
        service.append("task", event.unified_msg_origin, "123", identity, identity)
    rows = service.pending("task")
    service.repository.checkpoint("task", [{"role": "user", "content": "saved context"}],
                                  entered=[rows[0]["id"], rows[1]["id"]],
                                  processed=[rows[0]["id"]])
    db = Database(op.database.root)
    app = SimpleNamespace(database=db, state=op.app.state, tasks=op.app.tasks)
    restarted = Operations(
        op.context, app, op.database.root / "plugins" / "core")
    restarted.deliver_agent_result = AsyncMock()
    try:
        assert restarted.detail("task")["status"] == "needs_attention"
        assert restarted.task_inputs.pending("task") == []
        seen = []
        async def invoke(*args):
            seen.extend(restarted.task_inputs.pending("task"))
            assert restarted.task_inputs.repository.context("task")[0]["content"] == "saved context"
            return "done"
        restarted.invoke_agent = invoke
        result = await restarted.orchestrate(event, "resume", {"task_id": "task"})
        assert result["task_id"] == "task"
        await restarted.running["task"]
        assert [r["message_id"] for r in seen] == ["entered", "received"]
        assert len(restarted.list()) == 1
    finally:
        db.close()


async def test_cancel_before_execution_prevents_input_delivery(runtime):
    op, event, _ = runtime
    await create(op)
    op.invoke_agent = AsyncMock(return_value="should not run")
    task = await op.submit_agent(event, "researcher", "queued")
    await op.orchestrate(message(event), "append_input", {"task_id": task["task_id"], "text": "new"})
    await op.cancel(task["task_id"])
    assert op.task_inputs.pending(task["task_id"]) == []
    assert op.detail(task["task_id"])["status"] == "cancelled"
    assert op.detail(task["task_id"])["inputs"][0]["entered"] is None
    op.invoke_agent.assert_not_awaited()


async def test_retry_and_summary_instructions_are_added_to_saved_context(runtime):
    op, event, _ = runtime
    stored_task(op)
    service = op.task_inputs
    service.append("task", event.unified_msg_origin, "123", "m1", "supplement")
    row = service.pending("task")[0]
    service.repository.checkpoint("task", [{"role": "user", "content": "supplement"}],
                                  entered=[row["id"]], processed=[row["id"]])
    await service.continue_with("task", "reconcile unknown writes")
    await service.continue_with("task", "summarize children")
    assert [m["content"] for m in service.repository.context("task")] == [
        "supplement", "reconcile unknown writes", "summarize children"]
    assert service.pending("task") == []


async def test_delivery_and_processing_positions_survive_service_recreation(runtime):
    from shrink.services.task_inputs import TaskInputs, TaskCancelled
    from shrink.persistence.task_inputs import TaskInputsRepository
    op, event, _ = runtime
    stored_task(op)
    service = op.task_inputs
    service.append("task", event.unified_msg_origin, "123", "m1", "once")
    messages = []
    append = lambda text: messages.append({"role": "user", "content": text})
    identities = await service.deliver("task", append, lambda: messages)
    assert service.pending("task")[0]["entered"] is not None
    restarted = TaskInputs(TaskInputsRepository(op.database))
    messages = restarted.repository.context("task")
    assert await restarted.deliver("task", append, lambda: messages) == identities
    assert len(messages) == 1
    await restarted.processed("task", messages, identities)
    assert await restarted.deliver("task", append, lambda: messages) == []
    restarted.append("task", event.unified_msg_origin, "123", "m2", "never delivered")
    restarted.tasks.cancel("task")
    with pytest.raises(TaskCancelled):
        await restarted.deliver("task", append, lambda: messages)
    assert len(messages) == 1


async def test_checkpoint_failure_does_not_acknowledge_input(runtime, monkeypatch):
    op, event, _ = runtime
    stored_task(op)
    service = op.task_inputs
    service.append("task", event.unified_msg_origin, "123", "m1", "retry")
    def broken(*args, **kwargs):
        raise OSError("disk unavailable")
    monkeypatch.setattr(service.repository, "checkpoint_async", broken)
    with pytest.raises(OSError):
        await service.deliver("task", lambda text: None, lambda: [])
    assert service.pending("task")[0]["entered"] is None


@pytest.mark.parametrize("started", [False, True])
async def test_shutdown_keeps_unconsumed_inputs_for_original_task_resume(runtime, started):
    op, event, _ = runtime
    await create(op)
    entered = asyncio.Event()
    async def wait(*args):
        entered.set()
        await asyncio.Event().wait()
    op.invoke_agent = wait
    result = await op.submit_agent(event, "researcher", "work")
    task_id = result["task_id"]
    if started:
        await asyncio.wait_for(entered.wait(), 1)
    await op.orchestrate(message(event), "append_input", {"task_id": task_id, "text": "keep me"})
    await op.close()
    assert op.detail(task_id)["status"] == ("needs_attention" if started else "queued")
    assert op.detail(task_id)["inputs"][0]["entered"] is None
    op.deliver_agent_result.assert_not_awaited()
    restarted = Operations(op.context, op.app, op.database.root / "plugins" / "core")
    restarted.deliver_agent_result = AsyncMock()
    seen = []
    async def consume(*args):
        seen.extend(restarted.task_inputs.pending(task_id))
        return "done"
    restarted.invoke_agent = consume
    resumed = await restarted.orchestrate(event, "resume", {"task_id": task_id})
    await restarted.running[task_id]
    assert resumed["task_id"] == task_id and len(restarted.list()) == 1
    assert len(seen) == 1 and seen[0]["text"] == "keep me"
