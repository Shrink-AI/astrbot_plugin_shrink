import asyncio
import json
import sqlite3
import time
from unittest.mock import AsyncMock

import pytest

from shrink.application import Application
from shrink.persistence.database import Database, StateRepository
from shrink.persistence.history import HistoryRepository
from shrink.persistence.messages import MessageRepository
from shrink.persistence.task_inputs import TaskInputsRepository


@pytest.fixture
def app(tmp_path):
    value = Application(tmp_path, AsyncMock(return_value="甲决定去杭州，工具已查到车票。"),
                        AsyncMock(return_value=None), AsyncMock())
    yield value
    value.database.close()


def receive(app, identity="m1", text="去杭州", sender="100", session="bot:GroupMessage:1"):
    app.history.repository.session(session, instance="bot", platform="qq", kind="group",
                                   external_id="1", source="qq:group:1")
    return app.inbox.receive(
        {"id": "qq:bot:qq:group:1:" + identity, "user_id": "qq:" + sender,
         "source": "qq:group:1", "text": text, "name": "成员" + sender},
        session=session, sender=sender, external_id=identity)


async def test_c01_roles_tools_and_only_accepted_assistant(app):
    receive(app)
    app.history.tool_start("bot:GroupMessage:1", "call1", "tickets", {"city": "杭州"})
    app.history.tool_end("bot:GroupMessage:1", "call1", "票价100元")
    before = await app.history.contexts("bot:GroupMessage:1", 4000)
    assert [m["role"] for m in before] == ["user", "assistant", "tool"]
    assert before[1]["tool_calls"][0]["id"] == before[2]["tool_call_id"]
    app.history.sent("bot:GroupMessage:1", "qq:group:1", "查到了", identity="receipt1")
    receive(app, "m2", "上午有吗")
    after = await app.history.contexts("bot:GroupMessage:1", 4000)
    assert [m["role"] for m in after] == ["user", "assistant", "tool", "assistant", "user"]
    assert json.loads(after[1]["tool_calls"][0]["function"]["arguments"]) == {"city": "杭州"}
    assert "qq:100" in after[0]["content"]
    app.history.sent("bot:GroupMessage:1", "qq:group:1", "重复回执", identity="receipt1")
    assert len(app.history.repository.events("bot:GroupMessage:1")) == 5


async def test_c02_visible_before_debounce_and_identity_dedup(app):
    from shrink.services.debounce import Debouncer
    first, fresh = receive(app)
    debounce = Debouncer()
    pending = asyncio.create_task(debounce.collect(("room", "100"), "去杭州", [], "direct", quiet=.03))
    await asyncio.sleep(0)
    assert app.messages.get_many([first["message_id"]])[0]["text"] == "去杭州"
    assert app.inbox.repository.pending("chat")[0]["claimed"] is None
    duplicate, fresh2 = receive(app, text="被重复投递改变的文本")
    assert fresh and not fresh2 and duplicate["text"] == "去杭州"
    receive(app, "m2", "坐高铁")
    newest = asyncio.create_task(debounce.collect(("room", "100"), "坐高铁", [], "direct", quiet=.03))
    assert await pending is None
    assert (await newest).texts == ["去杭州", "坐高铁"]
    assert len(app.history.repository.events("bot:GroupMessage:1")) == 2


async def test_c03_people_and_bot_instances_are_isolated(app):
    receive(app, sender="100")
    receive(app, "m2", "不要替我买票", sender="200")
    receive(app, "other", "另一个机器人", session="other:GroupMessage:1")
    first = await app.history.contexts("bot:GroupMessage:1", 4000)
    assert len(first) == 2 and "qq:100" in first[0]["content"] and "qq:200" in first[1]["content"]
    assert "另一个机器人" not in str(first)


async def test_arrival_during_tool_keeps_protocol_pairs_and_separate_speaker(app):
    receive(app)
    app.history.tool_start("bot:GroupMessage:1", "call", "lookup", {})
    receive(app, "m2", "我另有问题", sender="200")
    app.history.tool_end("bot:GroupMessage:1", "call", "找到")
    result = await app.history.contexts("bot:GroupMessage:1", 4000)
    assert [m["role"] for m in result] == ["user", "assistant", "tool", "user"]
    assert "qq:200" in result[-1]["content"]
    rows = app.history.repository.events("bot:GroupMessage:1")
    assert [row["role"] for row in rows] == ["user", "assistant", "user", "tool"]


