import asyncio
import json
import threading
import time
from unittest.mock import AsyncMock

import pytest

from shrink.persistence.compositions import Compositions
from shrink.services.composition import ChatReply
from tests.test_composition_failures import app  # noqa: F401
from tests.test_agent_tasks import create, runtime  # noqa: F401
from tests.test_task_inputs import stored_task


async def test_chat_reply_is_not_archived_as_raw_material(app):
    await app.outbound.compose(ChatReply("normal chat"), kind="tool_result")
    assert app.database.rows("SELECT * FROM outbound_compositions") == []


@pytest.mark.parametrize("trigger", ["startup", "save"])
def test_composition_retention_removes_expired_material(app, trigger):
    repo = app.outbound.repository
    expired = repo.save("old", "", "result", {})
    recent = repo.save("recent", "", "result", {})
    with app.database.transaction() as con:
        con.execute("UPDATE outbound_compositions SET created=? WHERE id=?",
                    (time.time() - 15 * 86400, expired))
    if trigger == "startup":
        Compositions(app.database)
    else:
        repo.save("new", "", "result", {})
    ids = {row["id"] for row in app.database.rows("SELECT id FROM outbound_compositions")}
    assert expired not in ids and recent in ids


def test_checkpoint_strips_inline_images_without_mutating_live_context(runtime):
    op, _, _ = runtime
    stored_task(op)
    messages = [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,PRIVATE_PIXELS"}},
        {"type": "text", "text": "keep the caption"},
    ]}]
    op.task_inputs.repository.checkpoint("task", messages)
    saved = json.dumps(op.task_inputs.repository.context("task"))
    assert "PRIVATE_PIXELS" not in saved and "keep the caption" in saved
    assert "PRIVATE_PIXELS" in json.dumps(messages)


def test_checkpoint_writes_only_changed_messages(runtime):
    op, _, _ = runtime
    stored_task(op)
    statements = []
    op.database.connection.set_trace_callback(statements.append)
    messages = [{"role": "user", "content": "HISTORICAL_PAYLOAD" * 500}]
    try:
        op.task_inputs.repository.checkpoint("task", messages)
        statements.clear()
        for i in range(8):
            messages.append({"role": "assistant", "content": str(i)})
            op.task_inputs.repository.checkpoint("task", messages)
        writes = [s for s in statements if s.startswith(("INSERT", "UPDATE"))]
        assert not any("HISTORICAL_PAYLOAD" in s for s in writes)
        assert op.task_inputs.repository.context("task") == messages
    finally:
        op.database.connection.set_trace_callback(None)


async def test_async_checkpoint_serialization_and_sql_run_off_event_loop(runtime, monkeypatch):
    import sqlite3
    op, _, _ = runtime
    stored_task(op)
    main_thread = threading.get_ident()
    worker_threads = []
    sql_threads = []
    connect = sqlite3.connect
    def tracked_connect(*args, **kwargs):
        con = connect(*args, **kwargs)
        con.set_trace_callback(lambda sql: sql_threads.append(threading.get_ident()))
        return con
    monkeypatch.setattr(sqlite3, "connect", tracked_connect)
    class Message:
        def model_dump(self):
            worker_threads.append(threading.get_ident())
            return {"role": "user", "content": "large context"}
    await op.task_inputs.repository.checkpoint_async("task", [Message()])
    assert worker_threads and all(t != main_thread for t in worker_threads)
    assert sql_threads and all(t != main_thread for t in sql_threads)
    assert op.task_inputs.repository.context("task")[0]["content"] == "large context"


@pytest.mark.parametrize("outcome", ["completed", "cancelled"])
async def test_terminal_task_clears_checkpoint_but_preserves_inputs(runtime, outcome):
    op, event, _ = runtime
    await create(op)
    entered = asyncio.Event()
    async def invoke(*args):
        from shrink.services.task_context import CURRENT_TASK
        task_id = CURRENT_TASK.get()
        op.task_inputs.append(task_id, event.unified_msg_origin, "123", "m1", "retained")
        op.task_inputs.repository.checkpoint(task_id, [{"role": "user", "content": "obsolete"}])
        entered.set()
        if outcome == "cancelled":
            await asyncio.Event().wait()
        return "done"
    op.invoke_agent = invoke
    result = await op.submit_agent(event, "researcher", "work")
    await entered.wait()
    statements = []
    op.database.connection.set_trace_callback(statements.append)
    if outcome == "cancelled":
        await op.cancel(result["task_id"])
    await op.running[result["task_id"]]
    assert op.task_inputs.repository.context(result["task_id"]) is None
    assert op.detail(result["task_id"])["inputs"][0]["text"] == "retained"
    assert not any(sql.startswith("DELETE FROM task_context") or
                   sql.startswith("DELETE FROM task_execution_context") for sql in statements)


def test_existing_full_checkpoint_migrates_and_compacted_history_replaces_tail(runtime):
    from shrink.persistence.task_inputs import TaskInputsRepository
    op, _, _ = runtime
    stored_task(op)
    old = [{"role": "user", "content": "original"},
           {"role": "assistant", "content": "old answer"}]
    with op.database.transaction() as con:
        con.execute("INSERT INTO task_execution_context VALUES(?,?)", ("task", json.dumps(old)))
    repo = TaskInputsRepository(op.database)
    assert repo.context("task") == old
    assert op.database.rows("SELECT messages FROM task_execution_context")[0]["messages"] == "[]"
    compacted = [{"role": "user", "content": "summary"}]
    repo.checkpoint("task", compacted)
    assert TaskInputsRepository(op.database).context("task") == compacted


async def test_cancellation_waits_for_checkpoint_commit_before_cleanup(runtime):
    op, _, _ = runtime
    stored_task(op)
    writing, release = threading.Event(), threading.Event()
    class Message:
        def model_dump(self):
            writing.set()
            assert release.wait(5)
            return {"role": "user", "content": "must not reappear after cleanup"}
    write = asyncio.create_task(op.task_inputs.repository.checkpoint_async("task", [Message()]))
    try:
        await asyncio.wait_for(asyncio.to_thread(writing.wait), 2)
        write.cancel()
        await asyncio.sleep(0)
        assert not write.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await write
    op.task_inputs.repository.clear_context("task")
    assert op.task_inputs.repository.context("task") is None


async def test_cancelling_waiting_user_clears_checkpoint(runtime):
    op, event, _ = runtime
    stored_task(op, "waiting_user")
    op.task_inputs.repository.checkpoint("task", [{"role": "user", "content": "obsolete"}])
    await op.orchestrate(event, "cancel", {"task_id": "task"})
    assert op.detail("task")["status"] == "cancelled"
    assert op.task_inputs.repository.context("task") is None
