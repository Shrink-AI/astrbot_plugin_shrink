"""Excluded command bodies and duplicate platform propagation."""
from types import SimpleNamespace

import pytest

pytest.importorskip("astrbot.core.agent.runners.tool_loop_agent_runner")

from tests.host.test_real_turns import Event, world  # noqa: E402,F401
from shrink.persistence.inbox import input_id  # noqa: E402


@pytest.mark.parametrize("prefix", ["/", "!", "！", "other_handler "])
async def test_command_inbox_keeps_identity_without_body_or_retrievable_message(world, prefix):
    secret = "private-password-98765"
    event = Event(world, prefix + "login " + secret)
    if prefix == "other_handler ":
        event.extra["activated_handlers"] = [SimpleNamespace(
            event_filters=[type("CommandFilter", (), {})()], handler_module_path="another.plugin")]
    await world.adapter.receive(event)
    assert world.app.database.rows("SELECT * FROM messages") == []
    assert world.app.history.repository.events(event.unified_msg_origin) == []
    identity = input_id(event.unified_msg_origin, event.sender, event.message_obj.message_id)
    row = world.app.inbox.repository.get(identity)
    assert row and row["processed"] is not None and not row["message_id"]
    assert secret not in str(row)
    assert not event.stopped and not world.calls
    await world.adapter.receive(event)
    assert len(world.app.database.rows("SELECT * FROM input_events")) == 1
    assert not event.stopped


async def test_duplicate_delivery_disables_default_llm_without_stopping_other_plugins(world):
    original = Event(world, "你好")
    await world.turn(original)
    duplicate = Event(world, "你好")
    duplicate.message_obj.message_id = original.message_obj.message_id
    await world.adapter.receive(duplicate)
    assert not duplicate.stopped
    assert duplicate.call_llm is True
    assert not duplicate.get_extra("shrink_reply")
    assert len(world.calls) == 1
