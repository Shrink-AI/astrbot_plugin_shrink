import asyncio
import importlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from shrink.adapters.notifications import Notifications
from shrink.adapters.tool_notices import ToolNoticeHooks
from shrink.persistence.database import Database
from shrink.services.task_context import CURRENT_TASK
from shrink.services.turn_control import is_stop_request, unsupported_claim


class Event:
    unified_msg_origin = "test:FriendMessage:123"

    def __init__(self):
        self.extra = {"shrink_reply": True, "shrink_turn": SimpleNamespace(
            id="message-1", text="检查当前同步任务", source="qq:private:123", user_id="qq:123",
            umo=self.unified_msg_origin, private=True, reason="direct")}
        self.send = AsyncMock()
        self.stop_event = Mock()

    def get_extra(self, key, default=None):
        return self.extra.get(key, default)

    def set_extra(self, key, value):
        self.extra[key] = value

    def get_sender_id(self):
        return "123"

    def is_admin(self):
        return True


@pytest.fixture
def host_modules(monkeypatch):
    class Plain:
        def __init__(self, text):
            self.text = text
    class Chain:
        def __init__(self, chain):
            self.chain = chain
        def use_markdown(self, value):
            self.markdown = value
    monkeypatch.setitem(sys.modules, "astrbot.api", SimpleNamespace(logger=Mock()))
    monkeypatch.setitem(sys.modules, "astrbot.api.message_components", SimpleNamespace(
        At=type("At", (), {}), BaseMessageComponent=object, ComponentType=SimpleNamespace(Image=1),
        Image=type("Image", (), {}), Plain=Plain, Reply=type("Reply", (), {})))
    monkeypatch.setitem(sys.modules, "astrbot.core.message.message_event_result",
                        SimpleNamespace(MessageChain=Chain))
    sys.modules.pop("shrink.adapters.conversation", None)
    module = importlib.import_module("shrink.adapters.conversation")
    yield module
    sys.modules.pop("shrink.adapters.conversation", None)


@pytest.fixture
def app(tmp_path):
    database = Database(tmp_path)
    value = SimpleNamespace(database=database, tasks=SimpleNamespace(error=Mock()),
        outbound=SimpleNamespace(compose=AsyncMock(return_value="我看看这次同步的记录。"),
                                 record=Mock(return_value=True)))
    yield value
    database.close()


@pytest.mark.asyncio
async def test_parallel_tool_calls_notify_once_and_persist_across_recreation(app, host_modules):
    notifications = Notifications(app)
    plugin = SimpleNamespace(host=SimpleNamespace(conversation=SimpleNamespace(notifications=notifications)))
    event = Event()
    tool = SimpleNamespace(name="astrbot_file_read_tool")
    await asyncio.gather(*(ToolNoticeHooks.notify_before_tool(plugin, event, tool, {}) for _ in range(5)))
    app.outbound.compose.assert_awaited_once()
    event.send.assert_awaited_once()
    assert event.send.call_args.args[0].markdown is False
    app.outbound.record.assert_called_once()
    fresh_event = Event()
    await Notifications(app).send_once(fresh_event, "message-1:tools", {})
    fresh_event.send.assert_not_called()
    assert app.database.rows("SELECT state FROM notification_receipts")[0]["state"] == "platform_accepted"


@pytest.mark.asyncio
async def test_delegation_leaves_acceptance_to_final_reply(app, host_modules):
    notices = Notifications(app)
    plugin = SimpleNamespace(host=SimpleNamespace(conversation=SimpleNamespace(notifications=notices)))
    event = Event()
    for name in ("shrink_develop", "shrink_orchestrate"):
        await ToolNoticeHooks.notify_before_tool(plugin, event, SimpleNamespace(name=name), {})
    event.set_extra("shrink_background_task", "submitted-task")
    await ToolNoticeHooks.notify_before_tool(plugin, event, SimpleNamespace(name="read"), {})
    event.send.assert_not_called()
    app.outbound.compose.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_send_not_retried_and_background_tool_has_no_notice(app, host_modules):
    event = Event()
    event.send.side_effect = TimeoutError()
    notifications = Notifications(app)
    assert not await notifications.send_once(event, "key", {})
    assert not await notifications.send_once(event, "key", {})
    event.send.assert_awaited_once()
    assert app.database.rows("SELECT state FROM notification_receipts")[0]["state"] == "unknown"
    plugin = SimpleNamespace(host=SimpleNamespace(conversation=SimpleNamespace(notifications=notifications)))
    token = CURRENT_TASK.set("child")
    try:
        await ToolNoticeHooks.notify_before_tool(plugin, Event(), SimpleNamespace(name="read"), {})
    finally:
        CURRENT_TASK.reset(token)
    app.outbound.compose.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_failure_does_not_repeat_accepted_send(app, host_modules):
    event = Event()
    app.outbound.record.side_effect = RuntimeError("storage")
    notifications = Notifications(app)
    await notifications.send_once(event, "record", {})
    await notifications.send_once(event, "record", {})
    assert app.database.rows("SELECT state FROM notification_receipts")[0]["state"] == "platform_accepted"
    event.send.assert_awaited_once()


