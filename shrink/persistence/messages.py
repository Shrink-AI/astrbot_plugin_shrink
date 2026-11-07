import json
import time

from ..text_rules import learnable

REPEAT_WINDOW = 120


class MessageRepository:
    def __init__(self, database, state):
        self.database = database
        self.state = state

    # ---- ingest -------------------------------------------------------------------------------
    def ingest(self, message: dict) -> bool:
        """Store one platform message with identity, addressing metadata and the learning gate."""
        connection = self.database.connection
        if connection.execute("SELECT 1 FROM messages WHERE id=?", (message["id"],)).fetchone():
            return False
        now = time.time()
        uid = message["user_id"]
        name = message.get("name") or ""
        with self.database.transaction():
            old = connection.execute("SELECT aliases,kind FROM people WHERE id=?", (uid,)).fetchone()
            aliases = json.loads(old[0]) if old else []
            if name and name not in aliases:
                aliases.append(name)
            if old:
                connection.execute(
                    "UPDATE people SET name=CASE WHEN ?='' THEN name ELSE ? END,aliases=?,updated=? "
                    "WHERE id=?",
                    (name, name, json.dumps(aliases, ensure_ascii=False), now, uid),
                )
            else:
                connection.execute(
                    "INSERT INTO people(id,platform,account,name,aliases,kind,first_seen,updated) "
                    "VALUES(?,?,?,?,?,'human',?,?)",
                    (
                        uid,
                        message.get("platform", "qq"),
                        message.get("account", uid),
                        name or message.get("account", uid),
                        json.dumps(aliases, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
            is_bot = bool(old and old[1] == "bot")
            text = message.get("text", "")
            created = message.get("created", now)
            ok = learnable(text, sender_is_bot=is_bot)
            if ok and connection.execute(
                "SELECT 1 FROM messages WHERE user_id=? AND trim(text)=? AND created>? LIMIT 1",
                (uid, text.strip(), created - 86400),
            ).fetchone():
                ok = False
            if ok and text.strip():
                # A line repeated by several people is a chant, not a personal statement.
                repeats = connection.execute(
                    "SELECT COUNT(DISTINCT user_id) FROM messages WHERE source=? AND trim(text)=? "
                    "AND created>?",
                    (message["source"], text.strip(), created - REPEAT_WINDOW),
                ).fetchone()[0]
                ok = repeats < 2
            connection.execute(
                "INSERT INTO messages(id,user_id,source,source_name,speaker_name,text,quote,mentions,"
                "has_image,reply_to,addressee,is_bot,learnable,learned,digested,created) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,0,?,?,0,?)",
                (
                    message["id"],
                    uid,
                    message["source"],
                    message.get("source_name", ""),
                    name,
                    text,
                    json.dumps(message.get("quote", {}), ensure_ascii=False),
                    json.dumps(message.get("mentions", []), ensure_ascii=False),
                    int(bool(message.get("has_image"))),
                    message.get("reply_to", ""),
                    message.get("addressee", ""),
                    int(ok),
                    int(not ok),
                    created,
                ),
            )
        self.state.put("last_message", now)
        return True

    def record_bot(self, source, text, addressee="", reason="", created=None):
        """Store what the bot actually sent so context, digests and anti-repetition see it."""
        created = created or time.time()
        message_id = f"bot:{source}:{created:.6f}"
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO messages(id,user_id,source,source_name,speaker_name,text,"
                "quote,mentions,addressee,is_bot,learnable,learned,digested,created) "
                "VALUES(?,'self',?,'', '石英',?,?,'[]',?,1,0,1,0,?)",
                (
                    message_id,
                    source,
                    text,
                    json.dumps({"reason": reason}, ensure_ascii=False),
                    addressee,
                    created,
                ),
            )
        return message_id

    def set_addressee(self, message_id, addressee):
        with self.database.transaction() as connection:
            connection.execute("UPDATE messages SET addressee=? WHERE id=?", (addressee, message_id))

    # ---- conversation context -------------------------------------------------------------------
    def window(self, source, limit=200, before=None):
        """Newest-first messages of one conversation, with speaker kind."""
        return self.database.rows(
            "SELECT m.*,COALESCE(p.kind,'human') AS speaker_kind,COALESCE(p.name,m.speaker_name) "
            "AS person_name FROM messages m LEFT JOIN people p ON p.id=m.user_id "
            "WHERE m.source=? AND (? IS NULL OR m.created<=?) ORDER BY m.created DESC LIMIT ?",
            (source, before, before, limit),
        )

    def count_since(self, source, since, exclude_self=True):
        return self.database.connection.execute(
            "SELECT COUNT(*) FROM messages WHERE source=? AND created>? AND (?=0 OR is_bot=0)",
            (source, since, int(exclude_self)),
        ).fetchone()[0]

    def last_bot_reply_to(self, source, user_id):
        row = self.database.connection.execute(
            "SELECT MAX(created) FROM messages WHERE source=? AND is_bot=1 AND addressee=?",
            (source, user_id),
        ).fetchone()
        return row[0] if row and row[0] else 0

    def recent_bot(self, source, limit=5):
        return [
            r["text"]
            for r in self.database.rows(
                "SELECT text FROM messages WHERE source=? AND is_bot=1 ORDER BY created DESC LIMIT ?",
                (source, limit),
            )
        ]

    def elsewhere(self, user_id, source, since, limit=6):
        """The speaker's recent exchange with the bot in other conversations."""
        return self.database.rows(
            "SELECT * FROM messages WHERE source!=? AND created>? AND "
            "(user_id=? OR (is_bot=1 AND addressee=?)) ORDER BY created DESC LIMIT ?",
            (source, since, user_id, user_id, limit),
        )

    def get_many(self, ids):
        if not ids:
            return []
        marks = ",".join("?" for _ in ids)
        return self.database.rows(f"SELECT * FROM messages WHERE id IN ({marks})", tuple(ids))

    def around(self, ids, before=3, after=2, span=1800):
        """Cited messages with a few neighbours (within `span` seconds) from the same chat.

        Rows come in chat order; each carries `cited` and `gap` (messages were left out right
        before it)."""
        cited = {r["id"]: r for r in self.get_many(list(ids))}
        found = dict(cited)
        for row in cited.values():
            at = row["created"]
            for sql, args, n in (
                ("created<? AND created>=? ORDER BY created DESC", (at, at - span), before),
                ("created>? AND created<=? ORDER BY created", (at, at + span), after),
            ):
                for r in self.database.rows(
                    f"SELECT * FROM messages WHERE source=? AND {sql} LIMIT ?",
                    (row["source"], *args, n),
                ):
                    found.setdefault(r["id"], r)
        rows = sorted(found.values(), key=lambda r: (r["source"], r["created"], r["id"]))
        result, previous = [], None
        for r in rows:
            gap = bool(previous and previous["source"] == r["source"] and self.database.connection.execute(
                "SELECT 1 FROM messages WHERE source=? AND created>? AND created<? LIMIT 1",
                (r["source"], previous["created"], r["created"]),
            ).fetchone())
            result.append({**r, "cited": r["id"] in cited, "gap": gap})
            previous = r
        return result

    def containing(self, term, exclude=(), limit=5):
        """Recent human messages that use a word (bots' lines are left out)."""
        rows = self.database.rows(
            "SELECT m.* FROM messages m LEFT JOIN people p ON p.id=m.user_id "
            "WHERE instr(m.text,?)>0 AND m.is_bot=0 AND COALESCE(p.kind,'human')!='bot' "
            "ORDER BY m.created DESC LIMIT ?",
            (term, limit + len(exclude)),
        )
        return [r for r in rows if r["id"] not in set(exclude)][:limit]

    def search(self, source, term="", user_id="", since=0.0, limit=15):
        """One conversation's messages, newest first, filtered by word, speaker and time."""
        return self.database.rows(
            "SELECT m.*,COALESCE(p.kind,'human') AS speaker_kind,COALESCE(p.name,m.speaker_name) "
            "AS person_name FROM messages m LEFT JOIN people p ON p.id=m.user_id "
            "WHERE m.source=? AND m.created>=? AND (?='' OR instr(m.text,?)>0) "
            "AND (?='' OR m.user_id=?) AND length(trim(m.text))>0 "
            "ORDER BY m.created DESC LIMIT ?",
            (source, since, term, term, user_id, user_id, limit),
        )

    def between(self, source, start, end, limit=40):
        """Messages of one conversation in a time span, in chat order."""
        return self.database.rows(
            "SELECT m.*,COALESCE(p.kind,'human') AS speaker_kind,COALESCE(p.name,m.speaker_name) "
            "AS person_name FROM messages m LEFT JOIN people p ON p.id=m.user_id "
            "WHERE m.source=? AND m.created>? AND m.created<=? ORDER BY m.created LIMIT ?",
            (source, start, end, limit),
        )

    def by_user(self, user_id, limit=6):
        return self.database.rows(
            "SELECT * FROM messages WHERE user_id=? AND length(trim(text))>0 "
            "ORDER BY created DESC LIMIT ?",
            (user_id, limit),
        )

    def speakers(self, source, since):
        return self.database.rows(
            "SELECT DISTINCT user_id FROM messages WHERE source=? AND created>? AND is_bot=0",
            (source, since),
        )

    # ---- digest -----------------------------------------------------------------------------------
    def digest_candidates(self, older_than):
        return self.database.rows(
            "SELECT source,COUNT(*) AS n,MIN(created) AS first FROM messages "
            "WHERE digested=0 AND created<? GROUP BY source",
            (older_than,),
        )

    def undigested(self, source, older_than, limit=120):
        return self.database.rows(
            "SELECT m.*,COALESCE(p.kind,'human') AS speaker_kind FROM messages m "
            "LEFT JOIN people p ON p.id=m.user_id "
            "WHERE m.source=? AND m.digested=0 AND m.created<? ORDER BY m.created LIMIT ?",
            (source, older_than, limit),
        )

    def mark_digested(self, ids):
        with self.database.transaction() as connection:
            connection.executemany("UPDATE messages SET digested=1 WHERE id=?", [(i,) for i in ids])

    # ---- style learning ---------------------------------------------------------------------------
    def pending(self, limit):
        return self.database.rows(
            "SELECT * FROM messages WHERE learned=0 AND learnable=1 AND is_bot=0 "
            "AND length(trim(text))>0 ORDER BY created LIMIT ?",
            (limit,),
        )

    def recent(self):
        return self.database.rows(
            "SELECT * FROM messages WHERE learnable=1 AND is_bot=0 AND length(trim(text))>0 "
            "ORDER BY created DESC LIMIT 1000"
        )

    def mark_learned(self, batch):
        with self.database.transaction() as connection:
            connection.executemany(
                "UPDATE messages SET learned=1 WHERE id=?", [(m["id"],) for m in batch]
            )

    def mark_unlearnable(self, user_id):
        with self.database.transaction() as connection:
            connection.execute("UPDATE messages SET learnable=0 WHERE user_id=?", (user_id,))

    def interactions(self, user_id):
        row = self.database.connection.execute(
            "SELECT COUNT(*),MAX(created) FROM messages WHERE is_bot=1 AND addressee=?", (user_id,)
        ).fetchone()
        return {"replies": row[0] or 0, "last": row[1]}

    def said_to_bot(self, user_id, limit=8):
        return [
            r["text"]
            for r in self.database.rows(
                "SELECT text FROM messages WHERE user_id=? AND addressee='self' "
                "AND length(trim(text))>0 ORDER BY created DESC LIMIT ?",
                (user_id, limit),
            )
        ]
