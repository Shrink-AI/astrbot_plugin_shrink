"""Decision evidence references, scoped candidates and bounded audit records."""
import json
import time


class DecisionRepository:
    def __init__(self, database):
        self.db = database
        with database.transaction() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS decisions(
                  id INTEGER PRIMARY KEY, input_id TEXT NOT NULL, created REAL NOT NULL,
                  backend TEXT NOT NULL, version TEXT NOT NULL, candidates TEXT NOT NULL,
                  decision TEXT NOT NULL, fallback INTEGER NOT NULL, reason TEXT NOT NULL,
                  elapsed REAL NOT NULL, rule_direct INTEGER NOT NULL, confidence_kind TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS decisions_input ON decisions(input_id,id);
            """)

    def tasks(self, session, sender):
        if not self.db.rows("SELECT name FROM sqlite_master WHERE name='operation_tasks'"):
            return []
        return self.db.rows(
            "SELECT id,request,status FROM operation_tasks WHERE session=? AND sender=? "
            "ORDER BY started DESC LIMIT 24", (session, sender))

    def binding(self, identity):
        if not self.db.rows("SELECT name FROM sqlite_master WHERE name='task_inputs'"):
            return []
        return [r["task_id"] for r in self.db.rows(
            "SELECT task_id FROM task_inputs WHERE input_id=? ORDER BY id", (identity,))]

    def owned(self, task_id, session, sender):
        if not self.db.rows("SELECT name FROM sqlite_master WHERE name='operation_tasks'"):
            return False
        return bool(self.db.rows("SELECT id FROM operation_tasks WHERE id=? AND session=? AND sender=?",
                                 (task_id, session, sender)))

    def latest(self, identity):
        rows = self.db.rows("SELECT decision FROM decisions WHERE input_id=? ORDER BY id DESC LIMIT 1", (identity,))
        return json.loads(rows[0]["decision"]) if rows else None

    def save(self, identity, candidates, result):
        now = time.time()
        with self.db.transaction() as con:
            con.execute("DELETE FROM decisions WHERE created<?", (now - 14 * 86400,))
            cursor = con.execute(
                "INSERT INTO decisions(input_id,created,backend,version,candidates,decision,"
                "fallback,reason,elapsed,rule_direct,confidence_kind) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (identity, now, result["backend"], result["version"], json.dumps(candidates, ensure_ascii=False),
                 json.dumps(result["decision"], ensure_ascii=False), result["fallback"], result["reason"],
                 result["elapsed"], result["rule_direct"], result["confidence_kind"]))
        return cursor.lastrowid

    def list(self, limit=100, offset=0):
        rows = self.db.rows("SELECT * FROM decisions ORDER BY id DESC LIMIT ? OFFSET ?",
                           (max(1, min(limit, 500)), max(0, offset)))
        for row in rows:
            for key in ("candidates", "decision"):
                row[key] = json.loads(row[key])
            row["rule_direct"], row["fallback"] = bool(row["rule_direct"]), bool(row["fallback"])
        return rows

    def stats(self):
        row = self.db.rows("SELECT COUNT(*) AS total,COALESCE(SUM(rule_direct),0) AS direct FROM decisions")[0]
        return {**row, "rule_direct_ratio": row["direct"] / row["total"] if row["total"] else 0.0}

    def capability_selection(self, record_id, catalog, decision):
        rows = self.db.rows("SELECT candidates FROM decisions WHERE id=?", (record_id,))
        if not rows:
            return
        candidates = json.loads(rows[0]["candidates"])
        candidates["capabilities"] = catalog
        with self.db.transaction() as con:
            con.execute("UPDATE decisions SET candidates=?,decision=? WHERE id=?",
                        (json.dumps(candidates, ensure_ascii=False),
                         json.dumps(decision, ensure_ascii=False), record_id))
