"""The foreground helpers against the real host classes (AstrBot 4.29.4), not stand-ins.

Skipped unless the host package is installed:
    uv run --no-project --python 3.12 --with astrbot==4.29.4 --with pytest --with pytest-asyncio \
        python -m pytest tests/host -q

What runs for real: the host's agent runner, its follow-up queue and ordering gate, the tool
set a request carries, and the registry of active runs. The model and the tools are scripted.
The pipeline stage around them (session lock, history saving, platform sending) is not run.
"""
import asyncio

import pytest

pytest.importorskip("astrbot.core.agent.runners.tool_loop_agent_runner")

from astrbot.core.agent.hooks import BaseAgentRunHooks  # noqa: E402
from astrbot.core.agent.run_context import ContextWrapper  # noqa: E402
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner  # noqa: E402
from astrbot.core.agent.tool import FunctionTool, ToolSet  # noqa: E402
from astrbot.core.agent.tool_executor import BaseFunctionToolExecutor  # noqa: E402
from astrbot.core.pipeline.process_stage import follow_up  # noqa: E402
from astrbot.core.provider.entities import LLMResponse, ProviderRequest  # noqa: E402
from mcp.types import CallToolResult, TextContent  # noqa: E402

from shrink.adapters.foreground import (  # noqa: E402
    active_runner, release_follow_ups, request_stop, restrict)

UMO = "test:GroupMessage:1"


class Event:
    unified_msg_origin = UMO

    def __init__(self, text="你好", sender="100", message_id="m1"):
        self.extra, self.text, self.sender = {}, text, sender
        self.message_obj = type("M", (), {"message_id": message_id})()

    def get_extra(self, key, default=None):
        return self.extra.get(key, default)

    def set_extra(self, key, value):
        self.extra[key] = value

    def get_sender_id(self):
        return self.sender

    def get_message_str(self):
        return self.text

    def get_message_outline(self):
        return self.text


class Model:
    """Replies from a script: a string ends the run, a tuple of names calls those tools."""
    provider_config = {"id": "scripted"}

    def __init__(self, *script):
        self.script, self.seen = list(script), []

    def get_model(self):
        return "scripted"

    async def text_chat(self, **payload):
        self.seen.append(payload)
        step = self.script.pop(0)
        if isinstance(step, str):
            return LLMResponse(role="assistant", completion_text=step)
        return LLMResponse(role="tool", tools_call_name=list(step),
                           tools_call_args=[{} for _ in step],
                           tools_call_ids=[f"call-{len(self.seen)}-{i}" for i in range(len(step))])


def tool(name):
    async def handler(**kwargs):
        return name
    return FunctionTool(name=name, description=name,
                        parameters={"type": "object", "properties": {}}, handler=handler)


class Tools(BaseFunctionToolExecutor):
    ran, gates, cancelled = [], {}, []

    @classmethod
    async def execute(cls, tool, run_context, **tool_args):
        cls.ran.append(tool.name)
        gate = cls.gates.get(tool.name)
        try:
            if gate is not None:
                await gate.wait()
        except asyncio.CancelledError:
            cls.cancelled.append(tool.name)
            raise
        yield CallToolResult(content=[TextContent(type="text", text=tool.name + " ok")])


@pytest.fixture(autouse=True)
def clean():
    Tools.ran, Tools.gates, Tools.cancelled = [], {}, []
    follow_up._ACTIVE_AGENT_RUNNERS.clear()
    follow_up._FOLLOW_UP_ORDER_STATE.clear()
    yield
    follow_up._ACTIVE_AGENT_RUNNERS.clear()
    follow_up._FOLLOW_UP_ORDER_STATE.clear()


async def build(event, model, request):
    runner = ToolLoopAgentRunner()
    await runner.reset(provider=model, request=request,
                       run_context=ContextWrapper(context=type("C", (), {"event": event})()),
                       tool_executor=Tools, agent_hooks=BaseAgentRunHooks(), streaming=False)
    return runner


