"""Task facts, attempts and atomic compare-and-set storage."""
import json
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager


class Tasks:
    MIGRATION = "operation_task_states_v2"

    def __init__(self, database):
        self.db = database
        self._depth = 0
        self.needs_migration = not database.rows("SELECT value FROM kv WHERE key=?", (self.MIGRATION,))
        existing = database.rows("SELECT name FROM sqlite_master WHERE name='operation_tasks'")
        if existing and self.needs_migration:
            backup = database.root / "shrink.before-task-states-v2.db"
            if not backup.exists():
                # SQLite backup includes committed WAL pages.
                temporary = backup.with_suffix(".db.tmp")
                with closing(sqlite3.connect(temporary)) as destination:
                    database.connection.backup(destination)
                temporary.replace(backup)
        database.connection.executescript("""
        CREATE TABLE IF NOT EXISTS operation_tasks(
          id TEXT PRIMARY KEY, request TEXT NOT NULL, session TEXT NOT NULL,
          sender TEXT NOT NULL, status TEXT NOT NULL, started REAL NOT NULL,
          finished REAL, result TEXT NOT NULL DEFAULT '', failures INTEGER NOT NULL DEFAULT 0,
          provider TEXT NOT NULL, parent TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS operation_events(
          id INTEGER PRIMARY KEY, task_id TEXT NOT NULL, action TEXT NOT NULL,
          plugin TEXT NOT NULL DEFAULT '', path TEXT NOT NULL DEFAULT '',
          result TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS task_attempts(
          id TEXT PRIMARY KEY, task_id TEXT NOT NULL, executor TEXT NOT NULL,
          session_ref TEXT NOT NULL, checkpoint_ref TEXT NOT NULL,
          started REAL, finished REAL, status TEXT NOT NULL, outcome TEXT NOT NULL DEFAULT '',
          error_class TEXT NOT NULL DEFAULT '', retry_ordinal INTEGER NOT NULL DEFAULT 0,
          step INTEGER NOT NULL DEFAULT 0);
        CREATE INDEX IF NOT EXISTS task_attempts_task ON task_attempts(task_id, started);
        CREATE TABLE IF NOT EXISTS task_delivery(
          task_id TEXT PRIMARY KEY, state TEXT NOT NULL DEFAULT '');
        """)
        columns = {r["name"] for r in database.rows("PRAGMA table_info(operation_tasks)")}
        fields = {
            "kind": "TEXT NOT NULL DEFAULT 'development'",
            "agent": "TEXT NOT NULL DEFAULT 'shirnk_developer'",
            "dependencies": "TEXT NOT NULL DEFAULT '[]'",
            "next_run": "REAL",
            "retry_count": "INTEGER NOT NULL DEFAULT 0",
            "step": "INTEGER NOT NULL DEFAULT 0",
            "phase": "TEXT NOT NULL DEFAULT 'execute'",
            "wait_reason": "TEXT NOT NULL DEFAULT ''",
            "stop_requested": "INTEGER NOT NULL DEFAULT 0",
        }
        with database.transaction() as con:
            for name, declaration in fields.items():
                if name not in columns:
                    con.execute(f"ALTER TABLE operation_tasks ADD COLUMN {name} {declaration}")
            if "delivery" in columns:
                con.execute("INSERT OR IGNORE INTO task_delivery SELECT id,delivery FROM operation_tasks")
                con.execute("ALTER TABLE operation_tasks DROP COLUMN delivery")
            if "attempt_id" not in {r["name"] for r in database.rows("PRAGMA table_info(operation_events)")}:
                con.execute("ALTER TABLE operation_events ADD COLUMN attempt_id TEXT NOT NULL DEFAULT ''")

    @contextmanager
    def transaction(self):
        outer = self._depth == 0
        if outer:
            self.db.connection.execute("BEGIN IMMEDIATE")
        self._depth += 1
        try:
            yield self.db.connection
            if outer:
                self.db.connection.commit()
        except BaseException:
            if outer:
                self.db.connection.rollback()
            raise
        finally:
            self._depth -= 1

    def task(self, task_id):
        rows = self.db.rows(
            "SELECT t.*,COALESCE(d.state,'') delivery FROM operation_tasks t "
            "LEFT JOIN task_delivery d ON d.task_id=t.id WHERE t.id=?", (task_id,))
        if not rows:
            raise ValueError("任务不存在")
        return rows[0]

    def list(self):
        return self.db.rows("SELECT t.*,COALESCE(d.state,'') delivery FROM operation_tasks t "
                            "LEFT JOIN task_delivery d ON d.task_id=t.id ORDER BY t.started DESC")

    def create(self, request, session, sender, provider, *, kind="agent", agent="", parent="", dependencies=()):
        task_id = uuid.uuid4().hex
        with self.transaction() as con:
            con.execute("INSERT INTO operation_tasks(id,request,session,sender,provider,kind,agent,"
                        "parent,dependencies,status,started) VALUES(?,?,?,?,?,?,?,?,?,'queued',?)",
                        (task_id, request, session, sender, provider, kind, agent, parent,
                         json.dumps(dependencies), time.time()))
        return task_id

    def _transition(self, task_id, previous, state, fields):
        with self.transaction() as con:
            changed = con.execute("UPDATE operation_tasks SET status=?" +
                                  "".join(f",{key}=?" for key in fields) + " WHERE id=? AND status=?",
                                  (state, *fields.values(), task_id, previous)).rowcount
            if not changed:
                raise ValueError("任务状态已改变")

    def update(self, task_id, **fields):
        if set(fields) - {"result", "failures", "phase", "retry_count", "step"}:
            raise ValueError("无效任务字段；状态只能通过 transition 修改")
        with self.transaction() as con:
            con.execute("UPDATE operation_tasks SET " + ",".join(f"{key}=?" for key in fields) +
                        " WHERE id=?", (*fields.values(), task_id))

    def events(self, task_id):
        return self.db.rows("SELECT * FROM operation_events WHERE task_id=? ORDER BY id", (task_id,))

    def record(self, task_id, action, plugin="", path="", result=""):
        with self.transaction() as con:
            attempt = con.execute("SELECT id FROM task_attempts WHERE task_id=? "
                                  "ORDER BY rowid DESC LIMIT 1", (task_id,)).fetchone()
            cursor = con.execute("INSERT INTO operation_events(task_id,action,plugin,path,result,created,attempt_id)"
                                 " VALUES(?,?,?,?,?,?,?)",
                                 (task_id, action, plugin, path, str(result)[:40000], time.time(),
                                  attempt[0] if attempt else ""))
            if con.execute("SELECT 1 FROM sqlite_master WHERE name='input_events'").fetchone():
                from .inbox import input_id
                task = con.execute("SELECT session,sender FROM operation_tasks WHERE id=?", (task_id,)).fetchone()
                if task:
                    external = f"task-event:{cursor.lastrowid}"
                    identity = input_id(task["session"], task["sender"], external, "task")
                    now = time.time()
                    con.execute("INSERT INTO messages(id,user_id,source,text,learnable,learned,created)"
                                " VALUES(?,?,?,?,0,1,?)",
                                (external, task["sender"], task["session"],
                                 json.dumps({"action": action, "result": result}, ensure_ascii=False), now))
                    con.execute("INSERT INTO input_events"
                                "(id,external_id,source,sender,session,message_id,route,received,claimed,processed)"
                                " VALUES(?,?,?,?,?,?,'task_event',?,?,?)",
                                (identity, external, "task", task["sender"], task["session"], external, now, now, now))

    def attempts(self, task_id):
        return self.db.rows("SELECT * FROM task_attempts WHERE task_id=? ORDER BY rowid", (task_id,))

    def begin_attempt(self, task, executor, session_ref):
        identity = uuid.uuid4().hex
        with self.transaction() as con:
            con.execute("INSERT INTO task_attempts(id,task_id,executor,session_ref,checkpoint_ref,"
                        "started,status,retry_ordinal,step) VALUES(?,?,?,?,?,?,'running',?,?)",
                        (identity, task["id"], executor, session_ref, task["id"], time.time(),
                         task["retry_count"], task["step"]))
        return identity

    def end_attempt(self, task_id, status, outcome="", error_class=""):
        with self.transaction() as con:
            con.execute("UPDATE task_attempts SET status=?,outcome=?,error_class=?,finished=? "
                        "WHERE task_id=? AND finished IS NULL",
                        (status, outcome, error_class, time.time(), task_id))

    def migration_done(self):
        with self.transaction() as con:
            con.execute("INSERT OR IGNORE INTO kv VALUES(?, 'true')", (self.MIGRATION,))
        self.needs_migration = False
