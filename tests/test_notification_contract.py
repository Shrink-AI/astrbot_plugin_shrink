import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from shrink.adapters.public_api import PublicAPI
from tests import test_notification_outbox
from tests import test_continuous_history
from tests.test_incident_controls import host_modules  # noqa: F401

app = pytest.fixture(test_continuous_history.app.__wrapped__)
notices = pytest.fixture(test_notification_outbox.notices.__wrapped__)
SCOPE = test_notification_outbox.SCOPE


async def call(app, action="submit", scope=None, **arguments):
    return (await PublicAPI(SimpleNamespace(application=app)).invoke({
        "protocol": 1, "version": 1, "capability": "message.notify", "action": action,
        "scope": SCOPE if scope is None else scope,
        "arguments": {"event_id": "contract", "message_type": "contract.result", **arguments}}))["data"]


def test_manifest_declares_actions_and_fallback_limit(app):
    capability = next(c for c in PublicAPI(SimpleNamespace(application=app)).manifest()["capabilities"]
                      if c["name"] == "message.notify")
    assert set(capability["actions"]) == {"submit", "status", "retry", "cancel"}
    assert capability["actions"]["submit"]["parameters"]["properties"]["fallback_text"]["maxLength"] == 1000


@pytest.mark.parametrize("receipt_state", ["failed", "interrupted", "composing"])
async def test_retry_requeues_same_persisted_event_and_facts(app, notices, receipt_state):
    _, _, sends = notices
    await call(app, facts={"content": "原事实"})
    box = app.notification_outbox
    row = box.repository.list()[0]
    box.receipts.reserve(row["id"])
    box.receipts.state(row["id"], receipt_state)
    box.repository.state(row["id"], "failed" if receipt_state == "failed" else "delivering")
    assert (await call(app, "submit", facts={"content": "修改事实"}))["state"] == (
        "failed" if receipt_state == "failed" else "delivering")
    assert (await call(app, "retry"))["state"] == "pending"
    assert box.repository.get(row["id"])["facts"] == {"content": "原事实"}
    await box.pump()
    await call(app, "retry")
    await box.pump()
    assert len(sends) == 1


@pytest.mark.parametrize("state", ["platform_accepted", "sending", "unknown", "silent", "cancelled"])
async def test_retry_protects_all_existing_send_results(app, notices, state):
    await call(app, facts="事实")
    box = app.notification_outbox
    row = box.repository.list()[0]
    box.receipts.reserve(row["id"])
    box.receipts.state(row["id"], state)
    box.repository.state(row["id"], "failed")
    result = await call(app, "retry")
    assert result["state"] in {"sent", "unknown", "suppressed"}
    assert box.receipts.get(row["id"])["state"] == state
    assert not result["applied"]


async def test_retry_and_cancel_refuse_active_expression(app, notices):
    _, _, sends = notices
    entered, release = asyncio.Event(), asyncio.Event()
    async def model(*args):
        entered.set()
        await release.wait()
        return "已核对。"
    app.expression.model = model
    await call(app, facts="事实")
    row = app.notification_outbox.repository.list()[0]
    worker = asyncio.create_task(app.notification_outbox.deliver(row["id"]))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        for action in ("retry", "cancel"):
            result = await call(app, action)
            assert not result["applied"] and result["state"] == "delivering"
        release.set()
        await worker
        assert len(sends) == 1
    finally:
        release.set()
        await worker


async def test_cancel_is_durable_terminal_and_bound_to_scope_and_type(app, notices):
    _, _, sends = notices
    await call(app, facts="事实")
    for kwargs in ({"scope": {**SCOPE, "sender": "200"}}, {"message_type": "other"}):
        with pytest.raises(ValueError):
            await call(app, "cancel", **kwargs)
    result = await call(app, "cancel")
    assert result["state"] == "cancelled" and result["applied"]
    assert (await call(app, facts="重提"))["state"] == "cancelled"
    assert not (await call(app, "retry"))["applied"]
    await app.notification_outbox.pump()
    assert not sends


