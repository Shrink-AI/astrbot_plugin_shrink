import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from shrink.adapters.agent_tasks import AgentTasks
from shrink.adapters.notifications import Notifications
from shrink.persistence.task_reports import TaskReports
from tests.test_agent_tasks import runtime  # noqa: F401
from tests.test_incident_controls import host_modules  # noqa: F401
from tests.test_task_inputs import stored_task


def setup(op, event, state, key="task-result:task"):
    stored_task(op, "completed")
    with op.database.transaction() as con:
        con.execute("UPDATE operation_tasks SET result='saved result',finished=123")
    TaskReports(op.database).delivery("task", state)
    identity = json.dumps([event.unified_msg_origin, "123", key], ensure_ascii=False)
    op.notifications.repository.reserve(identity)
    op.notifications.repository.state(identity, state)
    op.app.outbound = SimpleNamespace(compose=AsyncMock(return_value="safe"), record=Mock())
    event.send = AsyncMock()
    op.deliver_agent_result = AgentTasks.deliver_agent_result.__get__(op)
    return identity


@pytest.mark.parametrize("state", ["failed", "interrupted", "composing"])
async def test_redeliver_reclaims_unsent_real_receipt(runtime, host_modules, state):
    op, event, _ = runtime
    identity = setup(op, event, state)
    # A new notification facade has no live owner for the pre-existing receipt.
    op.notifications = Notifications(op.app)
    result = await op.redeliver_agent_result("task", event)
    assert result["delivery"] == "platform_accepted"
    assert op.notifications.repository.get(identity)["state"] == "platform_accepted"
    event.send.assert_awaited_once()
    assert op.detail("task")["result"] == "saved result"


@pytest.mark.parametrize("state", ["sending", "unknown", "platform_accepted", "silent"])
async def test_redeliver_never_reclaims_protected_receipt(runtime, host_modules, state):
    op, event, _ = runtime
    identity = setup(op, event, state)
    # Receipt remains authoritative even when the task projection is stale.
    TaskReports(op.database).delivery("task", "failed")
    await op.redeliver_agent_result("task", event)
    assert op.notifications.repository.get(identity)["state"] == state
    event.send.assert_not_awaited()
    op.app.outbound.compose.assert_not_awaited()


async def test_explicit_retry_cannot_steal_live_composition(runtime, host_modules):
    op, event, _ = runtime
    setup(op, event, "failed")
    entered, release = asyncio.Event(), asyncio.Event()
    async def compose(*args, **kwargs):
        entered.set()
        await release.wait()
        return "safe"
    op.app.outbound.compose.side_effect = compose
    first = asyncio.create_task(op.notifications.send_once(event, "live", {}, retry=True))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert not await Notifications(op.app).send_once(event, "live", {}, retry=True)
    finally:
        release.set()
        await first
    event.send.assert_awaited_once()


@pytest.mark.parametrize("state", [
    "failed", "interrupted", "composing", "sending", "unknown", "platform_accepted", "silent",
])
async def test_deployment_recovers_unsent_once_and_does_not_rewrite_terminal_task(
        runtime, host_modules, monkeypatch, state):
    op, event, _ = runtime
    identity = setup(op, event, state, "deployment:deploy")
    op.task_inputs.repository.checkpoint("task", [{"role": "user", "content": "deployment context"}])
    op.context.send_message = AsyncMock(return_value=True)
    directory = op.deployments.root / "deploy"
    directory.mkdir()
    row = {"id": "deploy", "task_id": "task", "status": "completed",
           "session": event.unified_msg_origin, "sender": "123"}
    op.deployments.listing = lambda: [row]
    save = Mock(wraps=op.deployments.save_notification)
    op.deployments.save_notification = save
    snapshots = []
    async def tick(*args):
        snapshots.append(op.detail("task"))
        if len(snapshots) == 3:
            raise asyncio.CancelledError()
    monkeypatch.setattr(asyncio, "sleep", tick)
    with pytest.raises(asyncio.CancelledError):
        await op.deployments.loop()
    retryable = state in {"failed", "interrupted", "composing"}
    assert op.context.send_message.await_count == int(retryable)
    assert op.notifications.repository.get(identity)["state"] == (
        "platform_accepted" if retryable else state)
    assert snapshots[0] == snapshots[1] == snapshots[2]
    assert op.task_inputs.repository.context("task") is None
    if not retryable:
        assert snapshots[0]["finished"] == 123
    assert save.call_count == 1


async def test_deployment_failed_expression_waits_for_next_explicit_recovery(
        runtime, host_modules, monkeypatch):
    op, event, _ = runtime
    setup(op, event, "interrupted", "deployment:deploy")
    op.app.outbound.compose.side_effect = RuntimeError("expression unavailable")
    op.context.send_message = AsyncMock()
    (op.deployments.root / "deploy").mkdir()
    op.deployments.listing = lambda: [{
        "id": "deploy", "task_id": "task", "status": "completed",
        "session": event.unified_msg_origin, "sender": "123"}]
    snapshots = []
    async def tick(*args):
        snapshots.append(op.detail("task"))
        if len(snapshots) == 3:
            raise asyncio.CancelledError()
    monkeypatch.setattr(asyncio, "sleep", tick)
    with pytest.raises(asyncio.CancelledError):
        await op.deployments.loop()
    op.app.outbound.compose.assert_awaited_once()
    op.context.send_message.assert_not_awaited()
    assert snapshots[0] == snapshots[1] == snapshots[2]
