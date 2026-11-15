import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from shrink.adapters.notifications import Notifications
from shrink.adapters.public_api import PublicAPI
from shrink.persistence.tasks import Tasks
from shrink.services.notification_outbox import NotificationOutbox, task_fact
from shrink.services.task_states import TaskStates
from tests.test_continuous_history import app  # noqa: F401
from tests.test_incident_controls import host_modules  # noqa: F401


SCOPE = {"session": "bot:FriendMessage:100", "sender": "100"}


@pytest.fixture
def notices(app, host_modules):
    sends = []
    def event(session=SCOPE["session"], sender="100"):
        async def send(chain):
            sends.append((session, "".join(item.text for item in chain.chain)))
            return True
        return SimpleNamespace(unified_msg_origin=session, get_sender_id=lambda: sender,
                               get_extra=lambda key: None, send=AsyncMock(side_effect=send))
    app.notification_event = event
    app.outbound.resolve_scope = lambda scope: {"source": "qq:private:" + scope["sender"],
                                               "user_id": "qq:" + scope["sender"]}
    app.expression.model = AsyncMock(return_value="执行记录已核对，任务完成了。")
    return Notifications(app), event, sends


def task(app, status="completed", *, parent="", dependencies=()):
    states = TaskStates(Tasks(app.database))
    states.migrate()
    identity = states.repository.create("检查", SCOPE["session"], "100", "provider",
                                        parent=parent, dependencies=dependencies)
    states.start(identity)
    if status != "running":
        states.transition(identity, status)
    return states, identity


async def test_n01_three_channels_and_foreground_share_persisted_fact(app, notices):
    _, _, sends = notices
    states, identity = task(app)
    box = app.notification_outbox
    facts = {"task_id": identity, "status": "completed"}
    rows = [box.submit(SCOPE, key, facts, kind="task_result") for key in ("task-result", "parent-summary", "device-watch")]
    await box.pump()
    assert len(sends) == 1
    assert sorted(box.repository.get(r["id"])["state"] for r in rows) == ["sent", "suppressed", "suppressed"]
    assert len(app.database.rows("SELECT * FROM notification_receipts")) == 1
    assert box.repository.get(rows[0]["id"])["fact_refs"] == [task_fact(states.task(identity))]


async def test_n01_foreground_acceptance_suppresses_waiting_background(app, notices):
    _, _, sends = notices
    _, identity = task(app)
    box = app.notification_outbox
    row = box.submit(SCOPE, "background", {"task_id": identity, "status": "completed"}, kind="task_result")
    app.inbox.event(session=SCOPE["session"], sender="100", external_id="user", source="platform",
                    route="chat", text="结果呢")
    await box.pump()
    assert not sends
    box.acknowledge(SCOPE, identity, "accepted-foreground")
    app.inbox.repository.complete([i["id"] for i in app.inbox.repository.pending("chat")])
    await box.pump()
    assert not sends and box.repository.get(row["id"])["state"] == "suppressed"


@pytest.mark.parametrize("kind", ["tool_start", "tool_end", "file_read", "log", "auto_approval",
                                  "recoverable_error", "still_running", "internal_handoff"])
async def test_n03_quiet_events_are_audited_without_claiming_result(app, notices, kind):
    _, _, sends = notices
    box = app.notification_outbox
    row = box.submit(SCOPE, kind, {"fact": "过程"}, kind=kind, fact_refs=["shared"])
    await box.pump()
    assert box.repository.get(row["id"])["state"] == "suppressed"
    assert box.repository.get(row["id"])["decision"]["decision"]["notify"] == "silent"
    assert not sends and not app.database.rows("SELECT * FROM notification_fact_claims")
    assert app.inbox.repository.get(row["input_id"])["text"]
    box.submit(SCOPE, "important", {"status": "completed"}, fact_refs=["shared"])
    await box.pump()
    assert len(sends) == 1


