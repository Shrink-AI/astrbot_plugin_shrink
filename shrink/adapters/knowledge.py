"""AstrBot KB content authority with a local compatibility projection and durable outbox."""
import asyncio
import hashlib
import json
import sqlite3
import time
import re
from datetime import datetime, timedelta, timezone


class KnowledgeBridge:
    def __init__(self, context, app):
        self.context, self.app, self.db = context, app, app.database
        self.helpers = {}
        self.lock = asyncio.Lock()
        self.rerank_lock = asyncio.Lock()
        self.worker = None
        self.error = ""
        self.last_sync = 0
        self.canonical = {}
        self.retrieval_ok = False
        self.db.connection.executescript("""
        CREATE TABLE IF NOT EXISTS knowledge_links(
          kind TEXT NOT NULL, row_id INTEGER NOT NULL, kb_id TEXT NOT NULL,
          doc_id TEXT NOT NULL, digest TEXT NOT NULL, updated REAL NOT NULL,
          PRIMARY KEY(kind,row_id));
        CREATE TABLE IF NOT EXISTS knowledge_outbox(
          kind TEXT NOT NULL,row_id INTEGER NOT NULL,revision INTEGER NOT NULL DEFAULT 1,
          error TEXT NOT NULL DEFAULT '',PRIMARY KEY(kind,row_id));
        CREATE TABLE IF NOT EXISTS knowledge_guard(id INTEGER PRIMARY KEY,active INTEGER NOT NULL);
        INSERT OR IGNORE INTO knowledge_guard VALUES(1,0);
        CREATE TABLE IF NOT EXISTS knowledge_retired(
          kb_id TEXT NOT NULL,doc_id TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS knowledge_projection(
          kind TEXT NOT NULL,row_id INTEGER NOT NULL,body TEXT NOT NULL,digest TEXT NOT NULL,
          PRIMARY KEY(kind,row_id));
        """)
        # This is a read-only, last-verified cache, never an independently
        # editable memory store. Pending edits must not replace it before upload.
        for link in self.db.rows("SELECT * FROM knowledge_links"):
            cached = self.db.rows("SELECT body,digest FROM knowledge_projection WHERE kind=? AND row_id=?",
                                  (link["kind"], link["row_id"]))
            field = "text" if link["kind"] == "memories" else "summary"
            cached += self.db.rows(f"SELECT {field} body FROM {link['kind']} WHERE id=?", (link["row_id"],))
            for candidate in cached:
                if hashlib.sha256(candidate["body"].encode()).hexdigest() == link["digest"]:
                    self.cache(link["kind"], link["row_id"], candidate["body"])
                    break
        for table, field in (("memories", "text"), ("digests", "summary")):
            for action, row in (("INSERT", "new"), ("UPDATE", "new"), ("DELETE", "old")):
                changed = f" AND old.{field} IS NOT new.{field}" if action == "UPDATE" else ""
                self.db.connection.executescript(f"""
                CREATE TRIGGER IF NOT EXISTS knowledge_{table}_{action.lower()}
                AFTER {action} ON {table}
                WHEN (SELECT active FROM knowledge_guard WHERE id=1)=0{changed}
                BEGIN
                  INSERT INTO knowledge_outbox(kind,row_id) VALUES('{table}',{row}.id)
                  ON CONFLICT(kind,row_id) DO UPDATE SET revision=revision+1,error='';
                END;
                """)

    def status(self):
        return {"authority": self.app.state.get("knowledge_authority", "migrating"),
                "pending": self.db.rows("SELECT * FROM knowledge_outbox ORDER BY kind,row_id"),
                "linked": self.db.rows("SELECT kind,count(*) AS count FROM knowledge_links GROUP BY kind"),
                "last_sync": self.last_sync, "error": self.error,
                "rerank": self.app.state.get("rerank_status", {}),
                "retrieval": self.app.state.get("knowledge_retrieval", {}),
                "projection_errors": self.app.state.get("knowledge_projection_errors", []),
                "ready": len(self.helpers) == 2 and self.worker is not None}

    def cache(self, kind, row_id, body):
        with self.db.transaction() as con:
            con.execute("INSERT OR REPLACE INTO knowledge_projection VALUES(?,?,?,?)",
                        (kind, row_id, body, hashlib.sha256(body.encode()).hexdigest()))
        self.canonical[(kind, row_id)] = body

    async def start(self):
        while True:
            try:
                await self.initialize()
                return
            except Exception as exc:
                self.error = f"知识库初始化失败，稍后重试：{type(exc).__name__}: {str(exc)[:300]}"
                self.app.tasks.error("knowledge_initialize", exc)
                await asyncio.sleep(15)

    async def initialize(self):
        # AstrBot creates providers and KB services after plugin initialize().
        for _ in range(120):
            manager = getattr(self.context, "kb_manager", None)
            if manager is not None and getattr(manager, "kb_db", None) is not None:
                provider = await self.context.provider_manager.get_provider_by_id(
                    self.app.state.settings.embedding_provider)
                if provider is not None:
                    break
            await asyncio.sleep(1)
        else:
            raise RuntimeError("AstrBot知识库或向量模型未就绪")
        backup = self.app.state.root / "shrink-before-knowledge.db"
        if not backup.exists():
            destination = sqlite3.connect(backup)
            try:
                self.db.connection.backup(destination)
            finally:
                destination.close()
        manager = self.context.kb_manager
        for kind, title in (("memories", "石英记忆"), ("digests", "石英聊天摘要")):
            helper = await manager.get_kb_by_name(title)
            if helper is None:
                helper = await manager.create_kb(
                    kb_name=title, description="由石英筛选整理，来源和确认状态由石英核心管理。",
                    embedding_provider_id=self.app.state.settings.embedding_provider,
                    top_k_dense=40, top_k_sparse=40, top_m_final=40)
            # Scope/confidence filtering must precede the single core rerank.
            if helper.kb.rerank_provider_id:
                helper = await manager.update_kb(
                    kb_id=helper.kb.kb_id, kb_name=title, rerank_provider_id=None)
                if helper is None or helper.kb.rerank_provider_id:
                    raise RuntimeError("无法关闭记忆库筛选前的重复重排")
            self.helpers[kind] = helper
        await self.restore_projection()
        await self.rename_documents()
        with self.db.transaction() as con:
            for kind in self.helpers:
                con.execute(f"INSERT OR IGNORE INTO knowledge_outbox(kind,row_id) "
                            f"SELECT ?,id FROM {kind} WHERE id NOT IN "
                            "(SELECT row_id FROM knowledge_links WHERE kind=?)", (kind, kind))
        self.worker = asyncio.create_task(self.loop())

    def document_name(self, kind, row_id, body):
        rows = self.db.rows(f"SELECT * FROM {kind} WHERE id=?", (row_id,))
        row = rows[0] if rows else {}
        date = datetime.fromtimestamp(row.get("created", time.time()),
                                      timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
        subject = row.get("subject", "")
        people = self.db.rows("SELECT name FROM people WHERE id=?", (subject,)) if subject else []
        source = row.get("source", "")
        place = (people[0]["name"] if people else "") or (
            "私聊" if ":private:" in source else "群聊" if ":group:" in source else "资料")
        label = "聊天摘要" if kind == "digests" else {
            "topic": "话题", "event": "经历", "impression": "人物",
            "glossary": "词条", "lesson": "反馈"}.get(row.get("kind"), "记忆")
        title = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", body)
        title = re.sub(r"\s+", " ", title).strip(" 。")[:42]
        place = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", place)[:20]
        return f"{date} · {place} · {label} · {title or '无标题'}.txt"

    async def rename_documents(self):
        """Change display metadata only; preserve document IDs, chunks and embeddings."""
        from sqlalchemy import update
        from astrbot.core.knowledge_base.models import KBDocument
        async with self.context.kb_manager.kb_db.get_db() as session:
            for link in self.db.rows("SELECT * FROM knowledge_links"):
                body = self.canonical.get((link["kind"], link["row_id"]))
                if body is not None:
                    await session.execute(update(KBDocument).where(
                        KBDocument.doc_id == link["doc_id"]).values(
                        doc_name=self.document_name(link["kind"], link["row_id"], body)))
            await session.commit()

    async def restore_projection(self):
        """Reload canonical KB text; queued local edits remain queued, never discarded."""
        errors = []
        for link in self.db.rows("SELECT * FROM knowledge_links"):
            helper = self.helpers[link["kind"]]
            try:
                chunks = await helper.get_chunks_by_doc_id(link["doc_id"], limit=1000)
                if len(chunks) != 1:
                    raise ValueError("条目缺失或结构变化")
            except Exception as exc:
                errors.append(f"{link['doc_id']}: {str(exc)[:200]}")
                continue
            text = chunks[0]["content"]
            self.cache(link["kind"], link["row_id"], text)
            with self.db.transaction() as con:
                con.execute("UPDATE knowledge_links SET digest=? WHERE kind=? AND row_id=?",
                            (hashlib.sha256(text.encode()).hexdigest(), link["kind"], link["row_id"]))
            if self.db.rows("SELECT 1 FROM knowledge_outbox WHERE kind=? AND row_id=?",
                            (link["kind"], link["row_id"])):
                continue
            field = "text" if link["kind"] == "memories" else "summary"
            with self.db.transaction() as con:
                con.execute("UPDATE knowledge_guard SET active=1 WHERE id=1")
                try:
                    con.execute(f"UPDATE {link['kind']} SET {field}=? WHERE id=?", (text, link["row_id"]))
                    con.execute("UPDATE knowledge_links SET digest=? WHERE kind=? AND row_id=?",
                                (hashlib.sha256(text.encode()).hexdigest(), link["kind"], link["row_id"]))
                finally:
                    con.execute("UPDATE knowledge_guard SET active=0 WHERE id=1")
        self.app.state.put("knowledge_projection_errors", errors)

    async def sync(self):
        async with self.lock:
            self.error = ""
            pending = self.db.rows("SELECT * FROM knowledge_outbox ORDER BY kind,row_id LIMIT 8")
            for item in pending:
                kind, row_id = item["kind"], item["row_id"]
                field = "text" if kind == "memories" else "summary"
                rows = self.db.rows(f"SELECT {field} AS body FROM {kind} WHERE id=?", (row_id,))
                previous = self.db.rows("SELECT * FROM knowledge_links WHERE kind=? AND row_id=?", (kind, row_id))
                helper = self.helpers[kind]
                try:
                    if rows:
                        body = rows[0]["body"]
                        doc = await helper.upload_document(
                            file_name=self.document_name(kind, row_id, body),
                            file_content=None, file_type="txt",
                            pre_chunked_text=[body], tasks_limit=1, max_retries=1)
                        chunks = await helper.get_chunks_by_doc_id(doc.doc_id)
                        if len(chunks) != 1 or chunks[0]["content"] != body:
                            await helper.delete_document(doc.doc_id)
                            raise ValueError("知识库正文校验不一致")
                        with self.db.transaction() as con:
                            con.execute("INSERT OR REPLACE INTO knowledge_links VALUES(?,?,?,?,?,?)",
                                        (kind, row_id, helper.kb.kb_id, doc.doc_id,
                                         hashlib.sha256(body.encode()).hexdigest(), time.time()))
                        self.cache(kind, row_id, body)
                    else:
                        with self.db.transaction() as con:
                            con.execute("DELETE FROM knowledge_links WHERE kind=? AND row_id=?", (kind, row_id))
                            con.execute("DELETE FROM knowledge_projection WHERE kind=? AND row_id=?", (kind, row_id))
                        self.canonical.pop((kind, row_id), None)
                    with self.db.transaction() as con:
                        for old in previous:
                            con.execute("INSERT OR IGNORE INTO knowledge_retired VALUES(?,?)",
                                        (old["kb_id"], old["doc_id"]))
                        con.execute("DELETE FROM knowledge_outbox WHERE kind=? AND row_id=? AND revision=?",
                                    (kind, row_id, item["revision"]))
                except Exception as exc:
                    self.error = f"{type(exc).__name__}: {str(exc)[:500]}"
                    with self.db.transaction() as con:
                        con.execute("UPDATE knowledge_outbox SET error=? WHERE kind=? AND row_id=?",
                                    (self.error, kind, row_id))
            for old in self.db.rows("SELECT * FROM knowledge_retired LIMIT 8"):
                try:
                    helper = await self.context.kb_manager.get_kb(old["kb_id"])
                    await helper.delete_document(old["doc_id"])
                    with self.db.transaction() as con:
                        con.execute("DELETE FROM knowledge_retired WHERE doc_id=?", (old["doc_id"],))
                except Exception as exc:
                    self.error = str(exc)[:500]
            self.last_sync = time.time()
            if not self.db.rows("SELECT 1 FROM knowledge_outbox LIMIT 1"):
                self.app.state.put("knowledge_authority", "astrbot_kb")

    async def loop(self):
        while True:
            try:
                await self.sync()
            except Exception as exc:
                self.error = str(exc)[:500]
                self.app.tasks.error("knowledge_sync", exc)
            await asyncio.sleep(2 if not self.error else 15)

    async def candidates(self, query):
        self.retrieval_ok = False
        if self.app.state.get("knowledge_authority") != "astrbot_kb":
            return []
        try:
            result = await asyncio.wait_for(self.context.kb_manager.retrieve(
                query=query, kb_names=["石英记忆"], top_k_fusion=60, top_m_final=40), 10)
            self.retrieval_ok = True
            self.app.state.put("knowledge_retrieval", {"status": "ok", "at": time.time()})
            ids = []
            for hit in (result or {}).get("results", []):
                links = self.db.rows("SELECT row_id FROM knowledge_links WHERE kind='memories' AND doc_id=?",
                                     (hit["doc_id"],))
                ids.extend(x["row_id"] for x in links)
            return self.app.memory_repository.many(ids)
        except Exception as exc:
            self.app.state.put("knowledge_retrieval", {"status": "degraded", "error": str(exc)[:300],
                                                     "at": time.time()})
            self.app.tasks.error("knowledge_retrieve", exc)
            return []

    def resolve(self, rows, kind="memories"):
        """Use last verified KB bodies while edits remain in the durable outbox."""
        if self.app.state.get("knowledge_authority") != "astrbot_kb":
            return rows
        field = "text" if kind == "memories" else "summary"
        return [{**row, field: self.canonical[(kind, row["id"])]} for row in rows
                if (kind, row["id"]) in self.canonical]

    async def rerank(self, text, scored):
        if not scored:
            return scored
        try:
            provider = await self.context.provider_manager.get_provider_by_id("local_bge_reranker")
            if provider is None:
                raise ValueError("重排模型未就绪")
            async with asyncio.timeout(20):
                async with self.rerank_lock:
                    results = await provider.rerank(
                        text, [row["text"] for _, row in scored[:30]], top_n=30)
            if not results:
                raise ValueError("重排未返回结果")
            ranks = {r.index: r.relevance_score for r in results}
            # Inputs have already passed the existing scope/confidence rules.
            ordered = sorted(enumerate(scored[:30]),
                             key=lambda x: x[1][0] * (0.8 + 0.4 * ranks.get(x[0], 0)), reverse=True)
            self.app.state.put("rerank_status", {"status": "ok", "at": time.time()})
            return [pair for _, pair in ordered] + scored[30:]
        except Exception as exc:
            error = "重排超时（含排队，20秒预算）" if isinstance(exc, TimeoutError) else (str(exc) or type(exc).__name__)
            self.app.state.put("rerank_status", {"status": "degraded", "error": error[:300],
                                               "at": time.time()})
            return scored

    async def close(self):
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
