"""Input identities and consumption positions. Original content lives in messages."""
import json
import time


def input_id(session, sender, external_id, source="platform"):
    if not all(isinstance(value, str) and value for value in (session, sender, external_id)):
        raise ValueError("输入需要会话、可信提交者和原始标识")
    return json.dumps([source, session, sender, external_id], ensure_ascii=False, separators=(",", ":"))


class InboxRepository:
    def __init__(self, database):
        self.db = database
        with database.transaction() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS input_events(
                  id TEXT PRIMARY KEY, external_id TEXT NOT NULL, source TEXT NOT NULL,
                  sender TEXT NOT NULL, session TEXT NOT NULL, message_id TEXT NOT NULL,
                  route TEXT NOT NULL, received REAL NOT NULL, claimed REAL, processed REAL,
                  metadata TEXT NOT NULL DEFAULT '{}', result TEXT);
                CREATE INDEX IF NOT EXISTS input_pending ON input_events(route,processed,received);
            """)

    def get(self, identity):
        rows = self.db.rows(
            "SELECT i.*,m.text FROM input_events i LEFT JOIN messages m ON m.id=i.message_id WHERE i.id=?",
            (identity,))
        if not rows:
            return None
        row = rows[0]
        row["metadata"] = json.loads(row["metadata"])
        row["result"] = json.loads(row["result"]) if row["result"] is not None else None
        return row

    def receive(self, *, session, sender, external_id, source, message_id, route, metadata=None):
        identity = input_id(session, sender, external_id, source)
        with self.db.transaction() as con:
            if message_id and not con.execute("SELECT 1 FROM messages WHERE id=?", (message_id,)).fetchone():
                raise ValueError("输入原文不存在")
            con.execute(
                "INSERT OR IGNORE INTO input_events"
                "(id,external_id,source,sender,session,message_id,route,received,metadata) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (identity, external_id, source, sender, session, message_id, route, time.time(),
                 json.dumps(metadata or {}, ensure_ascii=False)))
        return self.get(identity)

    def pending(self, route=None, session=None, sender=None):
        rows = self.db.rows(
            "SELECT id FROM input_events WHERE processed IS NULL "
            "AND (? IS NULL OR route=?) AND (? IS NULL OR session=?) AND (? IS NULL OR sender=?) "
            "ORDER BY received,rowid", (route, route, session, session, sender, sender))
        return [self.get(row["id"]) for row in rows]

    def claim(self, identities):
        now = time.time()
        with self.db.transaction() as con:
            con.executemany("UPDATE input_events SET claimed=COALESCE(claimed,?) "
                            "WHERE id=? AND processed IS NULL", [(now, key) for key in identities])

    def complete(self, identities, *, result=None, route=None):
        now = time.time()
        encoded = json.dumps(result, ensure_ascii=False) if result is not None else None
        with self.db.transaction() as con:
            con.executemany(
                "UPDATE input_events SET claimed=COALESCE(claimed,?),processed=COALESCE(processed,?),"
                "result=COALESCE(?,result) WHERE id=? AND (? IS NULL OR route=?)",
                [(now, now, encoded, key, route, route) for key in identities])

    def route(self, identity, target):
        with self.db.transaction() as con:
            con.execute("UPDATE input_events SET route=? WHERE id=?", (target, identity))

    def complete_message(self, message_id):
        identities = self.db.rows("SELECT id FROM input_events WHERE message_id=? AND route='chat'", (message_id,))
        self.complete([row["id"] for row in identities], route="chat")

    def recover(self, route):
        with self.db.transaction() as con:
            con.execute("UPDATE input_events SET claimed=NULL WHERE processed IS NULL AND route=?", (route,))
        return self.pending(route)
