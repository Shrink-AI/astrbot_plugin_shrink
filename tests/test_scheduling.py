import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from shrink.application import Application
from shrink.adapters.knowledge import KnowledgeBridge


@pytest.mark.asyncio
async def test_t01_all_background_quotas_held_chat_context_still_finishes(tmp_path):
    from shrink.services.scheduling import Scheduling

    scheduling = Scheduling()
    model = AsyncMock(return_value="好嘞")
    app = Application(tmp_path, model, AsyncMock(return_value=None), AsyncMock())
    entered, release = asyncio.Event(), asyncio.Event()

    async def background():
        async with scheduling.use("execution"):
            entered.set()
            await release.wait()

    workers = [asyncio.create_task(background()) for _ in range(4)]
    try:
        await entered.wait()
        # Entering the fourth is an actual semaphore wait, observed without a time assertion.
        await scheduling.queued["execution"].wait()
        bridge = KnowledgeBridge(SimpleNamespace(), app)
        await bridge.rerank_lock.acquire()
        await app.attachments.slots.acquire()
        await app.attachments.slots.acquire()
        async with scheduling.use("chat"):
            rows = [(1.0, {"text": "已通过范围筛选的记忆"})]
            assert await asyncio.wait_for(bridge.rerank("问题", rows), 1) == rows
            assert await asyncio.wait_for(app.skills.relevant("你好"), 1) == []
            result = await app.expression.finish(
                "好嘞", user_text="在吗", form="short", reason="direct", recent=[])
            assert result.text == "好嘞"
        assert not release.is_set()
    finally:
        release.set()
        await asyncio.gather(*workers)
        await app.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["model", "embedding"])
async def test_models_and_embeddings_have_independent_lanes(tmp_path, resource):
    entered, release = asyncio.Event(), asyncio.Event()

    async def model(prompt, system="", images=None):
        if prompt == "background":
            entered.set()
            await release.wait()
        return prompt

    app = Application(tmp_path, model, model, AsyncMock())
    assert hasattr(app, "scheduling")
    call = app.history.model if resource == "model" else app.memory.embed
    async def maintenance():
        async with app.scheduling.use("maintenance"):
            return await call("background")
    worker = asyncio.create_task(maintenance())
    try:
        await entered.wait()
        async with app.scheduling.use("chat"):
            assert await asyncio.wait_for(app.history.model("chat"), 1) == "chat"
            assert await asyncio.wait_for(app.memory.embed("embedding"), 1) == "embedding"
        assert not worker.done()
    finally:
        release.set()
        await worker
        await app.close()


@pytest.mark.asyncio
async def test_history_foreground_does_not_queue_behind_background_compression(tmp_path):
    from shrink.services.scheduling import Scheduling

    app = Application(tmp_path, AsyncMock(), AsyncMock(), AsyncMock())
    lock = app.history.locks.setdefault("session", asyncio.Lock())
    await lock.acquire()
    try:
        async with Scheduling().use("chat"):
            assert await asyncio.wait_for(app.history.contexts("session", 100), 1) == []
    finally:
        lock.release()
        await app.close()


@pytest.mark.asyncio
async def test_busy_definition_lock_does_not_allow_foreground_write():
    from shrink.services.scheduling import Scheduling, shared_lock
    lock = asyncio.Lock()
    await lock.acquire()
    try:
        async with Scheduling().use("chat"):
            with pytest.raises(ValueError, match="尚未执行"):
                async with shared_lock(lock):
                    pytest.fail("a foreground write bypassed the shared lock")
    finally:
        lock.release()


@pytest.mark.asyncio
async def test_background_deduplication_computes_off_loop_and_rechecks_edits(tmp_path, monkeypatch):
    import threading
    app = Application(tmp_path, AsyncMock(), AsyncMock(), AsyncMock())
    repository = app.memory_repository
    old = repository.add("impression", "qq:100", "qq:group:1", "旧内容")
    new = repository.add("impression", "qq:100", "qq:group:1", "相近内容")
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    def compute(*args):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(3)
        return [(old, new)]
    monkeypatch.setattr(app.consolidation, "duplicates", compute, raising=False)
    run = app.runs.start("consolidate")
    worker = None
    try:
        worker = asyncio.create_task(app.consolidation.deduplicate(run))
        await asyncio.wait_for(entered.wait(), 1)
        repository.edit_memory(old, "用户刚改过的内容")
        async with app.scheduling.use("chat"):
            assert await app.history.contexts("session", 100) == []
        release.set()
        assert await worker == 0
        assert repository.get(old)["status"] != "superseded"
    finally:
        release.set()
        if worker is not None:
            await worker
        await app.close()


@pytest.mark.asyncio
async def test_foreground_compute_has_threads_even_when_background_pool_is_full():
    import threading
    from shrink.services.scheduling import Scheduling
    scheduling = Scheduling()
    entered = [asyncio.Event(), asyncio.Event()]
    release = threading.Event()
    loop = asyncio.get_running_loop()
    def blocked(index):
        loop.call_soon_threadsafe(entered[index].set)
        assert release.wait(3)
    workers = [asyncio.create_task(scheduling.compute(blocked, index)) for index in range(2)]
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered)), 1)
        async with scheduling.use("chat"):
            assert await asyncio.wait_for(scheduling.compute(lambda: "上下文完成"), 1) == "上下文完成"
        assert not release.is_set()
    finally:
        release.set()
        await asyncio.gather(*workers)
        scheduling.close()
