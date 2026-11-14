"""Incremental execution checkpoints; image payloads remain in the host's media cache."""
import asyncio
import json
import re
import sqlite3
import threading
import time

INLINE_IMAGE = re.compile(r"data:image/[^;,\s]+;base64,[A-Za-z0-9+/=\r\n]+")


def checkpoint_value(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    if isinstance(value, dict):
        if value.get("type") in {"image", "image_url"} and (
                "data" in value or "data:image/" in str(value.get("image_url", ""))):
            return {"type": "text", "text": "[Inline image omitted; retrieve from the original media source.]"}
        return {key: checkpoint_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [checkpoint_value(item) for item in value]
    if isinstance(value, str):
        return INLINE_IMAGE.sub("[Inline image omitted]", value)
    return value


class ExecutionContext:
    def __init__(self, database):
        self.db = database
        self.cached = {}
        self.lock = threading.RLock()
        with self.db.transaction() as con:
            con.execute("CREATE TABLE IF NOT EXISTS task_context_messages("
                        "task_id TEXT NOT NULL,position INTEGER NOT NULL,message TEXT NOT NULL,"
                        "PRIMARY KEY(task_id,position))")
            # Convert the previous full-context format once, within the same transaction.
            for row in con.execute("SELECT task_id,messages FROM task_execution_context "
                                   "WHERE messages!='[]'").fetchall():
                self.write(con, row["task_id"], json.loads(row["messages"]), migrating=True)

    def exists(self, task_id):
        return bool(self.db.rows("SELECT task_id FROM task_execution_context WHERE task_id=?", (task_id,)))

    def read(self, task_id):
        if not self.exists(task_id):
            return None
        return [json.loads(row["message"]) for row in self.db.rows(
            "SELECT message FROM task_context_messages WHERE task_id=? ORDER BY position", (task_id,))]

    def write(self, con, task_id, messages, *, entered=(), processed=(), migrating=False):
        encoded = [json.dumps(checkpoint_value(message), ensure_ascii=False) for message in messages]
        previous = self.cached.get(task_id)
        if previous is None:
            previous = [row[0] for row in con.execute(
                "SELECT message FROM task_context_messages WHERE task_id=? ORDER BY position", (task_id,))]
        with con:
            con.execute("INSERT OR IGNORE INTO task_execution_context VALUES(?,'[]')", (task_id,))
            if migrating:
                con.execute("UPDATE task_execution_context SET messages='[]' WHERE task_id=?", (task_id,))
            con.executemany("INSERT INTO task_context_messages VALUES(?,?,?) "
                            "ON CONFLICT(task_id,position) DO UPDATE SET message=excluded.message",
                            [(task_id, i, value) for i, value in enumerate(encoded)
                             if i >= len(previous) or previous[i] != value])
            if len(encoded) < len(previous):
                con.execute("DELETE FROM task_context_messages WHERE task_id=? AND position>=?",
                            (task_id, len(encoded)))
            now = time.time()
            con.executemany("UPDATE task_inputs SET entered=COALESCE(entered,?) WHERE task_id=? AND id=?",
                            [(now, task_id, identity) for identity in entered])
            con.executemany("UPDATE task_inputs SET processed=COALESCE(processed,?) WHERE task_id=? AND id=?",
                            [(now, task_id, identity) for identity in processed])
            con.executemany("UPDATE input_events SET claimed=COALESCE(claimed,?) "
                            "WHERE id=(SELECT input_id FROM task_inputs WHERE task_id=? AND id=?)",
                            [(now, task_id, identity) for identity in entered])
            con.executemany("UPDATE input_events SET processed=COALESCE(processed,?) "
                            "WHERE id=(SELECT input_id FROM task_inputs WHERE task_id=? AND id=?) "
                            "AND NOT EXISTS(SELECT 1 FROM task_inputs WHERE input_id=input_events.id AND processed IS NULL)",
                            [(now, task_id, identity) for identity in processed])
        self.cached[task_id] = encoded

    def save(self, task_id, messages, **positions):
        with self.lock:
            self.write(self.db.connection, task_id, messages, **positions)

    def _save_worker(self, task_id, messages, positions):
        with self.lock:
            con = sqlite3.connect(self.db.root / "shrink.db")
            try:
                self.write(con, task_id, messages, **positions)
            finally:
                con.close()

    async def save_async(self, task_id, messages, **positions):
        work = asyncio.create_task(asyncio.to_thread(self._save_worker, task_id, messages, positions))
        try:
            await asyncio.shield(work)
        except asyncio.CancelledError:
            # Complete the atomic commit before cancellation can delete a terminal checkpoint.
            await work
            raise

    def clear(self, task_id):
        with self.lock:
            self._clear(self.db.connection, task_id)

    def _clear(self, con, task_id):
        with con:
            con.execute("DELETE FROM task_context_messages WHERE task_id=?", (task_id,))
            con.execute("DELETE FROM task_execution_context WHERE task_id=?", (task_id,))
            self.cached.pop(task_id, None)

    def _clear_worker(self, task_id):
        with self.lock:
            con = sqlite3.connect(self.db.root / "shrink.db")
            try:
                self._clear(con, task_id)
            finally:
                con.close()

    async def clear_async(self, task_id):
        work = asyncio.create_task(asyncio.to_thread(self._clear_worker, task_id))
        try:
            await asyncio.shield(work)
        except asyncio.CancelledError:
            await work
            raise
