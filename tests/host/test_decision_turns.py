import asyncio
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("astrbot.core.agent.runners.tool_loop_agent_runner")

from tests.host import test_real_turns  # noqa: E402
from tests.host.test_real_turns import Event  # noqa: E402
from shrink.persistence.tasks import Tasks  # noqa: E402
from shrink.services.task_states import TaskStates  # noqa: E402

world = test_real_turns.world


async def test_d02_real_turn_decision_timeout_still_answers_direct_call(world):
    async def stall(payload):
        await asyncio.Event().wait()
    world.app.decisions.backends["haiku"] = stall
    world.app.state.put("settings", {**world.app.state.settings.model_dump(), "decision_timeout": .02})
    event = Event(world, "石英，帮我看看天气")
    await asyncio.wait_for(world.turn(event), 2)
    assert world.sent == ["回复：石英，帮我看看天气"]
    assert len(world.calls) == 1
    record = world.app.decisions.repository.list()[0]
    assert record["reason"] == "timeout" and record["fallback"]
    trace = world.app.turn_traces.since()[0]
    assert 0 < trace["phases"]["decision"] < .5 and trace["outcome"] == "sent"


async def test_ambiguous_stop_has_no_execution_tools_or_cancellation(world):
    states = TaskStates(Tasks(world.app.database))
    states.migrate()
    for _ in range(2):
        states.repository.create("work", "test:GroupMessage:1", "100", "test")
    model = AsyncMock()
    world.app.decisions.backends["haiku"] = model
    event = Event(world, "停止", admin=True)
    await world.turn(event)
    assert not world.ran
    assert len(states.repository.list()) == 2
    assert all(t["status"] == "queued" for t in states.repository.list())
    model.assert_not_awaited()
    contexts = str(world.requests[0]["contexts"])
    assert "只询问要停止的具体目标" in contexts
    assert world.requests[0]["tools"] == []


@pytest.mark.parametrize("failure", ["invalid", "timeout"])
async def test_failed_group_classification_leaves_unsolicited_turn_silent(world, failure):
    event = Event(world, "这张图真有意思")
    event.is_at_or_wake_command = False
    world.app.state.put("settings", {**world.app.state.settings.model_dump(),
                                    "spontaneous_probability": 1, "decision_timeout": .01})
    world.app.decisions.backends["haiku"] = AsyncMock(return_value="invalid")
    if failure == "timeout":
        async def stall(*args):
            await asyncio.Event().wait()
        world.app.decisions.backends["haiku"].side_effect = stall
    world.app.decisions.backends["haiku"].version = "test-v1"
    await world.turn(event)
    assert not world.calls and not world.sent
    pending = world.app.inbox.repository.pending("chat")
    assert not pending
    assert world.app.decisions.repository.list()[0]["fallback"]
    assert world.app.decisions.repository.list()[0]["decision"]["route"] == "silent"


async def test_silent_candidates_leave_budget_for_natural_chime_in(world):
    world.app.state.put("settings", {**world.app.state.settings.model_dump(),
                                    "spontaneous_probability": 1, "engaged_seconds": 0})
    routes = iter(["silent", "silent", "silent", "reply", "reply"])
    async def classify(payload):
        route = next(routes)
        assert payload["rules"]["participation"] == "spontaneous"
        return {"intent": "chat", "route": route, "conversation": payload["event"]["session"],
                "topic": None, "task": None, "evidence": [payload["event"]["id"]],
                "capability_candidates": [], "notify": "silent" if route == "silent" else "normal",
                "confidence": .8}
    world.app.decisions.backends["haiku"] = classify
    for i in range(6):
        event = Event(world, f"今天闲聊第{i}句")
        event.is_at_or_wake_command = False
        await world.turn(event)
    assert len(world.sent) == len(world.calls) == 2
    assert sum(r["reason"] == "spontaneous" and r["outcome"] == "sent"
               for r in world.app.turn_traces.since()) == 2
    assert world.app.decisions.repository.list()[0]["reason"] == "participation_budget"


async def test_simultaneous_candidates_cannot_exceed_send_budget(world):
    from tests.host.test_real_turns import until
    world.app.state.put("settings", {**world.app.state.settings.model_dump(),
                                    "decision_enabled": False, "spontaneous_probability": 1,
                                    "engaged_seconds": 0})
    world.model_gate = asyncio.Event()
    events = [Event(world, f"聊聊今天的趣事{i}", sender=str(200 + i)) for i in range(3)]
    for event in events:
        event.is_at_or_wake_command = False
    tasks = [asyncio.create_task(world.turn(event)) for event in events]
    await until(lambda: all(e.get_extra("shrink_reply") for e in events))
    world.model_gate.set()
    await asyncio.wait_for(asyncio.gather(*tasks), 10)
    assert len(world.sent) == 2
    assert any(e.get_extra("shrink_participation_limited") for e in events)
    assert not any(e.get_extra("shrink_send_unknown") for e in events)


async def test_rejected_send_releases_spontaneous_budget(world):
    world.app.state.put("settings", {**world.app.state.settings.model_dump(),
                                    "decision_enabled": False, "spontaneous_probability": 1,
                                    "spontaneous_limit": 1, "engaged_seconds": 0})
    event = Event(world, "聊聊这个游戏")
    event.is_at_or_wake_command = False
    event.send = AsyncMock(return_value=False)
    await world.turn(event)
    assert not world.sent
    next_event = Event(world, "刚玩了一个新游戏", sender="200")
    next_event.is_at_or_wake_command = False
    await world.turn(next_event)
    assert len(world.sent) == 1


async def test_main_model_skip_does_not_spend_spontaneous_budget(world):
    world.app.state.put("settings", {**world.app.state.settings.model_dump(),
                                    "decision_enabled": False, "spontaneous_probability": 1,
                                    "spontaneous_limit": 1})
    event = Event(world, "暂时没什么好补充")
    event.is_at_or_wake_command = False
    world.replies[event.message_str] = ["[[skip]]"]
    await world.turn(event)
    assert not world.sent
    next_event = Event(world, "聊聊这部新电影")
    next_event.is_at_or_wake_command = False
    await world.turn(next_event)
    assert len(world.sent) == 1
