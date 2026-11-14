import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from shrink.api.app import create_app
from shrink.application import Application
from shrink.adapters.public_api import PublicAPI


@pytest.fixture
def app(tmp_path):
    value = Application(tmp_path / "data", AsyncMock(return_value="提醒你喝水。"),
                        AsyncMock(return_value=None), AsyncMock())
    yield value
    value.database.close()


async def test_webui_inbox_requires_auth_and_replays_receipt_without_reexecuting(app, tmp_path):
    web = create_app(app, tmp_path, lambda: {})
    calls = []

    @web.post("/api/v1/test-operation")
    async def operate(body: dict):
        calls.append(body)
        return {"accepted": len(calls)}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web),
                                base_url="http://127.0.0.1:6186",
                                headers={"origin": "http://127.0.0.1:6186", "Idempotency-Key": "click1"}) as client:
        assert (await client.post("/api/v1/test-operation", json={"text": "操作"})).status_code == 401
        assert not app.inbox.repository.pending("webui")
        password = (app.state.root / "initial-password.txt").read_text().strip()
        login = await client.post("/api/v1/login", json={"password": password})
        client.headers["X-CSRF-Token"] = login.json()["csrf"]
        first = await client.post("/api/v1/test-operation", json={"text": "操作"})
        again = await client.post("/api/v1/test-operation", json={"text": "操作"})
        assert first.json() == again.json() == {"accepted": 1} and len(calls) == 1
        assert first.headers["X-Input-ID"] == "click1"
        row = app.database.rows("SELECT * FROM input_events WHERE source='webui'")[0]
        assert row["sender"] == "dashboard:administrator" and row["received"] <= row["claimed"] <= row["processed"]
        assert "password" not in str(app.database.rows("SELECT text FROM messages"))
        changed = await client.post("/api/v1/test-operation", json={"text": "换了操作"})
        assert changed.status_code == 409 and len(calls) == 1


async def test_webui_claimed_unknown_operation_is_not_blindly_replayed(app, tmp_path):
    web = create_app(app, tmp_path, lambda: {})
    row, _ = app.inbox.event(session="webui", sender="dashboard:administrator",
                             external_id="interrupted", source="webui", route="webui", text='{"text":"操作"}',
                             metadata={"method": "POST", "path": "/api/v1/test-operation", "query": ""})
    app.inbox.repository.claim([row["id"]])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web),
                                base_url="http://127.0.0.1:6186",
                                headers={"origin": "http://127.0.0.1:6186"}) as client:
        password = (app.state.root / "initial-password.txt").read_text().strip()
        response = await client.post("/api/v1/login", json={"password": password})
        client.headers.update({"X-CSRF-Token": response.json()["csrf"], "Idempotency-Key": "interrupted"})
        response = await client.post("/api/v1/test-operation", json={"text": "操作"})
        assert response.status_code == 409 and "核对" in response.json()["detail"]


async def test_reminder_capability_stable_event_uses_same_inbox_without_sending(app):
    api = PublicAPI(SimpleNamespace(application=app))
    request = {"protocol": 1, "version": 1, "capability": "message.compose", "action": "compose",
               "scope": {"session": "qq:FriendMessage:100", "sender": "100"},
               "arguments": {"facts": "定时喝水", "event_id": "alarm1:2026-11-14T12:00", "kind": "result"}}
    first, duplicate = await api.invoke(request), await api.invoke(request)
    assert first == duplicate and first["data"]["status"] == "ready"
    assert app.expression.model.await_count == 1
    row = app.database.rows("SELECT * FROM input_events")[0]
    assert row["source"] == "plugin" and row["processed"] is not None
    assert not app.history.repository.events("qq:FriendMessage:100")
    assert api.manifest()["capabilities"][0]["actions"]["compose"]["readonly"] is True


def test_task_events_enter_unified_inbox_with_original_reference(app):
    from shrink.persistence.tasks import Tasks
    repository = Tasks(app.database)
    task = repository.create("查日志", "qq:GroupMessage:1", "100", "test")
    repository.record(task, "tool_start", result=json.dumps({"name": "lookup"}))
    event = repository.events(task)[0]
    rows = app.database.rows("SELECT * FROM input_events WHERE source='task'")
    assert len(rows) == 1 and rows[0]["external_id"] == f"task-event:{event['id']}"
    assert rows[0]["sender"] == "100" and rows[0]["processed"] is not None
    assert "tool_start" in app.messages.get_many([rows[0]["message_id"]])[0]["text"]


def test_task_input_old_schema_migration_preserves_positions_and_is_idempotent(tmp_path):
    from shrink.persistence.database import Database
    from shrink.persistence.task_inputs import TaskInputsRepository
    db = Database(tmp_path)
    db.connection.executescript("""
        CREATE TABLE task_inputs(id INTEGER PRIMARY KEY,message_id TEXT,task_id TEXT,sender TEXT,
          session TEXT,text TEXT,received REAL,entered REAL,processed REAL,
          UNIQUE(task_id,session,sender,message_id));
        INSERT INTO task_inputs VALUES(1,'m1','t','100','qq:GroupMessage:1','补充',1,2,NULL);
        INSERT INTO task_inputs VALUES(2,'m2','t','100','qq:GroupMessage:1','完成',1,2,3);
    """)
    try:
        repository = TaskInputsRepository(db)
        rows = repository.all("t")
        assert rows[0]["entered"] == 2 and rows[0]["processed"] is None and rows[0]["text"] == "补充"
        assert [r["id"] for r in repository.pending("t")] == [1]
        assert TaskInputsRepository(db).all("t") == rows
        assert (tmp_path / "shrink.before-continuous-history-v1.db").exists()
        assert "text" not in {r["name"] for r in db.rows("PRAGMA table_info(task_inputs)")}
    finally:
        db.close()
