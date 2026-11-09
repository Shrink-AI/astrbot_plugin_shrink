"""Saved prompt edits. Every save or reset is a row; the latest row per key is in effect.
A row with ``text`` NULL means "back to the shipped default"."""

import time


class PromptRepository:
    def __init__(self, database):
        self.database = database

    def active(self):
        rows = self.database.rows(
            "SELECT p.key, p.text, p.created FROM prompt_versions p JOIN "
            "(SELECT key, MAX(id) AS id FROM prompt_versions GROUP BY key) latest ON latest.id = p.id"
        )
        return {r["key"]: r for r in rows if r["text"] is not None}

    def save(self, key, text, note=""):
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO prompt_versions(key, text, note, created) VALUES (?,?,?,?)",
                (key, text, note, time.time()),
            )
        return cursor.lastrowid

    def history(self, key, limit=30):
        return self.database.rows(
            "SELECT id, text, note, created FROM prompt_versions WHERE key=? ORDER BY id DESC LIMIT ?",
            (key, limit),
        )