async def start(event, model, *names):
    request = ProviderRequest(prompt=event.text, system_prompt="persona",
                              func_tool=ToolSet([tool(n) for n in names]))
    runner = await build(event, model, request)
    follow_up.register_active_runner(UMO, runner)
    return runner


async def drive(runner):
    return [response.type async for response in runner.step_until_done(8)]


def tool_results(runner):
    return [str(m.content) for m in runner.run_context.messages if m.role == "tool"]


def test_foreground_removes_host_execution_catalog_but_preserves_persona():
    from astrbot.core.astr_main_agent import SANDBOX_MODE_PROMPT, _build_local_mode_prompt
    from astrbot.core.skills.skill_manager import build_skills_prompt
    request = ProviderRequest(system_prompt="PERSONA\n" + build_skills_prompt([]) +
                              SANDBOX_MODE_PROMPT + _build_local_mode_prompt() + "\nMEMORY",
                              func_tool=ToolSet([tool("shrink_orchestrate")]))
    restrict(request)
    assert request.system_prompt.strip() == "PERSONA\n\nMEMORY"
    assert "tools" not in request.func_tool.get_tool("shrink_orchestrate").parameters["properties"]["action"]["enum"]


def test_foreground_tool_clone_keeps_bound_host_handler_without_copying_host():
    class Host:
        def __deepcopy__(self, memo):
            raise AssertionError("Host resources must not be copied")

        async def orchestrate(self, **kwargs):
            return "ok"
    host = Host()
    original = tool("shrink_orchestrate")
    original.handler = host.orchestrate
    original.parameters = {"original": True}
    request = ProviderRequest(func_tool=ToolSet([original]))
    restrict(request)
    narrowed = request.func_tool.get_tool("shrink_orchestrate")
    assert narrowed is not original
    assert narrowed.handler.__self__ is host
    assert original.parameters == {"original": True}


async def test_withheld_tool_cannot_be_executed_even_when_the_model_names_it():
    """T06: filtering the request's tool set is what blocks execution, not a hook."""
    background = ToolSet([tool("astrbot_execute_shell")])
    request = ProviderRequest(prompt="跑个脚本", system_prompt="persona", func_tool=ToolSet(
        [tool(n) for n in ("astrbot_execute_shell", "astrbot_execute_python",
                           "astrbot_shell_session", "transfer_to_dev", "shrink_lookup")]))
    removed = restrict(request)
    assert sorted(removed) == ["astrbot_execute_python", "astrbot_execute_shell",
                               "astrbot_shell_session", "transfer_to_dev"]
    model = Model(("astrbot_execute_shell", "transfer_to_dev", "shrink_lookup"), "查到了")
    runner = await build(Event(), model, request)
    await drive(runner)
    assert Tools.ran == ["shrink_lookup"]
    # The model was never offered the withheld tools, and naming them only costs a round.
    assert model.seen[0]["func_tool"].names() == ["shrink_lookup"]
    results = " ".join(tool_results(runner))
    assert "astrbot_execute_shell not found" in results and "transfer_to_dev not found" in results
    assert runner.get_final_llm_resp().completion_text == "查到了"
    # Tool sets built elsewhere, such as a background agent's, are untouched.
    assert background.get_tool("astrbot_execute_shell") is not None


async def test_follow_up_is_consumed_once_into_the_tool_result():
    """C11: the host hands the new words to the running turn and ends the new event."""
    old, new = Event("查一下部署状态"), Event("顺便看下日志", message_id="m2")
    Tools.gates["shrink_lookup"] = asyncio.Event()
    runner = await start(old, Model(("shrink_lookup",), "都看了"), "shrink_lookup")
    assert active_runner(old) is runner and active_runner(new) is None
    run = asyncio.create_task(drive(runner))
    while not Tools.ran:
        await asyncio.sleep(0)
    capture = follow_up.try_capture_follow_up(new)
    assert capture is not None
    Tools.gates["shrink_lookup"].set()
    consumed, activated = await asyncio.wait_for(follow_up.prepare_follow_up_capture(capture), 5)
    await run
    assert (consumed, activated) == (True, False)
    assert sum("顺便看下日志" in text for text in tool_results(runner)) == 1
    await follow_up.finalize_follow_up_capture(capture, activated=False, consumed_marked=True)
    # A third person in the same group is never folded into this run.
    assert follow_up.try_capture_follow_up(Event("我也有事", sender="200")) is None


