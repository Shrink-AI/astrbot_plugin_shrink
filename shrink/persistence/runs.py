"""Background run records and a change log that makes every run undoable."""

import json
import time

PRIMARY_KEYS = {
    "memories": "id",
    "nicknames": "id",
    "profiles": "subject",
    "expressions": "id",
    "digests": "id",
    "people": "id",
    "review_queue": "id",
    "skills": "id",
}


def record(connection, run_id, table, key, action, before=None):
    """Called by repositories inside their own transaction."""
    if run_id:
        connection.execute(
            "INSERT INTO run_changes(run_id,tbl,row_key,action,before) VALUES(?,?,?,?,?)",
            (run_id, table, str(key), action, json.dumps(before, ensure_ascii=False)),
        )


class RunRepository:
    def __init__(self, database):
        self.database = database

    def start(self, kind, source="", detail=""):
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO runs(kind,source,status,started,detail) VALUES(?,?,?,?,?)",
                (kind, source, "running", time.time(), detail),
            )
        return cursor.lastrowid

    def finish(self, run_id, status, stats, detail=""):
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE runs SET status=?,finished=?,stats=?,detail=? WHERE id=?",
                (status, time.time(), json.dumps(stats, ensure_ascii=False), detail, run_id),
            )

    def recover_interrupted(self):
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE runs SET status='interrupted',finished=? WHERE status='running'",
                (time.time(),),
            )

    @staticmethod
    def _row(row):
        row = dict(row)
        row["stats"] = json.loads(row.get("stats") or "{}")
        return row

    def get(self, run_id):
        rows = self.database.rows("SELECT * FROM runs WHERE id=?", (run_id,))
        if not rows:
            return None
        row = self._row(rows[0])
        row["changes"] = self.database.rows(
            "SELECT tbl,action,COUNT(*) AS n FROM run_changes WHERE run_id=? GROUP BY tbl,action",
            (run_id,),
        )
        return row

    def list(self, kind="", offset=0, limit=30):
        return [
            self._row(r)
            for r in self.database.rows(
                "SELECT * FROM runs WHERE (?='' OR kind=?) ORDER BY id DESC LIMIT ? OFFSET ?",
                (kind, kind, limit, max(0, offset)),
            )
        ]

    def last_finished(self, kind, source=""):
        rows = self.database.rows(
            "SELECT * FROM runs WHERE kind=? AND (?='' OR source=?) AND status='success' "
            "ORDER BY id DESC LIMIT 1",
            (kind, source, source),
        )
        return self._row(rows[0]) if rows else None

    def undo(self, run_id):
        """Revert every recorded change of a run, newest first. Returns touched memory ids."""
        touched = []
        with self.database.transaction() as connection:
            run = connection.execute("SELECT undone FROM runs WHERE id=?", (run_id,)).fetchone()
            if not run:
                raise ValueError("记录不存在")
            if run[0]:
                raise ValueError("这次记录已经撤销过")
            changes = connection.execute(
                "SELECT tbl,row_key,action,before FROM run_changes WHERE run_id=? ORDER BY id DESC",
                (run_id,),
            ).fetchall()
            for table, key, action, before in changes:
                pk = PRIMARY_KEYS[table]
                if action == "insert":
                    connection.execute(f"DELETE FROM {table} WHERE {pk}=?", (key,))
                elif action == "update":
                    values = json.loads(before or "{}")
                    if values:
                        columns = ",".join(f"{c}=?" for c in values)
                        connection.execute(
                            f"UPDATE {table} SET {columns} WHERE {pk}=?", (*values.values(), key)
                        )
                elif action == "delete":
                    values = json.loads(before or "{}")
                    columns = ",".join(values)
                    marks = ",".join("?" for _ in values)
                    connection.execute(
                        f"INSERT OR REPLACE INTO {table}({columns}) VALUES({marks})",
                        tuple(values.values()),
                    )
                if table == "memories":
                    touched.append(int(key))
            connection.execute("UPDATE runs SET undone=1 WHERE id=?", (run_id,))
        return touched