@pytest.mark.parametrize("text", ["你先停下来，别干了", "停止", "石英暂停", "别做了"])
def test_explicit_stops(text):
    assert is_stop_request(text)


@pytest.mark.parametrize("text", ["不要停止", "停止按钮怎么做", "刚才他说“别干了”", "取消任务的代码在哪"])
def test_stop_examples_are_not_commands(text):
    assert not is_stop_request(text)


def test_fallback_claims_need_execution_evidence():
    assert unsupported_claim("好，同步好了，等下直接推送到远端", [])
    assert unsupported_claim("马上就好，正在推了", [])
    assert not unsupported_claim("我先看看", [])
    assert not unsupported_claim("任务提交了", [], {"status": "queued"})
    assert unsupported_claim("同步好了", [{"name": "list", "result": "old tasks"}])
    assert unsupported_claim("同步好了", [], {"status": "queued"})
    assert not unsupported_claim("还没同步好了", [])


@pytest.mark.asyncio
async def test_stop_does_not_create_continuation_and_cancels_pipeline(app, host_modules):
    app.operations = SimpleNamespace(cancel_owned=AsyncMock(return_value=["one"]),
                                     create_agent=AsyncMock(), submit_agent=AsyncMock())
    adapter = host_modules.ConversationAdapter(app, "test")
    old = Event()
    turn = old.get_extra("shrink_turn")
    task = asyncio.create_task(asyncio.Event().wait())
    adapter.active_turns[(turn.umo, turn.user_id)] = [(old, task)]
    stop = Event()
    stop.get_extra("shrink_turn").text = "你先停下来，别干了"
    await adapter.stop_requested(stop, stop.get_extra("shrink_turn"))
    await asyncio.gather(task, return_exceptions=True)
    assert old.get_extra("shrink_cancelled") and task.cancelled()
    stop.stop_event.assert_called_once()
    await adapter.continue_or_report(old, turn, task)
    app.operations.create_agent.assert_not_called()
    app.operations.submit_agent.assert_not_called()


@pytest.mark.asyncio
async def test_read_only_timeout_does_not_create_new_task(app, host_modules):
    app.operations = SimpleNamespace(create_agent=AsyncMock(), submit_agent=AsyncMock())
    adapter = host_modules.ConversationAdapter(app, "test")
    adapter.send_gave_up = AsyncMock()
    event = Event()
    event.set_extra("shrink_tools", [{"name": "list", "result": "old tasks"}])
    task = asyncio.create_task(asyncio.sleep(0))
    await task
    await adapter.continue_or_report(event, event.get_extra("shrink_turn"), task)
    app.operations.submit_agent.assert_not_called()
    adapter.send_gave_up.assert_awaited_once()


@pytest.mark.asyncio
async def test_background_lookup_uses_owned_task_scope_not_stale_turn(app, host_modules):
    app.operations = SimpleNamespace(owned_task=Mock(return_value={
        "session": "test:FriendMessage:123", "sender": "123"}))
    app.outbound.resolve_scope = Mock(return_value={"source": "qq:private:123", "user_id": "qq:123"})
    app.tools = SimpleNamespace(lookup=AsyncMock(return_value="scoped memory"))
    adapter = host_modules.ConversationAdapter(app, "test")
    event = Event()
    event.get_extra("shrink_turn").source = "qq:private:other"
    token = CURRENT_TASK.set("owned")
    try:
        assert await adapter.lookup(event, "方法") == "scoped memory"
        app.tools.lookup.assert_awaited_once_with(
            "方法", source="qq:private:123", speaker="qq:123", private=True)
        app.operations.owned_task.side_effect = ValueError("wrong owner")
        with pytest.raises(ValueError):
            await adapter.lookup(event, "方法")
    finally:
        CURRENT_TASK.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize('text', ['好，同步好了，等下直接推送到远端', '马上就好，正在推了'])
async def test_fallback_claim_is_replaced_before_output(app, host_modules, text):
    from shrink.services.expression import Finished
    app.expression = SimpleNamespace(finish=AsyncMock(return_value=Finished(text)))
    app.messages = SimpleNamespace(recent_bot=Mock(return_value=[]))
    adapter = host_modules.ConversationAdapter(app, 'test')
    adapter.record_episode = Mock()
    response = SimpleNamespace(tools_call_name=[], role='assistant', completion_text=text, result_chain=None)
    await adapter.response(Event(), response)
    assert response.completion_text == '这轮没有拿到执行记录，还不能说已经做了。'
