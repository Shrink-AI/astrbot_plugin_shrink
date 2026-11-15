"""Stage 3 dependency scheduling, process death and approval replay."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import AsyncMock

import pytest

from shrink.application import Application
from shrink.persistence.clarifications import Clarifications
from shrink.persistence.database import Database
from shrink.persistence.task_inputs import TaskInputsRepository
from shrink.persistence.tasks import Tasks
from shrink.services.task_execution import TaskExecution
from shrink.services.task_inputs import TaskInputs
from shrink.services.task_states import TaskStates
from tests import test_authorizations

app = pytest.fixture(test_authorizations.app.__wrapped__)
service = pytest.fixture(test_authorizations.service.__wrapped__)


def executor(db, execute):
    states = TaskStates(Tasks(db))
    states.migrate()
    states.recover()
    return TaskExecution(states, TaskInputs(TaskInputsRepository(db), states), Clarifications(db),
                         execute, AsyncMock(), lambda *args: None)


async def test_dependency_waits_without_occupying_slot_then_runs_once(tmp_path):
    db = Database(tmp_path)
    gate, entered = asyncio.Event(), asyncio.Event()
    calls = []
    async def execute(row, request, summary):
        calls.append(row["id"])
        if row["id"] == upstream:
            entered.set()
            await gate.wait()
        return "完成"
    ex = executor(db, execute)
    upstream = ex.repository.create("前置", "room", "owner", "p")
    downstream = ex.repository.create("下游", "room", "owner", "p", dependencies=[upstream])
    try:
        ex.launch(upstream)
        ex.launch(downstream)
        await asyncio.wait_for(entered.wait(), 2)
        assert calls == [upstream] and ex.slots._value == 2
        gate.set()
        await asyncio.wait_for(asyncio.gather(*ex.running.values()), 3)
        await ex.tick()
        await ex.tick()
        assert calls == [upstream, downstream]
        assert ex.states.task(downstream)["status"] == "completed"
        assert len(ex.repository.attempts(downstream)) == 1
    finally:
        gate.set()
        await ex.close()
        ex.scheduling.close()
        db.close()


@pytest.mark.parametrize("outcome", ["completed", "failed", "cancelled"])
async def test_dependency_relationship_and_destination_survive_database_reopen(tmp_path, outcome):
    db = Database(tmp_path)
    ex = executor(db, AsyncMock(return_value="完成"))
    upstream = ex.repository.create("前置", "room", "owner", "p")
    downstream = ex.repository.create("下游", "room", "owner", "p", dependencies=[upstream])
    ex.states.start(upstream)
    ex.states.finish(upstream, "waiting_user", "等答复")
    await ex.launch(downstream)
    assert ex.states.task(downstream)["status"] == "waiting_external"
    ex.execute.assert_not_awaited()
    await ex.close()
    ex.scheduling.close()
    db.close()
    db = Database(tmp_path)
    execute = AsyncMock(return_value="下游完成")
    restored = executor(db, execute)
    try:
        assert json.loads(restored.states.task(downstream)["dependencies"]) == [upstream]
        await restored.tick()
        assert not restored.running
        if outcome == "cancelled":
            restored.states.cancel(upstream)
        else:
            restored.states.resume(upstream)
            restored.states.start(upstream)
            restored.states.finish(upstream, outcome, "前置结果")
        await restored.tick()
        await asyncio.gather(*restored.running.values())
        await restored.tick()
        if outcome == "completed":
            assert restored.states.task(downstream)["status"] == "completed"
            execute.assert_awaited_once()
            assert "前置结果" in execute.call_args.args[1]
        else:
            row = restored.states.task(downstream)
            assert row["status"] == "needs_attention" and row["wait_reason"] == "dependencies"
            execute.assert_not_awaited()
    finally:
        await restored.close()
        restored.scheduling.close()
        db.close()


CRASH_SCRIPT = r"""
import asyncio, json, sys
from pathlib import Path
from shrink.application import Application
from shrink.persistence.tasks import Tasks
from shrink.persistence.task_inputs import TaskInputsRepository
from shrink.services.task_states import TaskStates
from shrink.services.task_inputs import TaskInputs

root = Path(sys.argv[1])
app = Application(root, None, None, None)
states = TaskStates(Tasks(app.database))
states.migrate()
task = states.repository.create('write one object', 'room', 'owner', 'p')
states.start(task)
inputs = TaskInputs(TaskInputsRepository(app.database), states)
inputs.append(task, 'room', 'owner', 'done', 'ALREADY_PROCESSED')
async def seed():
    identities = await inputs.deliver(task, lambda text: None, lambda: [{'role':'user','content':'saved'}])
    await inputs.processed(task, [{'role':'user','content':'saved'}], identities)
    inputs.append(task, 'room', 'owner', 'pending', 'RECOVER_ONCE')
    box = app.notification_outbox
    async def transport(row, event, retry):
        box.receipts.reserve(row['id'], retry=retry)
        box.receipts.state(row['id'], 'sending', '已确认的说明')
        with (root / 'side-effects.jsonl').open('a', encoding='utf-8') as file:
            file.write(json.dumps({'id': row['event_id']}) + '\n')
        if row['event_id'] == 'accepted':
            box.receipts.state(row['id'], 'platform_accepted', '已确认的说明')
            return True
        # Simulate death after the platform acted but before a receipt can arrive.
        states.repository.record(task, 'tool_start:write', result='{"operation_id":"one-object"}')
        with app.database.transaction() as con:
            con.execute("UPDATE task_inputs SET processed=123 WHERE task_id=? AND processed IS NULL", (task,))
            con.execute("UPDATE notification_receipts SET state='platform_accepted' WHERE id=?", (row['id'],))
            (root / 'kill-ready.json').write_text(json.dumps({'task':task}), encoding='utf-8')
            await asyncio.Event().wait()
    box.transport = transport
    first = box.submit({'session':'room','sender':'owner'}, 'accepted', {'status':'completed'})
    await box.deliver(first['id'])
    box.repository.state(first['id'], 'delivering')  # acceptance committed before queue projection
    second = box.submit({'session':'room','sender':'owner'}, 'uncertain', {'status':'completed'})
    await box.deliver(second['id'])
