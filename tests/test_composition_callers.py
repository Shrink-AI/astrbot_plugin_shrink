import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from shrink.adapters.agent_tasks import AgentTasks
from shrink.services.composition import SILENT
from shrink.services.expression import ExpressionService
from tests.test_agent_tasks import create, runtime  # noqa: F401
from tests.test_incident_controls import Event, app, host_modules  # noqa: F401


async def test_timeout_notice_honors_silence_at_unified_entry(app, host_modules):
    app.outbound.compose.return_value = SILENT
    adapter = host_modules.ConversationAdapter(app, "test")
    event = Event()
    await adapter.send_gave_up(event, event.get_extra("shrink_turn"))
    event.send.assert_not_called()
    app.outbound.record.assert_not_called()
    assert app.database.rows("SELECT state FROM notification_receipts")[0]["state"] == "silent"


async def test_correction_silence_clears_reply_instead_of_falling_back(app, host_modules):
    text = "好，同步好了，等下直接推送到远端"
    app.expression = ExpressionService(
        app.state, SimpleNamespace(learned_persona=lambda: ""), None, AsyncMock(), app.tasks.error)
    app.expression.outbound = app.outbound
    app.messages = SimpleNamespace(recent_bot=Mock(return_value=[]))
    app.outbound.compose.return_value = SILENT
    adapter = host_modules.ConversationAdapter(app, "test")
    adapter.record_episode = Mock()
    event = Event()
    response = SimpleNamespace(tools_call_name=[], role="assistant", completion_text=text,
                               result_chain=None)
    await adapter.response(event, response)
    assert response.completion_text == ""
    assert event.get_extra("shrink_finished").skip


async def test_task_silence_preserves_result_without_reexecuting(runtime, host_modules):
    op, event, _ = runtime
    await create(op)
    op.invoke_agent = AsyncMock(return_value="secret task result")
    result = await op.submit_agent(event, "researcher", "work")
    await op.running[result["task_id"]]
    op.app.outbound = SimpleNamespace(compose=AsyncMock(return_value=SILENT), record=Mock())
    event.send = AsyncMock()
    await AgentTasks.deliver_agent_result(op, result["task_id"], event)
    assert op.detail(result["task_id"])["delivery"] == "silent"
    assert op.detail(result["task_id"])["result"] == "secret task result"
    op.invoke_agent.assert_awaited_once()
    event.send.assert_not_called()


@pytest.mark.parametrize("silent,accepted,state", [
    (True, True, "silent"), (False, True, "platform_accepted"), (False, False, "unknown"),
])
async def test_deployment_uses_unified_receipt_and_silence(
        runtime, host_modules, monkeypatch, silent, accepted, state):
    op, event, _ = runtime
    op.app.outbound = SimpleNamespace(
        compose=AsyncMock(return_value=SILENT if silent else "整理后的部署状态"), record=Mock())
    op.context.send_message = AsyncMock(return_value=accepted)
    row = {"id": "deployment", "status": "completed", "session": event.unified_msg_origin,
           "sender": "123", "result": "secret raw result"}
    directory = op.deployments.root / row["id"]
    directory.mkdir()
    op.deployments.listing = lambda: [row]
    async def end_loop(*args):
        raise asyncio.CancelledError()
    monkeypatch.setattr(asyncio, "sleep", end_loop)
    with pytest.raises(asyncio.CancelledError):
        await op.deployments.loop()
    receipt = json.loads((directory / "notification.json").read_text(encoding="utf8"))
    assert receipt["state"] == state
    assert "secret" not in receipt["text"]
    if silent:
        op.context.send_message.assert_not_called()
    else:
        op.context.send_message.assert_awaited_once()
    # A new loop never resends an accepted, silent or uncertain delivery.
    with pytest.raises(asyncio.CancelledError):
        await op.deployments.loop()
    assert op.context.send_message.await_count == (0 if silent else 1)
