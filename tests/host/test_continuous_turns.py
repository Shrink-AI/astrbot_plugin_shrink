"""Continuous history through real AstrBot requests, tools, lock and reply hooks."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("astrbot.core.agent.runners.tool_loop_agent_runner")

from tests.host.test_real_turns import Event, world, until  # noqa: E402,F401


def text(message):
    value = message.get("content")
    return value if isinstance(value, str) else "".join(p.get("text", "") for p in value or [])


async def test_c01_second_native_turn_contains_first_roles_tools_and_stable_system(world):
    world.replies["查下记忆"] = [("shrink_lookup",), "查到了"]
    await world.turn(Event(world, "查下记忆"))
    await world.turn(Event(world, "那接着说"))
    first = world.requests[0]["contexts"]
    second = world.requests[-1]["contexts"]
    assert text(first[0]) == text(second[0]) and first[0]["role"] == "system"
    assert "本轮参考资料" not in text(second[0])
    assert any(m["role"] == "user" and "查下记忆" in text(m) for m in second)
    assert any(m["role"] == "assistant" and text(m) == "查到了" for m in second)
    calls = [m for m in second if m.get("tool_calls")]
    assert len(calls) == 1 and calls[0]["tool_calls"][0]["function"]["name"] == "shrink_lookup"
    result = next(m for m in second if m["role"] == "tool")
    assert text(result) == "ok" and result["tool_call_id"] == calls[0]["tool_calls"][0]["id"]
    assert world.ran == ["shrink_lookup"]
    traces = world.app.turn_traces.since()
    assert traces[0]["system_prefix_same"] == 1 and traces[1]["system_prefix_same"] is None


async def test_c02_native_debounce_records_all_before_consumption_and_no_duplicate(world):
    settings = world.app.state.settings.model_dump()
    settings.update(debounce_enabled=True, debounce_seconds=.1)
    world.app.state.put("settings", settings)
    first, second = Event(world, "先选城市"), Event(world, "最好杭州")
    one = asyncio.create_task(world.turn(first))
    await until(lambda: len(world.app.inbox.repository.pending("chat")) == 1)
    assert world.calls == []
    two = asyncio.create_task(world.turn(second))
    await asyncio.gather(one, two)
    users = [text(m) for m in world.requests[-1]["contexts"]
             if m["role"] == "user" and text(m).startswith("[发言人 ")]
    assert len(users) == 2 and "先选城市" in users[0] and "最好杭州" in users[1]
    assert len(world.calls) == 1
    await world.turn(second)
    assert len(world.calls) == 1 and not world.app.inbox.repository.pending("chat")


async def test_c03_native_group_keeps_speakers_separate(world):
    await world.turn(Event(world, "我选杭州", sender="100"))
    await world.turn(Event(world, "我选南京", sender="200"))
    users = [text(m) for m in world.requests[-1]["contexts"]
             if m["role"] == "user" and text(m).startswith("[发言人 ")]
    assert len(users) == 2
    assert "qq:100" in users[0] and "杭州" in users[0]
    assert "qq:200" in users[1] and "南京" in users[1]


async def test_native_send_error_hook_does_not_record_unsent_assistant(world):
    event = Event(world, "测试发送")
    async def reject(chain):
        return False
    event.send = reject
    await world.turn(event)
    rows = world.app.history.repository.events(event.unified_msg_origin)
    assert [r["role"] for r in rows] == ["user"]
    assert world.app.turn_traces.since()[0]["outcome"] == "send_unknown"
    assert not world.app.inbox.repository.pending("chat")
    pending = world.app.inbox.repository.pending("chat_send_unknown")
    assert len(pending) == 1 and pending[0]["processed"] is None


async def test_native_history_writer_is_disabled_for_owned_request(world):
    from astrbot.core.provider.entities import ProviderRequest
    from astrbot.core.pipeline.process_stage.method.agent_sub_stages.internal import InternalAgentSubStage
    event = Event(world, "在吗")
    await world.adapter.receive(event)
    request = ProviderRequest(prompt="在吗", system_prompt="")
    await world.adapter.enrich(event, request)
    assert request.conversation is None
    manager = SimpleNamespace(update_conversation=AsyncMock())
    await InternalAgentSubStage._save_to_history(
        SimpleNamespace(conv_manager=manager), event, request, None, [], None)
    manager.update_conversation.assert_not_awaited()


async def test_native_recovery_requeues_pending_once_with_persisted_sender(world):
    from shrink.adapters.input_recovery import recover_inputs
    from shrink.persistence.inbox import InboxRepository
    from shrink.services.inbox import Inbox
    first, pending = Event(world, "已处理"), Event(world, "重启前未处理", sender="200")
    await world.turn(first)
    await world.adapter.receive(pending)
    queue = asyncio.Queue()
    context = SimpleNamespace(get_event_queue=lambda: queue, get_config=lambda **kwargs: {"admins_id": ["100"]},
                              send_message=AsyncMock(return_value=True))
    inbox = Inbox(InboxRepository(world.app.database), world.app.messages, world.app.history)
    await recover_inputs(context, inbox)
    await recover_inputs(context, inbox)
    assert queue.qsize() == 1
    restored = queue.get_nowait()
    assert restored.get_sender_id() == "200" and not restored.is_admin()
    await world.turn(restored)
    assert not inbox.repository.pending("chat")


async def test_revocation_bypasses_debounce_and_stops_owned_work(world):
    settings = world.app.state.settings.model_dump()
    settings.update(debounce_enabled=True, debounce_seconds=5)
    world.app.state.put("settings", settings)
    stopped = AsyncMock(return_value=[])
    world.app.operations.cancel_owned = stopped
    event = Event(world, "撤销所有授权", admin=True)
    await asyncio.wait_for(world.turn(event), 1)
    stopped.assert_awaited_once_with(event)
    assert event.stopped and not world.calls and not world.adapter.debouncer.pending


async def test_platform_recall_appends_state_and_does_not_replay_original(world):
    original = Event(world, "周一出发")
    await world.turn(original)
    recalled = Event(world, "")
    recalled.message_obj.raw_message = {"notice_type": "group_recall", "message_id": original.message_obj.message_id}
    await world.turn(recalled)
    duplicate = Event(world, "")
    duplicate.message_obj.raw_message = recalled.message_obj.raw_message
    await world.turn(duplicate)
    assert len(world.calls) == 1
    rows = world.app.history.repository.events(original.unified_msg_origin)
    assert sum(r["kind"] == "retracted" for r in rows) == 1
    context = await world.app.history.contexts(original.unified_msg_origin, 4000)
    assert "周一出发" not in text(context[0]) and "已撤回" in text(context[0])


async def test_native_timer_has_stable_run_identity_and_processed_position(world):
    from astrbot.core.provider.entities import ProviderRequest, LLMResponse
    event = Event(world, "定时检查")
    event.extra.update(cron_job={"id": "job", "run_started_at": "2026-11-14T12:00"},
                       cron_payload={"sender_id": "100"})
    await world.adapter.enrich(event, ProviderRequest(prompt=event.message_str))
    row = world.app.inbox.repository.pending("timer")[0]
    assert row["sender"] == "100" and row["claimed"] is not None
    await world.adapter.response(event, LLMResponse(role="assistant", completion_text="完成"))
    again = Event(world, "定时检查")
    again.extra.update(cron_job=event.extra["cron_job"], cron_payload=event.extra["cron_payload"])
    await world.adapter.enrich(again, ProviderRequest(prompt=again.message_str))
    assert again.stopped and not world.app.inbox.repository.pending("timer")


async def test_quoted_correction_updates_only_original_speakers_fact(world):
    from astrbot.api.message_components import At, Reply
    first = Event(world, "我选杭州")
    await world.turn(first)
    correction = Event(world, "更正：我选南京")
    correction.message_obj.message.insert(0, Reply(
        id=first.message_obj.message_id, sender_id=first.get_sender_id(), message_str="我选杭州"))
    correction.message_obj.message.append(At(qq="999"))
    await world.turn(correction)
    users = [text(m) for m in world.requests[-1]["contexts"]
             if m["role"] == "user" and text(m).startswith("[发言人 ")]
    assert any("旧内容不再作为当前事实" in value for value in users)
    assert any("更正：我选南京" in value for value in users)
    assert not any("我选杭州" in value for value in users)
    outsider = Event(world, "更正：替他选上海", sender="200")
    outsider.message_obj.message.insert(0, Reply(
        id=first.message_obj.message_id, sender_id=first.get_sender_id(), message_str="我选杭州"))
    await world.turn(outsider)
    assert sum(r["kind"] == "corrected" for r in world.app.history.repository.events(first.unified_msg_origin)) == 1
