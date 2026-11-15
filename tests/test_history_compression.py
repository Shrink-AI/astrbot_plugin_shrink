"""Persistent boundaries are independent of background summary availability."""
import asyncio
import json
import time
from unittest.mock import AsyncMock

import pytest

from shrink.application import Application
from shrink.services.history import ConversationHistory
from shrink.services.scheduling import LANE
from tests.test_continuous_history import receive

SESSION = "bot:GroupMessage:1"


@pytest.fixture
async def app(tmp_path):
    value = Application(tmp_path, AsyncMock(return_value="合成摘要"), AsyncMock(return_value=None), AsyncMock())
    for i in range(20):
        receive(value, str(i), f"合成记录{i}：" + "行程细节" * 40)
    yield value
    await value.close()


async def settled(app):
    await asyncio.wait_for(asyncio.gather(*list(app.tasks.jobs)), 2)


async def test_slow_summary_does_not_delay_chat_and_both_lock_paths_match(app):
    entered, release = asyncio.Event(), asyncio.Event()
    lanes = []
    async def slow(*args):
        lanes.append(LANE.get())
        entered.set()
        await release.wait()
        return "合成后台摘要"
    app.history.model = slow
    try:
        async with app.scheduling.use("chat"):
            started = time.perf_counter()
            first = await asyncio.wait_for(app.history.contexts(SESSION, 1500), .2)
            assert time.perf_counter() - started < .2
        await asyncio.wait_for(entered.wait(), 1)
        checkpoint = app.history.repository.checkpoint(SESSION)
        assert checkpoint["summary"] == ""
        assert json.loads(checkpoint["message_refs"])
        prefix = first
        for i in range(3):
            receive(app, "extra" + str(i), "补充")
            async with app.scheduling.use("chat"):
                result = await asyncio.wait_for(app.history.contexts(SESSION, 1500), .2)
            assert result[:len(prefix)] == prefix
        lock = app.history.locks.setdefault(SESSION, asyncio.Lock())
        await lock.acquire()
        try:
            async with app.scheduling.use("chat"):
                locked = await app.history.contexts(SESSION, 1500)
        finally:
            lock.release()
        assert locked == await app.history.contexts(SESSION, 1500)
        assert lanes == ["maintenance"]
        assert app.history.timeout >= 120
    finally:
        release.set()


@pytest.mark.parametrize("failure", ["exception", "timeout"])
async def test_failed_summary_has_bounded_background_attempts_and_stable_prefix(app, failure):
    async def blocked(*args):
        await asyncio.Event().wait()
    model = AsyncMock(side_effect=blocked if failure == "timeout" else RuntimeError("synthetic failure"))
    app.history.model = model
    app.history.timeout = .01
    app.history.retry_delays = (0, 0)
    first = await app.history.contexts(SESSION, 1500)
    await settled(app)
    checkpoint = app.history.repository.checkpoint(SESSION)
    assert checkpoint and checkpoint["summary"] == ""
    assert checkpoint["summary_state"] == "failed"
    assert model.await_count == 3
    for i in range(4):
        receive(app, "later" + str(i), "补充")
        assert (await app.history.contexts(SESSION, 1500))[:len(first)] == first
    await settled(app)
    assert model.await_count == 3
    assert app.history.read_checkpoint(SESSION, checkpoint["id"])["originals"]


async def test_summary_lands_in_same_checkpoint_and_each_prefix_is_stable(app):
    release = asyncio.Event()
    async def slow(*args):
        await release.wait()
        return "合成后台摘要"
    app.history.model = slow
    before = await asyncio.wait_for(app.history.contexts(SESSION, 1500), .2)
    checkpoint = app.history.repository.checkpoint(SESSION)
    assert await app.history.contexts(SESSION, 1500) == before
    release.set()
    await settled(app)
    after = await app.history.contexts(SESSION, 1500)
    assert after[0] != before[0] and after[1:] == before[1:]
    assert app.history.repository.checkpoint(SESSION)["id"] == checkpoint["id"]
    receive(app, "later", "补充")
    assert (await app.history.contexts(SESSION, 1500))[:-1] == after


