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


async def test_private_memory_platform_events_keep_granted_evidence_out_of_group_and_history(world):
    deliveries = install_transport(world)

    def private(text, sender):
        event = Event(world, text, sender=sender)
        event.is_private_chat = lambda: True
        event.get_group_id = lambda: ""
        event.unified_msg_origin = f"test:FriendMessage:{sender}"
        return event

    first, second = "100001", "100002"
    await world.turn(private("这里的验证词是金丝雀密钥", first))
    await world.turn(private("注册我的收件私聊", second))
    requests = len(world.requests)
    await world.turn(private("我要和咸鱼共享与你的私聊记忆", first))
    assert "私聊互授权 对方QQ号 全部记忆" in world.sent[-1]
    assert not world.app.database.rows("SELECT id FROM private_memory_consent")
    await world.turn(private("我需要双向授权", first))
    assert "双方确认后才生效" in world.sent[-1]
    await world.turn(private(f"我要和 QQ {second} 共享全部私聊记忆", first))
    assert "整理记忆" in world.sent[-1]
    assert len(world.requests) == requests
    identity = world.sent[-1].split()[-1]
    await world.turn(private(f"确认互授权 {identity}", first))
    await world.app.notification_outbox.pump()
    assert any(session.endswith(second) and "整理记忆" in text for session, text in deliveries)
    await world.turn(private(f"接受互授权 {identity}", second))
    await world.turn(private("查共享 金丝雀密钥", second))
    assert "这里的验证词是金丝雀密钥" in world.sent[-1]
    session = f"test:FriendMessage:{second}"
    assert not any("金丝雀" in (row["text"] or "") for row in world.app.history.repository.events(session))
    await world.turn(Event(world, "查共享 金丝雀", sender=second))
    assert not any("这里的验证词是" in str(request) for request in world.requests[2:])
    await world.turn(private(f"撤销互授权 {identity}", first))
    await world.turn(private("查共享 金丝雀", second))
    assert "没有查到" in world.sent[-1] and not world.hook_errors
