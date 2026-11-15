"""The real sibling plugin, using its aiohttp WebSocket transport and a simulated device."""
import asyncio
import importlib.util
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from shrink.adapters.authorizations import AuthorizationAdapter
from shrink.adapters.notifications import Notifications, notification_event
from shrink.adapters.public_api import PublicAPI
from tests.test_continuous_history import app  # noqa: F401
from tests.test_incident_controls import host_modules  # noqa: F401


SCOPE = {"session": "bot:FriendMessage:100", "sender": "100", "admin": True}


@pytest.fixture
def desktop_module(monkeypatch, host_modules):
    root = Path(os.environ.get("SHRINK_DESKTOP_PLUGIN_ROOT",
                               str(Path(__file__).resolve().parents[2] / "astrbot_plugin_shrink_desktop")))
    if not (root / "main.py").is_file() or not (root / "transport.py").is_file():
        pytest.skip("Real desktop plugin unavailable; set SHRINK_DESKTOP_PLUGIN_ROOT")
    monkeypatch.setitem(sys.modules, "astrbot.api.event", SimpleNamespace(AstrMessageEvent=object,
        filter=SimpleNamespace(llm_tool=lambda **kw: lambda f: f, on_llm_request=lambda: lambda f: f)))
    monkeypatch.setitem(sys.modules, "astrbot.api.star", SimpleNamespace(Context=object, Star=object,
                                                                       register=lambda *a: lambda c: c))
    name = "real_desktop_contract_plugin"
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


class Device:
    def __init__(self, client, credentials):
        self.client = client
        self.headers = {"Authorization": "Bearer " + credentials["token"], "X-Device-Id": credentials["device_id"]}
        self.seq = 0
        self.executions = {}
        self.wires = []
        self.drop_results = False
        self.acks = asyncio.Queue()
        self.ws = self.reader = None

    async def connect(self):
        self.ws = await self.client.ws_connect("/connect", headers=self.headers)
        await self.ws.receive_json()
        self.reader = asyncio.create_task(self.read())

    async def read(self):
        async for frame in self.ws:
            if frame.type.name != "TEXT":
                continue
            body = json.loads(frame.data)
            if body["type"] == "ack":
                await self.acks.put(body["seq"])
            elif body["type"] == "command":
                self.wires.append(body)
                assert "authorization" not in body["arguments"]
                # The Windows journal deduplicates the same command id.
                self.executions.setdefault(body["id"], {"ok": True})
                if not self.drop_results:
                    await self.ws.send_json({"type": "result", "id": body["id"], "result": self.executions[body["id"]]})

    async def publish(self, event):
        self.seq += 1
        await self.ws.send_json({"type": "event", "seq": self.seq, "event": event})
        assert await asyncio.wait_for(self.acks.get(), 2) == self.seq

    async def close(self):
        if self.ws:
            await self.ws.close()
        if self.reader:
            await self.reader


