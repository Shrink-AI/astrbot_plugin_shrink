"""Experience: record how tool-using replies went, reflect on them later (once the chat shows
whether the answer landed), and keep a small set of reusable know-how up to date."""

import asyncio
import json
import math
import re
import time

from ..config import BOT_NAME
from ..prompts.skills import REFLECT, reflect_prompt
from ..prompts import text as prompt_text
from ..text_rules import INJECTION, grams
from .model_output import ask_json
from .tool_evidence import execution_evidence

# Wait this long before judging an episode, so corrections and follow-ups are visible.
SETTLE_SECONDS = 600
FOLLOW_UP_LINES = 14
# bge-m3 scale, as in memory recall: below the floor counts as unrelated.
SEM_FLOOR = 0.6
SEM_FULL = 0.78
RELEVANT_AT = 0.3
RELEVANT_COUNT = 2
LIMITS = {"title": 20, "when": 60, "steps": 400}
PRIVATE_DETAIL = re.compile(r"\d{6,}|qq:|QQ号")
# Tools whose use teaches nothing about how to look things up.
TRIVIAL_TOOLS = {"shrink_remember", "shrink_find_meme"}
TOOL_RESULT_LIMIT = 500


def _cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    na = math.sqrt(sum(x * x for x in a)) or 1
    nb = math.sqrt(sum(x * x for x in b)) or 1
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def tool_step(name, args, result):
    """A compact, JSON-safe record of one tool call."""
    try:
        args_text = json.dumps(args or {}, ensure_ascii=False)
    except (TypeError, ValueError):
        args_text = str(args)
    evidence = execution_evidence(result)
    result = re.sub(r"\s+", " ", str(result or "")).strip()
    return {
        "name": str(name),
        "args": args_text[:300],
        "result": result[:TOOL_RESULT_LIMIT],
        "at": time.time(),
        **({'execution': evidence} if evidence else {}),
    }


