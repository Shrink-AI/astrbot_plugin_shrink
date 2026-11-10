import time


class MaintenanceRepository:
    def __init__(self, db):
        self.db = db
        db.connection.executescript("""
        CREATE TABLE IF NOT EXISTS capability_upkeep(
          kind TEXT, name TEXT, managed INTEGER NOT NULL DEFAULT 0,
          protected INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'active',
          origin TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '',
          snapshot TEXT NOT NULL DEFAULT '', version TEXT NOT NULL DEFAULT '',
          retired REAL, updated REAL NOT NULL, PRIMARY KEY(kind,name));
        CREATE TABLE IF NOT EXISTS capability_upkeep_audit(
          id INTEGER PRIMARY KEY,kind TEXT,name TEXT,action TEXT,reason TEXT,created REAL);
        CREATE TABLE IF NOT EXISTS capability_task_reviews(task_id TEXT PRIMARY KEY,episode INTEGER);
        """)

    def list(self):
        return self.db.rows("SELECT * FROM capability_upkeep ORDER BY updated DESC")

    def get(self, kind, name):
        return next((r for r in self.list() if (r["kind"], r["name"]) == (kind, name)), None)

    def register(self, kind, name, origin="", managed=False, protected=False):
        with self.db.transaction() as con:
            con.execute("INSERT OR IGNORE INTO capability_upkeep(kind,name,origin,managed,protected,updated)"
                        " VALUES(?,?,?,?,?,?)", (kind, name, origin, int(managed), int(protected), time.time()))

    def update(self, kind, name, **fields):
        allowed = {"managed", "protected", "state", "origin", "reason", "snapshot", "version", "retired"}
        if set(fields) - allowed:
            raise ValueError("无效维护字段")
        with self.db.transaction() as con:
            con.execute("UPDATE capability_upkeep SET " + ",".join(f"{k}=?" for k in fields)
                        + ",updated=? WHERE kind=? AND name=?", (*fields.values(), time.time(), kind, name))

    def audit(self, kind, name, action, reason):
        with self.db.transaction() as con:
            con.execute("INSERT INTO capability_upkeep_audit(kind,name,action,reason,created) VALUES(?,?,?,?,?)",
                        (kind, name, action, reason, time.time()))

    def history(self):
        return self.db.rows("SELECT * FROM capability_upkeep_audit ORDER BY id DESC LIMIT 100")

    def skill_inventory(self):
        return self.db.rows("""SELECT s.*,v.origin creator FROM skills s LEFT JOIN skill_versions v
            ON v.id=(SELECT min(id) FROM skill_versions WHERE skill_id=s.id)""")

    def created_agents(self):
        if not self.db.rows("SELECT name FROM sqlite_master WHERE name='operation_events'"):
            return {}
        return {r["plugin"]: r["result"] for r in self.db.rows(
            "SELECT plugin,result FROM operation_events WHERE action='agent_created' ORDER BY id")}

    def tasks(self):
        if not self.db.rows("SELECT name FROM sqlite_master WHERE name='operation_tasks'"):
            return []
        return self.db.rows("SELECT * FROM operation_tasks")

    def linked_skill_names(self):
        return {r["name"] for r in self.db.rows("SELECT name FROM native_skill_links")}

    def created_skills(self):
        if not self.db.rows("SELECT name FROM sqlite_master WHERE name='operation_events'"):
            return set()
        return {r["plugin"] for r in self.db.rows(
            "SELECT plugin FROM operation_events WHERE action='skill_created'")}

    def review_tasks(self):
        if not self.db.rows("SELECT name FROM sqlite_master WHERE name='operation_tasks'"):
            return []
        return self.db.rows("""SELECT t.* FROM operation_tasks t LEFT JOIN capability_task_reviews r
            ON r.task_id=t.id WHERE r.task_id IS NULL AND t.status='completed'
            AND t.delivery IN ('platform_accepted','delegated') ORDER BY t.finished LIMIT 20""")

    def task_events(self, task_id):
        return self.db.rows("SELECT action,result FROM operation_events WHERE task_id=? "
                            "AND action LIKE 'tool_end:%' ORDER BY id", (task_id,))

    def reviewed(self, task_id, episode):
        with self.db.transaction() as con:
            con.execute("INSERT OR IGNORE INTO capability_task_reviews VALUES(?,?)", (task_id, episode))

    def purge_skill(self, skill_id):
        with self.db.transaction() as con:
            for table, column in (("native_skill_links", "id"), ("native_skill_resolutions", "skill_id"),
                                  ("skill_versions", "skill_id"), ("skills", "id")):
                if self.db.rows("SELECT name FROM sqlite_master WHERE name=?", (table,)):
                    con.execute(f"DELETE FROM {table} WHERE {column}=?", (skill_id,))
            # Undo must not resurrect expired content after the recovery window.
            con.execute("DELETE FROM run_changes WHERE tbl='skills' AND row_key=?", (str(skill_id),))
