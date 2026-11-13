"""Whole foreground turns: the plugin's real hooks around the host's real runner, session lock,
follow-up queue and hook-error handling (AstrBot 4.29.4).

`turn()` below repeats the order of the host's agent stage: capture as a follow-up, waiting
hook, session lock, request hook, register the run, model/tool loop with response and tool
hooks, send hooks, unregister. Like the host, it swallows whatever a plugin hook raises,
cancellation included. Scripted: the model, the tools and the platform. Not run: the host's
own stage code, its history saving and its plugin registry.
"""
import asyncio
import time
import traceback

import pytest

pytest.importorskip("astrbot.core.agent.runners.tool_loop_agent_runner")

from astrbot.api.message_components import Plain  # noqa: E402
from astrbot.core.agent.hooks import BaseAgentRunHooks  # noqa: E402
from astrbot.core.agent.run_context import ContextWrapper  # noqa: E402
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner  # noqa: E402
from astrbot.core.agent.tool import FunctionTool, ToolSet  # noqa: E402
from astrbot.core.agent.tool_executor import BaseFunctionToolExecutor  # noqa: E402
from astrbot.core.message.message_event_result import MessageEventResult  # noqa: E402
from astrbot.core.pipeline.process_stage import follow_up  # noqa: E402
from astrbot.core.provider.entities import LLMResponse, ProviderRequest  # noqa: E402
from astrbot.core.utils.session_lock import session_lock_manager  # noqa: E402
from mcp.types import CallToolResult, TextContent  # noqa: E402

from shrink.adapters.conversation import ConversationAdapter  # noqa: E402
from shrink.application import Application  # noqa: E402

GROUP = "test:GroupMessage:1"


class Event:
    """A platform message as the plugin sees it; `sent` is what reached the chat."""
    counter = 0

    def __init__(self, world, text, sender="100", admin=False):
        Event.counter += 1
        self.world, self.sender, self.admin = world, sender, admin
        self.unified_msg_origin = GROUP
        self.message_str = text
        self.message_obj = type("M", (), {})()
        self.message_obj.message_str = text
        self.message_obj.message_id = f"m{Event.counter}"
        self.message_obj.timestamp = time.time()
        self.message_obj.message = [Plain(text)]
        self.extra = {"activated_handlers": []}
        self.is_at_or_wake_command = True
        self.call_llm = False
        self.stopped = False
        self.result = None

    def get_extra(self, key, default=None):
        return self.extra.get(key, default)

    def set_extra(self, key, value):
        self.extra[key] = value

    def get_sender_id(self):
        return self.sender

    def get_sender_name(self):
        return "成员" + self.sender

    def get_self_id(self):
        return "999"

    def get_platform_name(self):
        return "aiocqhttp"

    def get_platform_id(self):
        return "test"

    def get_group_id(self):
        return "1"

    def is_private_chat(self):
        return False

    def is_admin(self):
        return self.admin

    def get_message_str(self):
        return self.message_str

    def get_message_outline(self):
        return self.message_str

    def get_messages(self):
        return self.message_obj.message

    def stop_event(self):
        self.stopped = True

    def is_stopped(self):
        return self.stopped

    def get_result(self):
        return self.result

    async def send(self, chain):
        self.world.sent.append("".join(getattr(c, "text", "") for c in chain.chain))