async def test_n04_original_private_destination_survives_group_activity(app, notices):
    _, _, sends = notices
    _, identity = task(app)
    box = app.notification_outbox
    row = box.submit({**SCOPE, "session": "bot:GroupMessage:99"}, "watch",
                     {"task_id": identity, "status": "completed"}, kind="task_result")
    assert row["session"] == SCOPE["session"]
    await box.pump()
    assert sends[0][0] == SCOPE["session"]
    assert len(app.history.repository.events(SCOPE["session"])) == 1
    assert not app.history.repository.events("bot:GroupMessage:99")
    with pytest.raises(ValueError, match="创建者"):
        box.submit({**SCOPE, "sender": "200"}, "foreign", {"task_id": identity, "status": "completed"})


async def test_n05_unknown_receipt_never_replays_or_records_history(app, notices):
    notifications, event, sends = notices
    instance = event()
    instance.send.side_effect = TimeoutError()
    box = app.notification_outbox
    await notifications.send_once(instance, "unknown", {"status": "completed"}, kind="result")
    row = box.repository.list()[0]
    assert row["state"] == "unknown"
    assert not app.history.repository.events(SCOPE["session"])
    restored = NotificationOutbox(box.repository, box.receipts, box.inbox, box.decisions, box.error)
    restored.transport = box.transport
    await restored.pump()
    await restored.deliver(row["id"], event=instance, retry=True)
    instance.send.assert_awaited_once()
    assert not sends


async def test_accepted_receipt_survives_restart_even_before_queue_projection(app, notices):
    notifications, event, sends = notices
    box = app.notification_outbox
    await notifications.send_once(event(), "accepted", {"status": "completed"}, kind="result")
    row = box.repository.list()[0]
    box.repository.state(row["id"], "delivering")
    await box.pump()
    assert len(sends) == 1
    assert box.repository.get(row["id"])["state"] == "sent"
    assert len(app.history.repository.events(SCOPE["session"])) == 1


@pytest.mark.parametrize("receipt_state", ["failed", "interrupted", "composing"])
async def test_explicit_retry_still_reaches_real_receipt_table(app, notices, receipt_state):
    notifications, event, sends = notices
    box = app.notification_outbox
    row = box.submit(SCOPE, "retry", {"status": "completed"})
    box.receipts.reserve(row["id"])
    box.receipts.state(row["id"], receipt_state)
    box.repository.state(row["id"], "failed")
    assert await notifications.send_once(event(), "retry", {"status": "completed"}, kind="result", retry=True)
    assert len(sends) == 1 and box.receipts.get(row["id"])["state"] == "platform_accepted"


async def merge_backend(payload):
    return {"intent": "event", "route": "reply", "conversation": payload["event"]["session"],
            "topic": None, "task": None, "evidence": [payload["event"]["id"]],
            "capability_candidates": [], "notify": "merge", "confidence": .5}


async def test_progress_merge_defers_to_new_input_and_rechecks_after_expression(app, notices):
    _, _, sends = notices
    box = app.notification_outbox
    box.merge_seconds = 0
    app.decisions.backends[app.state.settings.decision_backend] = merge_backend
    _, identity = task(app, "running")
    first = box.submit(SCOPE, "p1", {"task_id": identity, "status": "running", "progress": 1}, kind="progress")
    second = box.submit(SCOPE, "p2", {"task_id": identity, "status": "running", "progress": 2}, kind="progress")
    async def expression(*args):
        app.inbox.event(session=SCOPE["session"], sender="100", external_id="new", source="platform",
                        route="chat", text="新要求")
        return "这部分已核对。"
    app.expression.model.side_effect = expression
    await box.pump()
    assert not sends
    assert box.repository.get(first["id"])["state"] == "pending"
    assert box.repository.get(second["id"])["merged_into"] == first["id"]
    assert box.receipts.get(first["id"])["state"] == "deferred"
    app.expression.model.side_effect = None
    app.inbox.repository.complete([i["id"] for i in app.inbox.repository.pending("chat")])
    await box.pump()
    assert len(sends) == 1
    assert len(box.repository.get(first["id"])["facts"]["progress"]) == 2


