"""Slang and in-jokes: look them up on the web, then explain them with the group's own usage."""

import asyncio
import json
import re

from ..prompts.knowledge import GLOSSARY, glossary_prompt
from ..prompts import text as prompt_text
from ..text_rules import INSULT
from .model_output import ask_json
from .quality import ACTIVE_AT


def _flat(text):
    return re.sub(r"\s+", " ", text or "")


class GlossaryService:
    def __init__(self, state, memory, search, model, error, review):
        self.state, self.memory, self.search = state, memory, search
        self.model, self.error, self.review = model, error, review

    async def lookup(self, term, source):
        if not (self.search and self.state.settings.glossary_search):
            return []
        try:
            results = await asyncio.wait_for(self.search(f"{term} 是什么意思 网络用语", source), 25)
        except Exception as exc:
            self.error("web_search", exc)
            return []
        return [r for r in results if isinstance(r, dict) and r.get("url")][:5]

    async def define(self, item, run_id):
        """Returns created / exists / rejected / failed."""
        term = item["term"]
        if term in self.memory.repository.glossary_terms():
            return "exists"
        results = await self.lookup(term, item["source"])
        numbered = "\n".join(
            f"[{i}] {r.get('title', '')[:60]} — {_flat(r.get('snippet', ''))[:220]}"
            for i, r in enumerate(results, 1)
        )
        usage = "\n".join(f"- {line}" for line in item["lines"][:4])
        if item.get("usage"):
            usage += f"\n（整理时的理解：{item['usage']}）"
        try:
            answer = await ask_json(self.model, glossary_prompt(term, usage, numbered),
                                    prompt_text(GLOSSARY), timeout=60)
        except Exception as exc:
            self.error("glossary", exc)
            return "failed"
        meaning = str(answer.get("meaning") or "").strip() if isinstance(answer, dict) else ""
        if not 2 <= len(meaning) <= 80 or INSULT.search(meaning):
            return "rejected"
        index = answer.get("source")
        cited = results[index - 1] if isinstance(index, int) and 1 <= index <= len(results) else None
        confidence = 0.7 if cited else 0.45
        claim = {
            "kind": "glossary",
            "subject": "",
            "term": term,
            "text": meaning,
            "quote": item["lines"][0][:120] if item["lines"] else "",
            "evidence": item["evidence"] + ([cited["url"]] if cited else []),
            "participants": [],
            "importance": 3,
            "certainty": "stated" if cited else "inferred",
            "confidence": confidence,
            "status": "active" if confidence >= ACTIVE_AT else "candidate",
            "valid_until": None,
            "source": "",
        }
        memory_id, _ = await self.memory.store(claim, "glossary", run_id)
        if memory_id and not cited:
            self.review.add("glossary_guess", memory_id, "没有搜到依据，按群里用法推测的释义")
        return "created" if memory_id else "failed"

    @staticmethod
    def parse_results(raw):
        """Normalize AstrBot search tool output (JSON string or error text)."""
        if not isinstance(raw, str) or raw.startswith("Error"):
            return []
        try:
            data = json.loads(raw)
        except ValueError:
            return []
        return [
            {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("snippet", "")}
            for r in data.get("results", [])
            if isinstance(r, dict)
        ]
