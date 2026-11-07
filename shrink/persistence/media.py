import time


class MemeRepository:
    def __init__(self, database):
        self.database = database
        self.root = database.root / "memes"

    def get(self, meme_id):
        rows = self.database.rows("SELECT * FROM memes WHERE id=?", (meme_id,))
        return rows[0] if rows else None

    def by_digest(self, digest):
        rows = self.database.rows("SELECT * FROM memes WHERE digest=?", (digest,))
        return rows[0] if rows else None

    def all(self):
        return self.database.rows("SELECT * FROM memes")

    def add(self, info, filename, title, tags, source):
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO memes(digest,phash,file,title,tags,source,created) VALUES(?,?,?,?,?,?,?)",
                (
                    info["digest"],
                    info["phash"],
                    filename,
                    title[:120],
                    tags[:500],
                    source,
                    time.time(),
                ),
            )
        return self.get(cursor.lastrowid)

    def choices(self, explicit, before):
        return self.database.rows(
            "SELECT id,title,tags FROM memes WHERE enabled=1 AND (? OR last_sent<?) "
            "ORDER BY last_sent ASC LIMIT 35",
            (explicit, before),
        )

    def mark_sent(self, meme_id, now):
        with self.database.transaction() as connection:
            connection.execute("UPDATE memes SET last_sent=? WHERE id=?", (now, meme_id))

    def delete(self, meme_id):
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM memes WHERE id=?", (meme_id,))

    def search(self, q="", offset=0, enabled=None):
        return self.database.rows(
            "SELECT * FROM memes WHERE (title LIKE ? OR tags LIKE ?) AND (? IS NULL OR enabled=?) "
            "ORDER BY id DESC LIMIT 60 OFFSET ?",
            (f"%{q[:100]}%", f"%{q[:100]}%", enabled, enabled, max(0, offset)),
        )

    def set_enabled(self, ids, enabled):
        with self.database.transaction() as connection:
            connection.executemany(
                "UPDATE memes SET enabled=? WHERE id=?", [(int(enabled), i) for i in ids]
            )

    def edit(self, meme_id, title, tags, enabled):
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE memes SET title=?,tags=?,enabled=? WHERE id=?",
                (title, tags, enabled, meme_id),
            )
        return bool(cursor.rowcount)
