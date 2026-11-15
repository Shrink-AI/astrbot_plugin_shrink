from unittest.mock import AsyncMock

import pytest

from shrink.services.decisions import DecisionContext, guidance
from tests import test_continuous_history
from tests.test_continuous_history import receive

app = test_continuous_history.app


def disable(app):
    app.state.put("settings", {**app.state.settings.model_dump(), "decision_enabled": False})
    model = AsyncMock(side_effect=AssertionError("disabled classifier called"))
    app.decisions.backends["haiku"] = model
    return model


@pytest.mark.parametrize("text,context", [
    ("今天有点累", DecisionContext(private=True, participation="direct")),
    ("帮我处理这个", DecisionContext(direct=True, participation="direct", admin=True)),
    ("今天有点累", DecisionContext(participation="engaged")),
    ("今天有点累", DecisionContext(participation="spontaneous")),
    ("你好", DecisionContext(participation="engaged")),
])
async def test_disabled_classifier_defers_to_main_without_extra_call(app, text, context, caplog):
    model = disable(app)
    event, _ = receive(app, text=text)
    with caplog.at_level("INFO"):
        result = await app.decisions.decide(event["id"], context)
    assert result["backend"] == "main_llm"
    assert result["reason"] == "decision_disabled"
    assert result["decision"]["route"] == "reply"
    assert not result["fallback"] and not result["rule_direct"]
    assert "自行判断" in guidance(result)
    model.assert_not_awaited()
    assert f"input={event['id']}" in caplog.text and "backend=main_llm" in caplog.text
    assert text not in caplog.text


@pytest.mark.parametrize("reason", [
    "quiet", "group_blacklisted", "bot_quiet", "addressed_other", "other_conversation_followup",
])
async def test_disabled_classifier_preserves_participation_boundaries(app, reason):
    model = disable(app)
    event, _ = receive(app, text="今天有点累")
    result = await app.decisions.decide(event["id"], DecisionContext(participation=reason))
    assert result["decision"]["route"] == "silent"
    model.assert_not_awaited()


async def test_disabled_classifier_preserves_stop_permissions(app):
    model = disable(app)
    event, _ = receive(app, text="停止")
    result = await app.decisions.decide(event["id"], DecisionContext(direct=True))
    assert result["reason"] == "stop_permission"
    assert result["decision"]["route"] == "ask"
    model.assert_not_awaited()


async def test_disabled_classifier_does_not_run_for_progress_notifications(app):
    from shrink.services.notification_selection import select
    model = disable(app)
    event, _ = receive(app, text="处理进展")
    result = await select(app.decisions, {
        "kind": "progress", "input_id": event["id"], "session": event["session"],
        "sender": event["sender"], "task_id": "", "facts": {}, "fact_refs": [],
    })
    assert result["reason"] == "decision_disabled"
    assert result["decision"]["notify"] == "silent"
    model.assert_not_awaited()
