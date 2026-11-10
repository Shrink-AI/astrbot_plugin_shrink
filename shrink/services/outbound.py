"""One persona-aware composition path; delivery remains owned by the caller."""
import asyncio
import json
from types import SimpleNamespace
import time

from ..config import persona_prompt
from ..prompts import render
from .reply_text import strip_reasoning, take_markers


class Outbound:
    def __init__(self, app):
        self.app = app
        self.resolve_scope = lambda scope: None

    async def compose(self, facts, *, request="", kind="result", instruction="", scope=None,
                      context=None, recent=None):
        try:
            return await self._compose(facts, request=request, kind=kind, instruction=instruction,
                                       scope=scope, context=context, recent=recent)
        except Exception as exc:
            self.app.tasks.error("outbound_composition", exc)
            # A completed chat answer is already present; a failed rewrite must not erase it.
            if kind == "tool_result" and isinstance(facts, str):
                return facts
            if kind == "tool_result" and isinstance(facts, dict) and isinstance(facts.get("answer"), str):
                return facts["answer"]
            return render("outbound.fallback")

    async def _compose(self, facts, *, request, kind, instruction, scope, context, recent):
        app = self.app
        source = self.resolve_scope(scope or {})
        recent = list(recent or [])
        evidence = {}
        if context is not None:
            evidence["context"] = context
        elif source:
            user = source["user_id"]
            turn = SimpleNamespace(source=source["source"], user_id=user, text=request,
                                   created=time.time(), id="", private=":private:" in source["source"])
            evidence["context"] = app.context.window(turn, same_source_only=True).text
            evidence["person"] = app.people.card(user, "接收者", source["source"])
            evidence["expressions"] = app.expression.examples(source["source"], request or str(facts))[0]
            recent = app.messages.recent_bot(source["source"], 3)
        prompt = render("outbound.compose", facts=json.dumps(facts, ensure_ascii=False),
                        request=json.dumps(request, ensure_ascii=False), kind=kind,
                        instruction=json.dumps(instruction, ensure_ascii=False),
                        context=json.dumps(evidence, ensure_ascii=False),
                        recent=json.dumps(recent, ensure_ascii=False))
        reply = await asyncio.wait_for(app.expression.model(
            prompt, persona_prompt(app.persona_repository.learned_persona())), 30)
        reply, _, _ = take_markers(strip_reasoning(reply or ""))
        if not reply.strip():
            raise ValueError("empty composition")
        return reply.strip()

    def record(self, scope, text, delivery_id):
        source = self.resolve_scope(scope or {})
        if not source or not delivery_id:
            return False
        from ..persistence.outbound import record_delivery
        return record_delivery(self.app, source, text, delivery_id)