asyncio.run(seed())
"""


async def test_r03_real_process_kill_rolls_back_partial_write_and_never_replays_sends(tmp_path):
    child_root = tmp_path / "child"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    process = subprocess.Popen([sys.executable, "-u", "-c", CRASH_SCRIPT, str(child_root)],
                               env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    ready = child_root / "kill-ready.json"
    deadline = time.monotonic() + 20
    try:
        while not ready.exists() and time.monotonic() < deadline and process.poll() is None:
            await asyncio.sleep(.02)
        if not ready.exists():
            if process.poll() is None:
                process.kill()
            _, errors = process.communicate(timeout=5)
            pytest.fail("child did not reach kill barrier: " + errors.decode("utf-8", "replace"))
        metadata = json.loads(ready.read_text("utf-8"))
        process.kill()
        process.communicate(timeout=5)
        assert process.returncode != 0
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
    reopened = Application(child_root, AsyncMock(return_value="不应发送"), AsyncMock(), AsyncMock())
    ex = executor(reopened.database, AsyncMock(return_value="核对完成"))
    box = reopened.notification_outbox
    box.transport = AsyncMock(side_effect=AssertionError("accepted or unknown must never replay"))
    try:
        task = metadata["task"]
        assert ex.states.task(task)["status"] == "reconciling"
        assert ex.states.unknown_tools(task)
        rows = ex.inputs.repository.all(task)
        assert rows[0]["processed"] is not None and rows[1]["processed"] is None
        await box.pump()
        await box.pump()
        assert {r["event_id"]: r["state"] for r in box.repository.list()} == {
            "accepted": "sent", "uncertain": "unknown"}
        box.transport.assert_not_awaited()
        seen = []
        async def consume(row):
            seen.append(row["text"])
            reopened.inbox.repository.complete([row["id"]])
        await reopened.inbox.recover("task", consume)
        await reopened.inbox.recover("task", consume)
        assert seen == ["RECOVER_ONCE"]
        await ex.inputs.resume(task, "room", "owner")
        appended = []
        messages = ex.inputs.repository.context(task)
        identities = await ex.inputs.deliver(task, appended.append, lambda: messages)
        assert len(appended) == 1 and "RECOVER_ONCE" in appended[0]
        await ex.inputs.processed(task, messages, identities)
        assert await ex.inputs.deliver(task, appended.append, lambda: messages) == []
        assert len(appended) == 1
        assert len((child_root / "side-effects.jsonl").read_text("utf-8").splitlines()) == 2
    finally:
        await ex.close()
        ex.scheduling.close()
        reopened.scheduling.close()
        reopened.database.close()


async def test_duplicate_answers_and_replayed_event_after_database_reopen_approve_once(app, service):
    from shrink.persistence.authorizations import Authorizations
    from shrink.persistence.inbox import InboxRepository
    from shrink.services.authorizations import AuthorizationService
    confirmation = test_authorizations.confirmation(service)
    await service.process(confirmation["id"])
    answer = test_authorizations.user(app, "同意", "answer")
    await asyncio.gather(service.process(confirmation["id"], answer),
                         service.process(confirmation["id"], answer))
    # Duplicate answer with a different delivery id is still the same confirmation.
    await service.process(confirmation["id"], test_authorizations.user(app, "同意", "redelivery"))
    body = confirmation["body"]
    root = app.database.root
    app.database.close()
    db = Database(root)
    try:
        restored = AuthorizationService(Authorizations(db), InboxRepository(db))
        restored.inbox_service = app.inbox
        restored.is_admin, restored.access, restored.command = service.is_admin, service.access, service.command
        event = restored.repository.event(confirmation["id"])
        # receive() uses the reopened inbox, matching a replay after restart.
        # Use the normal application wiring for intake rather than reconstructing history dependencies.
        reopened = Application(root, AsyncMock(), AsyncMock(), AsyncMock())
        try:
            restored.inbox_service = reopened.inbox
            replay = restored.receive(body, test_authorizations.SCOPE)
            assert replay["id"] == confirmation["id"]
            assert (await restored.process(replay["id"], answer))["state"] == "confirmed"
            await restored.recover()
            assert restored.repository.event(event["id"])["operation_id"] == event["operation_id"]
            assert len([c for c in service.command.await_args_list if c.args[1] == "approve"]) == 1
        finally:
            reopened.scheduling.close()
            reopened.database.close()
    finally:
        db.close()
        # Restore the fixture's connection so teardown closes an open connection.
        app.database = Database(root)