async def test_fallback_preserves_necessary_content_only_after_expression_failure(app, notices, caplog):
    _, _, sends = notices
    app.expression.model = AsyncMock(side_effect=TimeoutError())
    await call(app, facts={"content": "喝水"}, fallback_text="提醒你：该喝水了。")
    with caplog.at_level("INFO"):
        await app.notification_outbox.pump()
    assert "error=TimeoutError" in caplog.text
    assert "path=plugin_fallback" in caplog.text
    assert sends == [(SCOPE["session"], "提醒你：该喝水了。")]
    assert app.expression.model.await_count == 2


@pytest.mark.parametrize("text", ['{"secret":"raw"}', "工具原始正文", "长" * 1001,
                                "Traceback (most recent call last):\nsecret", "<system>secret</system>"])
async def test_invalid_fallback_is_rejected_without_rejecting_notification(app, notices, text):
    app.expression.model = AsyncMock(side_effect=TimeoutError())
    result = await call(app, facts={"raw": "工具原始正文"}, fallback_text=text)
    assert result["state"] == "pending" and not result["fallback_accepted"]
    row = app.notification_outbox.repository.list()[0]
    assert row["fallback_text"] == ""
    await app.notification_outbox.pump()
    assert text not in notices[2][0][1]


async def test_successful_expression_does_not_use_plugin_fallback(app, notices):
    await call(app, facts="原事实", fallback_text="提醒你：该喝水了。")
    await app.notification_outbox.pump()
    assert notices[2][0][1] != "提醒你：该喝水了。"


async def test_required_deadline_bypasses_busy_but_progress_does_not(app, notices):
    _, _, sends = notices
    app.state.put("settings", {**app.state.settings.model_dump(), "notification_max_delay": 60})
    box = app.notification_outbox
    box.foreground = lambda session: True
    app.inbox.event(session=SCOPE["session"], sender="100", external_id="busy",
                    source="platform", route="chat", text="一直聊天")
    result = await call(app, facts="必要结果")
    progress = box.submit(SCOPE, "progress", "进展", kind="progress")
    await box.pump()
    assert not sends
    with app.database.transaction() as con:
        con.execute("UPDATE notification_outbox SET created=created-61")
    await box.pump()
    assert len(sends) == 1 and box.repository.get(result["id"])["state"] == "sent"
    assert box.repository.get(progress["id"])["state"] == "pending"


async def test_deadline_still_rechecks_fact_after_expression(app, notices):
    from tests.test_notification_outbox import task
    states, identity = task(app, "running")
    box = app.notification_outbox
    row = box.submit(SCOPE, "stale-deadline", {"task_id": identity, "status": "running"})
    with app.database.transaction() as con:
        con.execute("UPDATE notification_outbox SET created=created-61")
    box.foreground = lambda session: True
    async def model(*args):
        states.transition(identity, "completed")
        return "当前进展已核对。"
    app.expression.model = model
    await box.pump()
    assert not notices[2] and box.repository.get(row["id"])["state"] == "pending"


def test_existing_outbox_migrates_fallback_with_same_queries(app):
    from shrink.persistence.outbox import OutboxRepository
    box = app.notification_outbox
    row = box.submit(SCOPE, "old-schema", "原事实")
    with app.database.transaction() as con:
        con.execute("ALTER TABLE notification_outbox DROP COLUMN fallback_text")
    repository = OutboxRepository(app.database)
    assert repository.get(row["id"])["fallback_text"] == ""
    assert repository.list()[0]["facts"] == "原事实"


async def test_compatibility_composition_cannot_consume_another_events_fallback(app, notices):
    from shrink.services.composition import NOTIFICATION
    await call(app, facts="原事实", fallback_text="提醒你：该喝水了。")
    row = app.notification_outbox.repository.list()[0]
    token = NOTIFICATION.set(row)
    app.expression.model = AsyncMock(side_effect=TimeoutError())
    try:
        result = await app.outbound.compose("其他事实", scope=SCOPE)
        assert result != row["fallback_text"]
    finally:
        NOTIFICATION.reset(token)