async def test_restart_finishes_pending_summary_without_new_checkpoint(app):
    entered = asyncio.Event()
    async def blocked(*args):
        entered.set()
        await asyncio.Event().wait()
    app.history.model = blocked
    before = await asyncio.wait_for(app.history.contexts(SESSION, 1500), .2)
    await asyncio.wait_for(entered.wait(), 1)
    checkpoint = app.history.repository.checkpoint(SESSION)
    root = app.database.root
    await app.close()
    restarted = Application(root, AsyncMock(return_value="重启后合成摘要"),
                            AsyncMock(return_value=None), AsyncMock())
    try:
        assert await restarted.history.contexts(SESSION, 1500) == before
        restarted.history.start()
        await settled(restarted)
        current = restarted.history.repository.checkpoint(SESSION)
        assert current["id"] == checkpoint["id"] and current["end_event"] == checkpoint["end_event"]
        assert current["summary"] == "重启后合成摘要"
        assert restarted.database.rows("SELECT count(*) n FROM conversation_checkpoints")[0]["n"] == 1
    finally:
        await restarted.close()


def test_orphan_tool_at_window_start_never_uses_negative_index():
    items = [({"id": i}, {"role": "tool", "content": "孤立结果", "tool_call_id": str(i)})
             for i in range(3)]
    assert ConversationHistory.tail(items, 1) == 0


def test_existing_checkpoints_receive_summary_job_fields(tmp_path):
    from shrink.persistence.database import Database
    from shrink.persistence.history import HistoryRepository
    db = Database(tmp_path)
    try:
        with db.transaction() as con:
            con.execute("CREATE TABLE conversation_checkpoints(id INTEGER PRIMARY KEY,session TEXT NOT NULL,"
                        "start_event INTEGER NOT NULL,end_event INTEGER NOT NULL,summary TEXT NOT NULL,"
                        "message_refs TEXT NOT NULL,created REAL NOT NULL,UNIQUE(session,end_event))")
            con.execute("INSERT INTO conversation_checkpoints VALUES(1,'synthetic',1,2,'合成旧摘要','[]',1)")
        current = HistoryRepository(db).checkpoint("synthetic")
        assert current["summary"] == "合成旧摘要"
        assert current["summary_state"] == "ready" and current["window_budget"] == 0
    finally:
        db.close()


async def test_new_summary_reserve_is_capped_and_existing_reserve_is_unchanged(app):
    app.history.spawn = None
    for i in range(70):
        receive(app, "large" + str(i), "容量边界" * 1000)
    await app.history.contexts(SESSION, 128000)
    checkpoint = app.history.repository.window(SESSION)
    assert checkpoint["summary_tokens"] == 4000
    with app.database.transaction() as con:
        con.execute("UPDATE conversation_checkpoints SET summary_tokens=25600 WHERE id=?", (checkpoint["id"],))
    await app.history.contexts(SESSION, 128000)
    assert app.history.repository.window(SESSION)["summary_tokens"] == 25600


async def test_small_window_reserve_scales_down_and_prompt_has_length_limit(app):
    await app.history.contexts(SESSION, 1500)
    checkpoint = app.history.repository.window(SESSION)
    assert checkpoint["summary_tokens"] == 300
    await settled(app)
    from shrink import prompts
    prompt = app.history.model.__wrapped__.await_args.args[0]
    allowance = max(1, checkpoint["summary_tokens"] - app.history.size(app.history.summary_message(checkpoint)))
    assert prompt == prompts.render("history.compress", messages=json.dumps(
        [m for r, m in app.history.render(app.history.repository.events(SESSION))
         if r["id"] <= checkpoint["end_event"]], ensure_ascii=False), max_tokens=allowance)
    assert str(allowance) in prompt
    assert "{{" not in prompt


async def test_next_summary_includes_previous_failed_range_when_it_fits(app):
    app.history.retry_delays = ()
    app.history.model = AsyncMock(side_effect=RuntimeError("synthetic failure"))
    await app.history.contexts(SESSION, 1500)
    await settled(app)
    first = app.history.repository.window(SESSION)
    assert first["summary_state"] == "failed"
    for i in range(20):
        receive(app, "next" + str(i), f"下一段{i}：" + "行程细节" * 40)
    app.history.model = AsyncMock(return_value="后续合成摘要")
    await app.history.contexts(SESSION, 1500)
    await settled(app)
    prompt = app.history.model.await_args.args[0]
    assert "合成记录0：" in prompt and "下一段0：" in prompt
    assert "未包含的更早记录" not in prompt