class World:
    def __init__(self, root):
        self.sent, self.ran, self.cancelled, self.calls = [], [], [], []
        self.gates, self.model_gate, self.replies = {}, None, {}
        self.hook_errors = []

        async def model(prompt, system="", images=None):
            return "好的，先停下。" if "已停止" in prompt else "整理过的结果"

        async def embed(text):
            return None

        async def nothing(*args, **kwargs):
            return None

        self.app = Application(root, model, embed, nothing)
        settings = self.app.state.settings.model_dump()
        settings.update(debounce_enabled=False, skills_enabled=False, meme_enabled=False,
                        split_probability=0.0)
        self.app.state.put("settings", settings)

        async def cancel_owned(event):
            return []
        self.app.operations = type("Ops", (), {"cancel_owned": staticmethod(cancel_owned)})()
        self.adapter = ConversationAdapter(self.app, "shrink")
        world = self

        class Tools(BaseFunctionToolExecutor):
            @classmethod
            async def execute(cls, tool, run_context, **tool_args):
                world.ran.append(tool.name)
                try:
                    if tool.name in world.gates:
                        await world.gates[tool.name].wait()
                except asyncio.CancelledError:
                    world.cancelled.append(tool.name)
                    raise
                yield CallToolResult(content=[TextContent(type="text", text="ok")])
        self.tools = Tools

    def cooperative(self, value=True):
        settings = self.app.state.settings.model_dump()
        settings["cooperative_stop"] = value
        self.app.state.put("settings", settings)

    async def hook(self, call):
        """The host logs and swallows anything a plugin hook raises, cancellation included."""
        try:
            await call
        except BaseException as exc:
            if not isinstance(exc, asyncio.CancelledError):
                self.hook_errors.append(traceback.format_exc()[-400:])

    async def text_chat(self, **payload):
        prompt = ""
        for message in payload["contexts"]:
            if getattr(message, "role", "") == "user":
                content = message.content
                prompt = content if isinstance(content, str) else "".join(
                    getattr(part, "text", "") for part in content)
                break
        self.calls.append(prompt)
        if self.model_gate is not None:
            await self.model_gate.wait()
        step = self.replies.get(prompt, [f"回复：{prompt}"])
        step = step.pop(0) if len(step) > 1 else step[0]
        if isinstance(step, tuple):
            return LLMResponse(role="tool", tools_call_name=list(step),
                               tools_call_args=[{} for _ in step],
                               tools_call_ids=[f"c{len(self.calls)}-{i}" for i in range(len(step))])
        return LLMResponse(role="assistant", completion_text=step)

    async def turn(self, event):
        adapter = self.adapter
        await self.hook(adapter.receive(event))
        if event.stopped or not event.get_extra("shrink_reply"):
            return
        capture = follow_up.try_capture_follow_up(event)
        consumed = activated = False
        try:
            if capture:
                consumed, activated = await follow_up.prepare_follow_up_capture(capture)
                if consumed:
                    event.set_extra("_follow_up_captured", {"target_run_id": ""})
                    return
            adapter.waiting(event)
            async with session_lock_manager.acquire_lock(event.unified_msg_origin):
                names = ("shrink_lookup", "astrbot_execute_shell", "transfer_to_dev")
                request = ProviderRequest(
                    prompt=event.message_str, system_prompt="",
                    func_tool=ToolSet([FunctionTool(
                        name=n, description=n, parameters={"type": "object", "properties": {}},
                        handler=None) for n in names]))
                await self.hook(adapter.enrich(event, request))
                if event.stopped:
                    return
                event.set_extra("offered_tools", request.func_tool.names())
                world = self

                class Hooks(BaseAgentRunHooks):
                    async def on_tool_start(self, run_context, tool, tool_args):
                        async def call():
                            adapter.tool_started(event, tool)
                        await world.hook(call())

                    async def on_tool_end(self, run_context, tool, tool_args, tool_result):
                        async def call():
                            adapter.tool_used(event, tool, tool_args, tool_result)
                        await world.hook(call())

                    async def on_agent_done(self, run_context, llm_response):
                        await world.hook(adapter.response(event, llm_response))

                runner = ToolLoopAgentRunner()
                await runner.reset(
                    provider=self, request=request, tool_executor=self.tools,
                    run_context=ContextWrapper(context=type("C", (), {"event": event})()),
                    agent_hooks=Hooks(), streaming=False)
                follow_up.register_active_runner(event.unified_msg_origin, runner)
                try:
                    aborted = False
                    async for response in runner.step_until_done(8):
                        aborted = aborted or response.type == "aborted"
                    final = runner.get_final_llm_resp()
                    text = "" if aborted or final is None else final.completion_text or ""
                    event.result = MessageEventResult(chain=[Plain(text)] if text else [])
                    await self.hook(adapter.decorate(event))
                    if event.result.chain:
                        self.sent.append("".join(c.text for c in event.result.chain))
                        await self.hook(adapter.sent(event))
                finally:
                    follow_up.unregister_active_runner(event.unified_msg_origin, runner)
        finally:
            if capture:
                await follow_up.finalize_follow_up_capture(
                    capture, activated=activated, consumed_marked=consumed)

    provider_config = {"id": "scripted"}

    def get_model(self):
        return "scripted"

    def traces(self):
        return {r["input_id"].rsplit(":", 1)[-1]: r for r in self.app.turn_traces.since()}


