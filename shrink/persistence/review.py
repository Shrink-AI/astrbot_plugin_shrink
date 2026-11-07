"""Items the administrator should confirm: conflicts, candidates, guesses."""

import time

PRIORITY = {
    "nickname_conflict": 1,
    "memory_conflict": 2,
    "memory_candidate": 3,
    "glossary_guess": 4,
    "nickname_guess": 4,
    "profile_new": 5,
    "suspected_bot": 5,
    "memory_unverified": 6,
}


# Kinds that ask about the same row; one target gets one open question.
FAMILY = {
    "memory_candidate": "memory", "memory_unverified": "memory", "glossary_guess": "memory",
    "nickname_conflict": "nickname", "nickname_guess": "nickname",
}


def family(kind):
    return FAMILY.get(kind, kind)


class ReviewRepository:
    def __init__(self, database):
        self.database = database

    def _kinds(self, kind):
        return [k for k, f in FAMILY.items() if f == family(kind)] or [kind]

    def add(self, kind, target, reason, priority=None):
        """Queue a question. A newer, more specific question about the same row replaces the old one."""
        priority = priority or PRIORITY.get(kind, 5)
        kinds = self._kinds(kind)
        marks = ",".join("?" for _ in kinds)
        with self.database.transaction() as connection:
            open_row = connection.execute(
                f"SELECT id FROM review_queue WHERE status='open' AND target=? AND kind IN ({marks}) "
                "ORDER BY id DESC LIMIT 1",
                (str(target), *kinds),
            ).fetchone()
            if open_row:
                connection.execute(
                    "UPDATE OR IGNORE review_queue SET kind=?,reason=?,priority=? WHERE id=?",
                    (kind, reason, priority, open_row[0]),
                )
                return
            connection.execute(
                "INSERT OR IGNORE INTO review_queue(kind,target,reason,priority,status,created) "
                "VALUES(?,?,?,?,'open',?)",
                (kind, str(target), reason, priority, time.time()),
            )

    def close_family(self, kind, target, status="done"):
        """Close every open question about the same row."""
        kinds = self._kinds(kind)
        marks = ",".join("?" for _ in kinds)
        with self.database.transaction() as connection:
            connection.execute(
                f"UPDATE review_queue SET status=?,decided=? WHERE status='open' AND target=? "
                f"AND kind IN ({marks})",
                (status, time.time(), str(target), *kinds),
            )

    def open(self, limit=5, offset=0):
        return self.database.rows(
            "SELECT * FROM review_queue WHERE status='open' ORDER BY priority,created,id "
            "LIMIT ? OFFSET ?",
            (limit, max(0, offset)),
        )

    def get(self, item_id):
        rows = self.database.rows("SELECT * FROM review_queue WHERE id=?", (item_id,))
        return rows[0] if rows else None

    def decide(self, item_id, status):
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE review_queue SET status=?,decided=? WHERE id=?",
                (status, time.time(), item_id),
            )
        return bool(cursor.rowcount)

    def close_target(self, kind, target, status="done"):
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE review_queue SET status=?,decided=? WHERE kind LIKE ? AND target=? "
                "AND status='open'",
                (status, time.time(), kind, str(target)),
            )

    def count_open(self):
        return self.database.connection.execute(
            "SELECT COUNT(*) FROM review_queue WHERE status='open'"
        ).fetchone()[0]
