"""Nightly upkeep: person/group cards, expiry, de-duplication."""

import asyncio
import datetime
import time
from zoneinfo import ZoneInfo

from ..prompts.knowledge import GROUP, PROFILE, group_prompt, profile_prompt
from ..prompts import text as prompt_text
from ..text_rules import INSULT
from .model_output import ask_json

NIGHT_HOUR = 4


def _date(ts):
    value = datetime.datetime.fromtimestamp(ts)
    return f"{value.month}月{value.day}日"


class ConsolidationService:
    def __init__(self, *, state, messages, digests, people, memory, review, runs, model, error):
        self.state, self.messages, self.digests, self.people = state, messages, digests, people
        self.memory, self.review, self.runs = memory, review, runs
        self.model, self.error = model, error
        self.lock = asyncio.Lock()
        self.capabilities = None

    # ---- cards -------------------------------------------------------------------------------------
    async def profile(self, uid, run_id=None):
        person = self.people.repository.get(uid)
        rows = self.memory.repository.for_subject(uid, limit=30)
        if not person or not rows:
            return False
        memories = "\n".join(f"- {r['text']}（{_date(r['updated'])}）" for r in rows)
        stats = self.messages.interactions(uid)
        stats_text = f"石英回复过 TA {stats['replies']} 次" + (
            f"，最近一次在{_date(stats['last'])}" if stats["last"] else ""
        )
        said = "\n".join("- " + t[:80] for t in self.messages.said_to_bot(uid))
        aliases = "、".join(self.people.names_of(uid)[1:6])
        answer = await ask_json(
            self.model, profile_prompt(person["name"], aliases, memories, stats_text, said),
            prompt_text(PROFILE), timeout=60,
        )
        if not isinstance(answer, dict):
            return False
        summary = str(answer.get("summary") or "").strip()[:200]
        relation = str(answer.get("relation") or "").strip()[:60]
        if not summary or INSULT.search(summary + relation):
            return False
        saved = self.people.repository.save_profile(
            uid, "person", summary, relation, [r["id"] for r in rows[:10]], run_id
        )
        if saved and not self.people.repository.profile(uid)["verified"]:
            self.review.add("profile_new", uid, "新的人物小结")
        return saved

    async def group(self, source, run_id=None):
        rows = self.digests.recent(source, limit=12)
        if len(rows) < 2:
            return False
        name = source.rsplit(":", 1)[-1]
        text = "\n".join(f"- {_date(r['start'])}：{r['summary'][:200]}" for r in rows)
        answer = await ask_json(self.model, group_prompt(name, text), prompt_text(GROUP), timeout=60)
        summary = str(answer.get("summary") or "").strip()[:220] if isinstance(answer, dict) else ""
        if not summary:
            return False
        return self.people.repository.save_profile(source, "group", summary, "", [], run_id)

    # ---- nightly -----------------------------------------------------------------------------------
    def due(self, now=None):
        now = now or time.time()
        last = self.state.get("last_consolidation", 0)
        current = datetime.datetime.fromtimestamp(now, ZoneInfo("Asia/Taipei"))
        previous = datetime.datetime.fromtimestamp(last, ZoneInfo("Asia/Taipei"))
        return current.date() != previous.date() and current.hour >= NIGHT_HOUR

    async def nightly(self):
        async with self.lock:
            run_id = self.runs.start("consolidate")
            stats = {"expired": 0, "profiles": 0, "groups": 0, "deduplicated": 0}
            self.item_errors = []
            since = self.state.get("last_consolidation", 0)
            try:
                for row in self.memory.repository.expired(time.time()):
                    self.memory.repository.update(row["id"], run_id, status="expired")
                    stats["expired"] += 1
                for uid in self.memory.repository.subjects_updated_since(since, 20):
                    if await self._safe(self.profile(uid, run_id)):
                        stats["profiles"] += 1
                for source in {d["source"] for d in self.digests.recent(limit=60) if d["created"] > since}:
                    if await self._safe(self.group(source, run_id)):
                        stats["groups"] += 1
                stats["deduplicated"] = self.deduplicate(run_id)
                self.state.put("last_consolidation", time.time())
                stats["errors"] = self.item_errors
                self.runs.finish(run_id, "failed" if self.item_errors else "success", stats,
                                 "部分记忆整理失败" if self.item_errors else "夜间记忆整理完成")
            except asyncio.CancelledError:
                self.runs.finish(run_id, "interrupted", stats)
                raise
            except Exception as exc:
                self.error("consolidation", exc)
                self.runs.finish(run_id, "failed", stats, type(exc).__name__)
            if self.capabilities:
                try:
                    stats["capabilities"] = await self.capabilities()
                except Exception as exc:
                    self.error("capability_maintenance", exc)
                    stats["capabilities"] = {"error": str(exc)[:200]}
            self.state.put("last_consolidation", time.time())
            return stats

    async def _safe(self, job):
        try:
            return await job
        except Exception as exc:
            self.error("consolidation_item", exc)
            if hasattr(self, "item_errors"):
                self.item_errors.append(type(exc).__name__)
            return False

    def deduplicate(self, run_id, limit=50):
        """Merge near-identical live memories about the same subject; the newer one survives."""
        merged = 0
        index = self.memory.repository.vector_index
        subjects = self.memory.repository.subjects_updated_since(0, 200)
        for subject in subjects:
            for kind in ("impression", "event"):
                rows = sorted(
                    self.memory.repository.same_kind(subject, kind), key=lambda r: r["updated"]
                )
                for i, old in enumerate(rows):
                    for new in rows[i + 1 :]:
                        a, b = index.get(old["id"]), index.get(new["id"])
                        if not a or not b or len(a) != len(b) or old["verified"] == 1:
                            continue
                        if sum(x * y for x, y in zip(a, b)) >= 0.92:
                            self.memory.repository.update(
                                old["id"], run_id, status="superseded", superseded_by=new["id"]
                            )
                            merged += 1
                            break
                    if merged >= limit:
                        return merged
        return merged