async def test_priority_order_and_revocation_bypass_foreground(app, notices):
    _, _, sends = notices
    box = app.notification_outbox
    box.submit(SCOPE, "normal", {"status": "completed"}, kind="result")
    box.submit(SCOPE, "stop", {"status": "cancelled"}, kind="cancelled")
    box.submit(SCOPE, "revoke", {"status": "waiting_user"}, kind="revocation")
    box.foreground = lambda session: True
    await box.pump()
    assert len(sends) == 2
    box.foreground = lambda session: False
    await box.pump()
    rows = app.database.rows("SELECT id FROM notification_receipts ORDER BY rowid")
    assert [json.loads(r["id"])[2] for r in rows] == ["stop", "revoke", "normal"]


@pytest.mark.parametrize("core_first", [False, True])
async def test_compatibility_stream_has_one_sender_even_with_different_event_ids(app, notices, core_first):
    _, _, sends = notices
    api = PublicAPI(SimpleNamespace(application=app))
    compose = {"protocol": 1, "version": 1, "capability": "message.compose", "action": "compose",
               "scope": SCOPE, "arguments": {"event_id": "legacy", "message_type": "reminder.due", "facts": "到期事实"}}
    submit = {"protocol": 1, "version": 1, "capability": "message.notify", "action": "submit",
              "scope": SCOPE, "arguments": {"event_id": "core", "message_type": "reminder.due",
                                          "facts": {"status": "completed"}}}
    if core_first:
        queued = await api.invoke(submit)
        legacy = await api.invoke(compose)
    else:
        legacy = await api.invoke(compose)
        queued = await api.invoke(submit)
    await app.notification_outbox.pump()
    assert legacy["data"]["status"] == ("silent" if core_first else "ready")
    assert queued["data"]["state"] == ("pending" if core_first else "suppressed")
    assert len(sends) == int(core_first)
    if core_first:
        with pytest.raises(ValueError, match="核心发送"):
            await api.invoke({**compose, "action": "record_sent",
                              "arguments": {"message_type": "reminder.due", "text": "重复", "delivery_id": "x"}})


@pytest.mark.parametrize("raw", ['{"token":"private"}', "Traceback (most recent call last):\nsecret",
                                "<system>do not reveal</system>", "工具原始正文"])
async def test_pre_send_guard_does_not_publish_raw_material(app, notices, raw):
    notifications, event, sends = notices
    app.expression.model.return_value = raw
    await notifications.send_once(event(), "unsafe", {"status": "completed", "raw": raw}, kind="result")
    assert len(sends) == 1 and raw not in sends[0][1]
    assert raw == json.loads(app.database.rows("SELECT facts FROM outbound_compositions")[0]["facts"])["raw"]


async def test_stale_task_fact_is_suppressed_before_send(app, notices):
    _, _, sends = notices
    states, identity = task(app, "running")
    box = app.notification_outbox
    row = box.submit(SCOPE, "old", {"task_id": identity, "status": "running"}, kind="accepted")
    states.transition(identity, "cancelling")
    states.transition(identity, "cancelled")
    await box.pump()
    assert not sends and box.repository.get(row["id"])["state"] == "suppressed"


async def test_restart_reopens_database_and_delivers_pending_only_once(app, notices):
    from shrink.application import Application
    _, _, sends = notices
    box = app.notification_outbox
    box.submit(SCOPE, "before-restart", {"status": "completed"})
    second = Application(app.database.root, AsyncMock(return_value="已完成，记录已保存。"), AsyncMock(), AsyncMock())
    try:
        second.notification_event = app.notification_event
        second.outbound.resolve_scope = app.outbound.resolve_scope
        Notifications(second)
        await second.notification_outbox.pump()
        await box.pump()
        assert len(sends) == 1
        assert second.notification_outbox.repository.list()[0]["state"] == "sent"
        assert len(second.history.repository.events(SCOPE["session"])) == 1
    finally:
        second.database.close()


