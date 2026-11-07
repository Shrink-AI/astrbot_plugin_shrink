class SessionRepository:
    def __init__(self, database):
        self.database = database

    def active(self, token_hash, now):
        rows = self.database.rows(
            "SELECT * FROM sessions WHERE token_hash=? AND expires>?", (token_hash, now)
        )
        return rows[0] if rows else None

    def rotate(self, old, token_hash, csrf, now):
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash=? OR expires<?", (old, now))
            connection.execute(
                "INSERT INTO sessions VALUES(?,?,?)", (token_hash, csrf, now + 28800)
            )

    def revoke(self, token_hash):
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))

    def revoke_all(self):
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM sessions")
