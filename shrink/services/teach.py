"""Directed learning: the administrator teaches or confirms; the model only proposes."""

import datetime
import json
import re
import time

from ..config import BOT_NAME
from ..persistence.review import family
from ..prompts.teach import PROPOSE, REVIEW, propose_prompt, review_prompt
from ..text_rules import GENERIC_ADDRESS, RELATIVE_DAY, SELF_IDENTITY
from .model_output import ask_json

KINDS = {"memory", "nickname", "glossary", "correct", "retire", "confirm", "mark_bot"}


class TeachService:
    def __init__(self, *, repository, people, memory, review, runs, messages, model, error):
        self.repository, self.people, self.memory = repository, people, memory
        self.review, self.runs, self.messages = review, runs, messages
        self.model, self.error = model, error

    # ---- conversation --------------------------------------------------------------------------------
    def resolve(self, text):
        found = []
        database = self.people.repository.database
        for row in database.rows("SELECT user_id,name FROM nicknames WHERE status='active'"):
            if len(row["name"]) >= 2 and row["name"] in text:
                found.append(row["user_id"])
        for row in database.rows("SELECT id,name,aliases,account FROM people"):
            names = [row["name"], row["account"], *json.loads(row["aliases"] or "[]")]
            if any(n and len(n) >= 2 and n in text for n in names):
                found.append(row["id"])
        return [u for u in dict.fromkeys(found)][:8]

    def context(self, uids, text):
        refs, roster = self.people.roster(uids)
        memory_refs, lines = {}, []
        for uid in uids:
            for row in self.memory.repository.for_subject(uid, ("impression", "event"), 6):
                ref = f"k{len(memory_refs) + 1}"
                memory_refs[ref] = row["id"]
                state = {1: "已确认", -1: "判定错误"}.get(row["verified"], "未确认")
                if row["status"] == "candidate":
                    state = "候选"
                lines.append(f"{ref} [{self.people.display(uid)}·{row['kind']}·{state}] {row['text']}")
        for term, memory_id in self.memory.repository.glossary_terms().items():
            if term in text:
                row = self.memory.repository.get(memory_id)
                ref = f"k{len(memory_refs) + 1}"
                memory_refs[ref] = memory_id
                lines.append(f"{ref} [黑话·{term}] {row['text']}")
        return refs, roster, memory_refs, "\n".join(lines)

    async def chat(self, session_id, text):
        if not self.repository.session(session_id):
            raise ValueError("会话不存在")
        history = "\n".join(
            ("管理员：" if m["role"] == "admin" else "你：") + m["text"][:300]
            for m in self.repository.messages(session_id, 10)
        )
        self.repository.add_message(session_id, "admin", text)
        uids = self.resolve(text)
        refs, roster, memory_refs, memories = self.context(uids, text)
        try:
            answer = await ask_json(
                self.model,
                propose_prompt(people=roster, memories=memories, history=history, message=text,
                               today=datetime.date.today().isoformat()),
                PROPOSE.system,
                timeout=150,
            )
        except Exception as exc:
            self.error("teach", exc)
            reason = "模型响应超时" if isinstance(exc, TimeoutError) else "模型没有返回有效结果"
            reply = f"这次没处理好（{reason}），可以再发一次或换个说法。"
            self.repository.add_message(session_id, "assistant", reply)
            return {"reply": reply, "proposals": []}
        answer = answer if isinstance(answer, dict) else {}
        reply = str(answer.get("reply") or "").strip()[:600] or "我整理了下面这些建议。"
        reply_id = self.repository.add_message(session_id, "assistant", reply)
        created = []
        proposals = answer.get("proposals") if isinstance(answer.get("proposals"), list) else []
        for raw in proposals[:12]:
            payload, problem = self.validate(raw, refs, memory_refs)
            kind = str(raw.get("type")) if isinstance(raw, dict) else "unknown"
            if problem:
                self.repository.add_proposal(session_id, reply_id, kind, raw if isinstance(raw, dict)
                                             else {}, "invalid", problem)
                continue
            created.append(self.repository.add_proposal(session_id, reply_id, kind, payload))
        return {"reply": reply, "proposals": [self.repository.proposal(i) for i in created]}

    def validate(self, raw, refs, memory_refs):
        if not isinstance(raw, dict) or raw.get("type") not in KINDS:
            return None, "未知的建议类型"
        kind = raw["type"]

        def person(key):
            uid = refs.get(str(raw.get(key) or ""))
            if not uid or uid == "self":
                return None
            return uid

        def memory(key):
            memory_id = memory_refs.get(str(raw.get(key) or ""))
            return memory_id if memory_id and self.memory.repository.get(memory_id) else None

        def text_ok(value, low=4, high=300):
            return isinstance(value, str) and low <= len(value.strip()) <= high and not (
                SELF_IDENTITY.search(value) or RELATIVE_DAY.search(value)
            )

        if kind == "memory":
            uid = person("subject")
            if not uid:
                return None, "找不到对应的人"
            if raw.get("kind") not in ("impression", "event", "topic") or not text_ok(raw.get("text")):
                return None, "内容不符合要求（长度、相对时间或涉及石英身份）"
            importance = raw.get("importance") if isinstance(raw.get("importance"), int) else 4
            return {
                "kind": raw["kind"], "subject": uid, "subject_name": self.people.display(uid),
                "text": raw["text"].strip(), "importance": max(1, min(5, importance)),
                "valid_until": raw.get("valid_until") if isinstance(raw.get("valid_until"), str)
                else None,
            }, None
        if kind == "nickname":
            uid = person("person")
            name = str(raw.get("name") or "").strip()
            if not uid:
                return None, "找不到对应的人"
            if not 1 <= len(name) <= 12 or name in GENERIC_ADDRESS or BOT_NAME in name:
                return None, "称呼不符合要求"
            scope = str(raw.get("scope") or "all")
            digits = re.sub(r"\D", "", scope)
            return {
                "user_id": uid, "user_name": self.people.display(uid), "name": name,
                "scope": f"qq:group:{digits}" if digits else "",
            }, None
        if kind == "glossary":
            term = str(raw.get("term") or "").strip()
            meaning = str(raw.get("meaning") or "").strip()
            if not 2 <= len(term) <= 20 or not 2 <= len(meaning) <= 80:
                return None, "黑话或释义长度不符合要求"
            return {"term": term, "meaning": meaning}, None
        if kind in ("correct", "retire", "confirm"):
            memory_id = memory("memory")
            if not memory_id:
                return None, "找不到对应的记忆"
            row = self.memory.repository.get(memory_id)
            payload = {"memory_id": memory_id, "old_text": row["text"]}
            if kind == "correct":
                if not text_ok(raw.get("text")):
                    return None, "改正后的内容不符合要求"
                payload["text"] = raw["text"].strip()
            if kind == "retire":
                payload["reason"] = str(raw.get("reason") or "")[:100]
            return payload, None
        uid = person("person")
        if not uid:
            return None, "找不到对应的人"
        return {"user_id": uid, "user_name": self.people.display(uid),
                "bot": bool(raw.get("bot", True))}, None

    # ---- applying ------------------------------------------------------------------------------------
    async def accept(self, proposal_id, payload=None):
        proposal = self.repository.proposal(proposal_id)
        if not proposal or proposal["status"] != "pending":
            raise ValueError("建议不存在或已处理")
        if payload is not None:
            merged = {**proposal["payload"], **{k: v for k, v in payload.items()
                                                if k in ("text", "name", "meaning", "importance")}}
            proposal["payload"] = merged
        run_id = self.runs.start("teach", detail=f"会话 {proposal['session_id']}")
        try:
            result = await self.apply(proposal, run_id)
        except Exception as exc:
            self.runs.finish(run_id, "failed", {}, type(exc).__name__)
            raise
        self.repository.decide(proposal_id, "accepted", result, proposal["payload"])
        self.runs.finish(run_id, "success", {"proposal": proposal_id, "kind": proposal["kind"]}, result)
        return result

    async def apply(self, proposal, run_id):
        kind, data = proposal["kind"], proposal["payload"]
        evidence = [f"teach:{proposal['session_id']}"]
        repo = self.memory.repository
        if kind == "memory":
            valid = None
            if data.get("valid_until"):
                try:
                    valid = datetime.datetime.strptime(data["valid_until"][:10], "%Y-%m-%d").timestamp() + 86399
                except ValueError:
                    valid = None
            memory_id = repo.add(
                data["kind"], "" if data["kind"] == "topic" else data["subject"], "admin",
                data["text"], evidence=evidence,
                participants=[data["subject"]] if data["kind"] == "topic" else [],
                importance=data.get("importance", 4), confidence=1.0, status="active",
                origin="admin_teach", run_id=run_id, verified=1, valid_until=valid,
            )
            await self.memory.index_memory(memory_id)
            return f"已写入记忆 #{memory_id}"
        if kind == "nickname":
            nid, outcome = self.people.repository.add_nickname(
                data["user_id"], data["name"], scope=data["scope"], origin="admin", confidence=1.0,
                evidence=evidence, run_id=run_id, verified=1,
            )
            if outcome in ("conflict", "rejected"):
                self.people.repository.set_nickname(
                    nid, run_id, user_id=data["user_id"], origin="admin", verified=1,
                    confidence=1.0, status="active",
                )
            self.review.close_target("nickname%", nid)
            self.people.invalidate()
            return f"已记录称呼“{data['name']}”"
        if kind == "glossary":
            existing = repo.glossary_terms().get(data["term"])
            if existing:
                repo.update(existing, run_id, text=data["meaning"], verified=1, confidence=1.0,
                            status="active", verified_at=time.time())
                await self.memory.index_memory(existing)
                return f"已更新黑话“{data['term']}”"
            memory_id = repo.add("glossary", "", "", data["meaning"], term=data["term"],
                                 evidence=evidence, confidence=1.0, origin="admin_teach",
                                 run_id=run_id, verified=1)
            await self.memory.index_memory(memory_id)
            return f"已写入黑话“{data['term']}”"
        if kind == "correct":
            old = repo.get(data["memory_id"])
            memory_id = repo.add(
                old["kind"], old["subject"], old["source"], data["text"], term=old["term"],
                evidence=json.loads(old["evidence"]) + evidence,
                participants=json.loads(old["participants"]), importance=old["importance"],
                confidence=1.0, origin="admin_teach", run_id=run_id, verified=1,
            )
            repo.update(old["id"], run_id, status="superseded", superseded_by=memory_id)
            await self.memory.index_memory(memory_id)
            self.review.close_target("%", old["id"])
            return f"已改正，新记忆 #{memory_id}"
        if kind == "retire":
            repo.update(data["memory_id"], run_id, status="rejected", verified=-1,
                        verified_at=time.time())
            self.review.close_target("%", data["memory_id"])
            return "已判定为错误"
        if kind == "confirm":
            repo.update(data["memory_id"], run_id, status="active", verified=1, confidence=1.0,
                        verified_at=time.time())
            self.review.close_target("%", data["memory_id"])
            return "已确认"
        if kind == "mark_bot":
            self.people.repository.set_kind(data["user_id"], "bot" if data["bot"] else "human",
                                            run_id)
            if data["bot"]:
                self.messages.mark_unlearnable(data["user_id"])
            return "已更新机器人标记"
        if kind == "question":
            return "问题已回答"
        raise ValueError("未知的建议类型")

    def reject(self, proposal_id):
        if not self.repository.decide(proposal_id, "rejected", "管理员拒绝"):
            raise ValueError("建议不存在或已处理")
        return True

    # ---- review queue --------------------------------------------------------------------------------
    def describe(self, item):
        repo = self.memory.repository
        kind, target = item["kind"], item["target"]
        if kind in ("memory_candidate", "memory_unverified", "glossary_guess"):
            row = repo.get(int(target))
            if not row:
                return None
            if row["kind"] == "glossary":
                return f"黑话“{row['term']}”的意思是：{row['text']}"
            who = self.people.display(row["subject"]) if row["subject"] else "群聊话题"
            return f"关于 {who}：{row['text']}"
        if kind == "memory_conflict":
            new_id, old_id = (int(x) for x in target.split(":"))
            new, old = repo.get(new_id), repo.get(old_id)
            if not new or not old:
                return None
            return f"新说法“{new['text']}”和已确认的“{old['text']}”矛盾，新说法对吗"
        if kind in ("nickname_conflict", "nickname_guess"):
            row = self.people.repository.nickname(int(target))
            if not row:
                return None
            return f"群里说的“{row['name']}”指的是 {self.people.display(row['user_id'])}"
        if kind == "profile_new":
            profile = self.people.repository.profile(target)
            if not profile:
                return None
            return f"{self.people.display(target)} 的小结：{profile['summary']}"
        if kind == "suspected_bot":
            if not self.people.repository.get(target):
                return None
            return f"{self.people.display(target)} 是机器人"
        return None

    def review_items(self, limit=30, offset=0):
        """Open questions, one per target row, each with the chat it came from."""
        rows = self.review.open(self.review.count_open())
        newest = {}
        for item in rows:
            key = (family(item["kind"]), item["target"])
            if key not in newest or item["id"] > newest[key]:
                newest[key] = item["id"]
        items = []
        for item in rows:
            if newest[(family(item["kind"]), item["target"])] != item["id"]:
                self.review.decide(item["id"], "merged")
                self.repository.decide_review(item["id"], "skip")
                continue
            text = self.describe(item)
            if text is None:
                self.review.decide(item["id"], "skipped")
                self.repository.decide_review(item["id"], "skip")
            else:
                items.append({**item, "text": text})
        offset = max(0, offset)
        return [{**item, **self.review_context(item)} for item in items[offset:offset + limit]]

    # ---- what the administrator needs to judge a question -------------------------------------------
    def _person(self, uid):
        person = self.people.repository.get(uid) if uid and uid != "self" else None
        if not person:
            return None
        names = [n for n in self.people.names_of(uid) if n != person["name"]][:6]
        return {"id": uid, "name": person["name"], "account": person["account"], "names": names,
                "bot": person["kind"] == "bot"}

    def _line(self, row):
        speaker = BOT_NAME if row["is_bot"] else (
            row["speaker_name"] or self.people.display(row["user_id"])
        )
        return {"id": row["id"], "speaker": speaker, "source": row["source"],
                "group": row["source_name"], "created": row["created"], "text": row["text"][:300],
                "cited": bool(row.get("cited")), "gap": bool(row.get("gap"))}

    def _chat(self, evidence, limit=4):
        ids = [e for e in evidence if isinstance(e, str) and not e.startswith("http")][-limit:]
        if not ids:
            return []
        before, after = (4, 2) if len(ids) == 1 else (2, 1)
        return [self._line(r) for r in self.messages.around(ids, before, after)]

    def _uses(self, term, chat):
        if len(term) < 2:
            return []
        return [self._line(r) for r in self.messages.containing(term, [c["id"] for c in chat])]

    def review_context(self, item):
        kind, target = item["kind"], item["target"]
        repo = self.memory.repository
        out = {"person": None, "chat": [], "uses": [], "memories": [], "links": [], "quote": "",
               "old": ""}
        if family(kind) == "memory":
            row = repo.get(int(target))
            evidence = json.loads(row["evidence"] or "[]")
            out.update(person=self._person(row["subject"]), chat=self._chat(evidence),
                       quote=row["quote"],
                       links=[e for e in evidence if str(e).startswith("http")][:3])
            if row["kind"] == "glossary":
                out["uses"] = self._uses(row["term"], out["chat"])
        elif kind == "memory_conflict":
            new_id, old_id = (int(x) for x in target.split(":"))
            new, old = repo.get(new_id), repo.get(old_id)
            out.update(person=self._person(new["subject"]),
                       chat=self._chat(json.loads(new["evidence"] or "[]")),
                       quote=new["quote"], old=old["text"])
        elif family(kind) == "nickname":
            row = self.people.repository.nickname(int(target))
            out.update(person=self._person(row["user_id"]),
                       chat=self._chat(json.loads(row["evidence"] or "[]")))
            out["uses"] = self._uses(row["name"], out["chat"])
        elif kind == "profile_new":
            out["person"] = self._person(target)
            out["memories"] = [
                {"id": r["id"], "text": r["text"], "origin": r["origin"], "verified": r["verified"],
                 "status": r["status"], "updated": r["updated"]}
                for r in repo.for_subject(target, limit=30)
            ]
        elif kind == "suspected_bot":
            out["person"] = self._person(target)
            out["chat"] = [self._line(r) for r in reversed(self.messages.by_user(target))]
        return out

    async def ask(self, session_id, limit=5):
        items = self.review_items(limit=limit)
        described = [item["text"] for item in items]
        if not items:
            reply = "现在没有需要你确认的数据。"
            self.repository.add_message(session_id, "assistant", reply)
            return {"reply": reply, "proposals": []}
        questions = {}
        try:
            answer = await ask_json(
                self.model,
                review_prompt("\n".join(f"{n}. {t}" for n, t in enumerate(described, 1))),
                REVIEW.system,
                timeout=60,
            )
            for q in answer.get("questions", []) if isinstance(answer, dict) else []:
                if isinstance(q, dict) and isinstance(q.get("n"), int) and q.get("text"):
                    questions[q["n"]] = str(q["text"])[:200]
        except Exception as exc:
            self.error("teach_review", exc)
        reply = "这几条我不太确定，帮我看看对不对："
        reply_id = self.repository.add_message(session_id, "assistant", reply)
        created = []
        for n, (item, text) in enumerate(zip(items, described), 1):
            created.append(self.repository.add_proposal(
                session_id, reply_id, "question",
                {"queue_id": item["id"], "kind": item["kind"], "target": item["target"],
                 "text": questions.get(n) or (text + "？"), "fact": text},
            ))
        return {"reply": reply, "proposals": [self.repository.proposal(i) for i in created]}

    async def verdict(self, queue_id, verdict, proposal_id=None):
        item = self.review.get(queue_id)
        if not item or item["status"] != "open":
            raise ValueError("这条已经处理过了")
        if verdict not in ("correct", "wrong", "skip"):
            raise ValueError("无效的判断")
        repo, now = self.memory.repository, time.time()
        kind, target = item["kind"], item["target"]
        if verdict == "skip":
            self.review.decide(queue_id, "skipped")
            self.review.close_family(kind, target, "skipped")
        else:
            right = verdict == "correct"
            if kind in ("memory_candidate", "memory_unverified", "glossary_guess"):
                repo.update(int(target), None, status="active" if right else "rejected",
                            verified=1 if right else -1, confidence=1.0 if right else 0.0,
                            verified_at=now)
            elif kind == "memory_conflict":
                new_id, old_id = (int(x) for x in target.split(":"))
                if right:
                    repo.update(new_id, None, status="active", verified=1, confidence=1.0,
                                verified_at=now)
                    repo.update(old_id, None, status="superseded", superseded_by=new_id)
                else:
                    repo.update(new_id, None, status="rejected", verified=-1, verified_at=now)
            elif kind in ("nickname_conflict", "nickname_guess"):
                self.people.repository.set_nickname(
                    int(target), None, status="active" if right else "rejected",
                    verified=1 if right else -1, confidence=1.0 if right else 0.0,
                )
                self.people.invalidate()
            elif kind == "profile_new":
                if right:
                    self.people.repository.verify_profile(target, 1)
                else:
                    self.people.repository.database.connection.execute(
                        "DELETE FROM profiles WHERE subject=?", (target,)
                    )
                    self.people.repository.database.connection.commit()
            elif kind == "suspected_bot" and right:
                self.people.repository.set_kind(target, "bot")
                self.messages.mark_unlearnable(target)
            self.review.decide(queue_id, "done")
            self.review.close_family(kind, target)
        self.repository.decide_review(queue_id, verdict)
        if proposal_id:
            self.repository.decide(proposal_id, "accepted" if verdict != "skip" else "rejected",
                                   {"correct": "确认正确", "wrong": "判定错误", "skip": "跳过"}[verdict])
        return True