async def test_compatibility_event_identity_cannot_change_owner_by_changing_type(app, notices):
    _, _, sends = notices
    api = PublicAPI(SimpleNamespace(application=app))
    await api.invoke({"protocol": 1, "version": 1, "capability": "message.compose", "action": "compose",
                      "scope": SCOPE, "arguments": {"facts": "事实", "event_id": "same", "message_type": "legacy.type"}})
    row = app.notification_outbox.submit(SCOPE, "same", {"status": "completed"}, message_type="different.type")
    await app.notification_outbox.pump()
    assert row["state"] == "suppressed" and not sends


async def test_urgent_lane_runs_while_background_expression_waits(app, notices):
    _, _, sends = notices
    box = app.notification_outbox
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0
    async def expression(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()
        return "任务状态已核对。"
    app.expression.model.side_effect = expression
    box.submit(SCOPE, "normal", {"status": "completed"})
    worker = asyncio.create_task(box.run())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        box.submit(SCOPE, "stop-now", {"status": "cancelled"}, kind="cancelled")
        async def wait_sent():
            while not sends:
                await asyncio.sleep(.01)
        await asyncio.wait_for(wait_sent(), 2)
        receipt = app.database.rows("SELECT id FROM notification_receipts WHERE state='platform_accepted'")[0]
        assert json.loads(receipt["id"])[2] == "stop-now"
        release.set()
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


async def test_progress_receipt_does_not_mark_final_task_result_delivered(app, notices):
    _, _, sends = notices
    states, identity = task(app, "running")
    box = app.notification_outbox
    box.merge_seconds = 0
    app.decisions.backends[app.state.settings.decision_backend] = merge_backend
    box.submit(SCOPE, "progress", {"task_id": identity, "status": "running", "progress": 1}, kind="progress")
    await box.pump()
    assert states.task(identity)["delivery"] == ""
    states.transition(identity, "completed")
    box.submit(SCOPE, "final", {"task_id": identity, "status": "completed"}, kind="task_result")
    await box.pump()
    assert len(sends) == 2 and states.task(identity)["delivery"] == "platform_accepted"


async def test_authorization_questions_merge_and_stale_confirmations_are_not_asked(app, notices):
    import time
    from shrink.adapters.authorizations import AuthorizationAdapter
    _, _, sends = notices
    service = app.authorizations
    service.is_admin = lambda sender: sender == "100"
    adapter = AuthorizationAdapter.__new__(AuthorizationAdapter)
    adapter.app, adapter.service = app, service
    adapter.context = SimpleNamespace(send_message=AsyncMock())
    base = {"provider": "desktop", "device": "pc", "execution_session": "exec",
            "category": "write", "expires": time.time() + 60}
    scope = {**SCOPE, "admin": True}
    first = await adapter.receive({**base, "approval_id": "a"}, scope)
    second = await adapter.receive({**base, "approval_id": "b"}, scope)
    assert not sends
    service.repository.update(first["id"], "held", "revoked")
    await asyncio.sleep(.21)
    await app.notification_outbox.pump()
    assert len(sends) == 1
    sent = next(r for r in app.notification_outbox.repository.list() if r["state"] == "sent")
    assert [c["confirmation_id"] for c in sent["facts"]["confirmations"]] == [second["id"]]


@pytest.mark.parametrize("outcome", ["failed", "rolled_back", "unknown"])
async def test_deployment_outcome_and_business_state_remain_separate(app, notices, outcome):
    _, _, sends = notices
    states, identity = task(app, "needs_attention")
    row = app.notification_outbox.submit(SCOPE, "deploy", {
        "task_id": identity, "status": outcome, "id": "deployment1"}, kind="deployment")
    await app.notification_outbox.pump()
    assert len(sends) == 1
    assert row["facts"]["status"] == "needs_attention" and row["facts"]["deployment_status"] == outcome
    assert states.task(identity)["status"] == "needs_attention"


def test_notification_acceptance_is_atomic_with_input_and_owner(app, monkeypatch):
    def failed(_):
        raise RuntimeError("storage")
    monkeypatch.setattr(app.notification_outbox.repository, "insert", failed)
    with pytest.raises(RuntimeError):
        app.notification_outbox.submit(SCOPE, "atomic", {"status": "completed"})
    assert not app.inbox.repository.pending("notification")
    assert not app.database.rows("SELECT * FROM notification_send_owners")
    assert not app.database.rows("SELECT * FROM notification_event_owners")


async def test_foreground_sending_recovery_never_reexpresses_under_pending_user_input(app, notices):
    _, _, sends = notices
    states, identity = task(app)
    box = app.notification_outbox
    receipt = box.foreground_begin(SCOPE, identity, "reply", task_fact(states.task(identity)), "完成了")
    app.inbox.event(session=SCOPE["session"], sender="100", external_id="next", source="platform",
                    route="chat", text="再看一下")
    await box.pump()
    assert box.repository.get(receipt)["state"] == "unknown"
    assert states.task(identity)["delivery"] == "unknown" and not sends


async def test_expression_queue_rechecks_new_input_before_starting_model(app, notices):  # noqa: F811
    _, _, sends = notices
    box = app.notification_outbox
    entered, release = asyncio.Event(), asyncio.Event()
    async def expression(*args):
        entered.set()
        await release.wait()
        return "这部分核对过了"
    app.expression.model.side_effect = expression
    one = box.submit(SCOPE, "one", {"status": "completed"})
    two = box.submit(SCOPE, "two", {"status": "completed"})
    first = asyncio.create_task(box.deliver(one["id"]))
    second = None
    try:
        await entered.wait()
        second = asyncio.create_task(box.deliver(two["id"]))
        await asyncio.wait_for(box.scheduling.queued["expression"].wait(), 1)
        app.inbox.event(session=SCOPE["session"], sender="100", external_id="new-input",
                        source="platform", route="chat", text="等等")
        release.set()
        await asyncio.wait_for(asyncio.gather(first, second), 2)
        assert not sends and app.expression.model.await_count == 1
        assert box.repository.get(two["id"])["state"] == "pending"
    finally:
        release.set()
        await asyncio.gather(*(t for t in (first, second) if t is not None), return_exceptions=True)


@pytest.mark.parametrize("kind", ["cancelled", "revocation", "question", "blocker"])
async def test_all_urgent_categories_bypass_occupied_expression_quota(app, notices, kind):  # noqa: F811
    _, _, sends = notices
    box = app.notification_outbox
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0
    async def expression(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()
        return "当前状态核对过了"
    app.expression.model.side_effect = expression
    normal = box.submit(SCOPE, "normal-quota", {"status": "completed"})
    first = asyncio.create_task(box.deliver(normal["id"]))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        urgent = box.submit(SCOPE, "urgent-quota", {"status": "waiting_user"}, kind=kind)
        assert await asyncio.wait_for(box.deliver(urgent["id"]), 1)
        assert len(sends) == 1 and not first.done() and not release.is_set()
    finally:
        release.set()
        await first


async def test_foreground_classifier_does_not_wait_for_notification_value_model(app, notices):  # noqa: F811
    from shrink.services.decisions import DecisionContext
    box = app.notification_outbox
    box.merge_seconds = 0
    entered, release = asyncio.Event(), asyncio.Event()
    async def backend(payload):
        notification = payload["event"]["source"] == "notification"
        if notification:
            entered.set()
            await release.wait()
        return {"intent": "event" if notification else "query", "route": "silent" if notification else "reply",
                "conversation": payload["event"]["session"], "topic": None, "task": None,
                "evidence": [payload["event"]["id"]], "capability_candidates": [],
                "notify": "silent" if notification else "normal", "confidence": 1.0}
    app.decisions.backends[app.state.settings.decision_backend] = backend
    row = box.submit(SCOPE, "value-model", {"status": "running"}, kind="progress")
    worker = asyncio.create_task(box.deliver(row["id"]))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        incoming, _ = app.inbox.event(session=SCOPE["session"], sender="100", external_id="classify-chat",
                                      source="platform", route="chat", text="查一下当前天气")
        result = await asyncio.wait_for(
            app.decisions.decide(incoming["id"], DecisionContext(direct=True)), 1)
        assert not result["fallback"] and not worker.done() and not release.is_set()
    finally:
        release.set()
        await worker
