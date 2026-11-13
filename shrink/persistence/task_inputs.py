"""Durable task inbox and the execution context containing accepted inputs."""
import time

from .execution_context import ExecutionContext


class TaskInputsRepository:
    def __init__(self, database):
        self.db = database
        with self.db.transaction() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS task_inputs(
                  id INTEGER PRIMARY KEY, message_id TEXT NOT NULL, task_id TEXT NOT NULL,
                  sender TEXT NOT NULL, session TEXT NOT NULL, text TEXT NOT NULL,
                  received REAL NOT NULL, entered REAL, processed REAL,
                  UNIQUE(task_id,session,sender,message_id));
                CREATE TABLE IF NOT EXISTS task_execution_context(
                  task_id TEXT PRIMARY KEY, messages TEXT NOT NULL);
            """)
        self.contexts = ExecutionContext(database)

    def all(self, task_id):
        return self.db.rows("SELECT * FROM task_inputs WHERE task_id=? ORDER BY id", (task_id,))

    def receive(self, task_id, session, sender, message_id, text):
        with self.db.transaction() as con:
            con.execute("INSERT OR IGNORE INTO task_inputs("
                        "message_id,task_id,sender,session,text,received) VALUES(?,?,?,?,?,?)",
                        (message_id, task_id, sender, session, text, time.time()))
        return self.db.rows("SELECT * FROM task_inputs WHERE "
                            "task_id=? AND session=? AND sender=? AND message_id=?",
                            (task_id, session, sender, message_id))[0]

    def pending(self, task_id):
        return self.db.rows("SELECT * FROM task_inputs WHERE task_id=? AND processed IS NULL "
                            "ORDER BY id", (task_id,))

    def context(self, task_id):
        return self.contexts.read(task_id)

    def has_context(self, task_id):
        return self.contexts.exists(task_id)

    def checkpoint(self, task_id, messages, *, entered=(), processed=()):
        self.contexts.save(task_id, messages, entered=entered, processed=processed)

    async def checkpoint_async(self, task_id, messages, *, entered=(), processed=()):
        await self.contexts.save_async(task_id, messages, entered=entered, processed=processed)

    def clear_context(self, task_id):
        self.contexts.clear(task_id)

    async def clear_context_async(self, task_id):
        await self.contexts.clear_async(task_id)

