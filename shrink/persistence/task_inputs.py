"""Durable task inbox and the execution context containing accepted inputs."""
import time

from .execution_context import ExecutionContext
from .inbox import InboxRepository, input_id


class TaskInputsRepository:
    def __init__(self, database):
        self.db = database
        self.inbox = InboxRepository(database)
        columns = {row["name"] for row in database.rows("PRAGMA table_info(task_inputs)")}
        if "text" in columns:
            from .history import HistoryRepository
            HistoryRepository(database)
            with database.transaction() as con:
                old = database.rows("SELECT * FROM task_inputs ORDER BY id")
                con.execute("ALTER TABLE task_inputs RENAME TO previous_task_inputs")
                self.create(con)
                for row in old:
                    saved = self._receive(con, row["task_id"], row["session"], row["sender"],
                                          row["message_id"], row["text"], row["id"])
                    con.execute("UPDATE input_events SET received=MIN(received,?),"
                                "claimed=COALESCE(claimed,?) WHERE id=?",
                                (row["received"], row["entered"], saved))
                    con.execute("UPDATE task_inputs SET entered=?,processed=? WHERE id=?",
                                (row["entered"], row["processed"], row["id"]))
                con.execute("UPDATE input_events SET processed=(SELECT MAX(processed) FROM task_inputs "
                            "WHERE input_id=input_events.id) WHERE route='task' AND NOT EXISTS("
                            "SELECT 1 FROM task_inputs WHERE input_id=input_events.id AND processed IS NULL)")
                con.execute("DROP TABLE previous_task_inputs")
        else:
            with database.transaction() as con:
                self.create(con)
        self.contexts = ExecutionContext(database)

    @staticmethod
    def create(con):
        con.execute("CREATE TABLE IF NOT EXISTS task_inputs("
                    "id INTEGER PRIMARY KEY,input_id TEXT NOT NULL,task_id TEXT NOT NULL,entered REAL,processed REAL,"
                    "UNIQUE(task_id,input_id))")
        con.execute("CREATE TABLE IF NOT EXISTS task_execution_context("
                    "task_id TEXT PRIMARY KEY,messages TEXT NOT NULL)")

    def _receive(self, con, task_id, session, sender, message_id, text, row_id=None):
        identity = input_id(session, sender, message_id)
        raw_id = "input:" + identity
        if not con.execute("SELECT 1 FROM input_events WHERE id=?", (identity,)).fetchone():
            con.execute("INSERT OR IGNORE INTO messages"
                        "(id,user_id,source,text,learnable,learned,created) VALUES(?,?,?,?,0,1,?)",
                        (raw_id, sender, session, text, time.time()))
            con.execute("INSERT INTO input_events"
                        "(id,external_id,source,sender,session,message_id,route,received) VALUES(?,?,?,?,?,?,?,?)",
                        (identity, message_id, "platform", sender, session, raw_id, "task", time.time()))
        con.execute("UPDATE input_events SET route='task' WHERE id=?", (identity,))
        added = con.execute("INSERT OR IGNORE INTO task_inputs(id,input_id,task_id) VALUES(?,?,?)",
                            (row_id, identity, task_id)).rowcount
        if added:
            con.execute("UPDATE input_events SET processed=NULL WHERE id=?", (identity,))
        return identity

    def rows(self, task_id, pending=False):
        return self.db.rows(
            "SELECT t.id,t.task_id,t.input_id,t.entered,i.external_id message_id,"
            "i.sender,i.session,i.received,t.processed,m.text FROM task_inputs t "
            "JOIN input_events i ON i.id=t.input_id JOIN messages m ON m.id=i.message_id "
            "WHERE t.task_id=? AND (?=0 OR t.processed IS NULL) ORDER BY t.id",
            (task_id, int(pending)))

    def all(self, task_id):
        return self.rows(task_id)

    def receive(self, task_id, session, sender, message_id, text):
        with self.db.transaction() as con:
            identity = self._receive(con, task_id, session, sender, message_id, text)
        return next(row for row in self.rows(task_id) if row["input_id"] == identity)

    def pending(self, task_id):
        return self.rows(task_id, pending=True)

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

