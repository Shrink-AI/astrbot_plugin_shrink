import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from shrink.adapters.agent_tasks import AgentTasks
from shrink.adapters.operations import Operations
from shrink.services.task_context import CURRENT_TASK
from shrink.services.task_errors import TaskFailure, classify_error
from tests.test_agent_tasks import Event, create, runtime  # noqa: F401
from tests.test_incident_controls import host_modules  # noqa: F401
from tests.test_task_inputs import message


async def test_retry_wait_releases_slot_and_scheduler_requeues_once(runtime):
    op, event, _ = runtime
    await create(op)
    op.agent_slots = asyncio.Semaphore(1)
    calls = []
    async def invoke(h, e, request):
        calls.append(request)
        if request.startswith("retry") and len(calls) == 1:
            raise TimeoutError("temporary")
        return "done"
    op.invoke_agent = invoke
    result = await op.submit_agent(event, "researcher", "retry")
    identity = result["task_id"]
    await op.running[identity]
    row = op.detail(identity)
    assert row["status"] == "retry_wait" and op.agent_slots._value == 1
    op.deliver_agent_result.assert_not_awaited()
    second = await op.submit_agent(event, "researcher", "unrelated")
    await op.running[second["task_id"]]
    assert op.detail(second["task_id"])["status"] == "completed"
    await op.execution.tick(row["next_run"] - .01)
    assert len(calls) == 2
    await op.execution.tick(row["next_run"])
    await op.execution.tick(row["next_run"])
    await op.running[identity]
    assert len(calls) == 3 and op.detail(identity)["status"] == "completed"
    attempts = op.detail(identity)["attempts"]
    assert [a["status"] for a in attempts] == ["failed", "succeeded"]
    assert [a["retry_ordinal"] for a in attempts] == [0, 1]
    assert all(a["session_ref"] == event.unified_msg_origin and a["checkpoint_ref"] == identity for a in attempts)


async def test_restart_keeps_retry_deadline_and_unconsumed_input(runtime):
    op, event, _ = runtime
    await create(op)
    op.invoke_agent = AsyncMock(side_effect=TimeoutError())
    submitted = await op.submit_agent(event, "researcher", "work")
    identity = submitted["task_id"]
    await op.running[identity]
    row = op.detail(identity)
    await op.orchestrate(message(event), "append_input", {"task_id": identity, "text": "supplement"})
    restored = Operations(op.context, op.app, op.database.root / "plugins" / "core")
    restored.task_event = lambda row: event
    restored.deliver_agent_result = AsyncMock()
    async def invoke(*args):
        pending = restored.task_inputs.pending(identity)
        assert [x["text"] for x in pending] == ["supplement"]
        return "done"
    restored.invoke_agent = invoke
    assert restored.detail(identity)["next_run"] == row["next_run"]
    await restored.execution.tick(row["next_run"] - .1)
    assert not restored.running
    await restored.execution.tick(row["next_run"])
    await restored.running[identity]
    assert restored.detail(identity)["status"] == "completed"


async def test_t05_cancelled_task_only_resumes_explicitly_for_original_owner(runtime):
    op, event, _ = runtime
    await create(op)
    entered = asyncio.Event()
    async def execute(*args):
        op.record("tool_start:write", result='{"id":"original"}')
        op.record("tool_end:write", result='{"arguments":{"id":"original"},"result":"saved"}')
        entered.set()
        await asyncio.Event().wait()
    op.invoke_agent = execute
    submitted = await op.submit_agent(event, "researcher", "write one")
    identity = submitted["task_id"]
    await entered.wait()
    assert (await op.cancel(identity))["status"] == "cancelled"
    restored = Operations(op.context, op.app, op.database.root / "plugins" / "core")
    restored.deliver_agent_result = AsyncMock()
    await restored.execution.tick(10**12)
    assert not restored.running
    foreign = Event()
    foreign.get_sender_id = lambda: "someone-else"
    with pytest.raises(ValueError, match="自己"):
        await restored.orchestrate(foreign, "resume", {"task_id": identity})
    seen = []
    async def verify(*args):
        seen.extend(restored.task_inputs.repository.context(identity))
        return "verified existing object"
    restored.invoke_agent = verify
    resumed = await restored.orchestrate(event, "resume", {"task_id": identity})
    assert resumed["task_id"] == identity
    await restored.running[identity]
    assert len(restored.list()) == 1
    assert "不重复执行已完成步骤" in str(seen) and "original" in str(seen)
    assert [a["status"] for a in restored.detail(identity)["attempts"]] == ["cancelled", "succeeded"]


async def test_cancel_unknown_tool_result_holds_inputs_and_never_auto_retries(runtime):
    op, event, _ = runtime
    await create(op)
    entered = asyncio.Event()
    async def execute(*args):
        op.record("tool_start:write", result='{"id":"original"}')
        entered.set()
        await asyncio.Event().wait()
    op.invoke_agent = execute
    identity = (await op.submit_agent(event, "researcher", "write"))["task_id"]
    await entered.wait()
    await op.orchestrate(message(event), "append_input", {"task_id": identity, "text": "keep"})
    assert (await op.cancel(identity))["status"] == "reconciling"
    assert op.task_inputs.pending(identity) == []
    await op.execution.tick(10**12)
    assert len(op.detail(identity)["attempts"]) == 1
    assert op.detail(identity)["inputs"][0]["entered"] is None
    assert op.detail(identity)["attempts"][0]["status"] == "unknown"


