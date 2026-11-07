class StatusRepository:
    def __init__(self, database, state, learning):
        self.database, self.state, self.learning = database, state, learning

    def _count(self, sql, args=()):
        return self.database.connection.execute(sql, args).fetchone()[0]

    def status(self):
        counts = {
            "people": self._count("SELECT COUNT(*) FROM people WHERE kind='human'"),
            "bots": self._count("SELECT COUNT(*) FROM people WHERE kind='bot'"),
            "messages": self._count("SELECT COUNT(*) FROM messages WHERE is_bot=0"),
            "bot_messages": self._count("SELECT COUNT(*) FROM messages WHERE is_bot=1"),
            "memories": self._count("SELECT COUNT(*) FROM memories WHERE status='active'"),
            "skills": self._count("SELECT COUNT(*) FROM skills WHERE status='active'"),
            "candidates": self._count("SELECT COUNT(*) FROM memories WHERE status='candidate'"),
            "topics": self._count(
                "SELECT COUNT(*) FROM memories WHERE kind='topic' AND status='active'"
            ),
            "glossary": self._count(
                "SELECT COUNT(*) FROM memories WHERE kind='glossary' AND status IN "
                "('active','candidate')"
            ),
            "nicknames": self._count("SELECT COUNT(*) FROM nicknames WHERE status='active'"),
            "expressions": self._count("SELECT COUNT(*) FROM expressions WHERE enabled=1"),
            "digests": self._count("SELECT COUNT(*) FROM digests"),
            "review_open": self._count("SELECT COUNT(*) FROM review_queue WHERE status='open'"),
            "memes": self._count("SELECT COUNT(*) FROM memes"),
            "memes_enabled": self._count("SELECT COUNT(*) FROM memes WHERE enabled=1"),
            "persona_versions": self._count("SELECT COUNT(*) FROM persona_versions"),
            "growth_versions": self._count(
                "SELECT COUNT(*) FROM persona_versions WHERE reason='自主学习'"
            ),
            "learning_successes": self._count(
                "SELECT COUNT(*) FROM learning_runs WHERE status='success' "
                "AND (diagnostics IS NULL OR json_extract(diagnostics,'$.mode')='apply')"
            ),
        }
        return {
            "counts": counts,
            "last_message": self.state.get("last_message"),
            "last_memory": self.state.get("last_memory"),
            "last_learning": self.state.get("last_learning"),
            "last_digest": self.state.get("last_digest"),
            "last_consolidation": self.state.get("last_consolidation"),
            "last_error": self.state.get("last_error"),
            "active_persona": self.state.get("active_persona"),
            "last_persona_update": self._count(
                "SELECT MAX(created) FROM persona_versions WHERE reason!='初始人格'"
            ),
            "pending_samples": self._count(
                "SELECT COUNT(*) FROM messages WHERE learned=0 AND learnable=1 AND is_bot=0 "
                "AND length(trim(text))>0"
            ),
            "undigested": self._count("SELECT COUNT(*) FROM messages WHERE digested=0"),
            "next_learning_check": (
                self.state.get("last_learning_check", 0) + self.state.settings.learning_hours * 3600
                if self.state.settings.learning_enabled
                else None
            ),
            "persona_sync": self.state.get("persona_sync"),
            "runs": [
                dict(r)
                for r in self.database.rows(
                    "SELECT id,kind,source,status,started,finished,stats,detail,undone FROM runs "
                    "ORDER BY id DESC LIMIT 8"
                )
            ],
            "learning_runs": [
                self.learning.learning_run(row)
                for row in self.database.rows(
                    "SELECT * FROM learning_runs ORDER BY id DESC LIMIT 5"
                )
            ],
        }
