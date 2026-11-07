"""Expression bank: in which situation real group members say what."""

import json
import time

from .runs import record


class ExpressionRepository:
    def __init__(self, database):
        self.database = database

    def add(self, scope, situation, phrase, evidence=(), run_id=None):
        now = time.time()
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO expressions(scope,situation,phrase,evidence,run,created,updated)"
                " VALUES(?,?,?,?,?,?,?)",
                (scope, situation, phrase, json.dumps(list(evidence), ensure_ascii=False),
                 f"run:{run_id}" if run_id else "", now, now),
            )
            if cursor.rowcount:
                record(connection, run_id, "expressions", cursor.lastrowid, "insert")
        return cursor.lastrowid if cursor.rowcount else None

    def usable(self, scope, limit=200):
        return self.database.rows(
            "SELECT * FROM expressions WHERE enabled=1 AND (scope=? OR scope='') "
            "ORDER BY uses ASC,updated DESC LIMIT ?",
            (scope, limit),
        )

    def used(self, ids):
        with self.database.transaction() as connection:
            connection.executemany(
                "UPDATE expressions SET uses=uses+1 WHERE id=?", [(i,) for i in ids]
            )

    def list(self, q="", offset=0, limit=60):
        return self.database.rows(
            "SELECT * FROM expressions WHERE situation LIKE ? OR phrase LIKE ? "
            "ORDER BY id DESC LIMIT ? OFFSET ?",
            (f"%{q[:100]}%", f"%{q[:100]}%", limit, max(0, offset)),
        )

    def set_enabled(self, expression_id, enabled):
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE expressions SET enabled=?,updated=? WHERE id=?",
                (int(enabled), time.time(), expression_id),
            )
        return bool(cursor.rowcount)

    def delete(self, expression_id):
        with self.database.transaction() as connection:
            cursor = connection.execute("DELETE FROM expressions WHERE id=?", (expression_id,))
        return bool(cursor.rowcount)
