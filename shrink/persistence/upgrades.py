"""SQLite backup, logical repair and upgrade evidence. No host or model dependencies."""
import hashlib
import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def connect(path, *, readonly=False):
    con = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True) if readonly else sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def digest(value):
    def encode(obj):
        if isinstance(obj, bytes):
            return {"blob": obj.hex()}
        raise TypeError(type(obj).__name__)
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), default=encode).encode()).hexdigest()


def file_hash(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def backup_database(path, backup):
    with closing(connect(path, readonly=True)) as source, closing(connect(backup)) as target:
        source.backup(target)
    return file_hash(backup)


def restore_database(path, backup):
    # The caller must close every connection before replacing the main file.
    for suffix in ("-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    temporary = Path(str(path) + ".restore")
    import shutil
    shutil.copyfile(backup, temporary)
    os.replace(temporary, path)


def table_rows(con, table):
    return [dict(row) for row in con.execute("SELECT * FROM " + quote(table) + " NOT INDEXED")]


def integrity(con):
    return [row[0] for row in con.execute("PRAGMA integrity_check")]


def logical_rebuild(path, prompt_rows):
    """Do not DROP a damaged btree: copy readable tables into independent pages."""
    temporary = Path(str(path) + ".rebuild")
    temporary.unlink(missing_ok=True)
    try:
        with closing(connect(path, readonly=True)) as source, closing(connect(temporary)) as target:
            schema = [dict(r) for r in source.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY rowid")]
            shadows = {r["name"] for r in source.execute("PRAGMA table_list") if r["type"] == "shadow"}
            tables = [r for r in schema if r["type"] == "table" and r["name"] not in shadows
                      and not r["name"].startswith("sqlite_")]
            virtual = [r for r in tables if r["sql"].upper().startswith("CREATE VIRTUAL")]
            ordinary = [r for r in tables if r not in virtual]
            for row in ordinary:
                target.execute(row["sql"])
                rows = prompt_rows if row["name"] == "prompt_versions" else table_rows(source, row["name"])
                if rows:
                    columns = list(rows[0])
                    target.executemany("INSERT INTO " + quote(row["name"]) + "(" +
                                       ",".join(map(quote, columns)) + ") VALUES(" +
                                       ",".join("?" for _ in columns) + ")",
                                       [tuple(item[k] for k in columns) for item in rows])
                if row["name"] != "prompt_versions":
                    if digest(sorted((digest(r) for r in rows))) != digest(
                            sorted(digest(r) for r in table_rows(target, row["name"]))):
                        raise ValueError("logical copy record mismatch")
            for row in virtual:
                target.execute(row["sql"])
                # Both supported FTS indexes are projections of their content tables.
                if row["name"] not in {"memory_fts", "digest_fts"}:
                    raise ValueError("unsupported virtual table in offline repair")
                target.execute("INSERT INTO " + quote(row["name"]) + "(" +
                               quote(row["name"]) + ") VALUES('rebuild')")
            for row in schema:
                if row["type"] in {"index", "trigger", "view"} and row["tbl_name"] not in shadows:
                    target.execute(row["sql"])
            if any(r["name"] == "sqlite_sequence" for r in schema):
                target.execute("DELETE FROM sqlite_sequence")
                target.executemany("INSERT INTO sqlite_sequence VALUES(?,?)",
                                   [tuple(r.values()) for r in table_rows(source, "sqlite_sequence")])
            for pragma in ("user_version", "application_id"):
                target.execute(f"PRAGMA {pragma}={int(source.execute('PRAGMA ' + pragma).fetchone()[0])}")
            target.commit()
            if integrity(target) != ["ok"]:
                raise ValueError("logical repair integrity check failed")
        for suffix in ("-wal", "-shm"):
            Path(str(path) + suffix).unlink(missing_ok=True)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class UpgradeStore:
    def __init__(self, database):
        self.db = database
        with database.transaction() as con:
            con.execute("CREATE TABLE IF NOT EXISTS upgrade_runs("
                        "version TEXT PRIMARY KEY,report TEXT NOT NULL,created REAL NOT NULL)")
            con.execute("CREATE TABLE IF NOT EXISTS upgrade_archive("
                        "id INTEGER PRIMARY KEY,category TEXT NOT NULL,identity TEXT NOT NULL,"
                        "value TEXT NOT NULL,created REAL NOT NULL)")

    def archive(self, category, identity, value):
        with self.db.transaction() as con:
            con.execute("INSERT INTO upgrade_archive(category,identity,value,created) VALUES(?,?,?,?)",
                        (category, identity, json.dumps(value, ensure_ascii=False), time.time()))

    def complete(self, version, report):
        with self.db.transaction() as con:
            con.execute("INSERT OR REPLACE INTO upgrade_runs VALUES(?,?,?)",
                        (version, json.dumps(report, ensure_ascii=False), time.time()))

    def remove_prefixes(self):
        rows = self.db.rows("SELECT * FROM kv WHERE key LIKE 'context_prefix:%'")
        with self.db.transaction() as con:
            for row in rows:
                self.archive("context_prefix", row["key"], row["value"])
                con.execute("DELETE FROM kv WHERE key=?", (row["key"],))
        return len(rows)

    def replace_settings(self, settings):
        with self.db.transaction() as con:
            con.execute("INSERT OR REPLACE INTO kv VALUES('settings',?)",
                        (json.dumps(settings, ensure_ascii=False),))

    def correct_message(self, identity, text):
        with self.db.transaction() as con:
            con.execute("UPDATE messages SET text=? WHERE id=?", (text, identity))
            if con.execute("SELECT 1 FROM sqlite_master WHERE name='conversation_events'").fetchone():
                con.execute("DELETE FROM conversation_checkpoints WHERE session IN "
                            "(SELECT session FROM conversation_events WHERE message_id=?)", (identity,))

    def dependent_records(self, corrections):
        messages = {r["id"]: r for r in table_rows(self.db.connection, "messages") if r["id"] in corrections}
        counts = {}
        for table, fields in (("memories", ("text", "quote", "evidence")),
                              ("expressions", ("phrase", "situation", "evidence")),
                              ("digests", ("summary",))):
            rows = table_rows(self.db.connection, table)
            counts[table] = {
                "explicit_references": sum(any(identity in (r[field] or "")
                                                for identity in messages for field in fields) for r in rows),
                "covering_time_ranges": sum(any(
                    r["source"] == m["source"] and r["start"] <= m["created"] <= r["end"]
                    for m in messages.values()) for r in rows) if table == "digests" else 0}
        return counts


def snapshot_records(con):
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return {name: table_rows(con, name) if name in names else [] for name in
            ("messages", "people", "memories", "operation_tasks", "notification_receipts")}


def latest_report(con, version):
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name='upgrade_runs'").fetchone():
        return None
    row = con.execute("SELECT report FROM upgrade_runs WHERE version=?", (version,)).fetchone()
    return json.loads(row[0]) if row else None


def inspect_upgrade(path, version, *, full=True):
    with closing(connect(path, readonly=True)) as con:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name='kv'").fetchone():
            return None
        stored = con.execute("SELECT value FROM kv WHERE key='settings'").fetchone()
        from .history import HistoryRepository
        result = {"settings": json.loads(stored[0]) if stored else {},
                "previous": latest_report(con, version),
                "current_history": bool(con.execute("SELECT 1 FROM kv WHERE key=?",
                                                   (HistoryRepository.MIGRATION,)).fetchone()),
                "old_delivery": any(r["name"] == "delivery"
                                    for r in con.execute("PRAGMA table_info(operation_tasks)")),
                "prompts": table_rows(con, "prompt_versions")}
        if full:
            result.update({"records": snapshot_records(con),
                           "task_events": table_rows(con, "operation_events") if con.execute(
                               "SELECT 1 FROM sqlite_master WHERE name='operation_events'").fetchone() else [],
                           "integrity": integrity(con)})
        else:
            # Only this small table needs structural validation on a completed startup.
            result["integrity"] = [r[0] for r in con.execute("PRAGMA integrity_check('prompt_versions')")]
        return result


def archive_settings(path, stored, mapped, unknown):
    from .database import Database
    db = Database(path.parent, filename=path.name, initialize=False)
    try:
        store = UpgradeStore(db)
        clean = {k: v for k, v in stored.items() if k not in mapped and k not in unknown}
        for old, new in mapped.items():
            clean.setdefault(new[0], new[1])
        with db.transaction():
            for category, values in (("settings_mapped", {k: v[1] for k, v in mapped.items()}),
                                     ("settings_unknown", unknown)):
                for key, value in values.items():
                    store.archive(category, key, value)
            store.replace_settings(clean)
    finally:
        db.close()


def retained_tables(db):
    return [r["name"] for r in db.rows(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def probe_data(db):
    messages = table_rows(db.connection, "messages")
    return {"sessions": db.rows("SELECT id FROM conversation_sessions ORDER BY id"),
            "people": db.rows("SELECT id FROM people ORDER BY id LIMIT 3"),
            "messages": messages, "receipts": table_rows(db.connection, "notification_receipts"),
            "memories": table_rows(db.connection, "memories"),
            "digests": table_rows(db.connection, "digests"),
            "now": max((r["created"] for r in messages), default=time.time()) + 1}


def prepare_migration_temp(db):
    db.connection.execute("PRAGMA temp_store=MEMORY")
