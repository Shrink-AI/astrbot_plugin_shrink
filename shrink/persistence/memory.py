import json
import math
import time

from .runs import record

LIVE = ("active", "candidate")
COLUMNS = (
    "kind", "subject", "term", "source", "text", "quote", "evidence", "participants", "importance",
    "confidence", "certainty", "status", "verified", "verified_at", "valid_until", "origin", "run",
    "superseded_by", "recalls", "last_recalled", "updated",
)


def _normalized(vector):
    norm = math.sqrt(sum(x * x for x in vector)) or 1
    return tuple(x / norm for x in vector)


class MemoryRepository:
    def __init__(self, database, state):
        self.database = database
        self.state = state
        # Exact normalized-vector index; SQLite remains the durable source of truth.
        self.vector_index = {}
        for row in self.database.rows("SELECT id,vector FROM memories WHERE vector IS NOT NULL"):
            self.vector_index[row["id"]] = _normalized(json.loads(row["vector"]))

    # ---- writes ------------------------------------------------------------------------------------
    def add(
        self, kind, subject, source, text, *, term="", quote="", evidence=(), participants=(),
        importance=3, confidence=0.6, certainty="stated", status="active", origin="chat",
        run_id=None, valid_until=None, verified=0, legacy_id=None,
    ):
        now = time.time()
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO memories(kind,subject,term,source,text,quote,evidence,"
                "participants,importance,confidence,certainty,status,verified,verified_at,"
                "valid_until,origin,run,created,updated,legacy_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    kind, subject, term, source, text, quote,
                    json.dumps(list(evidence), ensure_ascii=False),
                    json.dumps(list(participants), ensure_ascii=False),
                    int(importance), float(confidence), certainty, status, int(verified),
                    now if verified else None, valid_until, origin,
                    f"run:{run_id}" if run_id else "", now, now, legacy_id,
                ),
            )
            if not cursor.rowcount:
                return None
            record(connection, run_id, "memories", cursor.lastrowid, "insert")
        self.state.put("last_memory", now)
        return cursor.lastrowid

    def update(self, memory_id, run_id=None, **fields):
        fields = {k: v for k, v in fields.items() if k in COLUMNS}
        if not fields:
            return False
        fields.setdefault("updated", time.time())
        for key in ("evidence", "participants"):
            if key in fields and not isinstance(fields[key], str):
                fields[key] = json.dumps(list(fields[key]), ensure_ascii=False)
        with self.database.transaction() as connection:
            old = connection.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
            if not old:
                return False
            before = {k: old[k] for k in fields}
            if "text" in fields and fields["text"] != old["text"]:
                fields["vector"] = None
                before["vector"] = old["vector"]
            record(connection, run_id, "memories", memory_id, "update", before)
            columns = ",".join(f"{k}=?" for k in fields)
            connection.execute(
                f"UPDATE memories SET {columns} WHERE id=?", (*fields.values(), memory_id)
            )
        if "vector" in fields:
            self.vector_index.pop(memory_id, None)
        return True

    def edit_memory(self, memory_id, text):
        return self.update(memory_id, text=text)

    def delete_memory(self, memory_id):
        self.vector_index.pop(memory_id, None)
        with self.database.transaction() as connection:
            cursor = connection.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        return bool(cursor.rowcount)

    def set_vector(self, memory_id, text, vector):
        if not vector or not all(math.isfinite(v) for v in vector):
            return
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE memories SET vector=? WHERE id=? AND text=?",
                (json.dumps(vector), memory_id, text),
            )
        if cursor.rowcount:
            self.vector_index[memory_id] = _normalized(vector)

    def refresh_vectors(self, ids):
        for memory_id in ids:
            row = self.get(memory_id)
            if row and row["vector"]:
                self.vector_index[memory_id] = _normalized(json.loads(row["vector"]))
            else:
                self.vector_index.pop(memory_id, None)

    def mark_recalled(self, ids):
        if not ids:
            return
        now = time.time()
        with self.database.transaction() as connection:
            connection.executemany(
                "UPDATE memories SET recalls=recalls+1,last_recalled=? WHERE id=?",
                [(now, i) for i in ids],
            )

    # ---- reads -------------------------------------------------------------------------------------
    def get(self, memory_id):
        rows = self.database.rows("SELECT * FROM memories WHERE id=?", (memory_id,))
        return rows[0] if rows else None

    def many(self, ids):
        if not ids:
            return []
        marks = ",".join("?" for _ in ids)
        return self.database.rows(
            f"SELECT * FROM memories WHERE id IN ({marks})", tuple(int(i) for i in ids)
        )

    def unindexed(self):
        return self.database.rows(
            "SELECT id FROM memories WHERE vector IS NULL AND status IN ('active','candidate') LIMIT 8"
        )

    def fts(self, grams, limit=60):
        terms = [g for g in grams if len(g) >= 3][:30]
        if not terms:
            return []
        query = " OR ".join('"' + g.replace('"', "") + '"' for g in terms)
        return self.database.rows(
            "SELECT m.* FROM memory_fts f JOIN memories m ON m.id=f.rowid "
            "WHERE memory_fts MATCH ? AND m.status IN ('active','candidate') ORDER BY rank LIMIT ?",
            (query, limit),
        )

    def by_people(self, uids, limit=60):
        if not uids:
            return []
        marks = ",".join("?" for _ in uids)
        return self.database.rows(
            f"SELECT * FROM memories WHERE status IN ('active','candidate') AND (subject IN ({marks}) "
            f"OR EXISTS(SELECT 1 FROM json_each(memories.participants) WHERE value IN ({marks}))) "
            "ORDER BY updated DESC LIMIT ?",
            (*uids, *uids, limit),
        )

    def glossary_terms(self):
        return {
            r["term"]: r["id"]
            for r in self.database.rows(
                "SELECT id,term FROM memories WHERE kind='glossary' AND term!='' "
                "AND status IN ('active','candidate')"
            )
        }

    def for_subject(self, subject, kinds=("impression", "event"), limit=30):
        marks = ",".join("?" for _ in kinds)
        return self.database.rows(
            f"SELECT * FROM memories WHERE subject=? AND kind IN ({marks}) "
            "AND status IN ('active','candidate') ORDER BY importance DESC,updated DESC LIMIT ?",
            (subject, *kinds, limit),
        )

    def same_kind(self, subject, kind):
        return self.database.rows(
            "SELECT * FROM memories WHERE subject=? AND kind=? AND status IN ('active','candidate')",
            (subject, kind),
        )

    def created_since(self, subject, since):
        return self.database.connection.execute(
            "SELECT COUNT(*) FROM memories WHERE subject=? AND created>? AND origin='digest'",
            (subject, since),
        ).fetchone()[0]

    def search(
        self, q="", user_id="", source="", offset=0, kind="", status="", min_confidence=0.0,
        run="", limit=50,
    ):
        return self.database.rows(
            "SELECT id,kind,subject,term,source,text,quote,evidence,participants,"
            "importance,confidence,certainty,status,verified,valid_until,origin,run,superseded_by,"
            "recalls,last_recalled,created,updated,legacy_id FROM memories "
            "WHERE text LIKE ? AND (?='' OR subject=? OR EXISTS(SELECT 1 FROM "
            "json_each(memories.participants) WHERE value=?)) AND source LIKE ? "
            "AND (?='' OR kind=?) AND (?='' OR status=?) AND confidence>=? AND (?='' OR run=?) "
            "ORDER BY updated DESC LIMIT ? OFFSET ?",
            (
                f"%{q[:200]}%", user_id, user_id, user_id, f"%{source[:200]}%", kind, kind,
                status, status, min_confidence, run, run, limit, max(0, offset),
            ),
        )

    def expired(self, now):
        return self.database.rows(
            "SELECT id FROM memories WHERE status='active' AND valid_until IS NOT NULL "
            "AND valid_until<?",
            (now,),
        )

    def subjects_updated_since(self, since, limit=20):
        return [
            r["subject"]
            for r in self.database.rows(
                "SELECT subject,MAX(updated) AS u FROM memories WHERE subject LIKE '%:%' "
                "AND updated>? AND status='active' GROUP BY subject ORDER BY u DESC LIMIT ?",
                (since, limit),
            )
        ]

    def counts(self):
        return {
            f"{r['kind']}:{r['status']}": r["n"]
            for r in self.database.rows(
                "SELECT kind,status,COUNT(*) AS n FROM memories GROUP BY kind,status"
            )
        }
