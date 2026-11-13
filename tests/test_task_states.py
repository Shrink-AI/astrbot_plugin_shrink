import sqlite3

import pytest

from shrink.persistence.database import Database
from shrink.persistence.tasks import Tasks
from shrink.services.task_errors import TaskFailure, classify_error
from shrink.services.task_states import LEGACY, TRANSITIONS, TaskStates


@pytest.fixture
def states(tmp_path):
    db = Database(tmp_path)
    service = TaskStates(Tasks(db))
    service.migrate()
    yield service
    db.close()


def task(states, state="queued"):
    identity = states.repository.create("work", "session", "creator", "provider")
    if state != "queued":
        # Fixture setup can represent any persisted state, including a crashed process.
        with states.repository.db.transaction() as con:
            con.execute("UPDATE operation_tasks SET status=? WHERE id=?", (state, identity))
    return identity


@pytest.mark.parametrize("source,target", [(s, t) for s, targets in TRANSITIONS.items() for t in targets])
def test_all_declared_transitions(states, source, target):
    identity = task(states, source)
    states.transition(identity, target)
    assert states.task(identity)["status"] == target


@pytest.mark.parametrize("source,target", [
    ("completed", "running"), ("queued", "completed"), ("retry_wait", "running"),
    ("cancelled", "running"), ("cancelling", "completed"), ("running", "interrupted"),
])
def test_illegal_transitions_leave_state_unchanged(states, source, target):
    identity = task(states, source)
    with pytest.raises(ValueError):
        states.transition(identity, target)
    assert states.task(identity)["status"] == source


@pytest.mark.parametrize("category,target", [
    ("network", "retry_wait"), ("invalid_request", "needs_attention"),
    ("unknown_write", "reconciling"), ("permission", "waiting_user"),
    ("output_format", "retry_wait"), ("external_running", "waiting_external"),
    ("unknown_send", "reconciling"), ("unavailable", "needs_attention"),
])
def test_error_categories_have_explicit_destinations(category, target):
    assert classify_error(TaskFailure(category)).state == target


def test_retry_schedule_budget_and_attempts_are_durable(states):
    identity = task(states)
    for ordinal in range(4):
        states.start(identity)
        assert states.repository.attempts(identity)[-1]["retry_ordinal"] == ordinal
        state = states.failure(identity, TimeoutError("network"), now=100)
        if ordinal == 3:
            assert state == "needs_attention"
            break
        due = states.task(identity)["next_run"]
        assert state == "retry_wait"
        assert not states.retry_due(due - .01)
        assert states.retry_due(due) == [identity]
    attempts = states.repository.attempts(identity)
    assert len(attempts) == 4 and all(a["finished"] is not None for a in attempts)
    assert attempts[-1]["error_class"] == "unavailable"
    assert states.task(identity)["finished"] is None


def test_restart_recovers_unstarted_unknown_write_cancelled_and_ambiguous(states):
    queued = task(states)
    unknown = task(states)
    states.start(unknown)
    states.repository.record(unknown, "tool_start:write", result='{"id":"same-operation"}')
    cancelled = task(states, "cancelled")
    ambiguous = task(states)
    states.start(ambiguous)
    states.recover()
    assert [states.task(i)["status"] for i in (queued, unknown, cancelled, ambiguous)] == [
        "queued", "reconciling", "cancelled", "needs_attention"]
    assert not states.retry_due(10**12)


@pytest.mark.parametrize("unknown,expected", [(False, "cancelled"), (True, "reconciling")])
def test_cancel_requires_execution_acknowledgement(states, unknown, expected):
    identity = task(states)
    states.start(identity)
    if unknown:
        states.repository.record(identity, "tool_start:write", result="{}")
    assert states.cancel(identity) == "cancelling"
    assert states.stopped(identity) == expected
    states.recover()
    assert states.task(identity)["status"] == expected
    assert states.task(identity)["stop_requested"] == 1


def test_unknown_write_overrides_network_retry(states):
    identity = task(states)
    states.start(identity)
    states.repository.record(identity, "tool_start:write", result='{"key":"original"}')
    assert states.failure(identity, TimeoutError()) == "reconciling"
    assert states.task(identity)["next_run"] is None
    assert states.repository.attempts(identity)[0]["error_class"] == "unknown_write"


