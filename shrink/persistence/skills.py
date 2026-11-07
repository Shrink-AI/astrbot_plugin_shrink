"""Know-how (skills) with full version history, and the tool-using episodes they come from."""

import json
import time

from .runs import record

EPISODE_FIELDS = ("verdict", "lesson", "reviewed", "outcome", "answer")


class SkillRepository:
    def __init__(self, database):
        self.database = database

    # ---- skills --------------------------------------------------------------------------------
    def get(self, skill_id):
        rows = self.database.rows("SELECT * FROM skills WHERE id=?", (skill_id,))
        return rows[0] if rows else None

    def many(self, ids):
        if not ids:
            return []
        marks = ",".join("?" for _ in ids)
        return self.database.rows(f"SELECT * FROM skills WHERE id IN ({marks})", tuple(ids))

    def list(self, status="", offset=0, limit=50):
        return self.database.rows(
            "SELECT * FROM skills WHERE (?='' OR status=?) ORDER BY updated DESC LIMIT ? OFFSET ?",
            (status, status, limit, max(0, offset)),
        )

    def active(self, limit=300):
        return self.database.rows(
            "SELECT * FROM skills WHERE status='active' ORDER BY updated DESC LIMIT ?", (limit,)
        )

    def set_vector(self, skill_id, vector):
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE skills SET vector=? WHERE id=?", (json.dumps(vector), skill_id)
            )

    def counts(self):
        rows = self.database.rows("SELECT status, COUNT(*) AS n FROM skills GROUP BY status")
        return {r["status"]: r["n"] for r in rows}

    def _version(self, connection, skill_id, reason, episodes, origin, run_id):
        row = connection.execute(
            "SELECT version,title,trigger,steps,status FROM skills WHERE id=?", (skill_id,)
        ).fetchone()
        connection.execute(
            "INSERT INTO skill_versions(skill_id,version,title,trigger,steps,status,reason,"
            "episodes,origin,run,created) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                skill_id, row[0], row[1], row[2], row[3], row[4], reason,
                json.dumps(list(episodes)), origin, str(run_id or ""), time.time(),
            ),
        )

    def add(self, title, trigger, steps, reason, episodes=(), origin="reflection", run_id=None):
        now = time.time()
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO skills(title,trigger,steps,run,created,updated) VALUES(?,?,?,?,?,?)",
                (title, trigger, steps, str(run_id or ""), now, now),
            )
            skill_id = cursor.lastrowid
            record(connection, run_id, "skills", skill_id, "insert")
            self._version(connection, skill_id, reason, episodes, origin, run_id)
        return skill_id

    def revise(self, skill_id, reason, episodes=(), origin="reflection", run_id=None, **fields):
        """Change text or status; every change becomes a new version with its reason."""
        allowed = {k: v for k, v in fields.items() if k in ("title", "trigger", "steps", "status")}
        with self.database.transaction() as connection:
            old = connection.execute("SELECT * FROM skills WHERE id=?", (skill_id,)).fetchone()
            if not old:
                return False
            old = dict(old)
            changed = {k: v for k, v in allowed.items() if old[k] != v}
            if not changed:
                return False
            record(
                connection, run_id, "skills", skill_id, "update",
                {k: old[k] for k in (*changed, "version", "updated")},
            )
            columns = ",".join(f"{k}=?" for k in changed)
            stale = ",vector=NULL" if {"title", "trigger"} & set(changed) else ""
            connection.execute(
                f"UPDATE skills SET {columns}{stale},version=version+1,updated=? WHERE id=?",
                (*changed.values(), time.time(), skill_id),
            )
            self._version(connection, skill_id, reason, episodes, origin, run_id)
        return True

    def delete(self, skill_id):
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM skill_versions WHERE skill_id=?", (skill_id,))
            cursor = connection.execute("DELETE FROM skills WHERE id=?", (skill_id,))
        return bool(cursor.rowcount)

    def versions(self, skill_id):
        rows = self.database.rows(
            "SELECT * FROM skill_versions WHERE skill_id=? ORDER BY version DESC, id DESC",
            (skill_id,),
        )
        for row in rows:
            row["episodes"] = json.loads(row["episodes"] or "[]")
        return rows

    def used(self, ids):
        if not ids:
            return
        now = time.time()
        with self.database.transaction() as connection:
            connection.executemany(
                "UPDATE skills SET uses=uses+1,last_used=? WHERE id=?", [(now, i) for i in ids]
            )

    def score(self, skill_id, won):
        column = "wins" if won else "losses"
        with self.database.transaction() as connection:
            connection.execute(f"UPDATE skills SET {column}={column}+1 WHERE id=?", (skill_id,))

    # ---- episodes ------------------------------------------------------------------------------
    @staticmethod
    def _episode(row):
        row = dict(row)
        row["tools"] = json.loads(row["tools"] or "[]")
        row["skills"] = json.loads(row["skills"] or "[]")
        return row

    def add_episode(self, *, source, user_id, message_id, question, tools, answer, outcome,
                    skills, duration):
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO episodes(source,user_id,message_id,question,tools,answer,outcome,"
                "skills,duration,created) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    source, user_id, message_id, question[:500],
                    json.dumps(tools, ensure_ascii=False), answer[:1000], outcome,
                    json.dumps(list(skills)), round(duration, 1), time.time(),
                ),
            )
        return cursor.lastrowid

    def episode(self, episode_id):
        rows = self.database.rows("SELECT * FROM episodes WHERE id=?", (episode_id,))
        return self._episode(rows[0]) if rows else None

    def episodes(self, ids=None, offset=0, limit=30):
        if ids is not None:
            if not ids:
                return []
            marks = ",".join("?" for _ in ids)
            rows = self.database.rows(
                f"SELECT * FROM episodes WHERE id IN ({marks}) ORDER BY created DESC", tuple(ids)
            )
        else:
            rows = self.database.rows(
                "SELECT * FROM episodes ORDER BY created DESC LIMIT ? OFFSET ?",
                (limit, max(0, offset)),
            )
        return [self._episode(r) for r in rows]

    def unreviewed(self, older_than, limit=5):
        rows = self.database.rows(
            "SELECT * FROM episodes WHERE reviewed=0 AND created<? ORDER BY created LIMIT ?",
            (older_than, limit),
        )
        return [self._episode(r) for r in rows]

    def update_episode(self, episode_id, **fields):
        fields = {k: v for k, v in fields.items() if k in EPISODE_FIELDS}
        if not fields:
            return
        columns = ",".join(f"{k}=?" for k in fields)
        with self.database.transaction() as connection:
            connection.execute(
                f"UPDATE episodes SET {columns} WHERE id=?", (*fields.values(), episode_id)
            )
