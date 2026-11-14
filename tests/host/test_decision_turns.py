import asyncio
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("astrbot.core.agent.runners.tool_loop_agent_runner")

from tests.host.test_real_turns import Event, world  # noqa: E402,F401
from shrink.persistence.tasks import Tasks  # noqa: E402
from shrink.services.task_states import TaskStates  # noqa: E402


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


async def test_failed_group_classification_keeps_pending_input_for_recovery(world):
    event = Event(world, "这张图真有意思")
    event.is_at_or_wake_command = False
    world.app.state.put("settings", {**world.app.state.settings.model_dump(), "spontaneous_probability": 1})
    world.app.decisions.backends["haiku"] = AsyncMock(return_value="invalid")
    world.app.decisions.backends["haiku"].version = "test-v1"
    await world.turn(event)
    assert not world.calls and not world.sent
    pending = world.app.inbox.repository.pending("chat")
    assert len(pending) == 1 and pending[0]["text"] == event.message_str
    assert world.app.decisions.repository.list()[0]["fallback"]
