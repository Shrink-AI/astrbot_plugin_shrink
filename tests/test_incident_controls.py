import asyncio
import importlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from shrink.adapters.notifications import Notifications
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


def test_task_statuses_are_scoped_to_owner_and_group():
    from shrink.adapters.foreground import task_statuses
    event = Event()
    rows = [
        dict(id="own", sender="123", session=event.unified_msg_origin, request="a", status="running"),
        dict(id="elsewhere", sender="123", session="another", request="b", status="reconciling"),
        dict(id="other-person", sender="456", session=event.unified_msg_origin, request="c", status="running"),
    ]
    operations = SimpleNamespace(list=lambda: rows)
    assert [r["task_id"] for r in task_statuses(
        operations, event, SimpleNamespace(private=False))] == ["own"]
    assert [r["task_id"] for r in task_statuses(
        operations, event, SimpleNamespace(private=True))] == ["own", "elsewhere"]


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
        state=SimpleNamespace(settings=SimpleNamespace(cooperative_stop=False)),
        outbound=SimpleNamespace(compose=AsyncMock(return_value="我看看这次同步的记录。"),
                                 record=Mock(return_value=True)))
    yield value
    database.close()


@pytest.mark.asyncio
async def test_parallel_notices_send_once_and_persist_across_recreation(app, host_modules):
    notifications = Notifications(app)
    event = Event()
    await asyncio.gather(*(notifications.send_once(event, "message-1:result", {}) for _ in range(5)))
    app.outbound.compose.assert_awaited_once()
    event.send.assert_awaited_once()
    assert event.send.call_args.args[0].markdown is False
    app.outbound.record.assert_called_once()
    fresh_event = Event()
    await Notifications(app).send_once(fresh_event, "message-1:result", {})
    fresh_event.send.assert_not_called()
    assert app.database.rows("SELECT state FROM notification_receipts")[0]["state"] == "platform_accepted"


def test_tool_start_is_recorded_without_any_notice(app, host_modules):
    from shrink.services.debounce import Reply
    adapter = host_modules.ConversationAdapter(app, "test")
    event = Event()
    reply = Reply("查一下", "direct", 0.0)
    event.set_extra("shrink_inflight", (("k", "u"), reply))
    # A hook that returns a coroutine would have to wait; this one must finish synchronously.
    assert adapter.tool_started(event, SimpleNamespace(name="shrink_find_meme")) is None
    assert not reply.committed and not adapter.did_work(event)
    adapter.tool_started(event, SimpleNamespace(name="astrbot_file_read_tool"))
    assert reply.committed and adapter.did_work(event)
    assert event.get_extra("shrink_started_tools") == ["shrink_find_meme", "astrbot_file_read_tool"]
    event.send.assert_not_called()
    app.outbound.compose.assert_not_called()
    # A newer message can no longer withdraw a turn whose tool has started.
    reply.superseded = True
    assert not adapter.withdrawn(event)


def test_background_tool_start_leaves_the_foreground_event_alone(app, host_modules):
    adapter = host_modules.ConversationAdapter(app, "test")
    event = Event()
    token = CURRENT_TASK.set("child")
    try:
        adapter.tool_started(event, SimpleNamespace(name="astrbot_execute_shell"))
    finally:
        CURRENT_TASK.reset(token)
    assert event.get_extra("shrink_started_tools") is None


@pytest.mark.asyncio
async def test_unknown_send_is_not_retried(app, host_modules):
    event = Event()
    event.send.side_effect = TimeoutError()
    notifications = Notifications(app)
    assert not await notifications.send_once(event, "key", {})
    assert not await notifications.send_once(event, "key", {})
    event.send.assert_awaited_once()
    assert app.database.rows("SELECT state FROM notification_receipts")[0]["state"] == "unknown"


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
            "方法", source="qq:private:123", speaker="qq:123", private=True, request="")
        app.operations.owned_task.side_effect = ValueError("wrong owner")
        with pytest.raises(ValueError):
            await adapter.lookup(event, "方法")
    finally:
        CURRENT_TASK.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize('text', ['好，同步好了，等下直接推送到远端', '马上就好，正在推了'])
async def test_fallback_claim_is_replaced_before_output(app, host_modules, text):
    from shrink.services.expression import ExpressionService
    app.expression = ExpressionService(
        app.state, SimpleNamespace(learned_persona=lambda: ""), None, AsyncMock(), app.tasks.error)
    app.expression.outbound = app.outbound
    app.messages = SimpleNamespace(recent_bot=Mock(return_value=[]))
    adapter = host_modules.ConversationAdapter(app, 'test')
    adapter.record_episode = Mock()
    response = SimpleNamespace(tools_call_name=[], role='assistant', completion_text=text, result_chain=None)
    await adapter.response(Event(), response)
    assert response.completion_text == '我看看这次同步的记录'
    app.outbound.compose.assert_awaited_once()


