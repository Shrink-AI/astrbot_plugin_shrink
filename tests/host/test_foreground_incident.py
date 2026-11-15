from unittest.mock import AsyncMock

import pytest

pytest.importorskip("astrbot.core.agent.runners.tool_loop_agent_runner")

from tests.host import test_real_turns  # noqa: E402
from tests.host.test_real_turns import Event  # noqa: E402

world = pytest.fixture(test_real_turns.world.__wrapped__)


async def test_tool_budget_removes_tools_before_another_lookup(world):
    original = world.text_chat
    async def model(**payload):
        offered = getattr(payload.get("func_tool"), "tools", [])
        world.replies["查一下"] = [("shrink_lookup",)] if offered else ["需要继续调查，再交给后台"]
        return await original(**payload)
    world.text_chat = model
    await world.turn(Event(world, "查一下"))
    assert len(world.ran) <= 4
    assert len(world.calls) <= 5
    assert world.sent == ["需要继续调查，再交给后台"]


async def test_tool_budget_keeps_every_result_in_current_parallel_batch(world):
    world.replies["查一下"] = [("shrink_lookup",) * 6, "查好了"]
    await world.turn(Event(world, "查一下"))
    assert world.ran == ["shrink_lookup"] * 6
    assert len(world.requests) == 2
    final = world.requests[-1]
    assert final["tools"] == []
    calls = [call["id"] for message in final["contexts"]
             for call in message.get("tool_calls", [])]
    receipts = [message["tool_call_id"] for message in final["contexts"]
                if message["role"] == "tool"]
    assert len(calls) == 6
    assert receipts == calls


async def test_dispatch_disables_next_round_tools_after_batch_closes(world):
    original = world.adapter.tool_used
    def delegated(event, tool, args, result):
        event.set_extra("shrink_background_task", "submitted-task")
        original(event, tool, args, result)
        # This fixture does not own a real task store.
        event.set_extra("shrink_background_task", None)
    world.adapter.tool_used = delegated
    world.replies["查一下"] = [("shrink_lookup",), "已提交"]
    await world.turn(Event(world, "查一下"))
    assert len(world.ran) == 1
    assert world.requests[-1]["tools"] == []
    assert any(m["role"] == "tool" for m in world.requests[-1]["contexts"])


async def test_direct_skip_recovers_and_is_consumed_only_after_send(world):
    world.replies["我喜欢你"] = ["[[skip]]"]
    world.app.expression.model = AsyncMock(return_value="听到了，谢谢你")
    event = Event(world, "我喜欢你", sender="200")
    original = event.send
    async def send(chain):
        row = world.app.inbox.repository.get(event.get_extra("shrink_input_id"))
        assert row["processed"] is None
        await original(chain)
    event.send = send
    await world.turn(event)
    assert world.sent == ["听到了，谢谢你"]
    assert world.app.inbox.repository.get(event.get_extra("shrink_input_id"))["processed"] is not None


async def test_failed_platform_send_does_not_consume_input(world):
    event = Event(world, "你好")
    event.send = AsyncMock(return_value=False)
    await world.turn(event)
    row = world.app.inbox.repository.get(event.get_extra("shrink_input_id"))
    assert row["processed"] is None
    assert row["route"] == "chat_send_unknown"
    assert world.traces()[event.message_obj.message_id]["outcome"] == "send_unknown"


async def test_failed_extra_bubble_does_not_change_accepted_input(world):
    event = Event(world, "你好")
    original = event.send
    calls = 0
    async def send(chain):
        nonlocal calls
        calls += 1
        if calls > 1:
            return False
        await original(chain)
    event.send = send
    await world.turn(event)
    before = world.app.inbox.repository.get(event.get_extra("shrink_input_id"))
    await event.send(event.get_result())
    after = world.app.inbox.repository.get(event.get_extra("shrink_input_id"))
    assert before["processed"] is not None
    assert after["route"] == "chat" and after["processed"] == before["processed"]
    assert world.traces()[event.message_obj.message_id]["outcome"] == "sent"
