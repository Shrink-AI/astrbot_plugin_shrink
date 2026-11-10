"""Editing prompts from the dashboard: list, save with checks, reset, history, and trial runs."""

import asyncio
import random
import time

from .. import prompts
from ..config import persona_prompt
from ..prompts.chat import turn_prompt
from .expression import choose_form
from .perception import Turn

# Prompts that are part of the persona AstrBot keeps; changing them re-syncs it.
PERSONA_KEYS = {"chat.persona", "chat.style_header"}
TRIAL_LIMIT = 6


class PromptService:
    def __init__(self, repository, persona=None):
        self.repository, self.persona = repository, persona
        # Called with the changed key after every save or reset (e.g. to refresh tool descriptions).
        self.listeners = []

    def load(self):
        prompts.apply({k: r["text"] for k, r in self.repository.active().items()})

    def catalog(self):
        saved = self.repository.active()
        return [
            {
                "key": key,
                "group": entry["group"],
                "title": entry["title"],
                "note": entry.get("note", ""),
                "text": prompts.text(key),
                "default": entry["text"],
                "placeholders": prompts.placeholders(entry["text"]),
                "modified": key in saved,
                "updated": saved[key]["created"] if key in saved else None,
            }
            for key, entry in prompts.CATALOG.items()
        ]

    async def save(self, key, text, note=""):
        problem = prompts.check(key, text)
        if problem:
            raise ValueError(problem)
        text = text.strip("\n")
        if text == prompts.CATALOG[key]["text"]:
            return await self.reset(key, note or "改回默认")
        if text == prompts.text(key):
            return {"ok": True, "changed": False}
        self.repository.save(key, text, note)
        await self._changed(key)
        return {"ok": True, "changed": True}

    async def reset(self, key, note="恢复默认"):
        if key not in prompts.CATALOG:
            raise ValueError("没有这个提示词")
        if not prompts.overridden(key):
            return {"ok": True, "changed": False}
        self.repository.save(key, None, note)
        await self._changed(key)
        return {"ok": True, "changed": True}

    def history(self, key):
        if key not in prompts.CATALOG:
            return self.repository.history(key)
        default = prompts.CATALOG[key]["text"]
        return [
            {**row, "text": default if row["text"] is None else row["text"], "reset": row["text"] is None}
            for row in self.repository.history(key)
        ]

    def archived(self):
        return [{"key": key, "history": self.repository.history(key)}
                for key in self.repository.keys() if key not in prompts.CATALOG]

    async def _changed(self, key):
        self.load()
        if key in PERSONA_KEYS and self.persona:
            await self.persona.resync()
        for listener in self.listeners:
            listener(key)

    # ---- trial ---------------------------------------------------------------------------------
    async def trial(self, app, source, drafts, limit=4):
        """Replay the last few messages 石英 answered in ``source`` with the saved prompts and
        with ``drafts``, side by side. Nothing is sent or stored."""
        for key, value in (drafts or {}).items():
            problem = prompts.check(key, value)
            if problem:
                raise ValueError(f"{prompts.CATALOG.get(key, {}).get('title', key)}：{problem}")
        cases = self._cases(app, source, min(max(limit, 1), TRIAL_LIMIT))
        if not cases:
            raise ValueError("这个会话里还没有石英回过的消息，没法试")
        learned = app.persona_repository.learned_persona()
        rolls = [(random.random(), random.random()) for _ in cases]

        async def variant(use_drafts):
            with prompts.drafting(drafts if use_drafts else {}):
                return await asyncio.gather(*[
                    self._reply(app, row, learned, roll) for row, roll in zip(cases, rolls)
                ], return_exceptions=True)

        saved, drafted = await asyncio.gather(variant(False), variant(True))
        out = []
        for case, now, then in zip(cases, saved, drafted):
            out.append({
                "time": case["trigger"]["created"],
                "speaker": case["trigger"]["speaker_name"],
                "said": case["trigger"]["text"],
                "original": case["original"],
                "saved": _shown(now),
                "draft": _shown(then),
            })
        return out

    @staticmethod
    def _cases(app, source, limit):
        bots = app.database.rows(
            "SELECT text, created FROM messages WHERE source=? AND is_bot=1 AND text!='[表情]' "
            "ORDER BY created DESC LIMIT ?", (source, limit * 4),
        )
        cases = {}
        for bot in bots:
            trigger = app.database.rows(
                "SELECT * FROM messages WHERE source=? AND is_bot=0 AND created<? AND text!='' "
                "ORDER BY created DESC LIMIT 1", (source, bot["created"]),
            )
            if not trigger:
                continue
            row = trigger[0]
            cases.setdefault(row["id"], {"trigger": row, "original": []})["original"].insert(0, bot["text"])
            if len(cases) >= limit:
                break
        return sorted(cases.values(), key=lambda c: c["trigger"]["created"])

    async def _reply(self, app, case, learned, roll):
        row = case["trigger"]
        turn = Turn(
            id=row["id"], platform=row["user_id"].split(":", 1)[0], account=row["user_id"].split(":")[-1],
            user_id=row["user_id"], name=row["speaker_name"], source=row["source"],
            source_name=row["source_name"], text=row["text"], created=row["created"],
            private=":private:" in row["source"], addressee=row["addressee"],
        )
        window = app.context.window(turn)
        spoke = app.database.rows(
            "SELECT 1 FROM messages WHERE source=? AND is_bot=1 AND created BETWEEN ? AND ? LIMIT 1",
            (row["source"], row["created"] - app.state.settings.engaged_seconds, row["created"]),
        )
        reason = "direct" if row["addressee"] == "self" or turn.private else (
            "engaged" if spoke else "spontaneous")
        form = choose_form(turn.text, roll[0])
        cards = [app.people.card(turn.user_id, "发言人", turn.source), app.people.group_card(turn.source)]
        system = persona_prompt(learned) + "\n\n" + turn_prompt(
            people="\n".join(c for c in cards if c),
            lessons=app.memory.lessons(),
            chat=window.text,
            speaker=turn.name,
            reason=reason,
            form=form,
            allow_skip=reason != "direct",
        )
        started = time.monotonic()
        raw = await asyncio.wait_for(app.expression.model(turn.text, system), 90)
        finished = await app.expression.finish(
            raw, user_text=turn.text, form=form, reason=reason,
            recent=app.messages.recent_bot(turn.source, 3),
        )
        text = "（不回）" if finished.skip else " ／ ".join(t for t in (finished.text, finished.second) if t)
        return {"text": text or "（空）", "form": form, "reason": reason,
                "seconds": round(time.monotonic() - started, 1)}


def _shown(result):
    if isinstance(result, BaseException):
        return {"text": "", "error": f"{type(result).__name__}: {str(result)[:160]}"}
    return result
