"""Reflection: compress finished conversation segments into summaries and grounded memories."""

import asyncio
import datetime
import difflib
import json
import time

from ..ports import ProviderUnavailable
from ..prompts.digest import EXTRACT, VERIFY, extract_prompt, verify_prompt
from . import quality
from .model_output import ask_json

MIN_AGE = 600
FULL_BATCH = 40
SMALL_BATCH = 8
SMALL_BATCH_AGE = 7200
BATCH_LIMIT = 120
BACKOFF = 1800
SHORT_BACKOFF = 120


def _stamp(ts):
    value = datetime.datetime.fromtimestamp(ts)
    return f"{value.month:02d}-{value.day:02d} {value:%H:%M}"


class DigestService:
    def __init__(
        self, *, state, messages, digests, people, memory, glossary, review, runs, model, error,
        profile_hook=None,
    ):
        self.state, self.messages, self.digests, self.people = state, messages, digests, people
        self.memory, self.glossary, self.review, self.runs = memory, glossary, review, runs
        self.model, self.error = model, error
        self.profile_hook = profile_hook
        self.lock = asyncio.Lock()

    def due(self, now=None):
        now = now or time.time()
        backoff = self.state.get("digest_backoff", {})
        sources = []
        for row in self.messages.digest_candidates(now - MIN_AGE):
            if backoff.get(row["source"], 0) > now:
                continue
            if row["n"] >= FULL_BATCH or (
                row["n"] >= SMALL_BATCH and row["first"] < now - SMALL_BATCH_AGE
            ):
                sources.append(row["source"])
        return sources

    async def run_due(self):
        if not self.state.settings.digest_enabled:
            return []
        results = []
        for source in self.due():
            results.append(await self.digest(source))
        return results

    # ---- batch -------------------------------------------------------------------------------------
    def build_batch(self, rows, source, now):
        uids = []
        for row in rows:
            uids.append(row["user_id"] if not row["is_bot"] else "self")
            for mention in json.loads(row["mentions"] or "[]"):
                if isinstance(mention, dict) and mention.get("id"):
                    uids.append(mention["id"])
            if row["addressee"] and row["addressee"] not in ("other",):
                uids.append(row["addressee"])
        uids = [u for u in dict.fromkeys(uids) if u]
        refs, roster = self.people.roster(uids, scope=source)
        back = {uid: ref for ref, uid in refs.items()}
        bots = self.people.repository.bots()
        lines, text_lines = {}, []
        for index, row in enumerate(rows, 1):
            key = f"m{index}"
            row = dict(row)
            lines[key] = row
            speaker = "self" if row["is_bot"] else row["user_id"]
            name = "石英" if row["is_bot"] else (row["speaker_name"] or self.people.display(speaker))
            label = back.get(speaker, "?")
            if speaker in bots or row["is_bot"]:
                label += "，机器人"
            arrow = ""
            if row["addressee"]:
                arrow = f" →对 {back.get(row['addressee'], row['addressee'])}"
            text = (row["text"] or "").replace("\n", " ")[:300]
            if row["has_image"]:
                text = (text + " [图片]").strip()
            text_lines.append(f"{key} [{_stamp(row['created'])}] {name}({label}){arrow}: {text}")
        batch = quality.Batch(
            lines=lines,
            refs=refs,
            bots=bots,
            names=self.people.all_names([u for u in uids if u != "self"], scope=source),
            now=now,
        )
        return batch, roster, "\n".join(text_lines), [u for u in uids if u != "self"]

    # ---- run -----------------------------------------------------------------------------------------
    async def digest(self, source, force=False):
        async with self.lock:
            now = time.time()
            rows = self.messages.undigested(source, now - (0 if force else MIN_AGE), BATCH_LIMIT)
            if not rows:
                return {"source": source, "status": "empty"}
            run_id = self.runs.start("digest", source)
            stats = {
                "messages": len(rows), "prompt": EXTRACT.tag, "verify": None,
                "accepted": {}, "rejected": {}, "outcomes": {},
            }
            try:
                batch, roster, lines, uids = self.build_batch(rows, source, now)
                known_refs, known = self.memory.known_for(uids)
                glossary = "、".join(list(self.memory.repository.glossary_terms())[:60])
                name = rows[0]["source_name"] or source
                result = await ask_json(
                    self.model,
                    extract_prompt(group=name, people=roster, known=known, glossary=glossary,
                                   lines=lines),
                    EXTRACT.system,
                    timeout=120,
                )
                if not isinstance(result, dict):
                    raise ValueError("digest output is not an object")
                gated = self._gate(result, batch, stats)
                if self.state.settings.verify_enabled and gated["claims"]:
                    await self._verify(gated, batch, stats)
                self._dedupe_topics(gated, stats)
                summary = str(result.get("summary") or "")[:600]
                await self._write(gated, known_refs, run_id, source, stats)
                if summary:
                    self.digests.add(source, rows[0]["created"], rows[-1]["created"], len(rows),
                                     summary, run_id)
                self.messages.mark_digested([r["id"] for r in rows])
                self.state.put("last_digest", time.time())
                self.runs.finish(run_id, "success", stats, summary[:200])
                self._clear_backoff(source)
                return {"source": source, "status": "success", "run_id": run_id, "stats": stats}
            except asyncio.CancelledError:
                self.runs.finish(run_id, "interrupted", stats, "任务被中断")
                raise
            except Exception as exc:
                self.error("digest", exc)
                stats["error_type"] = type(exc).__name__
                self.runs.finish(run_id, "failed", stats, "整理失败：" + type(exc).__name__)
                backoff = self.state.get("digest_backoff", {})
                wait = SHORT_BACKOFF if isinstance(exc, ProviderUnavailable) else BACKOFF
                backoff[source] = time.time() + wait
                self.state.put("digest_backoff", backoff)
                return {"source": source, "status": "failed", "run_id": run_id}

    def _clear_backoff(self, source):
        backoff = self.state.get("digest_backoff", {})
        if source in backoff:
            backoff.pop(source)
            self.state.put("digest_backoff", backoff)

    @staticmethod
    def _count(stats, group, reason):
        bucket = stats[group].setdefault(reason, 0)
        stats[group][reason] = bucket + 1

    def _checked(self, result, key, check, cap, batch, stats):
        items = result.get(key) if isinstance(result.get(key), list) else []
        accepted = []
        for item in items[:cap]:
            normalized, problem = check(item, batch)
            if problem:
                self._count(stats, "rejected", f"{key}:{problem}")
            else:
                accepted.append(normalized)
        return accepted

    def _gate(self, result, batch, stats):
        claims = self._checked(result, "topics", quality.check_topic, 5, batch, stats)
        claims += self._checked(result, "memories", quality.check_memory, 8, batch, stats)
        claims, limited = quality.apply_limits(
            claims,
            lambda subject: self.memory.repository.created_since(subject, time.time() - 86400),
        )
        for _, reason in limited:
            self._count(stats, "rejected", f"limit:{reason}")
        lessons = self._checked(result, "lessons", quality.check_lesson, 3, batch, stats)
        return {
            "claims": claims + lessons,
            "nicknames": self._checked(result, "nicknames", quality.check_nickname, 5, batch, stats),
            "jargon": self._checked(result, "jargon", quality.check_jargon, 5, batch, stats),
        }

    def _dedupe_topics(self, gated, stats):
        """A topic that only restates a surviving personal memory adds nothing; drop it."""
        memories = [c for c in gated["claims"] if c["kind"] in ("impression", "event")]
        kept = []
        for claim in gated["claims"]:
            if claim["kind"] == "topic" and any(
                difflib.SequenceMatcher(None, claim["text"], m["text"]).ratio() >= 0.7
                for m in memories
            ):
                self._count(stats, "rejected", "topics:duplicates_memory")
                continue
            kept.append(claim)
        gated["claims"] = kept

    async def _verify(self, gated, batch, stats):
        """Second opinion: drop claims the cited lines do not directly support, or that are jokes."""
        by_id = {row["id"]: row for row in batch.lines.values()}
        entries, targets = [], []
        for claim in gated["claims"]:
            about = self.people.display(claim["subject"]) if claim["subject"] not in ("", "self") else ""
            targets.append(("claim", claim))
            entries.append((claim["text"] + (f"（关于 {about}）" if about else ""), claim["evidence"]))
        for nick in gated["nicknames"]:
            targets.append(("nickname", nick))
            entries.append(
                (f"“{nick['name']}”是在叫 {self.people.display(nick['user_id'])}", nick["evidence"])
            )
        text = []
        for n, (claim, evidence) in enumerate(entries, 1):
            cited = [by_id[e] for e in evidence if e in by_id]
            proof = "\n".join(
                f"   [{_stamp(r['created'])}] {'石英' if r['is_bot'] else r['speaker_name']}: "
                f"{r['text'][:200]}"
                for r in cited
            )
            text.append(f"{n}. 说法：{claim}\n   依据：\n{proof}")
        stats["verify"] = VERIFY.tag
        try:
            verdicts = await ask_json(self.model, verify_prompt("\n".join(text)), VERIFY.system,
                                      timeout=90)
            verdicts = {
                int(v.get("n")): v for v in verdicts if isinstance(v, dict) and str(v.get("n")).isdigit()
            } if isinstance(verdicts, list) else {}
        except Exception as exc:
            self.error("digest_verify", exc)
            verdicts = None
        keep_claims, keep_nicks = [], []
        for n, (kind, item) in enumerate(targets, 1):
            if verdicts is None:
                item["confidence"] = min(item["confidence"], 0.5)
                if kind == "claim":
                    item["status"] = "candidate" if item["kind"] != "lesson" else item["status"]
                (keep_claims if kind == "claim" else keep_nicks).append(item)
                continue
            verdict = verdicts.get(n)
            if not verdict or not verdict.get("supported") or verdict.get("joke"):
                self._count(stats, "rejected", f"verify:{kind}")
                continue
            (keep_claims if kind == "claim" else keep_nicks).append(item)
        if verdicts is None:
            stats["verify_failed"] = True
        gated["claims"], gated["nicknames"] = keep_claims, keep_nicks

    async def _write(self, gated, known_refs, run_id, source, stats):
        touched = set()
        for claim in gated["claims"]:
            memory_id, outcome = await self.memory.store(claim, "digest", run_id, known_refs)
            self._count(stats, "outcomes", outcome)
            self._count(stats, "accepted", claim["kind"])
            if memory_id and claim["subject"] and claim["subject"] != "self" and outcome in (
                "created", "superseded"
            ):
                touched.add(claim["subject"])
        for nick in gated["nicknames"]:
            status_ok = nick["confidence"] >= quality.ACTIVE_AT
            nid, outcome = self.people.repository.add_nickname(
                nick["user_id"], nick["name"], scope=source, origin="ai",
                confidence=nick["confidence"], evidence=nick["evidence"], run_id=run_id,
            )
            self._count(stats, "outcomes", "nickname_" + outcome)
            if outcome == "conflict":
                self.review.add("nickname_conflict", nid,
                                f"“{nick['name']}”也可能是在叫 {self.people.display(nick['user_id'])}")
            elif outcome == "created":
                self._count(stats, "accepted", "nickname")
                if not status_ok:
                    self.people.repository.set_nickname(nid, run_id, status="candidate")
                    self.review.add("nickname_guess", nid, "推断的称呼，等待确认")
                else:
                    self.people.repository.promote_global(nid)
        self.people.invalidate()
        for item in gated["jargon"][: self.state.settings.glossary_per_run]:
            outcome = await self.glossary.define(item, run_id)
            self._count(stats, "outcomes", "glossary_" + outcome)
        if self.profile_hook:
            for uid in list(touched)[:3]:
                try:
                    await self.profile_hook(uid, run_id)
                except Exception as exc:
                    self.error("profile", exc)
