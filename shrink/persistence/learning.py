import json
import time


class LearningRepository:
    def __init__(self, database, state):
        self.database = database
        self.state = state

    def learning_run(self, row):
        row = dict(row)
        row["diagnostics"] = (
            json.loads(row["diagnostics"])
            if row.get("diagnostics")
            else {"mode": "legacy", "decision": "unknown", "reason": "未记录人格判定原因"}
        )
        if row["status"] == "interrupted":
            row["diagnostics"].update(
                decision="interrupted", reason="任务中断，请查看当前人格版本及同步状态"
            )
        return row

    def recover_interrupted(self):
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE learning_runs SET status='interrupted',finished=?,detail=? WHERE status='running'",
                (time.time(), "进程中断，未完成本轮学习"),
            )

    def start(self, batch, diagnostics):
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO learning_runs(status,started,sample_count,detail,diagnostics) VALUES(?,?,?,?,?)",
                (
                    "running",
                    time.time(),
                    len(batch),
                    "正在分析样本",
                    json.dumps(diagnostics, ensure_ascii=False),
                ),
            )
        return cursor.lastrowid

    def finish(self, run_id, status, detail, diagnostics):
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE learning_runs SET status=?,finished=?,detail=?,diagnostics=? WHERE id=?",
                (status, time.time(), detail, json.dumps(diagnostics, ensure_ascii=False), run_id),
            )

    def list(self, offset=0, limit=20):
        return [
            self.learning_run(r)
            for r in self.database.rows(
                "SELECT * FROM learning_runs ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, max(0, offset)),
            )
        ]

    def get(self, run_id):
        rows = self.database.rows("SELECT * FROM learning_runs WHERE id=?", (run_id,))
        return self.learning_run(rows[0]) if rows else None