async def test_r03_reopened_inbox_delivers_unprocessed_once(app):
    one, _ = receive(app)
    two, _ = receive(app, "m2", "补充")
    app.inbox.repository.complete([one["id"]])
    first_processed = app.inbox.repository.get(one["id"])["processed"]
    app.inbox.repository.claim([two["id"]])
    db = Database(app.database.root)
    try:
        from shrink.persistence.inbox import InboxRepository
        from shrink.services.inbox import Inbox
        restarted = Inbox(InboxRepository(db), app.messages, app.history)
        seen = []

        async def consume(row):
            seen.append(row["id"])
            restarted.repository.complete([row["id"]])
        await restarted.recover("chat", consume)
        await restarted.recover("chat", consume)
        assert seen == [two["id"]]
        assert restarted.repository.get(one["id"])["processed"] == first_processed
    finally:
        db.close()


def test_task_input_shares_message_identity_and_raw_text(app):
    row, _ = receive(app)
    repository = TaskInputsRepository(app.database)
    first = repository.receive("task1", "bot:GroupMessage:1", "100", "m1", "模型改写的要求")
    duplicate = repository.receive("task1", "bot:GroupMessage:1", "100", "m1", "再改")
    assert first == duplicate and first["input_id"] == row["id"] and first["text"] == "去杭州"
    assert app.database.rows("SELECT count(*) n FROM messages")[0]["n"] == 1
    other = repository.receive("task2", "bot:GroupMessage:1", "100", "m1", "同一原文的另一任务绑定")
    assert other["input_id"] == first["input_id"] and other["text"] == "去杭州"
    repository.checkpoint("task1", [{"role": "user", "content": "去杭州"}], entered=[first["id"]],
                          processed=[first["id"]])
    assert app.inbox.repository.get(row["id"])["processed"] is None
    assert repository.pending("task2")[0]["id"] == other["id"]
    repository.checkpoint("task2", [{"role": "user", "content": "去杭州"}], entered=[other["id"]],
                          processed=[other["id"]])
    assert app.inbox.repository.get(row["id"])["processed"] is not None


async def test_compression_preserves_references_and_only_advances_affected_session(app):
    for i in range(12):
        receive(app, f"m{i}", "行程细节" * 45)
    receive(app, "other", "别的会话", session="other:GroupMessage:1")
    contexts = await app.history.contexts("bot:GroupMessage:1", 900)
    checkpoint = app.history.repository.checkpoint("bot:GroupMessage:1")
    assert checkpoint and "压缩检查点" in contexts[0]["content"]
    refs = json.loads(checkpoint["message_refs"])
    assert app.history.references("bot:GroupMessage:1", refs)[0]["text"] == "行程细节" * 45
    assert app.history.read_checkpoint("bot:GroupMessage:1", checkpoint["id"])["originals"]
    assert not app.history.repository.checkpoint("other:GroupMessage:1")
    calls = app.history.model.await_count
    await app.history.contexts("bot:GroupMessage:1", 900)
    assert app.history.model.await_count == calls
    with pytest.raises(ValueError, match="当前会话"):
        app.history.references("other:GroupMessage:1", refs)
    with pytest.raises(ValueError, match="当前会话"):
        app.history.read_checkpoint("other:GroupMessage:1", checkpoint["id"])


async def test_correction_during_summary_does_not_restore_stale_checkpoint(app):
    first, _ = receive(app, text="周一出发")
    for i in range(5):
        receive(app, f"long{i}", "行程" * 100)
    waiting, release = asyncio.Event(), asyncio.Event()
    async def summary(*args):
        waiting.set()
        await release.wait()
        return "周一出发"
    app.history.model = summary
    work = asyncio.create_task(app.history.contexts("bot:GroupMessage:1", 900))
    await waiting.wait()
    app.history.revise("bot:GroupMessage:1", first["message_id"], sender="qq:100",
                       identity="correct", text="周二出发")
    release.set()
    result = await work
    assert "周一" not in str(result) and "周二" in str(result)
    assert app.history.repository.checkpoint("bot:GroupMessage:1") is None


