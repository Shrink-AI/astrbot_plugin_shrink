from unittest.mock import AsyncMock

import pytest

pytest.importorskip("astrbot.core.agent.runners.tool_loop_agent_runner")

from tests.host import test_real_turns  # noqa: E402
from tests.host.test_real_turns import Event  # noqa: E402

world = test_real_turns.world


async def test_disabled_classifier_group_followup_reaches_main_with_prior_history(world):
    world.app.state.put("settings", {
        **world.app.state.settings.model_dump(), "decision_enabled": False,
        "spontaneous_probability": 1,
    })
    classifier = AsyncMock(side_effect=AssertionError("classifier must remain disabled"))
    world.app.decisions.backends["haiku"] = classifier
    first = Event(world, "石英，今天在整理资料")
    await world.turn(first)
    second = Event(world, "有点累了")
    second.is_at_or_wake_command = False
    await world.turn(second)
    assert len(world.calls) == 2
    assert "今天在整理资料" in str(world.requests[-1]["contexts"])
    assert "自行判断" in str(world.requests[-1]["contexts"])
    classifier.assert_not_awaited()


async def test_disabled_classifier_private_message_reaches_main(world):
    world.app.state.put("settings", {
        **world.app.state.settings.model_dump(), "decision_enabled": False,
    })
    classifier = AsyncMock(side_effect=AssertionError("classifier must remain disabled"))
    world.app.decisions.backends["haiku"] = classifier
    event = Event(world, "有点累了")
    event.unified_msg_origin = "test:FriendMessage:100"
    event.get_group_id = lambda: ""
    event.is_private_chat = lambda: True
    event.is_at_or_wake_command = False
    await world.turn(event)
    assert len(world.calls) == 1 and world.sent
    classifier.assert_not_awaited()
