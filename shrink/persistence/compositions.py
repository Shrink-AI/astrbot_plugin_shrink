"""Keep source material independent of expression and delivery."""
import json
import time

KEEP_SECONDS = 14 * 86400


class Compositions:
    def __init__(self, database):
        self.db = database
        with self.db.transaction() as con:
            con.execute("CREATE TABLE IF NOT EXISTS outbound_compositions("
                        "id INTEGER PRIMARY KEY,facts TEXT NOT NULL,request TEXT NOT NULL,"
                        "kind TEXT NOT NULL,scope TEXT NOT NULL,created REAL NOT NULL,"
                        "state TEXT NOT NULL DEFAULT 'composing',text TEXT NOT NULL DEFAULT '')")
            con.execute("CREATE INDEX IF NOT EXISTS compositions_created ON outbound_compositions(created)")
            con.execute("DELETE FROM outbound_compositions WHERE created<?", (time.time() - KEEP_SECONDS,))

    def save(self, facts, request, kind, scope):
        with self.db.transaction() as con:
            con.execute("DELETE FROM outbound_compositions WHERE created<?", (time.time() - KEEP_SECONDS,))
            return con.execute(
                "INSERT INTO outbound_compositions(facts,request,kind,scope,created) VALUES(?,?,?,?,?)",
                (json.dumps(facts, ensure_ascii=False), request, kind,
                 json.dumps(scope or {}, ensure_ascii=False), time.time())).lastrowid

    def finish(self, identity, state, text=""):
        with self.db.transaction() as con:
            con.execute("UPDATE outbound_compositions SET state=?,text=? WHERE id=?",
                        (state, text, identity))
