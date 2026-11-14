"""Durable grants, confirmation objects and once-only approval operations."""
import json
import time
import uuid


class Authorizations:
    def __init__(self, db):
        self.db = db
        with db.transaction() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS authorization_policies(
                  id TEXT PRIMARY KEY, grantor TEXT NOT NULL, input_id TEXT NOT NULL,
                  administrator TEXT NOT NULL, device TEXT NOT NULL, provider TEXT NOT NULL,
                  origin TEXT NOT NULL, task TEXT NOT NULL, execution_session TEXT NOT NULL,
                  categories TEXT NOT NULL, future INTEGER NOT NULL, expires REAL,
                  version INTEGER NOT NULL, state TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS authorization_audit(
                  id INTEGER PRIMARY KEY, policy TEXT NOT NULL, input_id TEXT NOT NULL,
                  action TEXT NOT NULL, actor TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS approval_events(
                  id TEXT PRIMARY KEY, input_id TEXT NOT NULL, body TEXT NOT NULL,
                  state TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', operation_id TEXT UNIQUE,
                  policy TEXT, policy_version INTEGER, answer_input TEXT, receipt TEXT, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS device_access_blocks(
                  provider TEXT NOT NULL, device TEXT NOT NULL, administrator TEXT NOT NULL,
                  input_id TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(provider,device,administrator));
            """)

    def policies(self, actor=None):
        rows = self.db.rows("SELECT * FROM authorization_policies WHERE (? IS NULL OR grantor=? OR administrator=?) "
                           "ORDER BY created DESC", (actor, actor, actor))
        for row in rows:
            row["categories"] = json.loads(row["categories"])
        return rows

    def policy(self, identity):
        return next((r for r in self.policies() if r["id"] == identity), None)

    def grant(self, values):
        identity = uuid.uuid4().hex
        with self.db.transaction() as con:
            con.execute("INSERT INTO authorization_policies VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1,'active',?)",
                        (identity, values["grantor"], values["input_id"], values["administrator"],
                         values["device"], values["provider"], values["origin"], values["task"],
                         values["execution_session"], json.dumps(values["categories"]), values["future"],
                         values["expires"], time.time()))
            self.audit(identity, values["input_id"], "grant", values["grantor"])
        return self.policy(identity)

    def audit(self, policy, input_id, action, actor):
        with self.db.transaction() as con:
            con.execute("INSERT INTO authorization_audit(policy,input_id,action,actor,created) VALUES(?,?,?,?,?)",
                        (policy, input_id, action, actor, time.time()))

    def change(self, identity, state, actor, input_id):
        with self.db.transaction() as con:
            con.execute("UPDATE authorization_policies SET state=?,version=version+1 WHERE id=?",
                        (state, identity))
            self.audit(identity, input_id, state, actor)

    def access_block(self, provider, device, administrator, input_id):
        with self.db.transaction() as con:
            con.execute("INSERT OR REPLACE INTO device_access_blocks VALUES(?,?,?,?,?)",
                        (provider, device, administrator, input_id, time.time()))
            for policy in self.policies():
                if policy["provider"] == provider and policy["device"] == device and (
                    not administrator or policy["administrator"] == administrator
                ):
                    self.change(policy["id"], "revoked", "device_access", input_id)

    def blocked(self, body):
        return bool(self.db.rows("SELECT 1 FROM device_access_blocks WHERE provider=? AND device=? "
                                "AND administrator IN ('',?)", (body["provider"], body["device"], body["administrator"])))

    def receive(self, identity, input_id, body):
        previous = self.find(identity)
        if previous:
            if previous["body"] != body:
                raise ValueError("确认标识不能复用于不同操作")
            return previous
        with self.db.transaction() as con:
            con.execute("INSERT INTO approval_events(id,input_id,body,state,created) VALUES(?,?,?,'pending',?)",
                        (identity, input_id, json.dumps(body, ensure_ascii=False), time.time()))
        return self.event(identity)

    def event(self, identity):
        rows = self.db.rows("SELECT * FROM approval_events WHERE id=?", (identity,))
        if not rows:
            raise ValueError("确认对象不存在")
        row = rows[0]
        row["body"] = json.loads(row["body"])
        row["receipt"] = json.loads(row["receipt"]) if row["receipt"] else None
        return row

    def find(self, identity):
        return self.event(identity) if self.db.rows("SELECT id FROM approval_events WHERE id=?", (identity,)) else None

    def events(self):
        return [self.event(r["id"]) for r in self.db.rows("SELECT id FROM approval_events ORDER BY created DESC")]

    def update(self, identity, state, reason="", receipt=None):
        with self.db.transaction() as con:
            con.execute("UPDATE approval_events SET state=?,reason=?,receipt=COALESCE(?,receipt) WHERE id=?",
                        (state, reason, json.dumps(receipt) if receipt is not None else None, identity))

    def reserve(self, identity, policy=None, answer_input=None):
        operation = "approval:" + identity
        with self.db.transaction() as con:
            changed = con.execute("UPDATE approval_events SET state='issuing',operation_id=?,policy=?,"
                                  "policy_version=?,answer_input=? WHERE id=? AND operation_id IS NULL",
                                  (operation, policy["id"] if policy else None,
                                   policy["version"] if policy else None, answer_input, identity)).rowcount
        return operation if changed else None
