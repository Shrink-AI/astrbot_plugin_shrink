import json
import time


class Clarifications:
    def __init__(self, database):
        self.db = database
        with self.db.transaction() as con:
            con.execute("CREATE TABLE IF NOT EXISTS task_clarifications("
                        "task_id TEXT PRIMARY KEY,question TEXT NOT NULL,context TEXT NOT NULL,"
                        "options TEXT NOT NULL,answer TEXT NOT NULL DEFAULT '',created REAL NOT NULL)")

    def ask(self, task_id, question, context, options):
        with self.db.transaction() as con:
            con.execute("INSERT INTO task_clarifications(task_id,question,context,options,created)"
                        " VALUES(?,?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET "
                        "question=excluded.question,context=excluded.context,options=excluded.options,"
                        "answer='',created=excluded.created",
                        (task_id, question, context, json.dumps(options, ensure_ascii=False), time.time()))

    def get(self, task_id):
        rows = self.db.rows("SELECT * FROM task_clarifications WHERE task_id=?", (task_id,))
        return rows[0] if rows else None

    def answer(self, task_id, answer):
        with self.db.transaction() as con:
            con.execute("UPDATE task_clarifications SET answer=? WHERE task_id=?", (answer, task_id))