@pytest.fixture
async def world(tmp_path):
    follow_up._ACTIVE_AGENT_RUNNERS.clear()
    follow_up._FOLLOW_UP_ORDER_STATE.clear()
    value = World(tmp_path / "data")
    yield value
    await asyncio.sleep(0)
    assert value.hook_errors == [], "a plugin hook raised; the host would only log it"
    await value.app.tasks.close()
    value.app.database.close()


async def until(condition, seconds=5):
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "condition not reached"
        await asyncio.sleep(0.01)


async def test_plain_turn_replies_once_without_long_tools_or_notices(world):
    """T06 and N06 through a whole turn."""
    world.replies["查下记忆"] = [("shrink_lookup",), "查到了"]
    event = Event(world, "查下记忆")
    await asyncio.wait_for(world.turn(event), 10)
    assert event.get_extra("offered_tools") == ["shrink_lookup"]
    assert world.ran == ["shrink_lookup"]
    # One message reaches the chat: the answer. Nothing is sent for the tool call itself.
    assert world.sent == ["整理过的结果"], "tool results pass through expression"
    await asyncio.sleep(0)
    trace = world.traces()[event.message_obj.message_id]
    assert trace["outcome"] == "sent" and trace["tools"] == ["shrink_lookup"]
    assert trace["model_calls"] == 2


async def test_people_in_one_group_queue_on_the_lock_and_are_traced_apart(world):
    """C13: lock wait is charged to the waiting person's turn, not mixed into model time."""
    world.model_gate = asyncio.Event()
    first, second = Event(world, "今天吃什么", sender="100"), Event(world, "明天下雨吗", sender="200")
    world.replies.update({"今天吃什么": ["吃面吧"], "明天下雨吗": ["看着不会下"]})
    one = asyncio.create_task(world.turn(first))
    await until(lambda: world.calls)
    two = asyncio.create_task(world.turn(second))
    await asyncio.sleep(0.3)
    assert len(world.calls) == 1, "the host runs one turn per session at a time"
    world.model_gate.set()
    await asyncio.wait_for(asyncio.gather(one, two), 10)
    assert world.sent == ["吃面吧", "看着不会下"]
    await asyncio.sleep(0)
    traces = world.traces()
    waited = traces[second.message_obj.message_id]
    assert waited["sender"] == "qq:200" and waited["phases"]["lock"] >= 0.25
    assert traces[first.message_obj.message_id]["phases"].get("lock", 0) < 0.1
    assert traces[first.message_obj.message_id]["phases"]["model"] >= 0.25


async def test_newer_message_withdraws_a_reply_still_being_written(world):
    """C10 without the stop: the old reply is generated, dropped, and nothing late arrives."""
    world.model_gate = asyncio.Event()
    old, new = Event(world, "帮我想个名字"), Event(world, "要两个字的")
    one = asyncio.create_task(world.turn(old))
    await until(lambda: world.calls)
    two = asyncio.create_task(world.turn(new))
    await asyncio.sleep(0.05)
    world.model_gate.set()
    await asyncio.wait_for(asyncio.gather(one, two), 10)
    assert world.sent == ["回复：帮我想个名字 要两个字的"]
    await asyncio.sleep(0)
    traces = world.traces()
    assert traces[old.message_obj.message_id]["outcome"] == "superseded"
    assert traces[new.message_obj.message_id]["outcome"] == "sent"
    assert not world.adapter.debouncer.replies and not world.adapter.active_turns


