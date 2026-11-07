"""Digest summaries of older conversation segments."""

import time

from .runs import record


class DigestRepository:
    def __init__(self, database):
        self.database = database

    def add(self, source, start, end, count, summary, run_id=None):
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO digests(source,start,end,count,summary,run,created) VALUES(?,?,?,?,?,?,?)",
                (source, start, end, count, summary, f"run:{run_id}" if run_id else "", time.time()),
            )
            record(connection, run_id, "digests", cursor.lastrowid, "insert")
        return cursor.lastrowid

    def before(self, source, end, limit=2):
        """The newest summaries that end before the verbatim window starts."""
        return self.database.rows(
            "SELECT * FROM digests WHERE source=? AND end<=? ORDER BY end DESC LIMIT ?",
            (source, end, limit),
        )

    def overlapping(self, source, since, limit=6):
        """Summaries of one conversation that end after `since`, oldest first."""
        rows = self.database.rows(
            "SELECT * FROM digests WHERE source=? AND end>=? ORDER BY end DESC LIMIT ?",
            (source, since, limit),
        )
        return list(reversed(rows))

    def search(self, grams, exclude=(), limit=2, source=None):
        terms = [g for g in grams if len(g) >= 3][:20]
        if not terms:
            return []
        query = " OR ".join('"' + g.replace('"', "") + '"' for g in terms)
        rows = self.database.rows(
            "SELECT d.* FROM digest_fts f JOIN digests d ON d.id=f.rowid WHERE digest_fts MATCH ? "
            "AND (? IS NULL OR d.source=?) ORDER BY rank LIMIT ?",
            (query, source, source, limit + len(exclude)),
        )
        return [r for r in rows if r["id"] not in set(exclude)][:limit]

    def recent(self, source="", limit=30, offset=0):
        return self.database.rows(
            "SELECT * FROM digests WHERE (?='' OR source=?) ORDER BY end DESC LIMIT ? OFFSET ?",
            (source, source, limit, max(0, offset)),
        )

    def last_end(self, source):
        row = self.database.connection.execute(
            "SELECT MAX(end) FROM digests WHERE source=?", (source,)
        ).fetchone()
        return row[0] or 0