class SkillService:
    def __init__(self, repository, messages, people, runs, state, embed, model, error):
        self.repository, self.messages, self.people = repository, messages, people
        self.runs, self.state, self.embed = runs, state, embed
        self.model, self.error = model, error
        self.lock = asyncio.Lock()

    async def vector(self, text):
        try:
            return await asyncio.wait_for(self.embed(text), 8)
        except Exception as exc:
            self.error("embedding", exc)
            return None

    # ---- recall ------------------------------------------------------------------------------------
    async def relevant(self, text, limit=RELEVANT_COUNT):
        """Active know-how that matches a request, by word overlap or meaning."""
        if not self.state.settings.skills_enabled or not (text or "").strip():
            return []
        skills = self.repository.active()
        if not skills:
            return []
        query = set(grams(text, limit=60))
        vector = await self.vector(text)
        scored = []
        for skill in skills:
            own = set(grams(skill["title"] + " " + skill["trigger"], limit=120))
            lexical = len(query & own) / max(1, min(len(query), 8)) if query else 0.0
            semantic = 0.0
            if vector and skill["vector"]:
                similarity = _cosine(vector, json.loads(skill["vector"]))
                semantic = min(1.0, max(0.0, (similarity - SEM_FLOOR) / (SEM_FULL - SEM_FLOOR)))
            score = max(min(1.0, lexical), semantic)
            if score >= RELEVANT_AT:
                scored.append((score, skill))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored[:limit]]

    @staticmethod
    def render(skills):
        return "\n".join(
            f"- {s['title']}（{s['trigger']}）：\n  " + s["steps"].replace("\n", "\n  ")
            for s in skills
        )

    async def index(self, skill_id):
        skill = self.repository.get(skill_id)
        if skill:
            vector = await self.vector(skill["title"] + "\n" + skill["trigger"])
            if vector:
                self.repository.set_vector(skill_id, vector)

    # ---- episodes ----------------------------------------------------------------------------------
    def record(self, turn, tools, answer, outcome, skill_ids):
        """Keep an episode worth reflecting on: real tool use, a timeout, or know-how that was used."""
        meaningful = [t for t in tools if t["name"] not in TRIVIAL_TOOLS]
        if not (meaningful or skill_ids or outcome != "ok"):
            return None
        if skill_ids:
            self.repository.used(skill_ids)
        return self.repository.add_episode(
            source=turn.source,
            user_id=turn.user_id,
            message_id=turn.id,
            question=turn.text,
            tools=tools,
            answer=answer or "",
            outcome=outcome,
            skills=skill_ids,
            duration=time.time() - turn.created,
        )

    def _follow_up(self, episode):
        rows = self.messages.between(
            episode["source"], episode["created"], episode["created"] + SETTLE_SECONDS,
            FOLLOW_UP_LINES * 3,
        )
        lines = []
        for row in rows:
            if row["is_bot"]:
                speaker = f"{BOT_NAME}（就是它）"
            elif row["user_id"] == episode["user_id"]:
                speaker = (row.get("person_name") or row["speaker_name"]) + "（提问的人）"
            elif row.get("addressee") == "self":
                speaker = row.get("person_name") or row["speaker_name"]
            else:
                continue
            text = re.sub(r"\s+", " ", row["text"] or "").strip()[:150]
            lines.append(f"{speaker}: {text or '[图片/表情]'}")
        return "\n".join(lines[:FOLLOW_UP_LINES])

    @staticmethod
    def _steps(tools):
        return "\n".join(
            f"{i}. {t['name']} {t['args']}\n   → {t['result'][:300]}"
            for i, t in enumerate(tools, 1)
        )

    # ---- reflection --------------------------------------------------------------------------------
    async def reflect_due(self, limit=3):
        if not self.state.settings.skills_enabled or self.lock.locked():
            return 0
        async with self.lock:
            done = 0
            for episode in self.repository.unreviewed(time.time() - SETTLE_SECONDS, limit):
                try:
                    await self.reflect(episode)
                except Exception as exc:
                    self.error("skill_reflection", exc)
                    self.repository.update_episode(episode["id"], reviewed=1, verdict="failed")
                done += 1
            return done

    async def reflect(self, episode):
        related = {s["id"]: s for s in self.repository.many(episode["skills"])}
        for skill in await self.relevant(episode["question"], limit=3):
            related.setdefault(skill["id"], skill)
        refs = {f"e{i}": s for i, s in enumerate(related.values(), 1)}
        known = "\n".join(
            f"{ref} {s['title']}｜{s['trigger']}｜{s['steps'].replace(chr(10), ' ')}"
            f"（用过{s['uses']}次，好{s['wins']}次，坏{s['losses']}次"
            + ("，这次用上了" if s["id"] in episode["skills"] else "")
            + "）"
            for ref, s in refs.items()
        )
        prompt = reflect_prompt(
            question=episode["question"],
            steps=self._steps(episode["tools"]),
            answer=episode["answer"],
            outcome=episode["outcome"],
            duration=episode["duration"],
            follow_up=self._follow_up(episode),
            skills=known,
        )
        result = await ask_json(self.model, prompt, prompt_text(REFLECT), timeout=90)
        if not isinstance(result, dict):
            raise ValueError("reflection is not an object")
        verdict = str(result.get("verdict") or "unclear")
        if verdict not in ("good", "bad", "unclear"):
            verdict = "unclear"
        lesson = str(result.get("lesson") or "").strip()[:200]
        for skill_id in episode["skills"]:
            if verdict != "unclear":
                self.repository.score(skill_id, verdict == "good")
        changed = await self.apply(result, refs, episode)
        self.retire_failing(episode["skills"], episode["id"])
        self.repository.update_episode(
            episode["id"], reviewed=1, verdict=verdict, lesson=lesson
        )
        return changed

    def _clean(self, result):
        fields = {}
        for key, limit in LIMITS.items():
            value = str(result.get(key) or "").strip()
            if not value:
                return None
            if len(value) > limit * 1.5:  # Some slack; far over means it ignored the format.
                return None
            fields[key] = value
        text = " ".join(fields.values())
        if INJECTION.search(text) or PRIVATE_DETAIL.search(text):
            return None
        return fields

    async def apply(self, result, refs, episode):
        action = str(result.get("action") or "none")
        reason = str(result.get("reason") or "").strip()[:120] or "复盘"
        target = refs.get(str(result.get("target") or "").strip())
        if action not in ("create", "update", "retire"):
            return None
        if action in ("update", "retire") and not target:
            return None
        fields = None
        if action in ("create", "update"):
            fields = self._clean(result)
            if not fields:
                return None
        run_id = self.runs.start("skill", episode["source"], f"复盘第 {episode['id']} 次求助")
        try:
            if action == "create":
                skill_id = self.repository.add(
                    fields["title"], fields["when"], fields["steps"], reason,
                    episodes=[episode["id"]], run_id=run_id,
                )
                stats = {"created": 1}
            elif action == "update":
                skill_id = target["id"]
                self.repository.revise(
                    skill_id, reason, episodes=[episode["id"]], run_id=run_id,
                    title=fields["title"], trigger=fields["when"], steps=fields["steps"],
                )
                stats = {"updated": 1}
            else:
                skill_id = target["id"]
                self.repository.revise(
                    skill_id, reason, episodes=[episode["id"]], run_id=run_id, status="retired"
                )
                stats = {"retired": 1}
            self.runs.finish(run_id, "success", stats, f"{action} 经验 #{skill_id}")
        except BaseException:
            self.runs.finish(run_id, "failed", {}, "写入经验失败")
            raise
        if action != "retire":
            await self.index(skill_id)
        return action, skill_id

    def retire_failing(self, ids, episode_id):
        """Know-how that keeps misleading gets switched off without waiting for a model to say so."""
        for skill in self.repository.many(list(ids)):
            if (
                skill["status"] == "active"
                and skill["losses"] >= 3
                and skill["losses"] >= 2 * max(1, skill["wins"])
            ):
                self.repository.revise(
                    skill["id"], "最近几次用了反而没办好，自动停用",
                    episodes=[episode_id], origin="rule", status="retired",
                )

    # ---- dashboard ---------------------------------------------------------------------------------
    def detail(self, skill_id):
        skill = self.repository.get(skill_id)
        if not skill:
            return None
        skill = {k: v for k, v in skill.items() if k != "vector"}
        versions = self.repository.versions(skill_id)
        ids = sorted({e for v in versions for e in v["episodes"]}, reverse=True)[:30]
        return {
            **skill,
            "versions": versions,
            "episodes": [self.describe(e) for e in self.repository.episodes(ids)],
        }

    def describe(self, episode):
        """An episode with who asked, where, and what was said around it."""
        person = self.people.repository.get(episode["user_id"])
        return {
            **episode,
            "user_name": person["name"] if person else episode["user_id"].split(":")[-1],
            "account": episode["user_id"].split(":")[-1],
            "follow_up": self._follow_up(episode),
        }

    async def edit(self, skill_id, **fields):
        changed = self.repository.revise(skill_id, "管理员修改", origin="admin", **fields)
        if changed and ({"title", "trigger"} & set(fields)):
            await self.index(skill_id)
        return changed
