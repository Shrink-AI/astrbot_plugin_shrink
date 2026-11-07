"""Directed-learning conversations and the proposals awaiting the administrator."""

import json
import time


class TeachRepository:
    def __init__(self, database):
        self.database = database

    def create_session(self, title="新对话"):
        now = time.time()
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO teach_sessions(title,created,updated) VALUES(?,?,?)", (title, now, now)
            )
        return cursor.lastrowid

    def sessions(self, limit=50):
        return self.database.rows(
            "SELECT s.*,(SELECT COUNT(*) FROM teach_proposals p WHERE p.session_id=s.id "
            "AND p.status='pending') AS pending FROM teach_sessions s ORDER BY updated DESC LIMIT ?",
            (limit,),
        )

    def session(self, session_id):
        rows = self.database.rows("SELECT * FROM teach_sessions WHERE id=?", (session_id,))
        return rows[0] if rows else None

    def add_message(self, session_id, role, text):
        now = time.time()
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO teach_messages(session_id,role,text,created) VALUES(?,?,?,?)",
                (session_id, role, text, now),
            )
            connection.execute(
                "UPDATE teach_sessions SET updated=?,title=CASE WHEN title='新对话' AND ?='admin' "
                "THEN substr(?,1,24) ELSE title END WHERE id=?",
                (now, role, text, session_id),
            )
        return cursor.lastrowid

    def messages(self, session_id, limit=200):
        return self.database.rows(
            "SELECT * FROM (SELECT * FROM teach_messages WHERE session_id=? ORDER BY id DESC "
            "LIMIT ?) ORDER BY id",
            (session_id, limit),
        )

    def add_proposal(self, session_id, message_id, kind, payload, status="pending", result=""):
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO teach_proposals(session_id,message_id,kind,payload,status,result,created)"
                " VALUES(?,?,?,?,?,?,?)",
                (session_id, message_id, kind, json.dumps(payload, ensure_ascii=False), status,
                 result, time.time()),
            )
        return cursor.lastrowid

    @staticmethod
    def _proposal(row):
        row = dict(row)
        row["payload"] = json.loads(row["payload"])
        return row

    def proposals(self, session_id):
        return [
            self._proposal(r)
            for r in self.database.rows(
                "SELECT * FROM teach_proposals WHERE session_id=? ORDER BY id", (session_id,)
            )
        ]

    def proposal(self, proposal_id):
        rows = self.database.rows("SELECT * FROM teach_proposals WHERE id=?", (proposal_id,))
        return self._proposal(rows[0]) if rows else None

    def decide(self, proposal_id, status, result="", payload=None):
        with self.database.transaction() as connection:
            if payload is not None:
                connection.execute(
                    "UPDATE teach_proposals SET payload=? WHERE id=?",
                    (json.dumps(payload, ensure_ascii=False), proposal_id),
                )
            cursor = connection.execute(
                "UPDATE teach_proposals SET status=?,result=?,decided=? WHERE id=? "
                "AND status='pending'",
                (status, result, time.time(), proposal_id),
            )
        return bool(cursor.rowcount)

    def decide_review(self, queue_id, verdict):
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE teach_proposals SET status=?,result=?,decided=? WHERE kind='question' "
                "AND status='pending' AND json_extract(payload,'$.queue_id')=?",
                ("rejected" if verdict == "skip" else "accepted",
                 {"correct": "确认正确", "wrong": "判定错误", "skip": "跳过"}[verdict],
                 time.time(), queue_id),
            )