def test_legacy_migration_backups_and_is_idempotent(tmp_path):
    db = Database(tmp_path)
    with db.transaction() as con:
        con.execute("CREATE TABLE operation_tasks(id TEXT PRIMARY KEY,request TEXT,session TEXT,sender TEXT,"
                    "status TEXT,started REAL,finished REAL,result TEXT,failures INTEGER,provider TEXT,parent TEXT,delivery TEXT)")
        for state in [*LEGACY, "completed", "cancelled", "queued", "running"]:
            con.execute("INSERT INTO operation_tasks VALUES(?,?,?, ?,?,1,NULL,'result',0,'p','','platform_accepted')",
                        (state, "request", "session", "creator", state))
    service = TaskStates(Tasks(db))
    service.migrate()
    backup = tmp_path / "shrink.before-task-states-v2.db"
    assert backup.is_file()
    with sqlite3.connect(backup) as con:
        assert con.execute("SELECT status FROM operation_tasks WHERE id='interrupted'").fetchone()[0] == "interrupted"
        assert con.execute("SELECT delivery FROM operation_tasks WHERE id='completed'").fetchone()[0] == "platform_accepted"
    before = backup.read_bytes()
    expected = {s: t for s, t in LEGACY.items()}
    assert {s: service.task(s)["status"] for s in LEGACY} == expected
    assert service.task("completed")["delivery"] == "platform_accepted"
    snapshots = service.repository.list()
    TaskStates(Tasks(db)).migrate()
    assert service.repository.list() == snapshots and backup.read_bytes() == before
    assert all("legacy" not in r["name"] for r in db.rows("PRAGMA table_info(operation_tasks)"))
    db.close()


def test_migration_rollback_preserves_original_statuses(tmp_path, monkeypatch):
    db = Database(tmp_path)
    repository = Tasks(db)
    for state in ("interrupted", "awaiting_input"):
        identity = repository.create(state, "session", "creator", "provider")
        with db.transaction() as con:
            con.execute("UPDATE operation_tasks SET status=? WHERE id=?", (state, identity))
    states = TaskStates(repository)
    before = repository.list()
    original = states.transition
    count = 0
    def broken(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("migration interrupted")
        return original(*args, **kwargs)
    monkeypatch.setattr(states, "transition", broken)
    with pytest.raises(OSError):
        states.migrate()
    assert repository.list() == before
    assert not db.rows("SELECT value FROM kv WHERE key=?", (repository.MIGRATION,))
    assert not db.rows("SELECT * FROM operation_events")
    monkeypatch.setattr(states, "transition", original)
    states.migrate()
    assert {r["status"] for r in repository.list()} == {"needs_attention", "waiting_user"}
    db.close()


def test_retry_budget_is_per_step_and_configuration_can_disable_retry(states):
    identity = task(states)
    states.start(identity)
    states.failure(identity, TimeoutError(), now=100)
    states.retry_due(200)
    states.start(identity)
    states.step_succeeded(identity)
    states.failure(identity, TimeoutError(), now=300)
    assert states.task(identity)["retry_count"] == 1
    assert states.task(identity)["next_run"] == 302
    states.retry_due(400)
    states.start(identity)
    states.retry_limit = 0
    assert states.failure(identity, TimeoutError()) == "needs_attention"


def test_queued_retry_recovers_after_due_transition_before_execution(states):
    identity = task(states)
    states.start(identity)
    states.failure(identity, TimeoutError(), now=100)
    states.retry_due(200)
    states.recover()
    assert states.task(identity)["status"] == "queued"


def test_tool_end_from_another_attempt_cannot_close_unknown_write(states):
    identity = task(states)
    states.start(identity)
    states.repository.record(identity, "tool_start:write", result='{"id":"same"}')
    states.failure(identity, TimeoutError())
    states.resume(identity)
    states.start(identity)
    states.repository.record(identity, "tool_end:write", result='{"arguments":{"id":"same"},"result":"ok"}')
    assert states.unknown_tools(identity)
