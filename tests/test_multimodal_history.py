import asyncio
import base64
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from shrink.application import Application
from shrink.adapters.chat_images import ChatImages
from shrink.services.history import ConversationHistory
from tests.test_continuous_history import receive

SESSION = "bot:GroupMessage:1"


@pytest.fixture
async def image_app(tmp_path):
    app = Application(tmp_path, AsyncMock(return_value="已有讨论及图片引用"), AsyncMock(), AsyncMock())
    yield app
    await app.close()


def png(color="red"):
    output = io.BytesIO()
    Image.new("RGB", (8, 12), color).save(output, format="PNG")
    return output.getvalue()


async def picture(app, identity="picture", session=SESSION, colors=("red", "blue")):
    row, _ = receive(app, identity, text="", session=session)
    with app.database.transaction() as con:
        con.execute("UPDATE messages SET has_image=1 WHERE id=?", (row["message_id"],))
    turn = SimpleNamespace(id=row["message_id"], source="qq:group:1")
    components = [SimpleNamespace(type="Plain", text="看第一张")]
    for color in colors:
        components += [SimpleNamespace(type="Image", url="base64://" + base64.b64encode(png(color)).decode()),
                       SimpleNamespace(type="Plain", text="然后")]
    app.attachments.capture(turn, components)
    await app.attachments.wait([r["id"] for r in app.attachments.for_message(turn.id)])
    return turn, components


async def test_separate_image_then_text_survives_other_member_and_restart(image_app):
    app = image_app
    turn, components = await picture(app)
    rows = app.attachments.for_message(turn.id)
    assert len(rows) == 2 and all(r["status"] == "saved" for r in rows)
    assert [r["position"] for r in rows] == [1, 3]
    app.attachments.capture(turn, components)
    assert app.attachments.for_message(turn.id) == rows
    receive(app, "other", "插话", sender="200")
    receive(app, "question", "对方想干什么")
    contexts = await app.history.contexts(SESSION, 10000)
    assert [p["type"] for p in contexts[0]["content"]] == ["text", "text", "image_url", "text", "image_url", "text"]
    assert "qq:200" in contexts[1]["content"]
    resolved = await ChatImages(app.attachments, SESSION).resolve(contexts)
    assert sum(p.get("type") == "image_url" for p in resolved[0]["content"]) == 2
    assert "data:image/png;base64," in json.dumps(resolved)
    assert "data:image/" not in json.dumps(app.database.rows("SELECT * FROM conversation_events"))
    restarted = Application(app.database.root, AsyncMock(), AsyncMock(), AsyncMock())
    try:
        assert await restarted.history.contexts(SESSION, 10000) == contexts
        again = await ChatImages(restarted.attachments, SESSION).resolve(contexts)
        assert again == resolved
    finally:
        await restarted.close()


async def test_image_budget_and_compression_keep_references_without_bytes(image_app):
    app = image_app
    turn, _ = await picture(app)
    contexts = await app.history.contexts(SESSION, 10000)
    value = contexts[0]
    without = {**value, "content": [p for p in value["content"] if p["type"] != "image_url"]}
    assert ConversationHistory.size(value) == ConversationHistory.size(without) + 2 * 765
    resolved = await ChatImages(app.attachments, SESSION).resolve(contexts)
    assert ConversationHistory.size(resolved[0]) == ConversationHistory.size(value)
    for i in range(10):
        receive(app, f"later{i}", "后续文字" * 50)
    app.history.spawn = None
    app.history.model = AsyncMock(return_value="已有讨论及原图引用")
    await app.history.contexts(SESSION, 1900)
    checkpoint = app.history.repository.window(SESSION)
    assert checkpoint
    await app.history.summarize_pending()
    prompt = app.history.model.call_args.args[0]
    originals = app.history.read_checkpoint(SESSION, checkpoint["id"])["originals"]
    original = next(r for r in originals if r["id"] == turn.id)
    assert len(original["images"]) == 2
    view = ConversationHistory.compression_view(value)
    assert "attachment:" in str(view) and "未解读" in str(view)
    assert "data:image/" not in prompt and "attachment:" in prompt