@pytest.mark.asyncio
async def test_foreground_has_no_deadline_but_remains_cancellable(app, host_modules, monkeypatch):
    adapter = host_modules.ConversationAdapter(app, "test")
    event = Event()
    loop = asyncio.get_running_loop()
    timer = Mock(side_effect=AssertionError("foreground must not arm a deadline"))
    monkeypatch.setattr(loop, "call_later", timer)
    adapter.arm_watchdog(event)
    adapter.arm_watchdog(event)
    key = (event.unified_msg_origin, "qq:123")
    assert len(adapter.active_turns[key]) == 1
    assert adapter.active_turns[key][0][1] is asyncio.current_task()
    timer.assert_not_called()


# ---- foreground occupancy (plan 2.2) ---------------------------------------------------------
class Toolset:
    def __init__(self, *names):
        self.tools = [SimpleNamespace(name=name) for name in names]

    def remove_tool(self, name):
        self.tools = [t for t in self.tools if t.name != name]

    def get_tool(self, name):
        return next((t for t in self.tools if t.name == name), None)


def test_foreground_request_loses_long_tools_but_shared_objects_survive():
    from shrink.adapters.foreground import restrict
    shared = Toolset("astrbot_execute_shell", "astrbot_execute_python", "astrbot_execute_ipython",
                     "astrbot_shell_session", "transfer_to_researcher", "shrink_lookup",
                     "shrink_orchestrate")
    background = Toolset(*(t.name for t in shared.tools))
    router = "Delegate with transfer_to_* tools."
    req = SimpleNamespace(func_tool=shared, system_prompt="persona\n" + router)
    removed = restrict(req, router)
    assert set(removed) == {"astrbot_execute_shell", "astrbot_execute_python",
                            "astrbot_execute_ipython", "astrbot_shell_session",
                            "transfer_to_researcher"}
    # The host resolves a call against this same set, so a withheld tool cannot be executed.
    assert all(req.func_tool.get_tool(name) is None for name in removed)
    assert [t.name for t in req.func_tool.tools] == ["shrink_lookup", "shrink_orchestrate"]
    assert "transfer_to" not in req.system_prompt and "persona" in req.system_prompt
    assert background.get_tool("astrbot_execute_shell") is not None
    assert restrict(SimpleNamespace(func_tool=None, system_prompt=None)) == []


def test_foreground_withholds_project_execution_and_unknown_external_tools():
    from shrink.adapters.foreground import restrict
    req = SimpleNamespace(system_prompt="", func_tool=Toolset(
        "astrbot_file_read_tool", "astrbot_grep_tool", "shrink_computer",
        "shrink_plugin_capability", "shirnk_crafttable", "external_new_tool",
        "shrink_orchestrate", "shrink_lookup", "shrink_find_meme"))
    restrict(req)
    assert [tool.name for tool in req.func_tool.tools] == [
        "shrink_orchestrate", "shrink_lookup", "shrink_find_meme"]


def test_stop_request_goes_through_the_run_and_blocks_new_follow_ups():
    from shrink.adapters.foreground import release_follow_ups, request_stop
    event = Event()
    assert not request_stop(event)
    assert event.get_extra("agent_stop_requested")
    runner = SimpleNamespace(request_stop=Mock(), _resolve_unconsumed_follow_ups=Mock())
    event.set_extra("shrink_runner", runner)
    assert request_stop(event)
    runner.request_stop.assert_called_once()
    assert release_follow_ups(runner) and not release_follow_ups(SimpleNamespace())
    runner._resolve_unconsumed_follow_ups.assert_called_once()


def test_turn_trace_phases_add_up_and_distribution_reports_percentiles(app):
    from shrink.persistence.turn_traces import TurnTraceRepository
    from shrink.services.turn_trace import TurnTrace, distribution
    now = [100.0]
    trace = TurnTrace("input", "umo", "qq:group:1", "qq:1", "direct", clock=lambda: now[0])
    for phase, seconds in (("lock", 1.5), ("context", 4.0), ("model", 0.5), ("send", 2.0)):
        now[0] += seconds
        trace.enter(phase)
    now[0] += 3.0
    trace.tool("shrink_lookup")
    now[0] += 1.0
    record = trace.finish("sent")
    assert trace.finish("again") is None
    assert record["outcome"] == "sent" and record["tools"] == ["shrink_lookup"]
    assert record["phases"] == {"debounce": 1.5, "lock": 4.0, "context": 0.5, "model": 2.0,
                                "send": 3.0, "tool": 1.0}
    assert sum(record["phases"].values()) == record["total"] == 12.0
    repository = TurnTraceRepository(app.database)
    repository.save(record)
    repository.save(record)
    rows = repository.since()
    assert len(rows) == 1 and rows[0]["phases"]["lock"] == 4.0
    report = distribution(rows + [{"total": 2.0, "phases": {"lock": 1.0}}])
    assert report["turns"] == 2 and report["lock"]["max"] == 4.0 and report["tool"]["p50"] == 0.0