async def test_default_normal_compressions_include_all_covered_messages_and_previous_summary(app):
    app.history.spawn = None
    budget = 128000
    previous = None
    serial = 0
    for _ in range(2):
        events = app.history.render(app.history.repository.events(SESSION))
        used = sum(app.history.size(m) for r, m in events
                   if not previous or r["id"] > previous["end_event"])
        used += previous["summary_tokens"] if previous else 0
        while used < budget:
            text = f"正常覆盖记录{serial:04d}：" + "合成记录内容" * 80
            receive(app, "normal" + str(serial), text)
            used += app.history.size({"role": "user", "content": "[发言人 qq:100 · 成员100]\n" + text})
            serial += 1
        await app.history.contexts(SESSION, budget)
        checkpoint = app.history.repository.window(SESSION)
        covered = [m for r, m in app.history.render(app.history.repository.events(SESSION))
                   if (previous["end_event"] if previous else 0) < r["id"] <= checkpoint["end_event"]]
        assert sum(app.history.size(m) for m in covered) > 40000
        app.history.model = AsyncMock(return_value="前次正常摘要" * 1500)
        await app.history._summarize_pending()
        prompt = app.history.model.await_args.args[0]
        assert "前面还有未包含的更早记录" not in prompt
        material = json.loads(prompt.splitlines()[-1])
        expected = ([app.history.summary_message(previous)] if previous else []) + covered
        assert material == expected
        previous = app.history.repository.window(SESSION)
        assert 3900 <= app.history.size(app.history.summary_message(previous)) <= 4000


@pytest.mark.parametrize(("budget", "reserved", "limit"), [
    (1500, 300, 32000), (128000, 4000, 144800), (2000000, 4000, 2204000),
    (128000, 25600, 166400),
])
def test_summary_input_limit_uses_checkpoint_window_and_persisted_reserve(budget, reserved, limit):
    assert ConversationHistory.summary_input_limit({
        "window_budget": budget, "summary_tokens": reserved}) == limit


@pytest.mark.parametrize("retracted", [False, True])
async def test_summary_input_is_bounded_to_recent_complete_blocks_after_missing_summary(app, retracted):
    app.history.spawn = None
    if retracted:
        await app.history.contexts(SESSION, 1500)
        original = app.history.repository.events(SESSION)[0]
        app.history.revise(SESSION, original["message_id"], sender="qq:100", identity="withdraw")
        assert app.history.repository.window(SESSION) is None
    for i in range(100):
        receive(app, "bounded" + str(i), f"有界记录{i:03d}：" + "完整内容" * 500)
        if i == 95:
            app.history.tool_start(SESSION, "bounded-tool", "synthetic", {"query": "整块参数"})
            app.history.tool_end(SESSION, "bounded-tool", "整块结果")
    await app.history.contexts(SESSION, 1500)
    checkpoint = app.history.repository.window(SESSION)
    app.history.model = AsyncMock(return_value="有界摘要")
    await app.history._summarize_pending()
    prompt, system = app.history.model.await_args.args
    assert app.history.size({"role": "user", "content": prompt}) + app.history.size(
        {"role": "system", "content": system}) <= app.history.summary_input_limit(checkpoint)
    assert "有界记录000：" not in prompt and "有界记录098：" in prompt
    assert "未包含的更早记录" in prompt
    assert "整块参数" in prompt and "整块结果" in prompt
    assert len(app.history.read_checkpoint(SESSION, checkpoint["id"])["originals"]) > 100


async def test_oversized_tool_block_is_projected_and_stays_retrievable(app):
    app.history.spawn = None
    app.history.tool_start(SESSION, "huge-tool", "synthetic", {"query": "超大块" * 20000})
    app.history.tool_end(SESSION, "huge-tool", "超大块结果")
    for i in range(15):
        receive(app, "recent" + str(i), f"最近完整记录{i}：" + "细节" * 40)
    await app.history.contexts(SESSION, 1500)
    checkpoint = app.history.repository.window(SESSION)
    app.history.model = AsyncMock(return_value="最近摘要")
    await app.history._summarize_pending()
    prompt = app.history.model.await_args.args[0]
    assert "超大块" * 20000 not in prompt and "最近完整记录0：" in prompt
    assert "record_ref" in prompt
    assert app.history.read_checkpoint(SESSION, checkpoint["id"])["originals"]
