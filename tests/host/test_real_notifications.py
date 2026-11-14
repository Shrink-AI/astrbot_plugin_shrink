"""Real runner foreground turns, with the actual core queue and receipt repositories."""
import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("astrbot.core.agent.runners.tool_loop_agent_runner")

from tests.host.test_real_turns import Event, GROUP, until, world  # noqa: E402, F401
from shrink.adapters.notifications import notification_event  # noqa: E402
from shrink.adapters.public_api import PublicAPI  # noqa: E402
from shrink.persistence.tasks import Tasks  # noqa: E402
from shrink.services.task_states import TaskStates  # noqa: E402


def completed(world, origin):
    states = TaskStates(Tasks(world.app.database))
    states.migrate()
    identity = states.repository.create("核对文件", origin, "100", "provider")
    states.start(identity)
    states.transition(identity, "completed", result="执行完毕")
    return states, identity


def install_transport(world):
    deliveries = []
    async def send_message(session, chain):
        text = "".join(getattr(c, "text", "") for c in chain.chain)
        deliveries.append((session, text))
        world.sent.append(text)
        return True
    context = SimpleNamespace(send_message=send_message)
    world.app.notification_event = lambda session, sender: notification_event(context, session, sender)
    world.app.outbound.resolve_scope = lambda scope: {
        "source": "qq:private:100" if "FriendMessage" in scope["session"] else "qq:group:1",
        "user_id": "qq:" + scope["sender"]}
    return deliveries


async def test_n01_real_turn_foreground_parent_and_watch_report_one_fact_once(world):
    deliveries = install_transport(world)
    states, identity = completed(world, GROUP)
    world.app.operations.owned_task = lambda event, key: states.task(key)
    world.model_gate = asyncio.Event()
    event = Event(world, "核对任务结果", admin=True)
    event.set_extra("shrink_background_task", identity)
    turn = asyncio.create_task(world.turn(event))
    await until(lambda: world.calls)
    box = world.app.notification_outbox
    rows = [box.submit({"session": GROUP, "sender": "100"}, channel,
                       {"task_id": identity, "status": "completed"}, kind="task_result")
            for channel in ("task-complete", "parent-summary", "device-watch")]
    await box.pump()
    assert not deliveries
    world.model_gate.set()
    await asyncio.wait_for(turn, 10)
    await box.pump()
    assert len(world.sent) == 1 and not deliveries
    assert all(box.repository.get(row["id"])["state"] == "suppressed" for row in rows)
    assert event.get_extra("shrink_sent") and not world.hook_errors


async def test_n04_real_group_turn_does_not_move_private_task_notification(world):
    deliveries = install_transport(world)
    private = "test:FriendMessage:100"
    _, identity = completed(world, private)
    world.model_gate = asyncio.Event()
    event = Event(world, "群里聊点别的", admin=True)
    turn = asyncio.create_task(world.turn(event))
    await until(lambda: world.calls)
    api = PublicAPI(SimpleNamespace(application=world.app))
    result = await api.invoke({"protocol": 1, "version": 1, "capability": "message.notify", "action": "submit",
                              "scope": {"session": GROUP, "sender": "100"},
                              "arguments": {"event_id": "private-result", "message_type": "computer.result",
                                            "kind": "task_result", "task_id": identity,
                                            "facts": {"task_id": identity, "status": "completed"}}})
    assert result["data"]["session"] == private
    await world.app.notification_outbox.pump()
    assert [session for session, _ in deliveries] == [private]
    assert len(world.app.history.repository.events(private)) == 1
    world.model_gate.set()
    await asyncio.wait_for(turn, 10)
    assert event.get_extra("shrink_sent") and not world.hook_errors
    assert len([row for row in world.app.history.repository.events(GROUP) if row["role"] == "assistant"]) == 1


async def test_real_turn_with_stale_task_reference_still_reports_actual_tool_result(world):
    def missing(event, identity):
        raise ValueError("task scope changed")
    world.app.operations.owned_task = missing
    world.replies["查资料"] = [("shrink_lookup",), "查到了资料"]
    event = Event(world, "查资料", admin=True)
    event.set_extra("shrink_background_task", "missing")
    await asyncio.wait_for(world.turn(event), 10)
    assert world.ran == ["shrink_lookup"] and len(world.sent) == 1
    assert not event.get_extra("shrink_reported_fact") and not world.hook_errors


async def test_n05_real_foreground_send_unknown_blocks_same_background_fact(world):
    deliveries = install_transport(world)
    states, identity = completed(world, GROUP)
    world.app.operations.owned_task = lambda event, key: states.task(key)
    event = Event(world, "核对结果", admin=True)
    event.set_extra("shrink_background_task", identity)
    async def ambiguous(chain):
        world.sent.append("platform may have accepted")
        raise TimeoutError()
    event.send = ambiguous
    box = world.app.notification_outbox
    row = box.submit({"session": GROUP, "sender": "100"}, "pending-background",
                     {"task_id": identity, "status": "completed"}, kind="task_result")
    with pytest.raises(TimeoutError):
        await asyncio.create_task(world.turn(event))
    await asyncio.sleep(0)
    await box.pump()
    assert len(world.sent) == 1 and not deliveries
    assert box.repository.get(row["id"])["state"] == "suppressed"
    assert any(r["state"] == "unknown" and r["kind"] == "foreground" for r in box.repository.list())
    assert states.task(identity)["delivery"] == "unknown"
    assert not [r for r in world.app.history.repository.events(GROUP) if r["role"] == "assistant"]