async def test_scope_revocation_missing_and_unsupported_are_explicit(image_app):
    app = image_app
    turn, _ = await picture(app, colors=("red",))
    contexts = await app.history.contexts(SESSION, 10000)
    other = await ChatImages(app.attachments, "other:GroupMessage:1").resolve(contexts)
    assert "不属于当前会话" in str(other) and "data:image/" not in str(other)
    unsupported = await ChatImages(app.attachments, SESSION).resolve(contexts, modalities=["text"])
    assert "不支持图片" in str(unsupported)
    row = app.attachments.for_message(turn.id)[0]
    _, path = app.attachments.file(row["id"])
    path.unlink()
    missing = await ChatImages(app.attachments, SESSION).resolve(contexts)
    assert "副本不存在" in str(missing)
    app.history.revise(SESSION, turn.id, sender="qq:100", identity="remove")
    revoked = await ChatImages(app.attachments, SESSION).resolve(contexts)
    assert "已撤回或纠正" in str(revoked)
    assert "image_url" not in str(await app.history.contexts(SESSION, 10000))


@pytest.mark.parametrize("pixel_limit", [90, 40])
async def test_oversized_image_keeps_reference_and_other_content(image_app, monkeypatch, pixel_limit):
    app = image_app
    turn, _ = await picture(app)
    oversized, normal = app.attachments.for_message(turn.id)
    _, normal_path = app.attachments.file(normal["id"])
    normal_bytes = io.BytesIO()
    Image.new("RGB", (4, 4)).save(normal_bytes, format="PNG")
    normal_path.write_bytes(normal_bytes.getvalue())
    contexts = await app.history.contexts(SESSION, 10000)
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", pixel_limit)

    resolver = ChatImages(app.attachments, SESSION)
    resolved = await resolver.resolve(contexts)
    parts = resolved[0]["content"]
    assert "看第一张" in [part.get("text") for part in parts]
    assert f"图片像素超过安全上限；原图引用 attachment:{oversized['id']}" in str(parts)
    assert [part["image_url"]["id"] for part in parts if part["type"] == "image_url"] == [
        "attachment:" + normal["id"]
    ]
    assert resolver.counts["failed"] == resolver.counts["loaded"] == 1
    assert resolver.compact(resolved) == contexts


async def test_pending_wait_does_not_cancel_or_duplicate_download(image_app):
    app = image_app
    row, _ = receive(app, "pending", text="")
    started, release = asyncio.Event(), asyncio.Event()
    original = app.attachments.download
    async def slow(locator, target):
        started.set()
        await release.wait()
        return await original(locator, target)
    app.attachments.download = AsyncMock(side_effect=slow)
    turn = SimpleNamespace(id=row["message_id"], source="qq:group:1")
    parts = [SimpleNamespace(type="Image", url="base64://" + base64.b64encode(png()).decode())]
    app.attachments.capture(turn, parts)
    await started.wait()
    assert "base64://" not in str(app.database.rows("SELECT * FROM chat_attachments"))
    identity = app.attachments.for_message(turn.id)[0]["id"]
    task = app.attachments.downloads[identity]
    await app.attachments.wait([identity], timeout=.01)
    assert not task.done()
    app.attachments.capture(turn, parts)
    assert app.attachments.downloads[identity] is task
    real_wait = app.attachments.wait
    app.attachments.wait = AsyncMock()
    pending = await ChatImages(app.attachments, SESSION).resolve(await app.history.contexts(SESSION, 10000))
    assert "图片保存中" in str(pending)
    app.attachments.wait = real_wait
    release.set()
    await task
    assert app.attachments.download.call_count == 1


async def test_tool_image_replay_keeps_parallel_calls_adjacent(image_app):
    app = image_app
    turn, _ = await picture(app, colors=("red",))
    ref = "attachment:" + app.attachments.for_message(turn.id)[0]["id"]
    calls = [{"id": i, "type": "function", "function": {"name": "history", "arguments": "{}"}}
             for i in ("a", "b")]
    app.history.repository.append(SESSION, "parallel", "assistant", tool_calls=calls)
    app.history.tool_end(SESSION, "a", "第一张", image_refs=[ref])
    app.history.tool_end(SESSION, "b", "第二个结果")
    contexts = await app.history.contexts(SESSION, 10000)
    index = next(i for i, m in enumerate(contexts) if m.get("tool_calls"))
    assert [m.get("tool_call_id") for m in contexts[index + 1:index + 3]] == ["a", "b"]
    assert contexts[index + 3]["content"][-1]["image_url"]["url"] == ref