async def test_cooperative_stop_ends_a_superseded_run_and_leaks_nothing(world):
    """C10/C14 with the stop: the old run ends early and its stop text never reaches the chat."""
    world.cooperative()
    world.model_gate = asyncio.Event()
    old, new = Event(world, "帮我想个名字"), Event(world, "要两个字的")
    one = asyncio.create_task(world.turn(old))
    await until(lambda: world.calls)
    two = asyncio.create_task(world.turn(new))
    # The old run ends although its model call never returns.
    await asyncio.wait_for(one, 5)
    assert world.sent == []
    world.model_gate.set()
    await asyncio.wait_for(two, 10)
    assert world.sent == ["回复：帮我想个名字 要两个字的"]
    assert not any("stopped" in text.lower() for text in world.sent)
    await asyncio.sleep(0)
    assert world.traces()[old.message_obj.message_id]["outcome"] == "superseded"
    assert not follow_up._ACTIVE_AGENT_RUNNERS and not world.adapter.debouncer.replies


async def test_turn_that_started_a_tool_is_not_withdrawn(world):
    """Work that began is reported; the newer message gets its own answer."""
    world.cooperative()
    world.gates["shrink_lookup"] = asyncio.Event()
    world.replies["查下部署"] = [("shrink_lookup",), "部署正常"]
    old, new = Event(world, "查下部署"), Event(world, "顺便看日志")
    one = asyncio.create_task(world.turn(old))
    await until(lambda: world.ran)
    two = asyncio.create_task(world.turn(new))
    await asyncio.sleep(0.05)
    assert world.cancelled == [], "a started tool is never interrupted by a newer message"
    world.gates["shrink_lookup"].set()
    await asyncio.wait_for(asyncio.gather(one, two), 10)
    assert world.sent == ["整理过的结果"], "tool results pass through expression"
    await asyncio.sleep(0)
    traces = world.traces()
    # The host folded the new words into the running turn's tool result: consumed once.
    assert traces[new.message_obj.message_id]["outcome"] == "follow_up"
    assert sum("顺便看日志" in call for call in world.calls) == 0
    assert not world.adapter.debouncer.replies


@pytest.mark.parametrize("cooperative", [False, True])
async def test_stop_command_ends_a_running_turn(world, cooperative):
    """C14: "停" ends the turn either way, and only the acknowledgement is sent."""
    world.cooperative(cooperative)
    world.gates["shrink_lookup"] = asyncio.Event()
    world.replies["查很久"] = [("shrink_lookup",), "迟到的结果"]
    old = Event(world, "查很久", admin=True)
    one = asyncio.create_task(world.turn(old))
    await until(lambda: world.ran)
    stop = Event(world, "停止", admin=True)
    await asyncio.wait_for(world.turn(stop), 10)
    await asyncio.wait_for(asyncio.gather(one, return_exceptions=True), 5)
    assert world.sent == ["好的，先停下。"]
    assert world.cancelled == ["shrink_lookup"]
    assert not follow_up._ACTIVE_AGENT_RUNNERS
    later = Event(world, "在吗", admin=True)
    await asyncio.wait_for(world.turn(later), 10)
    assert world.sent[-1] == "回复：在吗", "the session is usable again after a stop"


@pytest.mark.parametrize("cooperative", [False, True])
async def test_stop_landing_inside_a_plugin_hook_still_ends_the_turn(world, cooperative):
    """C14: the host swallows a cancellation raised inside a hook; the turn must end anyway."""
    world.cooperative(cooperative)
    inside = asyncio.Event()
    release = asyncio.Event()
    recall = world.app.memory.recall_for

    async def slow_recall(query, **kwargs):
        inside.set()
        await release.wait()
        return await recall(query, **kwargs)
    world.app.memory.recall_for = slow_recall
    old = Event(world, "慢慢准备上下文", admin=True)
    one = asyncio.create_task(world.turn(old))
    await asyncio.wait_for(inside.wait(), 5)
    world.app.memory.recall_for = recall
    stop = Event(world, "停止", admin=True)
    await asyncio.wait_for(world.turn(stop), 10)
    release.set()
    await asyncio.wait_for(asyncio.gather(one, return_exceptions=True), 5)
    assert world.calls == [], "no model call is spent on a stopped turn"
    assert world.sent == ["好的，先停下。"]
    later = Event(world, "在吗", admin=True)
    await asyncio.wait_for(world.turn(later), 10)
    assert world.sent[-1] == "回复：在吗"