@pytest.mark.parametrize("failure", ["timeout", "empty", "exception"])
async def test_compression_failure_falls_back_without_blocking(app, failure):
    for i in range(5):
        receive(app, str(i), str(i) * 200)
    if failure == "timeout":
        async def slow(*args):
            await asyncio.sleep(10)
        app.history.model = slow
        app.history.timeout = .01
    elif failure == "empty":
        app.history.model = AsyncMock(return_value="")
    else:
        app.history.model = AsyncMock(side_effect=RuntimeError("unavailable"))
    result = await asyncio.wait_for(app.history.contexts("bot:GroupMessage:1", 500), .5)
    assert "4" * 200 in result[-1]["content"]
    assert not app.history.repository.checkpoint("bot:GroupMessage:1")


async def test_retraction_and_correction_remove_old_facts_even_from_checkpoint(app):
    first, _ = receive(app, text="我决定周一出发")
    receive(app, "m2", "其他细节" * 100)
    await app.history.contexts("bot:GroupMessage:1", 400)
    assert app.history.repository.checkpoint("bot:GroupMessage:1")
    app.history.revise("bot:GroupMessage:1", first["message_id"], sender="qq:100", identity="correction",
                       text="改为周二出发")
    assert app.history.repository.checkpoint("bot:GroupMessage:1") is None
    result = await app.history.contexts("bot:GroupMessage:1", 4000)
    assert "周一" not in str(result) and "周二" in str(result) and "纠正" in str(result)
    with pytest.raises(ValueError):
        app.history.revise("bot:GroupMessage:1", first["message_id"], sender="qq:200", identity="forged")
    app.history.revise("bot:GroupMessage:1", first["message_id"], sender="qq:100", identity="retract")
    assert "周二" not in str(await app.history.contexts("bot:GroupMessage:1", 4000))
    assert "周一" not in str(app.messages.window("qq:group:1"))
    assert app.messages.get_many([first["message_id"]])[0]["text"] == "我决定周一出发"


def test_input_receive_rolls_back_raw_and_history_together(app, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("log unavailable")
    monkeypatch.setattr(app.history.repository, "append", fail)
    with pytest.raises(RuntimeError):
        receive(app)
    assert not app.database.rows("SELECT * FROM messages")
    assert not app.database.rows("SELECT * FROM input_events")


def test_same_external_number_from_different_sources_has_independent_receipts(app):
    chat, _ = receive(app)
    plugin, _ = app.inbox.event(session="bot:GroupMessage:1", sender="100", external_id="m1",
                                source="plugin", route="composition", text="提醒事件")
    assert plugin["id"] != chat["id"]
    app.inbox.repository.complete([plugin["id"]])
    assert app.inbox.repository.get(chat["id"])["processed"] is None
    task = TaskInputsRepository(app.database).receive("task", "bot:GroupMessage:1", "100", "m1", "改写")
    assert task["input_id"] == chat["id"] and task["text"] == "去杭州"


def test_migration_with_messages_digests_is_idempotent_and_backed_up(tmp_path):
    db = Database(tmp_path)
    try:
        messages = MessageRepository(db, StateRepository(db))
        messages.ingest({"id": "qq:bot:qq:group:1:m1", "source": "qq:group:1", "user_id": "qq:100",
                         "text": "旧消息", "created": 100})
        messages.record_bot("qq:group:1", "旧回复", created=101)
        from shrink.persistence.context import DigestRepository
        DigestRepository(db).add("qq:group:1", 99, 102, 2, "旧摘要")
        first = HistoryRepository(db)
        events = first.events("bot:GroupMessage:1")
        assert [r["role"] for r in events] == ["user", "assistant"]
        assert first.checkpoint("bot:GroupMessage:1")["summary"] == "旧摘要"
        backup = tmp_path / "shrink.before-continuous-history-v1.db"
        assert backup.exists()
        saved = backup.read_bytes()
        assert HistoryRepository(db).events("bot:GroupMessage:1") == events
        assert backup.read_bytes() == saved
        with sqlite3.connect(backup) as con:
            assert con.execute("SELECT count(*) FROM messages").fetchone()[0] == 2
            assert not con.execute("SELECT 1 FROM sqlite_master WHERE name='conversation_events'").fetchone()
    finally:
        db.close()


def test_system_prefix_metric_reports_equality_not_cache_hit(app):
    receive(app)
    repo = app.history.repository
    assert repo.compare_prefix("bot:GroupMessage:1", "fixed") is None
    assert repo.compare_prefix("bot:GroupMessage:1", "fixed") is True
    assert repo.compare_prefix("bot:GroupMessage:1", "changed") is False