@pytest.mark.asyncio
async def test_superseded_or_stopped_turn_leaves_before_the_model(app, host_modules):
    from shrink.services.debounce import Reply
    adapter = host_modules.ConversationAdapter(app, "test")
    app.context = SimpleNamespace(window=Mock(side_effect=AssertionError("no context work")))
    for extras in ({"shrink_inflight": (("k", "u"), Reply("旧", "direct", 0.0, superseded=True))},
                   {"shrink_cancelled": True}):
        event = Event()
        event.extra.update(extras)
        await adapter.enrich(event, SimpleNamespace(func_tool=None, system_prompt=""))
        event.stop_event.assert_called_once()
    response = SimpleNamespace(tools_call_name=[], completion_text="迟到的回复",
                               result_chain=SimpleNamespace(chain=["x"]))
    event = Event()
    event.set_extra("shrink_inflight", (("k", "u"), Reply("旧", "direct", 0.0, superseded=True)))
    await adapter.response(event, response)
    assert response.completion_text == "" and response.result_chain.chain == []
    app.outbound.compose.assert_not_called()


@pytest.mark.asyncio
async def test_turn_end_clears_only_its_own_record_and_releases_queued_messages(app, host_modules):
    adapter = host_modules.ConversationAdapter(app, "test")
    key = ("umo", "qq:123")
    old_event, new_event = Event(), Event()
    runner = SimpleNamespace(_resolve_unconsumed_follow_ups=Mock())

    async def old_turn():
        reply = adapter.debouncer.begin(key, "第一句", "direct")
        adapter.track(old_event, key, reply, old_event.get_extra("shrink_turn"), 0.0)
        old_event.set_extra("shrink_runner", runner)
        raise RuntimeError("step failed")

    await asyncio.gather(asyncio.create_task(old_turn()), return_exceptions=True)
    await asyncio.sleep(0)
    assert key not in adapter.debouncer.replies
    runner._resolve_unconsumed_follow_ups.assert_called_once()

    newer = {}

    async def consumed_turn():
        reply = adapter.debouncer.begin(key, "第二句", "direct")
        adapter.track(new_event, key, reply, new_event.get_extra("shrink_turn"), 0.0)
        newer["reply"] = adapter.debouncer.begin(key, "第三句", "direct")
        new_event.set_extra("_follow_up_captured", {"target_run_id": "1"})

    await asyncio.create_task(consumed_turn())
    await asyncio.sleep(0)
    # The finished turn must not clear the record a newer message has since created.
    assert adapter.debouncer.replies[key] is newer["reply"]
    outcomes = [r["outcome"] for r in app.database.rows("SELECT outcome FROM turn_traces")]
    assert sorted(outcomes) == ["ended", "follow_up"]


@pytest.mark.asyncio
async def test_extra_sends_run_after_the_send_hook_returns(app, host_modules):
    adapter = host_modules.ConversationAdapter(app, "test")
    spawned = []
    app.tasks.spawn = lambda coroutine: spawned.append(coroutine)
    app.messages = SimpleNamespace(record_bot=Mock())
    app.expressions = SimpleNamespace(used=Mock())
    event = Event()
    event.extra.update(shrink_text="好", shrink_examples=[7],
                       shrink_finished=SimpleNamespace(second="还有一句", skip=False))
    await adapter.sent(event)
    # Only the first bubble is recorded while the host still holds the session lock.
    app.messages.record_bot.assert_called_once()
    app.expressions.used.assert_called_once_with([7])
    event.send.assert_not_called()
    assert len(spawned) == 1
    spawned[0].close()


@pytest.mark.asyncio
async def test_cooperative_stop_uses_the_run_instead_of_cancelling(app, host_modules):
    app.operations = SimpleNamespace(cancel_owned=AsyncMock(return_value=[]))
    app.state.settings.cooperative_stop = True
    adapter = host_modules.ConversationAdapter(app, "test")
    running, queued = Event(), Event()
    runner = SimpleNamespace(request_stop=Mock())
    running.set_extra("shrink_runner", runner)
    turn = running.get_extra("shrink_turn")
    tasks = [asyncio.create_task(asyncio.Event().wait()) for _ in range(2)]
    adapter.active_turns[(turn.umo, turn.user_id)] = [(running, tasks[0]), (queued, tasks[1])]
    stop = Event()
    await adapter.stop_requested(stop, stop.get_extra("shrink_turn"))
    await asyncio.sleep(0)
    runner.request_stop.assert_called_once()
    assert running.get_extra("agent_stop_requested") and running.get_extra("shrink_cancelled")
    # The run ends itself; a turn with no run yet is still cancelled directly.
    assert not tasks[0].cancelled() and tasks[1].cancelled()
    tasks[0].cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancel_inside_the_request_hook_stops_the_event(app, host_modules):
    """The host swallows the cancellation; without a stopped event it would call the model."""
    adapter = host_modules.ConversationAdapter(app, "test")

    async def cancelled(event, req):
        raise asyncio.CancelledError()
    adapter.prepare = cancelled
    event = Event()
    event.set_extra("shrink_cancelled", True)
    with pytest.raises(asyncio.CancelledError):
        await adapter.enrich(event, SimpleNamespace())
    event.stop_event.assert_called_once()
    other = Event()
    with pytest.raises(asyncio.CancelledError):
        await adapter.enrich(other, SimpleNamespace())
    other.stop_event.assert_not_called()
