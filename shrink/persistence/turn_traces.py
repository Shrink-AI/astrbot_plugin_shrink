import json
import time

KEEP_SECONDS = 14 * 86400


class TurnTraceRepository:
    def __init__(self, database):
        self.db = database
        with self.db.transaction() as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS turn_traces("
                "run_id TEXT PRIMARY KEY,input_id TEXT NOT NULL,umo TEXT NOT NULL,"
                "source TEXT NOT NULL,sender TEXT NOT NULL,reason TEXT NOT NULL,"
                "outcome TEXT NOT NULL,task_id TEXT NOT NULL DEFAULT '',started REAL NOT NULL,"
                "total REAL NOT NULL,phases TEXT NOT NULL,tools TEXT NOT NULL,"
                "model_calls INTEGER NOT NULL DEFAULT 0)")
            con.execute("CREATE INDEX IF NOT EXISTS turn_traces_started ON turn_traces(started)")
            con.execute("DELETE FROM turn_traces WHERE started<?", (time.time() - KEEP_SECONDS,))

    def save(self, record):
        with self.db.transaction() as con:
            con.execute(
                "INSERT OR REPLACE INTO turn_traces(run_id,input_id,umo,source,sender,reason,"
                "outcome,task_id,started,total,phases,tools,model_calls) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (record["run_id"], record["input_id"], record["umo"], record["source"],
                 record["sender"], record["reason"], record["outcome"], record["task_id"],
                 record["started"], record["total"],
                 json.dumps(record["phases"]), json.dumps(record["tools"], ensure_ascii=False),
                 record["model_calls"]))

    def since(self, started=0.0, limit=5000):
        rows = self.db.rows(
            "SELECT * FROM turn_traces WHERE started>=? ORDER BY started DESC LIMIT ?",
            (started, limit))
        for row in rows:
            row["phases"] = json.loads(row["phases"])
            row["tools"] = json.loads(row["tools"])
        return rows