@pytest_asyncio.fixture
async def rig(app, desktop_module, tmp_path, monkeypatch):
    m = desktop_module
    plugin = object.__new__(m.ShrinkDesktop)
    plugin.transport = m.Transport(tmp_path / "desktop")
    sent = []
    async def send(session, chain):
        sent.append((session, "".join(p.text for p in chain.chain)))
        return True
    context = SimpleNamespace(get_config=lambda: {"admins_id": ["100"]}, send_message=AsyncMock(side_effect=send))
    plugin.context = context
    core = PublicAPI(SimpleNamespace(application=app))
    registry = [
        SimpleNamespace(activated=True, name="astrbot_plugin_shrink", star_cls=SimpleNamespace(
            plugin_capabilities=core.manifest, invoke_capability=core.invoke)),
        SimpleNamespace(activated=True, name="astrbot_plugin_shrink_desktop", star_cls=plugin)]
    client = m.CapabilityClient(lambda: registry)
    monkeypatch.setattr(m, "CapabilityClient", lambda: client)
    app.plugin_bus = client
    app.authorization_adapter = AuthorizationAdapter(app, context)
    app.notification_event = lambda session, sender: notification_event(context, session, sender)
    app.outbound.resolve_scope = lambda scope: {"source": "qq:private:" + scope["sender"], "user_id": "qq:" + scope["sender"]}
    app.expression.model = AsyncMock(return_value="这次操作需要你确认。")
    Notifications(app)
    server = TestClient(TestServer(plugin.transport.application))
    await server.start_server()
    response = await server.post("/pair", json={"code": plugin.transport.pairing(SCOPE)["code"]})
    credentials = await response.json()
    device_id = credentials["device_id"]
    with plugin.transport.db:
        plugin.transport.db.execute("INSERT INTO sessions VALUES(?,?,?)", (device_id, "s", plugin.transport.scope(SCOPE)))
    original_command = plugin.transport.command
    async def short_command(*args, **kwargs):
        return await original_command(*args, **{**kwargs, "timeout": .1})
    plugin.transport.command = short_command
    plugin.transport.validate_approval = plugin.validate_approval
    device = Device(server, credentials)
    rig = SimpleNamespace(app=app, plugin=plugin, device=device, id=device_id, sent=sent, client=client)
    yield rig
    await device.close()
    await server.close()
    await plugin.transport.close()


def user(rig, text, identity):
    row, _ = rig.app.inbox.event(session=SCOPE["session"], sender="100", external_id=identity,
                                 source="platform", route="chat", text=text)
    rig.app.inbox.repository.complete([row["id"]])
    return row["id"]


async def grant(rig):
    return await rig.app.authorizations.grant(user(rig, f"授权 {rig.id} s execute", "grant"), SCOPE,
        {"device": rig.id, "provider": "astrbot_plugin_shrink_desktop", "execution_session": "s", "categories": ["execute"]})


async def emit(rig, approval="a", tool="Bash"):
    await rig.device.publish({"kind": "confirmation", "session_id": "s", "native_id": "native", "state": "waiting_confirmation",
        "detail": {"approval_id": approval, "expires": datetime.fromtimestamp(time.time() + 600, timezone.utc).isoformat(),
                   "request": {"subtype": "can_use_tool", "tool_name": tool, "input": {"command": "PRIVATE COMMAND"}}}})
    await rig.plugin.deliver_events()
    return next(e for e in rig.app.authorizations.repository.events() if e["body"]["approval_id"] == approval)


async def flush(rig):
    with rig.app.database.transaction() as con:
        con.execute("UPDATE notification_outbox SET available=0")
    await rig.app.notification_outbox.pump()


async def test_real_plugin_confirmation_policy_silent_once_receipt(rig):
    await rig.device.connect()
    await grant(rig)
    row = await emit(rig)
    assert row["state"] == "confirmed"
    await rig.plugin.deliver_events()
    await rig.app.authorizations.recover()
    await flush(rig)
    assert len(rig.device.executions) == len(rig.device.wires) == 1
    assert not rig.sent
    assert row["receipt"]["request"]["arguments"]["allow"] is True
    assert row["receipt"]["result"] == {"ok": True}
    assert "ungrant" not in rig.plugin.plugin_capabilities()["capabilities"][0]["actions"]


async def test_real_plugin_question_original_session_and_natural_agreement(rig):
    await rig.device.connect()
    row = await emit(rig)
    await flush(rig)
    assert row["state"] == "waiting_user" and [s for s, _ in rig.sent] == [SCOPE["session"]]
    answer = user(rig, "同意", "answer")
    result = await rig.app.authorization_adapter.manage("answer", {}, SCOPE, answer)
    assert result["state"] == "confirmed" and len(rig.device.executions) == 1
    assert not rig.app.authorizations.repository.policies()