async def test_t04_question_and_final_result_use_distinct_durable_receipts(runtime, host_modules):
    from shrink.services.task_execution import AwaitingUserInput
    op, event, _ = runtime
    await create(op)
    op.deliver_agent_result = AgentTasks.deliver_agent_result.__get__(op)
    event.send = AsyncMock(return_value=True)
    op.app.outbound = SimpleNamespace(compose=AsyncMock(return_value="safe reply"), record=lambda *a: None)
    async def execute(*args):
        identity = CURRENT_TASK.get()
        if not op.clarifications.get(identity):
            op.record("tool:read", result="completed read")
            op.clarifications.ask(identity, "which scope?", "already read", [])
            raise AwaitingUserInput()
        assert op.clarifications.get(identity)["answer"] == "original scope"
        return "finished"
    op.invoke_agent = execute
    identity = (await op.submit_agent(event, "researcher", "work"))["task_id"]
    await op.running[identity]
    assert op.detail(identity)["status"] == "waiting_user"
    assert event.send.await_count == 1
    result = await op.orchestrate(event, "resume", {"task_id": identity, "answer": "original scope"})
    assert result["task_id"] == identity
    await op.running[identity]
    assert event.send.await_count == 2
    receipts = op.database.rows("SELECT * FROM notification_receipts")
    assert len(receipts) == 2 and all(r["state"] == "platform_accepted" for r in receipts)
    assert op.detail(identity)["status"] == "completed"
    assert len([e for e in op.detail(identity)["events"] if e["action"] == "tool:read"]) == 1
    assert len(op.list()) == 1


@pytest.mark.parametrize("error,expected", [
    (TimeoutError(), "network"), (ConnectionError(), "network"),
    (ValueError(), "invalid_request"), (KeyError("missing tool"), "invalid_request"),
    (PermissionError(), "permission"), (json.JSONDecodeError("format", "", 0), "output_format"),
    (RuntimeError("offline"), "unavailable"),
])
def test_host_independent_exception_classification(error, expected):
    assert classify_error(error).category == expected


@pytest.mark.parametrize("code,category", [(429, "network"), (503, "network"), (403, "permission"), (400, "invalid_request")])
def test_http_error_status_classification(code, category):
    error = RuntimeError("opaque")
    error.status_code = code
    assert classify_error(error).category == category


async def test_stop_without_acknowledgement_reconciles_and_blocks_early_resume(runtime):
    op, event, _ = runtime
    await create(op)
    entered, release = asyncio.Event(), asyncio.Event()
    async def execute(*args):
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        return "late result"
    op.invoke_agent = execute
    op.execution.stop_timeout = .01
    identity = (await op.submit_agent(event, "researcher", "work"))["task_id"]
    await entered.wait()
    try:
        assert (await op.cancel(identity))["status"] == "reconciling"
        with pytest.raises(ValueError, match="尚未停止"):
            await op.orchestrate(event, "resume", {"task_id": identity})
        assert op.detail(identity)["stop_requested"] == 1
    finally:
        release.set()
        await op.running[identity]
    assert op.detail(identity)["status"] == "cancelled"
    op.deliver_agent_result.assert_not_awaited()


async def test_http_resume_preserves_task_identity_and_exposes_attempts(runtime):
    from fastapi import FastAPI
    import httpx
    from shrink.api.operations import router_for
    from shrink.services.task_execution import AwaitingUserInput
    op, event, _ = runtime
    await create(op)
    async def execute(*args):
        identity = CURRENT_TASK.get()
        if not op.clarifications.get(identity):
            op.clarifications.ask(identity, "which?", "facts", [])
            raise AwaitingUserInput()
        return "done"
    op.invoke_agent = execute
    identity = (await op.submit_agent(event, "researcher", "work"))["task_id"]
    await op.running[identity]
    op.app.operations = op
    op.task_event = lambda row: event
    api = FastAPI()
    api.include_router(router_for(op.app))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        detail = (await client.get("/api/v1/operations/tasks/" + identity)).json()
        assert detail["status"] == "waiting_user" and len(detail["attempts"]) == 1
        reply = await client.post("/api/v1/operations/tasks/" + identity + "/resume", json={"answer": "yes"})
        assert reply.status_code == 200 and reply.json()["task_id"] == identity
        await op.running[identity]
        detail = (await client.get("/api/v1/operations/tasks/" + identity)).json()
        assert detail["status"] == "completed" and len(detail["attempts"]) == 2
        assert len((await client.get("/api/v1/operations/tasks")).json()) == 1


async def test_permission_denial_waits_for_answer_without_automatic_retry(runtime):
    op, event, _ = runtime
    await create(op)
    op.invoke_agent = AsyncMock(side_effect=PermissionError("denied"))
    identity = (await op.submit_agent(event, "researcher", "work"))["task_id"]
    await op.running[identity]
    assert op.detail(identity)["status"] == "waiting_user"
    assert op.clarifications.get(identity)["question"]
    await op.execution.tick(10**12)
    op.invoke_agent.assert_awaited_once()


async def test_dependency_failure_does_not_execute_or_duplicate_origin_report(runtime):
    op, event, _ = runtime
    await create(op)
    op.invoke_agent = AsyncMock(side_effect=TaskFailure("unavailable"))
    first = (await op.submit_agent(event, "researcher", "source"))["task_id"]
    second = (await op.submit_agent(event, "researcher", "summary", [first]))["task_id"]
    await op.running[second]
    op.invoke_agent.assert_awaited_once()
    op.deliver_agent_result.assert_awaited_once_with(first, event)
    assert op.detail(second)["status"] == "needs_attention"
    assert op.detail(second)["attempts"] == []
