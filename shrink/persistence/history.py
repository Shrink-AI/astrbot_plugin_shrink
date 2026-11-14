"""Continuous role log, referenced originals, and replaceable compression checkpoints."""
import json
import sqlite3
import time
from contextlib import closing


class HistoryRepository:
    MIGRATION = "continuous_history_v1"

    def __init__(self, database):
        self.db = database
        migrate = not database.rows("SELECT 1 FROM kv WHERE key=?", (self.MIGRATION,))
        old_inputs = database.rows("SELECT 1 FROM sqlite_master WHERE name='task_inputs'")
        if migrate and (database.rows("SELECT 1 FROM messages LIMIT 1") or old_inputs):
            backup = database.root / "shrink.before-continuous-history-v1.db"
            if not backup.exists():
                temporary = backup.with_suffix(".db.tmp")
                with closing(sqlite3.connect(temporary)) as destination:
                    database.connection.backup(destination)
                temporary.replace(backup)
        with database.transaction() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS conversation_sessions(
                  id TEXT PRIMARY KEY,instance TEXT NOT NULL,platform TEXT NOT NULL,
                  kind TEXT NOT NULL,external_id TEXT NOT NULL,source TEXT NOT NULL,
                  system_prefix TEXT);
                CREATE TABLE IF NOT EXISTS conversation_events(
                  id INTEGER PRIMARY KEY,event_key TEXT NOT NULL UNIQUE,session TEXT NOT NULL,
                  role TEXT NOT NULL,message_id TEXT, speaker TEXT NOT NULL DEFAULT '',
                  tool_calls TEXT,tool_call_id TEXT,kind TEXT NOT NULL DEFAULT 'message',
                  target TEXT NOT NULL DEFAULT '',created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS conversation_order ON conversation_events(session,id);
                CREATE TABLE IF NOT EXISTS conversation_checkpoints(
                  id INTEGER PRIMARY KEY,session TEXT NOT NULL,start_event INTEGER NOT NULL,
                  end_event INTEGER NOT NULL,summary TEXT NOT NULL,message_refs TEXT NOT NULL,
                  created REAL NOT NULL,UNIQUE(session,end_event));
            """)
        if migrate:
            self.migrate()

    def session(self, identity, *, instance="", platform="", kind="", external_id="", source=""):
        with self.db.transaction() as con:
            con.execute("INSERT OR IGNORE INTO conversation_sessions"
                        "(id,instance,platform,kind,external_id,source) VALUES(?,?,?,?,?,?)",
                        (identity, instance, platform, kind, external_id, source))
            matches = con.execute(
                "SELECT id FROM conversation_sessions WHERE source=? AND instance!=''", (source,)).fetchall()
            if source and identity != source and len(matches) == 1:
                con.execute("UPDATE conversation_events SET session=? WHERE session=?", (identity, source))
                con.execute("UPDATE conversation_checkpoints SET session=? WHERE session=?", (identity, source))

    def migrate(self):
        from .inbox import InboxRepository, input_id
        InboxRepository(self.db)
        # Old message IDs contain the instance; old bot rows can only be assigned when unambiguous.
        rows = self.db.rows("SELECT * FROM messages ORDER BY created,rowid")
        sources = {}
        with self.db.transaction() as con:
            for row in rows:
                if row["is_bot"]:
                    continue
                marker = ":" + row["source"] + ":"
                prefix = row["id"].split(marker, 1)[0] if marker in row["id"] else ""
                instance = prefix.split(":", 1)[-1] if ":" in prefix else ""
                platform, _, rest = row["source"].partition(":")
                kind, _, external = rest.partition(":")
                session = f"{instance}:{'FriendMessage' if kind == 'private' else 'GroupMessage'}:{external}"
                if not instance:
                    session = row["source"]
                sources.setdefault(row["source"], set()).add(session)
                con.execute("INSERT OR IGNORE INTO conversation_sessions "
                            "(id,instance,platform,kind,external_id,source) VALUES(?,?,?,?,?,?)",
                            (session, instance, platform, kind, external, row["source"]))
            for row in rows:
                candidates = sources.get(row["source"], {row["source"]})
                marker = ":" + row["source"] + ":"
                prefix = row["id"].split(marker, 1)[0] if marker in row["id"] else ""
                instance = prefix.split(":", 1)[-1] if ":" in prefix else ""
                kind = "FriendMessage" if ":private:" in row["source"] else "GroupMessage"
                session = f"{instance}:{kind}:{row['source'].split(':', 2)[-1]}" if instance else (
                    next(iter(candidates)) if len(candidates) == 1 else row["source"])
                con.execute("INSERT OR IGNORE INTO conversation_events"
                            "(event_key,session,role,message_id,speaker,created) VALUES(?,?,?,?,?,?)",
                            ("message:" + row["id"], session, "assistant" if row["is_bot"] else "user",
                             row["id"], row["user_id"], row["created"]))
                if not row["is_bot"]:
                    sender = row["user_id"].split(":", 1)[-1]
                    external = row["id"].split(marker, 1)[-1] if marker in row["id"] else row["id"]
                    con.execute("INSERT OR IGNORE INTO input_events"
                                "(id,external_id,source,sender,session,message_id,route,received,claimed,processed)"
                                " VALUES(?,?,'platform',?,?,?,'chat',?,?,?)",
                                (input_id(session, sender, external), external, sender, session, row["id"],
                                 row["created"], row["created"], row["created"]))
            for digest in self.db.rows("SELECT * FROM digests ORDER BY end"):
                for session in sources.get(digest["source"], {digest["source"]}):
                    covered = self.db.rows(
                        "SELECT id,message_id FROM conversation_events WHERE session=? AND created>=? "
                        "AND created<=? ORDER BY id", (session, digest["start"], digest["end"]))
                    if covered:
                        con.execute("INSERT OR IGNORE INTO conversation_checkpoints"
                                    "(session,start_event,end_event,summary,message_refs,created) VALUES(?,?,?,?,?,?)",
                                    (session, covered[0]["id"], covered[-1]["id"], digest["summary"],
                                     json.dumps([r["message_id"] for r in covered]), time.time()))
            con.execute("INSERT INTO kv VALUES(?, 'true')", (self.MIGRATION,))

    def append(self, session, event_key, role, message_id=None, *, speaker="", tool_calls=None,
               tool_call_id=None, kind="message", target=""):
        with self.db.transaction() as con:
            con.execute("INSERT OR IGNORE INTO conversation_events"
                        "(event_key,session,role,message_id,speaker,tool_calls,tool_call_id,kind,target,created)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (event_key, session, role, message_id, speaker,
                         json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None,
                         tool_call_id, kind, target, time.time()))

    def events(self, session):
        return self.db.rows(
            "SELECT e.*,m.text,m.speaker_name FROM conversation_events e LEFT JOIN messages m "
            "ON m.id=e.message_id WHERE e.session=? ORDER BY e.id", (session,))

    def revised(self, message_id):
        return bool(self.db.rows("SELECT 1 FROM conversation_events WHERE target=? LIMIT 1", (message_id,)))

    def checkpoint(self, session, identity=None):
        rows = self.db.rows(
            "SELECT * FROM conversation_checkpoints WHERE session=? AND (? IS NULL OR id=?) "
            "ORDER BY end_event DESC LIMIT 1", (session, identity, identity))
        return rows[0] if rows else None

    def compress(self, session, start, end, summary, refs):
        with self.db.transaction() as con:
            con.execute("INSERT OR REPLACE INTO conversation_checkpoints"
                        "(session,start_event,end_event,summary,message_refs,created) VALUES(?,?,?,?,?,?)",
                        (session, start, end, summary, json.dumps(refs), time.time()))

    def invalidate(self, session):
        with self.db.transaction() as con:
            con.execute("DELETE FROM conversation_checkpoints WHERE session=?", (session,))

    def compare_prefix(self, session, prefix):
        rows = self.db.rows("SELECT system_prefix FROM conversation_sessions WHERE id=?", (session,))
        previous = rows[0]["system_prefix"] if rows else None
        with self.db.transaction() as con:
            con.execute("UPDATE conversation_sessions SET system_prefix=? WHERE id=?", (prefix, session))
        return None if previous is None else previous == prefix