async def test_unconsumed_follow_up_resumes_as_its_own_turn_when_the_run_ends():
    """C12, normal end: a run that calls no tool cannot consume; the message is released."""
    old, new = Event("在吗"), Event("人呢", message_id="m2")
    runner = await start(old, Model("在"))
    capture = follow_up.try_capture_follow_up(new)
    await drive(runner)
    consumed, activated = await asyncio.wait_for(follow_up.prepare_follow_up_capture(capture), 5)
    assert (consumed, activated) == (False, True)
    await follow_up.finalize_follow_up_capture(capture, activated=True, consumed_marked=False)


async def test_run_that_dies_mid_step_strands_follow_ups_until_released():
    """C12, failure: the host does not release queued messages when a step raises."""
    old, new = Event("查一下"), Event("快点", message_id="m2")
    runner = await start(old, Model(("shrink_lookup",)), "shrink_lookup")

    async def broken(*args, **kwargs):
        raise RuntimeError("step failed")
        yield
    runner._handle_function_tools = broken
    capture = follow_up.try_capture_follow_up(new)
    with pytest.raises(RuntimeError):
        await drive(runner)
    waiting = asyncio.create_task(follow_up.prepare_follow_up_capture(capture))
    await asyncio.sleep(0.05)
    assert not waiting.done(), "host now releases these itself; the safety net can go"
    # What ConversationAdapter.track does when the old turn's task ends.
    assert release_follow_ups(runner)
    assert await asyncio.wait_for(waiting, 5) == (False, True)
    await follow_up.finalize_follow_up_capture(capture, activated=True, consumed_marked=False)


async def test_cooperative_stop_ends_the_run_and_frees_queued_messages():
    """C10/C14: stopping through the run releases the follow-up; later ones are not captured."""
    old, new = Event("帮我查很久的东西"), Event("算了换个问题", message_id="m2")
    Tools.gates["shrink_lookup"] = asyncio.Event()
    runner = await start(old, Model(("shrink_lookup",), "不该出现的回复"), "shrink_lookup")
    run = asyncio.create_task(drive(runner))
    while not Tools.ran:
        await asyncio.sleep(0)
    capture = follow_up.try_capture_follow_up(new)
    before = len(runner.run_context.messages)
    assert request_stop(old)
    types = await asyncio.wait_for(run, 5)
    assert types[-1] == "aborted" and runner.was_aborted() and runner.done()
    assert await asyncio.wait_for(follow_up.prepare_follow_up_capture(capture), 5) == (False, True)
    await follow_up.finalize_follow_up_capture(capture, activated=True, consumed_marked=False)
    # No further message is queued on the stopped run, so nothing waits behind it.
    assert follow_up.try_capture_follow_up(Event("还有一句", message_id="m3")) is None
    # Costs of the host's stop, to be accepted before the setting is turned on:
    assert Tools.cancelled == ["shrink_lookup"], "the running tool is interrupted"
    added = runner.run_context.messages[before:]
    assert [m.role for m in added][-2:] == ["user", "assistant"], "a stop pair joins the history"
    assert runner.get_final_llm_resp().completion_text != "不该出现的回复"


async def test_stop_before_the_first_model_call_spends_no_model_round():
    old = Event("写一篇长文")
    model = Model("不该生成")
    runner = await start(old, model)
    assert request_stop(old)
    await drive(runner)
    assert runner.was_aborted() and model.seen == []
