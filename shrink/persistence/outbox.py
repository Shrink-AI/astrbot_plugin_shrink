"""Durable notification facts, send ownership and queue positions."""
import json
import time


class OutboxRepository:
    def __init__(self, database):
        self.db = database
        with database.transaction() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS notification_outbox(
                  id TEXT PRIMARY KEY, event_id TEXT NOT NULL, input_id TEXT NOT NULL,
                  session TEXT NOT NULL, sender TEXT NOT NULL, message_type TEXT NOT NULL,
                  kind TEXT NOT NULL, task_id TEXT NOT NULL, facts TEXT,
                  fact_refs TEXT NOT NULL, request TEXT NOT NULL, priority INTEGER NOT NULL,
                  state TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', merged_into TEXT NOT NULL DEFAULT '',
                  created REAL NOT NULL, available REAL NOT NULL, decision TEXT NOT NULL DEFAULT '{}');
                CREATE INDEX IF NOT EXISTS outbox_ready ON notification_outbox(state,available,priority,created);
                CREATE TABLE IF NOT EXISTS notification_fact_claims(
                  session TEXT NOT NULL, fact TEXT NOT NULL, owner TEXT NOT NULL,
                  PRIMARY KEY(session,fact));
                CREATE TABLE IF NOT EXISTS notification_send_owners(
                  session TEXT NOT NULL, sender TEXT NOT NULL, message_type TEXT NOT NULL,
                  owner TEXT NOT NULL, PRIMARY KEY(session,sender,message_type));
                CREATE TABLE IF NOT EXISTS notification_event_owners(
                  id TEXT PRIMARY KEY, owner TEXT NOT NULL);
            """)

    def ownership(self, session, sender, message_type, owner):
        with self.db.transaction() as con:
            con.execute("INSERT OR IGNORE INTO notification_send_owners VALUES(?,?,?,?)",
                        (session, sender, message_type, owner))
            return con.execute("SELECT owner FROM notification_send_owners WHERE session=? AND sender=? "
                               "AND message_type=?", (session, sender, message_type)).fetchone()[0] == owner

    def event_owner(self, identity, owner):
        with self.db.transaction() as con:
            con.execute("INSERT OR IGNORE INTO notification_event_owners VALUES(?,?)", (identity, owner))
            return con.execute("SELECT owner FROM notification_event_owners WHERE id=?", (identity,)).fetchone()[0] == owner

    def get(self, identity):
        rows = self.db.rows("SELECT o.*,m.text input_facts FROM notification_outbox o "
                           "JOIN input_events i ON i.id=o.input_id JOIN messages m ON m.id=i.message_id "
                           "WHERE o.id=?", (identity,))
        if not rows:
            return None
        row = rows[0]
        row["facts"] = row["facts"] or row["input_facts"]
        row.pop("input_facts")
        for key in ("facts", "fact_refs", "decision"):
            row[key] = json.loads(row[key])
        return row

    def insert(self, value):
        with self.db.transaction() as con:
            con.execute(
                "INSERT OR IGNORE INTO notification_outbox"
                "(id,event_id,input_id,session,sender,message_type,kind,task_id,facts,fact_refs,request,"
                "priority,state,reason,created,available) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                tuple(None if key == "facts" else json.dumps(value[key], ensure_ascii=False)
                      if key == "fact_refs" else value[key]
                      for key in ("id", "event_id", "input_id", "session", "sender", "message_type", "kind",
                                  "task_id", "facts", "fact_refs", "request", "priority", "state", "reason",
                                  "created", "available")))
        return self.get(value["id"])

    def state(self, identity, state, reason="", *, decision=None, merged_into=""):
        with self.db.transaction() as con:
            con.execute("UPDATE notification_outbox SET state=?,reason=?,merged_into=?,"
                        "decision=COALESCE(?,decision) WHERE id=?",
                        (state, reason, merged_into,
                         json.dumps(decision, ensure_ascii=False) if decision else None, identity))

    def claim_facts(self, row):
        with self.db.transaction() as con:
            owners = [con.execute("SELECT owner FROM notification_fact_claims WHERE session=? AND fact=?",
                                  (row["session"], ref)).fetchone() for ref in row["fact_refs"]]
            if owners and all(owner and owner[0] != row["id"] for owner in owners):
                return False
            con.executemany("INSERT OR IGNORE INTO notification_fact_claims VALUES(?,?,?)",
                            [(row["session"], ref, row["id"]) for ref in row["fact_refs"]])
        return True

    def acknowledge(self, session, refs, receipt):
        with self.db.transaction() as con:
            con.executemany("INSERT INTO notification_fact_claims VALUES(?,?,?) "
                            "ON CONFLICT(session,fact) DO UPDATE SET owner=excluded.owner",
                            [(session, ref, "foreground:" + receipt) for ref in refs])

    def ready(self, now=None):
        return [self.get(r["id"]) for r in self.db.rows(
            "SELECT id FROM notification_outbox WHERE state IN ('pending','merging','delivering') "
            "AND available<=? ORDER BY priority,created,rowid", (time.time() if now is None else now,))]

    def list(self, limit=100):
        return [self.get(r["id"]) for r in self.db.rows(
            "SELECT id FROM notification_outbox ORDER BY created DESC LIMIT ?", (max(1, min(limit, 500)),))]

    def task(self, identity):
        if not self.db.rows("SELECT name FROM sqlite_master WHERE name='operation_tasks'"):
            raise ValueError("任务不存在")
        rows = self.db.rows("SELECT * FROM operation_tasks WHERE id=?", (identity,))
        if not rows:
            raise ValueError("任务不存在")
        return rows[0]

    def siblings(self, row):
        if not row["task_id"]:
            return []
        return [self.get(r["id"]) for r in self.db.rows(
            "SELECT id FROM notification_outbox WHERE task_id=? AND session=? AND state IN ('pending','merging') "
            "AND kind IN ('progress','tool_progress') AND id!=?",
            (row["task_id"], row["session"], row["id"]))]

    def merge(self, leader, others):
        facts = {"status": (leader["facts"].get("status") if isinstance(leader["facts"], dict) else None),
                 "progress": [r["facts"] for r in [*others, leader]]}
        refs = list(dict.fromkeys(ref for r in [leader, *others] for ref in r["fact_refs"]))
        with self.db.transaction() as con:
            con.execute("UPDATE notification_outbox SET facts=?,fact_refs=? WHERE id=?",
                        (json.dumps(facts, ensure_ascii=False), json.dumps(refs, ensure_ascii=False), leader["id"]))
            for other in others:
                self.state(other["id"], "suppressed", "merged", merged_into=leader["id"])
        return self.get(leader["id"])

    def merge_questions(self, row):
        if not isinstance(row["facts"], dict) or not row["facts"].get("confirmations"):
            return row
        others = [self.get(r["id"]) for r in self.db.rows(
            "SELECT id FROM notification_outbox WHERE session=? AND sender=? AND kind='question' "
            "AND state='pending' AND id!=?", (row["session"], row["sender"], row["id"]))]
        others = [r for r in others if isinstance(r["facts"], dict) and r["facts"].get("confirmations")]
        entries = {c["confirmation_id"]: c for r in [row, *others] for c in r["facts"]["confirmations"]
                   if isinstance(c, dict) and c.get("confirmation_id")}
        if not entries:
            return row
        with self.db.transaction() as con:
            facts = {**row["facts"], "confirmations": list(entries.values())}
            self.facts(row["id"], facts)
            for other in others:
                self.state(other["id"], "suppressed", "merged_confirmation_questions", merged_into=row["id"])
        return self.get(row["id"])

    def facts(self, identity, facts):
        with self.db.transaction() as con:
            con.execute("UPDATE notification_outbox SET facts=? WHERE id=?", (json.dumps(facts, ensure_ascii=False), identity))

    def waiting_confirmations(self, confirmations):
        return [c for c in confirmations if self.db.rows(
            "SELECT 1 FROM approval_events WHERE id=? AND state='waiting_user'", (c["confirmation_id"],))]