async def test_real_plugin_natural_continuous_scope_then_different_category(rig):
    await rig.device.connect()
    first = await emit(rig)
    result = await rig.app.authorization_adapter.manage("answer", {"confirmation_id": first["id"]}, SCOPE,
                                                       user(rig, "同意，以后这类不用问了", "answer"))
    assert result["state"] == "confirmed"
    policy = rig.app.authorizations.repository.policies()[0]
    assert (policy["device"], policy["administrator"], policy["execution_session"], policy["future"]) == (rig.id, "100", "s", 0)
    assert policy["categories"] == ["execute"] and policy["task"] == ""
    assert (await emit(rig, "b"))["state"] == "confirmed"
    assert (await emit(rig, "c", "Write"))["state"] == "waiting_user"
    await flush(rig)
    assert len(rig.device.executions) == 2 and len(rig.sent) == 1


async def test_real_plugin_revoked_queued_approval_not_dispatched(rig):
    policy = await grant(rig)
    body = {"provider": "astrbot_plugin_shrink_desktop", "device": rig.id, "execution_session": "s",
            "approval_id": "offline", "category": "execute", "expires": time.time() + 600}
    row = rig.app.authorizations.receive(body, SCOPE)
    result = await rig.app.authorizations.process(row["id"])
    assert result["state"] == "unknown"
    rig.app.authorizations.change(user(rig, "撤销授权 " + policy["id"], "revoke"), SCOPE, policy["id"], "revoked")
    await rig.device.connect()
    await rig.device.publish({"kind": "barrier"})
    assert not rig.device.wires
    receipt = await rig.app.authorizations.process(row["id"])
    assert receipt["state"] == "held" and receipt["receipt"]["result"]["state"] == "not_executed"


async def test_real_plugin_reconnect_same_command_never_second_approval(rig):
    await grant(rig)
    await rig.device.connect()
    rig.device.drop_results = True
    row = await emit(rig)
    assert row["state"] == "unknown" and len(rig.device.executions) == 1
    await rig.device.close()
    rig.device.drop_results = False
    await rig.device.connect()
    await rig.device.publish({"kind": "barrier"})
    result = await rig.app.authorizations.process(row["id"])
    assert result["state"] == "confirmed"
    assert len(rig.device.executions) == 1 and len(rig.device.wires) == 2
    assert rig.device.wires[0] == rig.device.wires[1]


async def test_real_plugin_notify_core_owns_send_once_and_legacy_delivery_not_replayed(rig):
    await rig.device.connect()
    event = {"kind": "result", "session_id": "s", "state": "completed", "detail": {"text": "结果资料"}}
    await rig.device.publish(event)
    await rig.plugin.deliver_events()
    await rig.plugin.deliver_events()
    assert not rig.sent
    await flush(rig)
    await flush(rig)
    assert len(rig.sent) == 1
    assert len(rig.app.database.rows("SELECT * FROM notification_receipts")) == 1
    assert not rig.plugin.transport.rows("SELECT * FROM reply_records")
    await rig.device.publish(event)
    with rig.plugin.transport.db:
        rig.plugin.transport.db.execute("INSERT INTO deliveries VALUES(?,?,?,'platform_accepted')",
            (rig.id, rig.device.seq, rig.plugin.transport.scope(SCOPE)))
    await rig.plugin.deliver_events()
    assert len(rig.app.notification_outbox.repository.list()) == 1


async def test_real_plugin_natural_denial_sends_false_and_records_receipt(rig):
    await rig.device.connect()
    row = await emit(rig)
    result = await rig.app.authorization_adapter.manage("answer", {}, SCOPE, user(rig, "不同意", "deny"))
    assert result["state"] == "confirmed" and not result["allow"]
    assert rig.device.wires[0]["arguments"]["allow"] is False
    assert not rig.app.authorizations.repository.policies()
    assert rig.app.authorizations.repository.event(row["id"])["answer_input"]