async def test_history_lists_pure_images_and_preserves_session_scope(image_app):
    app = image_app
    turn, _ = await picture(app)
    text = app.tools.history("qq:group:1", session=SESSION)
    assert turn.id in text and "图片2张" in text and "saved" in text
    assert turn.id not in app.tools.history("qq:group:1", session="other:GroupMessage:1")


async def test_identical_images_keep_positions_and_download_failure_is_distinct(image_app):
    app = image_app
    turn, _ = await picture(app, colors=("red", "red"))
    rows = app.attachments.for_message(turn.id)
    assert len(rows) == 2 and all(r["status"] == "saved" for r in rows)
    assert all(app.attachments.get(r["id"])["download_source"] == "" for r in rows)
    resolver = ChatImages(app.attachments, SESSION)
    contexts = await app.history.contexts(SESSION, 10000)
    assert resolver.compact(await resolver.resolve(contexts)) == contexts
    with app.database.transaction() as con:
        con.execute("UPDATE chat_attachments SET status='failed' WHERE id=?", (rows[0]["id"],))
    resolver = ChatImages(app.attachments, SESSION)
    failed = await resolver.resolve(await app.history.contexts(SESSION, 10000))
    assert "图片下载失败" in str(failed)
    compact = resolver.compact(failed)
    assert sum(p["type"] == "image_url" for p in compact[0]["content"]) == 2


async def test_legacy_images_migrate_in_stable_order_without_downloading(tmp_path):
    import sqlite3
    database = sqlite3.connect(tmp_path / "shrink.db")
    database.execute("""CREATE TABLE chat_attachments(
        id TEXT PRIMARY KEY,message_id TEXT NOT NULL,source TEXT NOT NULL,
        name TEXT NOT NULL,kind TEXT NOT NULL,locator TEXT NOT NULL,path TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL,bytes INTEGER NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT '',
        created REAL NOT NULL,UNIQUE(message_id,locator))""")
    database.executemany("INSERT INTO chat_attachments VALUES(?,?,'qq:group:1','old.png','image',?,"
                         "'original.png','saved',1,'',1)", [
                             ("b", "legacy", "old:b"), ("a", "legacy", "old:a")])
    database.commit()
    database.close()
    root = tmp_path / "attachments"
    root.mkdir()
    (root / "original.png").write_bytes(png())
    app = Application(tmp_path, AsyncMock(), AsyncMock(), AsyncMock())
    try:
        app.history.repository.append(SESSION, "legacy", "user", "legacy", speaker="qq:100")
        app.messages.raw("legacy", "qq:group:1", "", sender="qq:100")
        with app.database.transaction() as con:
            con.execute("UPDATE messages SET has_image=1 WHERE id='legacy'")
        app.attachments.download = AsyncMock(side_effect=AssertionError("must not redownload"))
        app.attachments.resume()
        rows = app.attachments.for_message("legacy")
        assert [row["id"] for row in rows] == ["a", "b"]
        assert [row["position"] for row in rows] == [0, 1]
        contexts = await app.history.contexts(SESSION, 10000)
        assert "attachment:a" in str(contexts) and "attachment:b" in str(contexts)
        loaded = await ChatImages(app.attachments, SESSION).resolve(contexts)
        assert sum(p["type"] == "image_url" for p in loaded[0]["content"]) == 2
        app.attachments.download.assert_not_called()
    finally:
        await app.close()


async def test_pending_image_restarts_from_staged_original(tmp_path):
    app = Application(tmp_path, AsyncMock(), AsyncMock(), AsyncMock())
    release = asyncio.Event()
    original_download = app.attachments.download
    async def stalled(*args):
        await release.wait()
        return await original_download(*args)
    app.attachments.download = stalled
    row, _ = receive(app, "pending-restart", text="")
    turn = SimpleNamespace(id=row["message_id"], source="qq:group:1")
    app.attachments.capture(turn, [SimpleNamespace(type="Image", url="base64://" + base64.b64encode(png()).decode())])
    await app.close()
    restarted = Application(tmp_path, AsyncMock(), AsyncMock(), AsyncMock())
    try:
        restarted.attachments.resume()
        rows = restarted.attachments.for_message(turn.id)
        await restarted.attachments.wait([a["id"] for a in rows])
        loaded = await ChatImages(restarted.attachments, SESSION).resolve(
            await restarted.history.contexts(SESSION, 10000))
        assert "data:image/png;base64," in str(loaded)
        assert not list(restarted.attachments.root.glob("*.incoming"))
    finally:
        await restarted.close()
