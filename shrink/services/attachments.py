"""Keep received chat images and stickers before platform links expire."""
import asyncio
import base64
import mimetypes
import json
import re
import shutil
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

import httpx

MAX_FILE = 64 * 1024 * 1024


class Attachments:
    def __init__(self, app):
        self.app, self.db = app, app.database
        self.root = app.state.root / "attachments"
        self.root.mkdir(exist_ok=True)
        self.slots = app.scheduling.attachments
        self.downloads = {}
        self.db.connection.execute("""
        CREATE TABLE IF NOT EXISTS chat_attachments(
          id TEXT PRIMARY KEY,message_id TEXT NOT NULL,source TEXT NOT NULL,
          name TEXT NOT NULL,kind TEXT NOT NULL,locator TEXT NOT NULL,path TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL,bytes INTEGER NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT '',
          created REAL NOT NULL,UNIQUE(message_id,locator))
        """)
        self.db.connection.commit()
        with self.db.transaction() as con:
            columns = {r["name"] for r in con.execute("PRAGMA table_info(chat_attachments)")}
            if "position" not in columns:
                con.execute("ALTER TABLE chat_attachments ADD COLUMN position INTEGER NOT NULL DEFAULT 0")
                positions = {}
                for row in con.execute("SELECT id,message_id FROM chat_attachments ORDER BY created,id").fetchall():
                    position = positions.get(row["message_id"], 0)
                    con.execute("UPDATE chat_attachments SET position=? WHERE id=?", (position, row["id"]))
                    positions[row["message_id"]] = position + 1
            if "download_source" not in columns:
                con.execute("ALTER TABLE chat_attachments ADD COLUMN download_source TEXT NOT NULL DEFAULT ''")
                con.execute("UPDATE chat_attachments SET download_source=locator WHERE status!='saved'")
                con.execute("UPDATE chat_attachments SET locator='attachment:' || id")
                for row in con.execute("SELECT id,download_source FROM chat_attachments "
                                       "WHERE download_source LIKE 'base64://%'").fetchall():
                    source, status, error = self.stage_source(row["id"], row["download_source"])
                    con.execute("UPDATE chat_attachments SET download_source=?,status=?,error=? WHERE id=?",
                                (source, status, error, row["id"]))
            con.execute("CREATE INDEX IF NOT EXISTS attachments_message ON chat_attachments(message_id,position)")

    def capture(self, turn, components):
        parts = []
        for position, component in enumerate(components):
            kind = str(getattr(component, "type", "")).split(".")[-1].lower()
            if kind == "plain":
                parts.append({"type": "text", "text": str(getattr(component, "text", ""))})
            if kind != "image":
                continue
            locator = str(getattr(component, "url", "") or
                          getattr(component, "file_", "") or getattr(component, "file", "") or "")
            if not locator:
                continue
            name = str(getattr(component, "name", "") or
                       {"image": "图片", "record": "语音", "video": "视频"}.get(kind, "附件"))
            name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name)[:160]
            existing = self.db.rows("SELECT id FROM chat_attachments WHERE message_id=? AND position=?",
                                    (turn.id, position))
            attachment_id = existing[0]["id"] if existing else uuid.uuid4().hex
            source, status, error = self.stage_source(attachment_id, locator) if not existing else ("", "pending", "")
            with self.db.transaction() as con:
                cursor = con.execute(
                    "INSERT OR IGNORE INTO chat_attachments"
                    "(id,message_id,source,name,kind,locator,status,created,position,download_source,error) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (attachment_id, turn.id, turn.source, name, kind, "attachment:" + attachment_id,
                     status, time.time(), position, source, error))
            if cursor.rowcount and status == "pending":
                self.schedule(attachment_id)
            parts.append(self.part(attachment_id))
        if any(p["type"] == "image_url" for p in parts):
            self.app.history.repository.set_parts(turn.id, parts)

    @staticmethod
    def part(identity):
        return {"type": "image_url", "image_url": {"url": "attachment:" + identity}}

    def stage_source(self, identity, locator):
        if not locator.startswith("base64://"):
            return locator, "pending", ""
        path = self.root / (identity + ".incoming")
        try:
            if len(locator) > MAX_FILE * 4 // 3 + 16:
                raise ValueError("附件超过64 MiB")
            self.write_base64(locator[9:], path)
            return str(path.resolve()), "pending", ""
        except (ValueError, OSError):
            path.unlink(missing_ok=True)
            return "", "failed", "内联图片保存失败"

    def schedule(self, identity):
        task = self.downloads.get(identity)
        if task is None or task.done():
            task = self.app.tasks.spawn(self.save(identity))
            self.downloads[identity] = task
            task.add_done_callback(lambda finished: self.downloads.pop(identity, None))
        return task

    def resume(self):
        for row in self.db.rows("SELECT id FROM chat_attachments WHERE kind='image' AND status IN ('pending','saving')"):
            self.schedule(row["id"])

    async def wait(self, identities, timeout=5):
        pending = [self.downloads[i] for i in set(identities) if i in self.downloads]
        if pending:
            await asyncio.wait(pending, timeout=timeout)

    def for_message(self, message_id):
        return self.db.rows("SELECT id,message_id,position,status FROM chat_attachments "
                            "WHERE message_id=? ORDER BY position,created,id", (message_id,))

    def authorize(self, identity, session):
        row = self.get(identity)
        matches = self.db.rows("SELECT 1 FROM conversation_events WHERE session=? AND message_id=?",
                               (session, row["message_id"]))
        if not matches:
            raise ValueError("图片不属于当前会话")
        if self.app.history.repository.revised(row["message_id"]):
            raise ValueError("原图片消息已撤回或纠正")
        return row

    def message_images(self, message_id, session):
        messages = self.db.rows("SELECT session FROM conversation_events WHERE message_id=?", (message_id,))
        if not messages:
            raise ValueError("消息不存在")
        if not any(row["session"] == session for row in messages):
            raise ValueError("消息不属于当前会话")
        if self.app.history.repository.revised(message_id):
            raise ValueError("原图片消息已撤回或纠正")
        return self.for_message(message_id)

    def parts_for(self, row):
        parts = json.loads(row.get("content_parts") or "[]")
        if parts:
            return parts
        return [self.part(a["id"]) for a in self.for_message(row["message_id"])] if (
            row["message_id"] and row.get("has_image")) else []

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
                    mime = await self.download(row["download_source"], target)
                    suffix = Path(row["name"]).suffix[:12] or mimetypes.guess_extension(mime) or ".bin"
                    final = self.root / (attachment_id + suffix)
                    target.replace(final)
                    name = row["name"] if Path(row["name"]).suffix else row["name"] + suffix
                    with self.db.transaction() as con:
                        con.execute("UPDATE chat_attachments SET status='saved',path=?,name=?,bytes=?,"
                                    "error='',download_source='' WHERE id=?",
                                    (final.name, name, final.stat().st_size, attachment_id))
                    incoming = self.root / (attachment_id + ".incoming")
                    if row["download_source"] == str(incoming.resolve()):
                        incoming.unlink(missing_ok=True)
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
                            await self.app.scheduling.compute(output.write, data)
                    return response.headers.get("content-type", "").split(";")[0]
        if locator.startswith("base64://"):
            if len(locator) > MAX_FILE * 4 // 3 + 16:
                raise ValueError("附件超过64 MiB，未自动保存")
            await self.app.scheduling.compute(self.write_base64, locator[9:], target)
            return "application/octet-stream"
        local = Path(url2pathname(urlsplit(locator).path) if locator.startswith("file://") else locator)
        if not local.is_file():
            raise ValueError("平台未提供可用的附件路径")
        if local.stat().st_size > MAX_FILE:
            raise ValueError("附件超过64 MiB，未自动保存")
        await self.app.scheduling.compute(shutil.copyfile, local, target)
        return mimetypes.guess_type(local.name)[0] or "application/octet-stream"

    @staticmethod
    def write_base64(encoded, target):
        target.write_bytes(base64.b64decode(encoded, validate=True))

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
