"""People, their nicknames as used by others, and person/group profile cards."""

import json
import time

from .runs import record


class PeopleRepository:
    def __init__(self, database):
        self.database = database

    # ---- people ----------------------------------------------------------------------------------
    def get(self, uid):
        rows = self.database.rows("SELECT * FROM people WHERE id=?", (uid,))
        return rows[0] if rows else None

    def many(self, ids):
        if not ids:
            return {}
        marks = ",".join("?" for _ in ids)
        return {
            r["id"]: r
            for r in self.database.rows(f"SELECT * FROM people WHERE id IN ({marks})", tuple(ids))
        }

    def bots(self):
        return {r["id"] for r in self.database.rows("SELECT id FROM people WHERE kind='bot'")}

    def set_kind(self, uid, kind, run_id=None):
        with self.database.transaction() as connection:
            old = connection.execute("SELECT kind FROM people WHERE id=?", (uid,)).fetchone()
            if not old:
                return False
            connection.execute(
                "UPDATE people SET kind=?,updated=? WHERE id=?", (kind, time.time(), uid)
            )
            record(connection, run_id, "people", uid, "update", {"kind": old[0]})
        return True

    def search(self, q="", kind="", source="", offset=0, limit=50):
        like = f"%{q[:100]}%"
        return self.database.rows(
            "SELECT p.*,(SELECT COUNT(*) FROM memories m WHERE m.subject=p.id AND "
            "m.status IN ('active','candidate')) AS memory_count,"
            "(SELECT MAX(created) FROM messages WHERE user_id=p.id) AS last_seen "
            "FROM people p WHERE (?='' OR p.id LIKE ? OR p.name LIKE ? OR p.aliases LIKE ? OR "
            "EXISTS(SELECT 1 FROM nicknames n WHERE n.user_id=p.id AND n.name LIKE ? "
            "AND n.status='active')) AND (?='' OR p.kind=?) AND (?='' OR EXISTS("
            "SELECT 1 FROM messages s WHERE s.user_id=p.id AND s.source LIKE ?)) "
            "ORDER BY last_seen DESC LIMIT ? OFFSET ?",
            (q, like, like, like, like, kind, kind, source, f"%{source}%", limit, max(0, offset)),
        )

    def sources(self, uid):
        return self.database.rows(
            "SELECT source,COUNT(*) AS n,MAX(created) AS last FROM messages WHERE user_id=? "
            "GROUP BY source ORDER BY last DESC",
            (uid,),
        )

    def known_sources(self):
        return self.database.rows(
            "SELECT source,MAX(source_name) AS name,COUNT(*) AS n,MAX(created) AS last "
            "FROM messages GROUP BY source ORDER BY last DESC"
        )

    # ---- nicknames ---------------------------------------------------------------------------------
    def nicknames(self, uid=None, scope=None, status="active"):
        return self.database.rows(
            "SELECT * FROM nicknames WHERE (? IS NULL OR user_id=?) AND "
            "(? IS NULL OR scope='' OR scope=?) AND (?='' OR status=?) ORDER BY verified DESC,id",
            (uid, uid, scope, scope, status, status),
        )

    def nickname_by_name(self, scope, name):
        rows = self.database.rows(
            "SELECT * FROM nicknames WHERE name=? AND (scope=? OR scope='') AND status='active' "
            "ORDER BY scope DESC",
            (name, scope),
        )
        return rows[0] if rows else None

    def add_nickname(
        self, uid, name, scope="", origin="ai", confidence=0.6, evidence=(), run_id=None,
        verified=0,
    ):
        """Returns (id, outcome) where outcome is created/merged/conflict/exists."""
        now = time.time()
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT id,user_id,verified,origin,evidence,confidence,status FROM nicknames "
                "WHERE scope=? AND name=?",
                (scope, name),
            ).fetchone()
            if existing:
                nid, owner, was_verified, old_origin, old_evidence, old_conf, status = existing
                if status == "rejected" and origin != "admin":
                    return nid, "rejected"
                if owner != uid:
                    return nid, "conflict"
                if old_origin == "admin" and origin != "admin":
                    return nid, "exists"
                merged = list(dict.fromkeys(json.loads(old_evidence) + list(evidence)))[-20:]
                record(
                    connection, run_id, "nicknames", nid, "update",
                    {"evidence": old_evidence, "confidence": old_conf, "updated": now},
                )
                connection.execute(
                    "UPDATE nicknames SET evidence=?,confidence=MAX(confidence,?),updated=?,"
                    "verified=MAX(verified,?),origin=CASE WHEN ?='admin' THEN 'admin' ELSE origin END "
                    "WHERE id=?",
                    (json.dumps(merged, ensure_ascii=False), confidence, now, verified, origin, nid),
                )
                return nid, "merged"
            cursor = connection.execute(
                "INSERT INTO nicknames(user_id,name,scope,origin,confidence,status,verified,evidence,"
                "run,created,updated) VALUES(?,?,?,?,?,'active',?,?,?,?,?)",
                (
                    uid, name, scope, origin, confidence, verified,
                    json.dumps(list(evidence), ensure_ascii=False),
                    f"run:{run_id}" if run_id else origin, now, now,
                ),
            )
            record(connection, run_id, "nicknames", cursor.lastrowid, "insert")
            return cursor.lastrowid, "created"

    def promote_global(self, nickname_id):
        """A nickname seen for the same person in several groups becomes global."""
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT user_id,name FROM nicknames WHERE id=?", (nickname_id,)
            ).fetchone()
            if not row:
                return False
            scopes = connection.execute(
                "SELECT COUNT(DISTINCT scope) FROM nicknames WHERE user_id=? AND name=? AND scope!=''",
                row,
            ).fetchone()[0]
            taken = connection.execute(
                "SELECT 1 FROM nicknames WHERE scope='' AND name=?", (row[1],)
            ).fetchone()
            if scopes >= 2 and not taken:
                connection.execute("UPDATE nicknames SET scope='' WHERE id=?", (nickname_id,))
                connection.execute(
                    "DELETE FROM nicknames WHERE user_id=? AND name=? AND scope!='' AND id!=?",
                    (*row, nickname_id),
                )
                return True
        return False

    def nickname(self, nickname_id):
        rows = self.database.rows("SELECT * FROM nicknames WHERE id=?", (nickname_id,))
        return rows[0] if rows else None

    def set_nickname(self, nickname_id, run_id=None, **fields):
        with self.database.transaction() as connection:
            old = connection.execute(
                "SELECT * FROM nicknames WHERE id=?", (nickname_id,)
            ).fetchone()
            if not old:
                return False
            before = {k: old[k] for k in fields}
            record(connection, run_id, "nicknames", nickname_id, "update", before)
            columns = ",".join(f"{k}=?" for k in fields)
            connection.execute(
                f"UPDATE nicknames SET {columns},updated=? WHERE id=?",
                (*fields.values(), time.time(), nickname_id),
            )
        return True

    def delete_nickname(self, nickname_id):
        with self.database.transaction() as connection:
            cursor = connection.execute("DELETE FROM nicknames WHERE id=?", (nickname_id,))
        return bool(cursor.rowcount)

    # ---- profiles ----------------------------------------------------------------------------------
    def profile(self, subject):
        rows = self.database.rows("SELECT * FROM profiles WHERE subject=?", (subject,))
        return rows[0] if rows else None

    def profiles(self, kind="group"):
        return self.database.rows(
            "SELECT * FROM profiles WHERE kind=? ORDER BY updated DESC", (kind,)
        )

    def save_profile(self, subject, kind, summary, relation="", evidence=(), run_id=None):
        now = time.time()
        with self.database.transaction() as connection:
            old = connection.execute("SELECT * FROM profiles WHERE subject=?", (subject,)).fetchone()
            if old and old["verified"]:
                return False
            if old:
                record(
                    connection, run_id, "profiles", subject, "update",
                    {k: old[k] for k in ("summary", "relation", "evidence", "updated", "run")},
                )
                connection.execute(
                    "UPDATE profiles SET summary=?,relation=?,evidence=?,run=?,updated=? "
                    "WHERE subject=?",
                    (summary, relation, json.dumps(list(evidence)), f"run:{run_id}", now, subject),
                )
            else:
                connection.execute(
                    "INSERT INTO profiles(subject,kind,summary,relation,evidence,run,updated) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (subject, kind, summary, relation, json.dumps(list(evidence)), f"run:{run_id}",
                     now),
                )
                record(connection, run_id, "profiles", subject, "insert")
        return True

    def verify_profile(self, subject, verified):
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE profiles SET verified=? WHERE subject=?", (verified, subject)
            )
        return bool(cursor.rowcount)
