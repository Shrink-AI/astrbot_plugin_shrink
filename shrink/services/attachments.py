"""Keep received chat images and stickers before platform links expire."""
import asyncio
import base64
import mimetypes
import re
import shutil
import time
import uuid
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx

MAX_FILE = 64 * 1024 * 1024


class Attachments:
    def __init__(self, app):
        self.app, self.db = app, app.database
        self.root = app.state.root / "attachments"
        self.root.mkdir(exist_ok=True)
        self.slots = asyncio.Semaphore(2)
        self.db.connection.execute("""
        CREATE TABLE IF NOT EXISTS chat_attachments(
          id TEXT PRIMARY KEY,message_id TEXT NOT NULL,source TEXT NOT NULL,
          name TEXT NOT NULL,kind TEXT NOT NULL,locator TEXT NOT NULL,path TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL,bytes INTEGER NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT '',
          created REAL NOT NULL,UNIQUE(message_id,locator))
        """)
        self.db.connection.commit()

    def capture(self, turn, components):
        for component in components:
            kind = str(getattr(component, "type", "")).split(".")[-1].lower()
            if kind != "image":
                continue
            locator = str(getattr(component, "url", "") or
                          getattr(component, "file_", "") or getattr(component, "file", "") or "")
            if not locator:
                continue
            name = str(getattr(component, "name", "") or
                       {"image": "图片", "record": "语音", "video": "视频"}.get(kind, "附件"))
            name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name)[:160]
            attachment_id = uuid.uuid4().hex
            with self.db.transaction() as con:
                cursor = con.execute(
                    "INSERT OR IGNORE INTO chat_attachments"
                    "(id,message_id,source,name,kind,locator,status,created) VALUES(?,?,?,?,?,?,'pending',?)",
                    (attachment_id, turn.id, turn.source, name, kind, locator, time.time()))
            if cursor.rowcount:
                self.app.tasks.spawn(self.save(attachment_id))

    def resume(self):
        for row in self.db.rows("SELECT id FROM chat_attachments WHERE kind='image' AND status IN ('pending','saving')"):
            self.app.tasks.spawn(self.save(row["id"]))

    async def save(self, attachment_id):
        async with self.slots:
            row = self.get(attachment_id)
            if row["kind"] != "image":
                return
            if row["status"] == "saved":
                return
            target = self.root / (attachment_id + ".part")
            error = ""
            for attempt in range(3):
                try:
                    with self.db.transaction() as con:
                        con.execute("UPDATE chat_attachments SET status='saving' WHERE id=?", (attachment_id,))
                    mime = await self.download(row["locator"], target)
                    suffix = Path(row["name"]).suffix[:12] or mimetypes.guess_extension(mime) or ".bin"
                    final = self.root / (attachment_id + suffix)
                    target.replace(final)
                    name = row["name"] if Path(row["name"]).suffix else row["name"] + suffix
                    with self.db.transaction() as con:
                        con.execute("UPDATE chat_attachments SET status='saved',path=?,name=?,bytes=?,"
                                    "error='',locator='' WHERE id=?",
                                    (final.name, name, final.stat().st_size, attachment_id))
                    return
                except asyncio.CancelledError:
                    target.unlink(missing_ok=True)
                    raise
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"[:500]
                    target.unlink(missing_ok=True)
                    if attempt < 2:
                        await asyncio.sleep((1, 5)[attempt])
            with self.db.transaction() as con:
                con.execute("UPDATE chat_attachments SET status='failed',error=? WHERE id=?",
                            (error, attachment_id))

    async def download(self, locator, target):
        if locator.startswith(("http://", "https://")):
            async with httpx.AsyncClient(timeout=45, follow_redirects=True, trust_env=False) as client:
                async with client.stream("GET", locator) as response:
                    response.raise_for_status()
                    if int(response.headers.get("content-length", 0)) > MAX_FILE:
                        raise ValueError("附件超过64 MiB，未自动保存")
                    size = 0
                    with target.open("wb") as output:
                        async for data in response.aiter_bytes(65536):
                            size += len(data)
                            if size > MAX_FILE:
                                raise ValueError("附件超过64 MiB，未自动保存")
                            output.write(data)
                    return response.headers.get("content-type", "").split(";")[0]
        if locator.startswith("base64://"):
            if len(locator) > MAX_FILE * 4 // 3 + 16:
                raise ValueError("附件超过64 MiB，未自动保存")
            target.write_bytes(base64.b64decode(locator[9:], validate=True))
            return "application/octet-stream"
        local = Path(unquote(urlsplit(locator).path) if locator.startswith("file://") else locator)
        if not local.is_file():
            raise ValueError("平台未提供可用的附件路径")
        if local.stat().st_size > MAX_FILE:
            raise ValueError("附件超过64 MiB，未自动保存")
        await asyncio.to_thread(shutil.copyfile, local, target)
        return mimetypes.guess_type(local.name)[0] or "application/octet-stream"

    def get(self, attachment_id, source=None):
        rows = self.db.rows("SELECT * FROM chat_attachments WHERE id=?", (attachment_id,))
        if not rows or (source is not None and rows[0]["source"] != source):
            raise ValueError("当前会话找不到这个附件")
        return rows[0]

    def file(self, attachment_id, source=None):
        row = self.get(attachment_id, source)
        if row["status"] != "saved":
            raise ValueError("附件尚未保存：" + (row["error"] or row["status"]))
        path = (self.root / row["path"]).resolve()
        if not path.is_relative_to(self.root.resolve()) or not path.is_file():
            raise ValueError("附件副本不存在")
        return row, path

    def list(self, source, keyword=""):
        return self.db.rows(
            "SELECT id,message_id,source,name,kind,status,bytes,error,created FROM chat_attachments "
            "WHERE source=? AND name LIKE ? ORDER BY created DESC LIMIT 100",
            (source, f"%{keyword[:100]}%"))
