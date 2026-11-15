"""Real 4.29.4 runner; scripted provider and platform, event-controlled shared resources."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("astrbot.core.agent.runners.tool_loop_agent_runner")

from tests.host.test_real_turns import Event  # noqa: E402
from tests.host import test_real_turns  # noqa: E402
from shrink.adapters.knowledge import KnowledgeBridge  # noqa: E402
from shrink.persistence.tasks import Tasks  # noqa: E402
from shrink.persistence.task_inputs import TaskInputsRepository  # noqa: E402
from shrink.services.task_inputs import TaskInputs  # noqa: E402
from shrink.services.task_states import TaskStates  # noqa: E402
from shrink.services.task_execution import TaskExecution  # noqa: E402

world = pytest.fixture(test_real_turns.world.__wrapped__)


async def test_real_tool_reply_is_already_final_and_expression_calls_are_zero(world):
    world.app.expression.model = AsyncMock(return_value="不应再改写")
    world.replies["查一下"] = [("shrink_lookup",), "好嘞，查到了"]
    event = Event(world, "查一下")
    await world.turn(event)
    assert world.sent == ["好嘞，查到了"]
    world.app.expression.model.assert_not_awaited()
    trace = world.traces()[event.message_obj.message_id]
    assert trace["model_calls"] == 2 and trace["expression_model_calls"] == 0


async def test_real_explicit_code_reply_is_final_without_another_expression_call(world):
    text = '配置可以这样写：\n```json\n{"label":"本轮要求：示例"}\n```'
    world.app.expression.model = AsyncMock(side_effect=AssertionError("no recovery"))
    world.replies["给我代码"] = [("shrink_lookup",), text]
    event = Event(world, "给我代码")
    await world.turn(event)
    assert world.sent == [text]
    world.app.expression.model.assert_not_awaited()
    assert world.traces()[event.message_obj.message_id]["expression_model_calls"] == 0


@pytest.mark.parametrize("budget", [None, 13])
async def test_foreground_does_not_load_execution_grilling(world, monkeypatch, budget):
    waits = []
    original = asyncio.wait_for
    async def planning_guidance(text):
        return "准备方法"
    world.app.native_skills = SimpleNamespace(planning_guidance=planning_guidance)
    if budget:
        world.app.state.put("settings", {**world.app.state.settings.model_dump(),
                                        "foreground_grilling_timeout": budget})
    async def wait(coro, timeout):
        if getattr(coro, "__name__", "") == "planning_guidance":
            waits.append(timeout)
        return await original(coro, timeout)
    monkeypatch.setattr(asyncio, "wait_for", wait)
    world.replies["查一下"] = ["好嘞"]
    await world.turn(Event(world, "查一下"))
    assert world.sent == ["好嘞"] and waits == []


async def test_t01_real_context_and_reply_finish_with_three_slots_and_waiting_work(world):
    app = world.app
    release, started = asyncio.Event(), asyncio.Event()
    count = 0
    async def execute(*args):
        nonlocal count
        count += 1
        if count == 3:
            started.set()
        await release.wait()
        return "执行结束"
    states = TaskStates(Tasks(app.database))
    states.migrate()
    execution = TaskExecution(
        states, TaskInputs(TaskInputsRepository(app.database), states),
        SimpleNamespace(get=lambda _: None), execute, AsyncMock(), app.tasks.error, app.scheduling)
    for _ in range(4):
        execution.launch(states.repository.create("后台工作", "background", "100", "provider"))
    bridge = KnowledgeBridge(SimpleNamespace(), app)
    rows = [(1.0, {"text": "已经按原范围筛选的资料"})]
    original = app.memory.recall_for
    async def recall(query):
        assert await bridge.rerank(query.text, rows) == rows
        return await original(query)
    app.memory.recall_for = recall
    try:
        await asyncio.wait_for(started.wait(), 2)
        await asyncio.wait_for(app.scheduling.queued["execution"].wait(), 2)
        await bridge.lock.acquire()
        await bridge.rerank_lock.acquire()
        await app.attachments.slots.acquire()
        await app.attachments.slots.acquire()
        event = Event(world, "你好")
        world.replies["你好"] = ["好嘞"]
        await asyncio.wait_for(world.turn(event), 2)
        assert world.sent == ["好嘞"] and count == 3 and not release.is_set()
        assert sum(row["status"] == "queued" for row in states.repository.list()) == 1
        trace = world.traces()[event.message_obj.message_id]
        assert "context" in trace["phases"] and "chat_queue" in trace["phases"]
    finally:
        release.set()
        await execution.close()


async def test_c13_group_senders_keep_lock_and_model_phases_when_execution_is_full(world):
    app = world.app
    model_entered, release_model, waiting_lock = asyncio.Event(), asyncio.Event(), asyncio.Event()
    for _ in range(3):
        await app.scheduling.slots["execution"].acquire()
    original_chat, original_waiting = world.text_chat, world.adapter.waiting
    async def model(**kwargs):
        model_entered.set()
        await release_model.wait()
        return await original_chat(**kwargs)
    def waiting(event):
        original_waiting(event)
        if event.sender == "200":
            waiting_lock.set()
    world.text_chat, world.adapter.waiting = model, waiting
    one, two = Event(world, "你好", "100"), Event(world, "收到", "200")
    world.replies.update({"你好": ["好嘞"], "收到": ["那再等等"]})
    first = asyncio.create_task(world.turn(one))
    second = None
    try:
        await asyncio.wait_for(model_entered.wait(), 2)
        second = asyncio.create_task(world.turn(two))
        await asyncio.wait_for(waiting_lock.wait(), 2)
        assert not second.done()
        release_model.set()
        await asyncio.wait_for(asyncio.gather(first, second), 3)
        traces = world.traces()
        assert traces[one.message_obj.message_id]["sender"] == "qq:100"
        assert traces[two.message_obj.message_id]["sender"] == "qq:200"
        assert "model" in traces[one.message_obj.message_id]["phases"]
        assert "lock" in traces[two.message_obj.message_id]["phases"]
        assert world.sent == ["好嘞", "那再等等"]
    finally:
        release_model.set()
        await asyncio.gather(*(t for t in (first, second) if t is not None), return_exceptions=True)
        for _ in range(3):
            app.scheduling.slots["execution"].release()


async def test_foreground_tool_keeps_chat_lane_after_request_hook_returns(world):
    from shrink.services.scheduling import LANE
    entered, release = asyncio.Event(), asyncio.Event()
    lanes = []
    async def embed(text):
        if text == "background":
            entered.set()
            await release.wait()
        return None
    world.app.memory.embed = world.app.scheduling.wrap(embed, "embedding")
    async def background():
        async with world.app.scheduling.use("maintenance"):
            await world.app.memory.embed("background")
    original = world.tools
    class Tools(original):
        @classmethod
        async def execute(cls, tool, run_context, **args):
            lanes.append(LANE.get())
            await world.app.memory.embed("tool lookup")
            async for result in super().execute(tool, run_context, **args):
                yield result
    world.tools = Tools
    world.replies["查一下"] = [("shrink_lookup",), "查到了"]
    worker = asyncio.create_task(background())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(world.turn(Event(world, "查一下")), 2)
        assert lanes == ["chat"] and world.sent == ["查到了"] and not release.is_set()
    finally:
        release.set()
        await worker
