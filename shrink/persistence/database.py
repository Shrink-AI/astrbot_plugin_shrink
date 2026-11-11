import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from ..config import Settings
from .schema import migrate_schema


class Database:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        (root / "memes").mkdir(exist_ok=True)
        self.connection = sqlite3.connect(root / "shrink.db")
        self.connection.row_factory = sqlite3.Row
        try:
            migrate_schema(self.connection)
        except BaseException:
            self.connection.close()
            raise

    @contextmanager
    def transaction(self):
        # Repository transactions are synchronous; never hold one across an await.
        with self.connection:
            yield self.connection

    def rows(self, sql, args=()):
        return [dict(row) for row in self.connection.execute(sql, args)]

    def close(self):
        self.connection.close()


class StateRepository:
    def __init__(self, database: Database):
        self.database = database
        self.root = database.root
        stored = self.get("settings")
        if stored is None:
            self.put("settings", Settings().model_dump())
        elif set(Settings.model_fields) - set(stored):
            # Write defaults for settings added in newer versions so the stored copy is complete.
            self.put("settings", Settings(**stored).model_dump())
        # Administrator-requested, one-time migration. Preserve all existing groups
        # and allow later explicit settings edits without re-adding removed entries.
        migration = "group_blacklist_reporting_fix_v1"
        if not self.get(migration):
            settings = self.get("settings")
            settings["group_blacklist"] = list(dict.fromkeys([
                *settings.get("group_blacklist", []), "1037550568", "790980552"]))
            with self.database.transaction() as con:
                for key, value in (("settings", settings), (migration, True)):
                    con.execute("INSERT OR REPLACE INTO kv VALUES (?,?)",
                                (key, json.dumps(value, ensure_ascii=False)))

    def get(self, key, default=None):
        rows = self.database.rows("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def put(self, key, value):
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO kv VALUES (?,?)",
                (key, json.dumps(value, ensure_ascii=False)),
            )

    @property
    def settings(self):
        return Settings(**self.get("settings"))
