import hashlib
import json
import time


class PersonaRepository:
    def __init__(self, database, state):
        self.database = database
        self.state = state

    def add_version(self, text, evidence, reason, reject_digest=None):
        with self.database.transaction():
            digest = hashlib.sha256(text.encode()).hexdigest()
            cursor = self.database.connection.execute(
                "INSERT INTO persona_versions(text,digest,evidence,reason,created) VALUES(?,?,?,?,?)",
                (text, digest, json.dumps(evidence, ensure_ascii=False), reason, time.time()),
            )
            self.database.connection.execute(
                "INSERT OR REPLACE INTO kv VALUES ('active_persona',?)",
                (json.dumps(cursor.lastrowid),),
            )

            if reject_digest:
                self.database.connection.execute(
                    "INSERT OR REPLACE INTO rejected_personas VALUES(?,?)",
                    (reject_digest, time.time()),
                )
        return cursor.lastrowid

    def learned_persona(self):
        row = self.database.connection.execute(
            "SELECT text FROM persona_versions WHERE id=?", (self.state.get("active_persona", 0),)
        ).fetchone()
        return row[0] if row else ""

    def get(self, version_id):
        rows = self.database.rows("SELECT * FROM persona_versions WHERE id=?", (version_id,))
        return rows[0] if rows else None

    def versions(self):
        return self.database.rows("SELECT * FROM persona_versions ORDER BY id DESC LIMIT 100")

    def rejected(self):
        return {r["digest"] for r in self.database.rows("SELECT digest FROM rejected_personas")}
