import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from shrink.application import Application
from shrink.adapters.notifications import Notifications
from shrink.adapters.plugin_protocol import CapabilityClient
from shrink.adapters.public_api import PublicAPI
from shrink.services.composition import ChatReply, Silent


@pytest.fixture
def app(tmp_path):
    model = AsyncMock(side_effect=RuntimeError("secret exception"))
    value = Application(tmp_path, model, model, model)
    yield value
    value.database.close()


@pytest.mark.parametrize("facts", [
    '{"logs":["secret tool JSON"]}', {"answer": "secret answer", "body": "secret event"},
    "secret log line", "secret prompt", {"status": ["secret invalid state"]},
])
@pytest.mark.parametrize("failure", ["timeout", "empty", "error"])
async def test_raw_material_never_becomes_fallback_and_is_retained(app, facts, failure):
    model = app.expression.model
    if failure == "timeout":
        async def hang(*args):
            await asyncio.Event().wait()
        model.side_effect = hang
        app.outbound.attempt_timeout = 0.001
    elif failure == "empty":
        model.side_effect = None
        model.return_value = " "
    text = await app.outbound.compose(facts, kind="tool_result")
    assert isinstance(text, str) and "secret" not in text
    rows = app.database.rows("SELECT * FROM outbound_compositions")
    assert len(rows) == 1 and json.loads(rows[0]["facts"]) == facts
    assert rows[0]["text"] == text
    assert model.await_count == (1 if failure == "error" else 2)


async def test_only_explicit_chat_reply_can_survive_a_failed_rewrite(app):
    reply = '{"answer":"用户明确要求的 JSON 答复"}'
    assert await app.outbound.compose(ChatReply(reply), kind="tool_result") == reply
    assert await app.outbound.compose(reply, kind="tool_result") != reply
    finished = await app.expression.finish(
        "主模型写好的聊天回复", user_text="说一下", form="tool_result", reason="direct", recent=[])
    assert finished.text == "主模型写好的聊天回复"


async def test_chat_finish_honors_an_explicit_silent_result(app):
    from shrink.services.composition import SILENT
    app.outbound.compose = AsyncMock(return_value=SILENT)
    result = await app.expression.finish(
        "old reply", user_text="test", form="tool_result", reason="direct", recent=[])
    assert result.skip and result.text == ""


async def test_expression_retry_only_calls_expression_and_keeps_one_source_record(app):
    app.expression.model.side_effect = [TimeoutError("secret"), "整理好的结果"]
    assert await app.outbound.compose({"body": "original"}) == "整理好的结果"
    assert app.expression.model.await_count == 2
    assert len(app.database.rows("SELECT * FROM outbound_compositions")) == 1


@pytest.mark.parametrize("status,expected", [
    ("running", "仍在执行"), ("completed", "执行已结束"), ("failed", "执行失败"),
    ("cancelled", "已停止"), ("waiting_user", "等待你的决定"), ("unknown", "还不能"),
])
async def test_necessary_fallback_uses_only_known_status(app, status, expected):
    text = await app.outbound.compose(
        {"status": status, "body": "secret body", "answer": "secret prompt"}, kind="task_result")
    assert expected in text and "secret" not in text


async def test_silence_is_persisted_and_never_sent_or_recorded_as_chat(app):
    event = SimpleNamespace(
        unified_msg_origin="qq:GroupMessage:1", get_sender_id=lambda: "1",
        get_extra=lambda *a: None, send=AsyncMock())
    notices = Notifications(app)
    app.outbound.record = AsyncMock()
    assert isinstance(await app.outbound.compose("secret log", kind="progress"), Silent)
    assert not await notices.send_once(event, "stable", "secret log")
    assert not await notices.send_once(event, "stable", "secret log")
    event.send.assert_not_awaited()
    app.outbound.record.assert_not_called()
    receipt = app.database.rows("SELECT * FROM notification_receipts")[0]
    assert receipt["state"] == "silent" and receipt["text"] == ""
    assert not app.messages.recent_bot("qq:group:1", 5)


async def test_public_capability_preserves_silence_and_cannot_claim_chat_provenance(app):
    api = PublicAPI(SimpleNamespace(application=app))
    provider = SimpleNamespace(name="core", activated=True, star_cls=SimpleNamespace(
        plugin_capabilities=api.manifest, invoke_capability=api.invoke))
    client = CapabilityClient(lambda: [provider])
    silent = await client.call("message.compose", "compose",
                               {"facts": "secret JSON", "kind": "progress"})
    assert silent == {"status": "silent", "reason": "expression_unavailable"}
    assert "text" not in silent
    ready = await client.call("message.compose", "compose", {
        "facts": "secret JSON", "kind": "tool_result", "chat_reply": True})
    assert ready["status"] == "ready" and "secret" not in ready["text"]
    assert len(app.database.rows("SELECT * FROM outbound_compositions")) == 2
