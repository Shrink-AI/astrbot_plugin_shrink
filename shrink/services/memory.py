"""Long-term memory: store with merge/supersede protection, recall with multi-signal scoring."""

import asyncio
import datetime
import json
import math
import time
from dataclasses import dataclass, field

from ..text_rules import INSULT, grams, normalize
from .quality import ACTIVE_AT

HALF_LIFE = {"topic": 30, "event": 30, "impression": 180, "glossary": 180, "lesson": 60}
# Calibrated on bge-m3 against production memories: unrelated pairs reach ~0.61,
# clearly related ones 0.65-0.75. Below the floor counts as unrelated.
SEM_FLOOR = 0.6
SEM_FULL = 0.8
THRESHOLD = 0.16
MERGE_SIMILARITY = 0.92
STALE_TOPIC_DAYS = 60


@dataclass
class Query:
    text: str
    source: str
    speaker: str = ""
    mentioned: list = field(default_factory=list)
    window: list = field(default_factory=list)
    private: bool = False


def _date(ts):
    value = datetime.datetime.fromtimestamp(ts)
    return f"{value.month}月{value.day}日"


def _where(source):
    if ":private:" in source:
        return "私聊"
    return source.rsplit(":", 1)[-1] + "群" if source else ""


class MemoryService:
    def __init__(self, repository, state, embed, error, people=None, review=None):
        self.repository, self.state, self.embed, self.error = repository, state, embed, error
        self.people, self.review = people, review

    async def vector(self, text):
        try:
            return await asyncio.wait_for(self.embed(text), 8)
        except Exception as exc:
            self.error("embedding", exc)
            return None

    async def index_memory(self, memory_id):
        row = self.repository.get(memory_id)
        if row:
            vector = await self.vector(row["text"])
            if vector:
                self.repository.set_vector(memory_id, row["text"], vector)

    # ---- storing -------------------------------------------------------------------------------------
    def _similarity(self, vector, memory_id):
        stored = self.repository.vector_index.get(memory_id)
        if not vector or not stored or len(stored) != len(vector):
            return 0.0
        norm = math.sqrt(sum(x * x for x in vector)) or 1
        return sum(a * b for a, b in zip(vector, stored)) / norm

    async def store(self, item, origin, run_id, known=None):
        """Write one gated item. Returns (memory_id, outcome) with outcome in
        created / merged / superseded / conflict / unchanged."""
        known = known or {}
        vector = await self.vector(item["text"])
        same = known.get(item.get("same_as") or "")
        candidates = (
            self.repository.same_kind(item["subject"], item["kind"])
            if item["subject"]
            else self.repository.by_people(item["participants"], 80)
        )
        candidates = [c for c in candidates if c["kind"] == item["kind"]]
        best, best_score = None, 0.0
        for row in candidates:
            if normalize(row["text"]) == normalize(item["text"]):
                best, best_score = row, 1.0
                break
            score = self._similarity(vector, row["id"])
            if score > best_score:
                best, best_score = row, score
        if same and same["kind"] == item["kind"]:
            best, best_score = same, 1.0
        if best and best_score >= MERGE_SIMILARITY:
            if best["verified"] == 1:
                return best["id"], "unchanged"
            evidence = list(dict.fromkeys(json.loads(best["evidence"]) + item["evidence"]))[-30:]
            confidence = min(0.95, max(best["confidence"], item["confidence"]) + 0.1)
            participants = list(
                dict.fromkeys(json.loads(best["participants"]) + item["participants"])
            )
            self.repository.update(
                best["id"],
                run_id,
                evidence=evidence,
                participants=participants,
                importance=max(best["importance"], item["importance"]),
                confidence=confidence,
                status="active" if confidence >= ACTIVE_AT else best["status"],
            )
            return best["id"], "merged"
        replaced = known.get(item.get("replaces") or "")
        if replaced and replaced["verified"] == 1:
            memory_id = self.repository.add(
                item["kind"], item["subject"], item["source"], item["text"],
                quote=item["quote"], evidence=item["evidence"], participants=item["participants"],
                importance=item["importance"], confidence=min(item["confidence"], 0.5),
                certainty=item["certainty"], status="candidate", origin=origin, run_id=run_id,
                valid_until=item.get("valid_until"),
            )
            if self.review and memory_id:
                self.review.add("memory_conflict", f"{memory_id}:{replaced['id']}",
                                "和管理员确认过的记忆矛盾")
            await self._index(memory_id, vector, item["text"])
            return memory_id, "conflict"
        memory_id = self.repository.add(
            item["kind"], item["subject"], item["source"], item["text"],
            term=item.get("term", ""), quote=item["quote"], evidence=item["evidence"],
            participants=item["participants"], importance=item["importance"],
            confidence=item["confidence"], certainty=item["certainty"], status=item["status"],
            origin=origin, run_id=run_id, valid_until=item.get("valid_until"),
        )
        await self._index(memory_id, vector, item["text"])
        if replaced and memory_id:
            self.repository.update(
                replaced["id"], run_id, status="superseded", superseded_by=memory_id
            )
            return memory_id, "superseded"
        if memory_id and item["status"] == "candidate" and self.review:
            self.review.add("memory_candidate", memory_id, "置信度不足，等待印证或确认")
        return memory_id, "created"

    async def _index(self, memory_id, vector, text):
        if memory_id and vector:
            self.repository.set_vector(memory_id, text, vector)

    async def remember(self, message, text):
        """Explicit “记住” requests. Third-party or hostile content waits for review."""
        about_self = not self.people or not any(
            uid != message["user_id"] for uid in self.people.mentioned(text, message["source"])
        )
        hostile = bool(INSULT.search(text))
        status = "active" if about_self and not hostile else "candidate"
        memory_id = self.repository.add(
            "impression", message["user_id"], message["source"], text,
            quote=message["text"][:200], evidence=[message["id"]], importance=5,
            confidence=0.8 if status == "active" else 0.5, certainty="stated", status=status,
            origin="explicit", legacy_id="explicit:" + message["id"],
        )
        if memory_id and status == "candidate" and self.review:
            self.review.add("memory_candidate", memory_id, "群友要求记住的内容涉及他人，等待确认")
        return memory_id, status

    # ---- recall --------------------------------------------------------------------------------------
    async def recall_for(self, query: Query, budget=None, limit=None):
        """Score memories against the turn; return (rendered block, chosen rows)."""
        settings = self.state.settings
        budget = budget or settings.memory_budget
        limit = limit or settings.recall_count
        now = time.time()
        text = query.text or ""
        query_grams = grams(text)
        vector = await self.vector(text) if text.strip() else None
        people = [query.speaker, *query.mentioned]
        candidates = {}
        for row in self.repository.fts(query_grams):
            candidates[row["id"]] = row
        for row in self.repository.by_people(
            [p for p in dict.fromkeys(people + query.window) if p], 80
        ):
            candidates.setdefault(row["id"], row)
        terms = {t: i for t, i in self.repository.glossary_terms().items() if t in text}
        for row in self.repository.many(list(terms.values())):
            candidates.setdefault(row["id"], row)
        if vector:
            for memory_id in self._nearest(vector, 40):
                if memory_id not in candidates:
                    row = self.repository.get(memory_id)
                    if row:
                        candidates[memory_id] = row
        scored = []
        for row in candidates.values():
            score = self._score(row, query, vector, query_grams, terms, now)
            if score >= THRESHOLD:
                scored.append((score, row))
        scored.sort(key=lambda x: x[0], reverse=True)
        chosen, per_subject, used = [], {}, 0
        for score, row in scored:
            key = row["subject"] or row["kind"]
            if per_subject.get(key, 0) >= 3:
                continue
            if any(self._duplicate(row, other) for other in chosen):
                continue
            cost = len(row["text"]) + 20
            if used + cost > budget:
                continue
            chosen.append(row)
            per_subject[key] = per_subject.get(key, 0) + 1
            used += cost
            if len(chosen) >= limit:
                break
        self.repository.mark_recalled([r["id"] for r in chosen])
        return self.render(chosen, query), chosen

    def _nearest(self, vector, count):
        norm = math.sqrt(sum(x * x for x in vector)) or 1
        matches = []
        for memory_id, stored in self.repository.vector_index.items():
            if len(stored) != len(vector):
                continue
            score = sum(a * b for a, b in zip(vector, stored)) / norm
            if score >= SEM_FLOOR:
                matches.append((score, memory_id))
        return [m for _, m in sorted(matches, reverse=True)[:count]]

    def _score(self, row, query, vector, query_grams, terms, now):
        if row["status"] != "active":
            return 0.0
        if row["valid_until"] and row["valid_until"] < now:
            return 0.0
        age_days = max(0.0, (now - row["updated"]) / 86400)
        if (
            row["kind"] == "topic" and age_days > STALE_TOPIC_DAYS and row["importance"] <= 2
            and not row["recalls"]
        ):
            return 0.0
        semantic = self._similarity(vector, row["id"]) if vector else 0.0
        semantic = min(1.0, max(0.0, (semantic - SEM_FLOOR) / (SEM_FULL - SEM_FLOOR)))
        memory_grams = set(grams(row["text"], limit=200))
        lexical = (
            len([g for g in query_grams if g in memory_grams]) / max(1, min(len(query_grams), 12))
            if query_grams
            else 0.0
        )
        glossary = 1.0 if row["kind"] == "glossary" and row["term"] in terms else 0.0
        relevance = max(semantic, min(1.0, lexical), glossary)
        participants = json.loads(row["participants"] or "[]")
        # Talking about someone makes what we know about them relevant, even with no word overlap.
        if row["subject"] and row["subject"] in query.mentioned:
            relevance = max(relevance, 0.3)
        elif row["kind"] == "topic" and set(query.mentioned) & set(participants):
            relevance = max(relevance, 0.2)
        person = 0.0
        if row["subject"] and row["subject"] == query.speaker:
            person = 1.0
        elif row["subject"] and row["subject"] in query.mentioned:
            person = 0.8
        elif query.speaker in participants or set(query.mentioned) & set(participants):
            person = 0.7
        elif row["subject"] in query.window or set(query.window) & set(participants):
            person = 0.3
        same_place = row["source"] == query.source
        if (
            not same_place
            and row["subject"] not in ("", query.speaker, *query.mentioned)
            and relevance < 0.35
        ):
            return 0.0  # Unmentioned third parties from elsewhere need strong relevance.
        if ":private:" in row["source"] and not same_place and row["subject"] != query.speaker:
            return 0.0
        quality = 0.6 + 0.4 * row["confidence"]
        importance = 0.7 + 0.075 * row["importance"]
        decay = 0.5 ** (age_days / HALF_LIFE.get(row["kind"], 60))
        boost = 1 + 0.03 * min(row["recalls"], 10) + (0.2 if row["verified"] == 1 else 0)
        return (
            relevance * (1 + 0.6 * person) * quality * importance * (0.4 + 0.6 * decay) * boost
            + (0.05 if same_place and relevance else 0)
        )

    def _duplicate(self, row, other):
        if normalize(row["text"]) == normalize(other["text"]):
            return True
        a = self.repository.vector_index.get(row["id"])
        b = self.repository.vector_index.get(other["id"])
        if a and b and len(a) == len(b):
            return sum(x * y for x, y in zip(a, b)) >= 0.9
        return False

    def render(self, rows, query):
        if not rows:
            return ""
        groups, topics, glossary = {}, [], []
        for row in rows:
            when = _date(row["updated"])
            where = _where(row["source"])
            private = ":private:" in row["source"] and query.source != row["source"]
            note = "（私聊里知道的，别在群里提细节）" if private else ""
            suffix = f"（{when}{'，' + where if where and row['source'] != query.source else ''}）{note}"
            if row["kind"] == "glossary":
                guess = "（推测）" if row["confidence"] < ACTIVE_AT else ""
                glossary.append(f"- {row['term']}：{row['text']}{guess}")
            elif row["kind"] == "topic":
                names = "、".join(
                    self.people.display(p) if self.people else p
                    for p in json.loads(row["participants"] or "[]") if p != "self"
                )
                topics.append(f"- {('和' + names + '聊过：') if names else ''}{row['text']}{suffix}")
            else:
                groups.setdefault(row["subject"], []).append(f"- {row['text']}{suffix}")
        lines = []
        for subject, items in groups.items():
            name = self.people.display(subject) if self.people else subject
            nick = self.people.nicknames_of(subject)[:3] if self.people else []
            lines.append(f"关于 {name}" + (f"（{'、'.join(nick)}）" if nick else "") + "：")
            lines.extend(items)
        if topics:
            lines.append("之前聊过：")
            lines.extend(topics)
        if glossary:
            lines.append("黑话：")
            lines.extend(glossary)
        return "\n".join(lines)

    def lessons(self, limit=3):
        rows = [
            r for r in self.repository.search(kind="lesson", status="active", limit=limit * 2)
            if not r["valid_until"] or r["valid_until"] > time.time()
        ]
        return "\n".join("- " + r["text"] for r in rows[:limit])

    def known_for(self, uids, per_person=4):
        """Existing memories to show the digest model, with short refs k1.. for same_as/replaces."""
        refs, lines = {}, []
        for uid in uids:
            if uid == "self":
                continue
            for row in self.repository.for_subject(uid, limit=per_person):
                ref = f"k{len(refs) + 1}"
                refs[ref] = row
                name = self.people.display(uid) if self.people else uid
                lines.append(f"{ref} [{name}] {row['text']}")
        return refs, "\n".join(lines)
