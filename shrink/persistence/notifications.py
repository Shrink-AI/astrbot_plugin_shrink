import time


class NotificationRepository:
    def __init__(self, database):
        self.db = database
        with self.db.transaction() as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS notification_receipts("
                "id TEXT PRIMARY KEY,state TEXT NOT NULL,text TEXT NOT NULL DEFAULT '',"
                "created REAL NOT NULL,error TEXT NOT NULL DEFAULT '')")

    def reserve(self, identity):
        with self.db.transaction() as con:
            return bool(con.execute(
                "INSERT OR IGNORE INTO notification_receipts(id,state,created) VALUES(?,?,?)",
                (identity, "composing", time.time())).rowcount)

    def state(self, identity, state, text=""):
        with self.db.transaction() as con:
            con.execute("UPDATE notification_receipts SET state=?,text=? WHERE id=?",
                        (state, text, identity))

    def fail(self, identity, state, error):
        with self.db.transaction() as con:
            con.execute("UPDATE notification_receipts SET state=?,error=? "
                        "WHERE id=? AND state!='platform_accepted'", (state, error, identity))