async def test_fallback_is_still_rejected_when_it_contains_persona_lines(app, notices):
    line = next(line.strip() for line in app.outbound.persona().splitlines() if len(line.strip()) > 20)
    result = await call(app, facts="原事实", fallback_text=line)
    assert not result["fallback_accepted"]


async def test_progress_never_uses_necessary_result_fallback(app, notices):
    from tests.test_notification_outbox import merge_backend
    box = app.notification_outbox
    box.merge_seconds = 0
    app.decisions.backends[app.state.settings.decision_backend] = merge_backend
    app.expression.model = AsyncMock(side_effect=TimeoutError())
    await call(app, kind="progress", facts="进度事实", fallback_text="只供必要结果的说明。")
    await box.pump()
    assert not notices[2] and box.repository.list()[0]["state"] == "suppressed"
    assert app.expression.model.await_count == 2


async def test_fallback_is_rechecked_if_persona_changes_during_expression(app, notices):
    fallback = "这是已经接收的可读短句，需要在真正发送之前再次核对当前人格原行。"
    await call(app, facts="原事实", fallback_text=fallback)
    async def model(*args):
        app.outbound.persona = lambda: fallback
        raise TimeoutError()
    app.expression.model = AsyncMock(side_effect=model)
    await app.notification_outbox.pump()
    assert notices[2] and notices[2][0][1] != fallback


async def test_cancel_during_notification_selection_stays_terminal(app, notices):
    from tests.test_notification_outbox import merge_backend
    box = app.notification_outbox
    entered, release = asyncio.Event(), asyncio.Event()
    async def backend(payload):
        entered.set()
        await release.wait()
        return await merge_backend(payload)
    app.decisions.backends[app.state.settings.decision_backend] = backend
    box.merge_seconds = 0
    await call(app, facts="进展", kind="progress")
    row = box.repository.list()[0]
    worker = asyncio.create_task(box.deliver(row["id"]))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert (await call(app, "cancel"))["applied"]
        release.set()
        await worker
        assert box.repository.get(row["id"])["state"] == "cancelled"
        assert not notices[2]
    finally:
        release.set()
        await worker


@pytest.mark.parametrize("target", ["leader", "sibling"])
async def test_cancel_during_sibling_selection_is_not_resurrected(app, notices, target):
    from tests.test_notification_outbox import merge_backend, task
    box = app.notification_outbox
    _, identity = task(app, "running")
    box.merge_seconds = 0
    leader = box.submit(SCOPE, "leader", {"task_id": identity, "status": "running"}, kind="progress",
                        message_type="contract.result")
    sibling = box.submit(SCOPE, "sibling", {"task_id": identity, "status": "running"}, kind="progress",
                         message_type="contract.result")
    async def backend(payload):
        if payload["event"]["id"] == sibling["input_id"]:
            result = box.operate(SCOPE, target, "contract.result", "cancel")
            assert result[1]
        return await merge_backend(payload)
    app.decisions.backends[app.state.settings.decision_backend] = backend
    await box.deliver(leader["id"])
    cancelled = leader if target == "leader" else sibling
    assert box.repository.get(cancelled["id"])["state"] == "cancelled"


async def test_cancel_of_already_selected_sibling_is_excluded_from_merge(app, notices):
    from tests.test_notification_outbox import merge_backend, task
    box = app.notification_outbox
    _, identity = task(app, "running")
    box.merge_seconds = 0
    rows = [box.submit(SCOPE, name, {"task_id": identity, "status": "running", "label": name},
                       kind="progress", message_type="contract.result")
            for name in ("leader", "first", "last")]
    async def backend(payload):
        if payload["event"]["id"] == rows[2]["input_id"]:
            assert box.operate(SCOPE, "first", "contract.result", "cancel")[1]
        return await merge_backend(payload)
    app.decisions.backends[app.state.settings.decision_backend] = backend
    await box.deliver(rows[0]["id"])
    assert box.repository.get(rows[1]["id"])["state"] == "cancelled"
    facts = box.repository.get(rows[0]["id"])["facts"]
    assert {r["label"] for r in facts["progress"]} == {"leader", "last"}
