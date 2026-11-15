"""Load the real sibling reminder; the bus, outbox and databases are real."""
import asyncio
import importlib.util
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from shrink.adapters.notifications import Notifications, notification_event
from shrink.adapters.public_api import PublicAPI
from shrink.application import Application
from tests import test_incident_controls

host_modules = pytest.fixture(test_incident_controls.host_modules.__wrapped__)

PRIVATE = "bot:FriendMessage:100"
GROUP = "bot:GroupMessage:88"


@pytest.fixture
def reminder_module(monkeypatch, host_modules):
    root = Path(os.environ.get("SHRINK_REMINDER_PLUGIN_ROOT", str(
        Path(__file__).resolve().parents[2] / "astrbot_plugin_shirnk_ext_reminder")))
    if not all((root / name).is_file() for name in ("main.py", "delivery.py", "storage.py")):
        pytest.skip("Real reminder plugin unavailable; set SHRINK_REMINDER_PLUGIN_ROOT")
    monkeypatch.setitem(sys.modules, "astrbot.api.event", SimpleNamespace(
        AstrMessageEvent=object, filter=SimpleNamespace(llm_tool=lambda **kw: lambda f: f)))
    monkeypatch.setitem(sys.modules, "astrbot.api.star", SimpleNamespace(
        Context=object, Star=object, register=lambda *a: lambda c: c))
    name = "real_reminder_contract_plugin"
    package = ModuleType(name)
    package.__path__ = [str(root)]
    monkeypatch.setitem(sys.modules, name, package)
    spec = importlib.util.spec_from_file_location(name + ".main", root / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module
    for key in list(sys.modules):
        if key.startswith(name + "."):
            sys.modules.pop(key, None)


class Cron:
    def __init__(self):
        self.jobs = {}

    async def add_active_job(self, **args):
        identity = str(len(self.jobs) + 1)
        job = SimpleNamespace(job_id=identity, **args)
        self.jobs[identity] = job
        return job

    async def list_jobs(self):
        return list(self.jobs.values())

    async def update_job(self, identity, **args):
        self.jobs[identity].__dict__.update(args)

    async def delete_job(self, identity):
        self.jobs.pop(identity, None)


@pytest_asyncio.fixture
async def rig(reminder_module, tmp_path, monkeypatch):
    import importlib
    main = reminder_module
    delivery = importlib.import_module(main.__package__ + ".delivery")
    storage = importlib.import_module(main.__package__ + ".storage")
    client_class = delivery.CapabilityClient
    plugin = object.__new__(main.ReminderExtension)
    plugin._create_lock = asyncio.Lock()
    sent, attempts = [], []
    async def send(session, chain):
        payload = (session, "".join(p.text for p in chain.chain))
        attempts.append(payload)
        if state.unknown:
            raise TimeoutError("platform result unknown")
        sent.append(payload)
        return True
    context = SimpleNamespace(cron_manager=Cron(), send_message=send)
    plugin.context = context
    state = SimpleNamespace(app=None, database=None, plugin=plugin, sent=sent, attempts=attempts,
                            unknown=False, bus=None)
    def reopen():
        if state.app:
            state.app.scheduling.close()
            state.app.database.close()
            state.database.close()
        state.app = Application(tmp_path / "core", AsyncMock(return_value="该喝水了。"), AsyncMock(), AsyncMock())
        state.app.notification_event = lambda session, sender: notification_event(context, session, sender)
        state.app.outbound.resolve_scope = lambda scope: {
            "source": "qq:private:100" if scope["session"] == PRIVATE else "qq:group:88", "user_id": "qq:100"}
        Notifications(state.app)
        api = PublicAPI(SimpleNamespace(application=state.app))
        bus = client_class(lambda: [SimpleNamespace(activated=True, name="core",
            star_cls=SimpleNamespace(plugin_capabilities=api.manifest, invoke_capability=api.invoke))])
        state.bus = bus
        state.database = storage.Database(tmp_path / "reminder")
        plugin._service = delivery.ReminderDelivery(context, state.database, plugin._compose, plugin._record_sent)
        plugin._service.client = lambda _: pytest.fail("core-owned reminder used legacy sender")
    monkeypatch.setattr(delivery, "CapabilityClient", lambda: state.bus)
    state.reopen = reopen
    reopen()
    yield state
    state.app.scheduling.close()
    state.app.database.close()
    state.database.close()


async def due(rig, session=PRIVATE, content="喝水"):
    result = await rig.plugin.invoke_capability({
        "protocol": 1, "version": 1, "capability": "reminders.manage", "action": "create",
        "scope": {"admin": True, "session": session, "sender": "100"},
        "arguments": {"content": content, "time_str": datetime.fromtimestamp(
            time.time() + 3600, timezone.utc).isoformat()}})
    identity = result["data"]["job_id"]
    with rig.database.transaction() as con:
        con.execute("UPDATE reminder_deliveries SET due=?,next_attempt=0 WHERE id=?",
                    (time.time() - 1, identity))
    await rig.plugin._service.deliver(identity)
    return identity


async def poll(rig, identity):
    with rig.database.transaction() as con:
        con.execute("UPDATE reminder_deliveries SET next_attempt=0 WHERE id=?", (identity,))
    return await rig.plugin._service.deliver(identity)


@pytest.mark.parametrize("session", [PRIVATE, GROUP])
async def test_due_real_reminder_is_sent_once_in_original_session(rig, session):
    identity = await due(rig, session)
    await rig.app.notification_outbox.pump()
    await poll(rig, identity)
    await rig.app.notification_outbox.pump()
    await poll(rig, identity)
    assert rig.sent == [(session, "该喝水了。")]
    assert rig.plugin._service.get(identity)["status"] == "sent"
    assert len(rig.app.database.rows("SELECT * FROM notification_receipts")) == 1


async def test_continuous_foreground_has_bounded_delay(rig):
    box = rig.app.notification_outbox
    box.foreground = lambda session: True
    identity = await due(rig, GROUP)
    rig.app.inbox.event(session=GROUP, sender="200", external_id="chat",
                        source="platform", route="chat", text="一直在聊")
    await box.pump()
    assert not rig.sent
    with rig.app.database.transaction() as con:
        con.execute("UPDATE notification_outbox SET created=created-61")
    await box.pump()
    assert (await poll(rig, identity))["status"] == "sent"
    assert rig.sent[0][0] == GROUP


async def test_failed_expression_uses_persisted_reminder_content(rig):
    rig.app.expression.model.side_effect = TimeoutError("PRIVATE exception")
    identity = await due(rig)
    await rig.app.notification_outbox.pump()
    assert rig.app.expression.model.await_count == 2
    assert rig.sent == [(PRIVATE, "提醒你：喝水。")]
    assert (await poll(rig, identity))["status"] == "sent"


@pytest.mark.parametrize("expression", ["failed", "verbatim"])
async def test_long_reminder_keeps_complete_content_once(rig, expression):
    content = "核对明天需要携带的资料和物品，" * 12
    assert len(content) > 80
    if expression == "failed":
        rig.app.expression.model.side_effect = TimeoutError("expression unavailable")
    else:
        rig.app.expression.model.return_value = "提醒你：" + content
    identity = await due(rig, content=content)
    await rig.app.notification_outbox.pump()
    await poll(rig, identity)
    await rig.app.notification_outbox.pump()
    assert len(rig.sent) == len(rig.attempts) == 1
    assert content in rig.sent[0][1]
    assert (await poll(rig, identity))["status"] == "sent"


async def test_confirmed_unsent_failure_retries_same_event_without_duplicate(rig):
    identity = await due(rig)
    compose = rig.app.outbound.compose
    rig.app.outbound.compose = AsyncMock(side_effect=ConnectionError("before send"))
    await rig.app.notification_outbox.pump()
    assert (await poll(rig, identity))["notification_state"] == "failed"
    rig.app.outbound.compose = compose
    assert (await poll(rig, identity))["notification_state"] == "pending"
    await rig.app.notification_outbox.pump()
    await poll(rig, identity)
    await rig.app.notification_outbox.pump()
    assert len(rig.sent) == len(rig.attempts) == 1
    assert len(rig.app.notification_outbox.repository.list()) == 1


async def test_cancel_withdraws_queued_notification(rig):
    identity = await due(rig)
    result = await rig.plugin._service.cancel(identity, PRIVATE, "100")
    await rig.app.notification_outbox.pump()
    assert result["status"] == "cancelled" and not rig.sent
    assert rig.app.notification_outbox.repository.list()[0]["state"] == "cancelled"
    rig.reopen()
    await rig.app.notification_outbox.pump()
    assert (await poll(rig, identity))["status"] == "cancelled" and not rig.sent


async def test_unknown_platform_result_is_never_retried_after_both_databases_reopen(rig):
    rig.unknown = True
    identity = await due(rig)
    await rig.app.notification_outbox.pump()
    assert (await poll(rig, identity))["status"] == "unknown"
    rig.reopen()
    rig.unknown = False
    await poll(rig, identity)
    await rig.app.notification_outbox.pump()
    assert len(rig.attempts) == 1 and not rig.sent
    assert rig.app.notification_outbox.repository.list()[0]["state"] == "unknown"


async def test_restart_recovers_pending_and_preserves_accepted_receipts(rig):
    identity = await due(rig)
    rig.reopen()
    rig.app.expression.model.side_effect = TimeoutError()
    await rig.app.notification_outbox.pump()
    assert (await poll(rig, identity))["status"] == "sent"
    assert rig.sent == [(PRIVATE, "提醒你：喝水。")]
    rig.reopen()
    await rig.app.notification_outbox.pump()
    await poll(rig, identity)
    assert len(rig.sent) == 1
