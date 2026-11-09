import base64
from types import SimpleNamespace

import pytest

from shrink.application import Application


async def unavailable(*args):
    raise RuntimeError("not configured")


@pytest.mark.asyncio
async def test_attachment_copy_survives_original_and_is_scoped(tmp_path):
    app = Application(tmp_path / "state", unavailable, unavailable, unavailable)
    original = tmp_path / "表情.png"
    original.write_text("保留这份原文", encoding="utf-8")
    turn = SimpleNamespace(id="message1", source="qq:private:123")
    app.attachments.capture(turn, [SimpleNamespace(type="Image", url="", file_=str(original),
                                                   name="表情.png")])
    import asyncio
    await asyncio.gather(*list(app.tasks.jobs))
    row = app.attachments.list(turn.source)[0]
    original.unlink()
    saved, path = app.attachments.file(row["id"], turn.source)
    assert path.read_text("utf-8") == "保留这份原文"
    assert saved["locator"] == "" and row["status"] == "saved"
    with pytest.raises(ValueError, match="当前会话"):
        app.attachments.file(row["id"], "qq:private:456")
    await app.close()


@pytest.mark.asyncio
async def test_attachment_names_cannot_escape_archive(tmp_path):
    app = Application(tmp_path, unavailable, unavailable, unavailable)
    turn = SimpleNamespace(id="message1", source="qq:private:123")
    app.attachments.capture(turn, [SimpleNamespace(
        type="Image", url="base64://" + base64.b64encode(b"content").decode(),
        name="../../escape.png")])
    import asyncio
    await asyncio.gather(*list(app.tasks.jobs))
    row = app.attachments.list(turn.source)[0]
    _, path = app.attachments.file(row["id"], turn.source)
    assert path.parent == app.attachments.root
    assert "/" not in row["name"]
    await app.close()


@pytest.mark.asyncio
async def test_regular_files_audio_video_are_not_archived(tmp_path):
    app = Application(tmp_path, unavailable, unavailable, unavailable)
    turn = SimpleNamespace(id="message1", source="qq:private:123")
    app.attachments.capture(turn, [SimpleNamespace(type=kind, url="https://example.test/file")
                                   for kind in ("File", "Record", "Video")])
    assert not app.attachments.list(turn.source)
    assert not app.tasks.jobs
    await app.close()
