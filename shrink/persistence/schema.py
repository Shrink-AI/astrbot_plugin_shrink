"""Create the SQLite schema (idempotent)."""

VERSION = 4

TABLES = [
    "CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    """CREATE TABLE IF NOT EXISTS people(
      id TEXT PRIMARY KEY, platform TEXT NOT NULL, account TEXT NOT NULL, name TEXT NOT NULL,
      aliases TEXT NOT NULL DEFAULT '[]', kind TEXT NOT NULL DEFAULT 'human',
      first_seen REAL NOT NULL, updated REAL NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS messages(
      id TEXT PRIMARY KEY, user_id TEXT NOT NULL, source TEXT NOT NULL,
      source_name TEXT NOT NULL DEFAULT '', speaker_name TEXT NOT NULL DEFAULT '',
      text TEXT NOT NULL, quote TEXT NOT NULL DEFAULT '{}', mentions TEXT NOT NULL DEFAULT '[]',
      has_image INTEGER NOT NULL DEFAULT 0, reply_to TEXT NOT NULL DEFAULT '',
      addressee TEXT NOT NULL DEFAULT '', is_bot INTEGER NOT NULL DEFAULT 0,
      learnable INTEGER NOT NULL DEFAULT 1, learned INTEGER NOT NULL DEFAULT 0,
      digested INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS messages_source_time ON messages(source, created)",
    "CREATE INDEX IF NOT EXISTS messages_learning ON messages(learned, learnable, created)",
    "CREATE INDEX IF NOT EXISTS messages_digest ON messages(digested, source, created)",
    "CREATE INDEX IF NOT EXISTS messages_user_time ON messages(user_id, created)",
    """CREATE TABLE IF NOT EXISTS memories(
      id INTEGER PRIMARY KEY, kind TEXT NOT NULL, subject TEXT NOT NULL DEFAULT '',
      term TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '', text TEXT NOT NULL,
      quote TEXT NOT NULL DEFAULT '', evidence TEXT NOT NULL DEFAULT '[]',
      participants TEXT NOT NULL DEFAULT '[]', importance INTEGER NOT NULL DEFAULT 3,
      confidence REAL NOT NULL DEFAULT 0.6, certainty TEXT NOT NULL DEFAULT 'stated',
      status TEXT NOT NULL DEFAULT 'active', verified INTEGER NOT NULL DEFAULT 0,
      verified_at REAL, valid_until REAL, origin TEXT NOT NULL DEFAULT 'chat',
      run TEXT NOT NULL DEFAULT '', superseded_by INTEGER, recalls INTEGER NOT NULL DEFAULT 0,
      last_recalled REAL, created REAL NOT NULL, updated REAL NOT NULL, vector TEXT,
      legacy_id TEXT UNIQUE)""",
    "CREATE INDEX IF NOT EXISTS memories_subject ON memories(subject, status)",
    "CREATE INDEX IF NOT EXISTS memories_kind ON memories(kind, status)",
    "CREATE INDEX IF NOT EXISTS memories_run ON memories(run)",
    """CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
      text, content='memories', content_rowid='id', tokenize='trigram')""",
    """CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
      INSERT INTO memory_fts(rowid,text) VALUES(new.id,new.text); END""",
    """CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
      INSERT INTO memory_fts(memory_fts,rowid,text) VALUES('delete',old.id,old.text); END""",
    """CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE OF text ON memories BEGIN
      INSERT INTO memory_fts(memory_fts,rowid,text) VALUES('delete',old.id,old.text);
      INSERT INTO memory_fts(rowid,text) VALUES(new.id,new.text); END""",
    """CREATE TABLE IF NOT EXISTS nicknames(
      id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL,
      scope TEXT NOT NULL DEFAULT '', origin TEXT NOT NULL DEFAULT 'ai',
      confidence REAL NOT NULL DEFAULT 0.6, status TEXT NOT NULL DEFAULT 'active',
      verified INTEGER NOT NULL DEFAULT 0, evidence TEXT NOT NULL DEFAULT '[]',
      run TEXT NOT NULL DEFAULT '', created REAL NOT NULL, updated REAL NOT NULL,
      UNIQUE(scope, name))""",
    "CREATE INDEX IF NOT EXISTS nicknames_user ON nicknames(user_id)",
    """CREATE TABLE IF NOT EXISTS profiles(
      subject TEXT PRIMARY KEY, kind TEXT NOT NULL DEFAULT 'person', summary TEXT NOT NULL,
      relation TEXT NOT NULL DEFAULT '', evidence TEXT NOT NULL DEFAULT '[]',
      verified INTEGER NOT NULL DEFAULT 0, run TEXT NOT NULL DEFAULT '', updated REAL NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS expressions(
      id INTEGER PRIMARY KEY, scope TEXT NOT NULL DEFAULT '', situation TEXT NOT NULL,
      phrase TEXT NOT NULL, evidence TEXT NOT NULL DEFAULT '[]',
      enabled INTEGER NOT NULL DEFAULT 1, uses INTEGER NOT NULL DEFAULT 0,
      run TEXT NOT NULL DEFAULT '', created REAL NOT NULL, updated REAL NOT NULL,
      UNIQUE(scope, phrase))""",
    """CREATE TABLE IF NOT EXISTS digests(
      id INTEGER PRIMARY KEY, source TEXT NOT NULL, start REAL NOT NULL, end REAL NOT NULL,
      count INTEGER NOT NULL, summary TEXT NOT NULL, run TEXT NOT NULL DEFAULT '',
      created REAL NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS digests_source ON digests(source, end)",
    """CREATE VIRTUAL TABLE IF NOT EXISTS digest_fts USING fts5(
      summary, content='digests', content_rowid='id', tokenize='trigram')""",
    """CREATE TRIGGER IF NOT EXISTS digests_ai AFTER INSERT ON digests BEGIN
      INSERT INTO digest_fts(rowid,summary) VALUES(new.id,new.summary); END""",
    """CREATE TRIGGER IF NOT EXISTS digests_ad AFTER DELETE ON digests BEGIN
      INSERT INTO digest_fts(digest_fts,rowid,summary) VALUES('delete',old.id,old.summary); END""",
    """CREATE TABLE IF NOT EXISTS runs(
      id INTEGER PRIMARY KEY, kind TEXT NOT NULL, source TEXT NOT NULL DEFAULT '',
      status TEXT NOT NULL, started REAL NOT NULL, finished REAL,
      stats TEXT NOT NULL DEFAULT '{}', detail TEXT NOT NULL DEFAULT '',
      undone INTEGER NOT NULL DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS run_changes(
      id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, tbl TEXT NOT NULL, row_key TEXT NOT NULL,
      action TEXT NOT NULL, before TEXT)""",
    "CREATE INDEX IF NOT EXISTS run_changes_run ON run_changes(run_id)",
    """CREATE TABLE IF NOT EXISTS review_queue(
      id INTEGER PRIMARY KEY, kind TEXT NOT NULL, target TEXT NOT NULL, reason TEXT NOT NULL,
      priority INTEGER NOT NULL DEFAULT 5, status TEXT NOT NULL DEFAULT 'open',
      created REAL NOT NULL, decided REAL, UNIQUE(kind, target, reason))""",
    """CREATE TABLE IF NOT EXISTS teach_sessions(
      id INTEGER PRIMARY KEY, title TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS teach_messages(
      id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL, role TEXT NOT NULL,
      text TEXT NOT NULL, created REAL NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS teach_proposals(
      id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL, message_id INTEGER,
      kind TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
      result TEXT NOT NULL DEFAULT '', created REAL NOT NULL, decided REAL)""",
    """CREATE TABLE IF NOT EXISTS persona_versions(
      id INTEGER PRIMARY KEY, text TEXT NOT NULL, digest TEXT NOT NULL,
      evidence TEXT NOT NULL, reason TEXT NOT NULL, created REAL NOT NULL)""",
    "CREATE TABLE IF NOT EXISTS rejected_personas(digest TEXT PRIMARY KEY, created REAL NOT NULL)",
    """CREATE TABLE IF NOT EXISTS learning_runs(
      id INTEGER PRIMARY KEY, status TEXT NOT NULL, started REAL NOT NULL, finished REAL,
      sample_count INTEGER NOT NULL DEFAULT 0, detail TEXT NOT NULL, diagnostics TEXT)""",
    """CREATE TABLE IF NOT EXISTS memes(
      id INTEGER PRIMARY KEY, digest TEXT NOT NULL UNIQUE, phash TEXT NOT NULL,
      file TEXT NOT NULL, title TEXT NOT NULL, tags TEXT NOT NULL,
      source TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
      created REAL NOT NULL, last_sent REAL NOT NULL DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS sessions(
      token_hash TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires REAL NOT NULL)""",
    # 0.6: know-how 石英 builds from its own tool use and revises from how it turned out.
    """CREATE TABLE IF NOT EXISTS skills(
      id INTEGER PRIMARY KEY, title TEXT NOT NULL, trigger TEXT NOT NULL, steps TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'active', version INTEGER NOT NULL DEFAULT 1,
      uses INTEGER NOT NULL DEFAULT 0, wins INTEGER NOT NULL DEFAULT 0,
      losses INTEGER NOT NULL DEFAULT 0, last_used REAL, run TEXT NOT NULL DEFAULT '',
      vector TEXT, created REAL NOT NULL, updated REAL NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS skill_versions(
      id INTEGER PRIMARY KEY, skill_id INTEGER NOT NULL, version INTEGER NOT NULL,
      title TEXT NOT NULL, trigger TEXT NOT NULL, steps TEXT NOT NULL, status TEXT NOT NULL,
      reason TEXT NOT NULL, episodes TEXT NOT NULL DEFAULT '[]', origin TEXT NOT NULL,
      run TEXT NOT NULL DEFAULT '', created REAL NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS skill_versions_skill ON skill_versions(skill_id, version)",
    """CREATE TABLE IF NOT EXISTS episodes(
      id INTEGER PRIMARY KEY, source TEXT NOT NULL, user_id TEXT NOT NULL,
      message_id TEXT NOT NULL, question TEXT NOT NULL, tools TEXT NOT NULL DEFAULT '[]',
      answer TEXT NOT NULL DEFAULT '', outcome TEXT NOT NULL, skills TEXT NOT NULL DEFAULT '[]',
      duration REAL NOT NULL DEFAULT 0, verdict TEXT NOT NULL DEFAULT '',
      lesson TEXT NOT NULL DEFAULT '', reviewed INTEGER NOT NULL DEFAULT 0,
      created REAL NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS episodes_review ON episodes(reviewed, created)",
]

def _create(connection):
    for statement in TABLES:
        connection.execute(statement)


def migrate_schema(connection):
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.commit()
    connection.execute("BEGIN IMMEDIATE")
    try:
        _create(connection)
        connection.execute(f"PRAGMA user_version={VERSION}")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
