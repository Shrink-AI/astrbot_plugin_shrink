"""One persona-aware composition path; delivery remains owned by the caller."""
import asyncio
import json
from types import SimpleNamespace
import time

import httpx

from ..persistence.compositions import Compositions
from .composition import ChatReply, SILENT, Silent, fallback

from ..config import persona_prompt
from ..prompts import render
from .reply_text import strip_reasoning, take_markers


class Outbound:
    def __init__(self, app):
        self.app = app
        self.resolve_scope = lambda scope: None
        self.repository = Compositions(app.database)
        self.attempt_timeout = 15

    async def compose(self, facts, *, request="", kind="result", instruction="", scope=None,
                      context=None, recent=None):
        chat = facts if isinstance(facts, ChatReply) else None
        if chat is not None:
            facts = {"answer": chat.text, "execution": chat.execution}
        identity = None if chat is not None else self.repository.save(facts, request, kind, scope)
        result = None
        for attempt in range(2):
            try:
                result = await self._compose(facts, request=request, kind=kind, instruction=instruction,
                                             scope=scope, context=context, recent=recent)
                break
            except Exception as exc:
                self.app.tasks.error("outbound_composition", exc)
                if attempt == 0 and isinstance(exc, (TimeoutError, ConnectionError, OSError,
                                                     httpx.TransportError, EmptyComposition)):
                    continue
                if chat is not None:
                    clean, _, _ = take_markers(strip_reasoning(chat.text))
                    result = clean.strip() or render("outbound.fallback")
                elif kind in {"progress", "tool_progress"}:
                    result = SILENT
                else:
                    known = facts.get("status") if isinstance(facts, dict) else None
                    result = fallback(known) or render("outbound.fallback")
                break
        if chat is None and isinstance(result, str):
            from .outbound_guard import safe_expression
            result = safe_expression(result, facts, kind, persona_prompt(self.app.persona_repository.learned_persona()))
        if identity is not None:
            self.repository.finish(identity, "silent" if isinstance(result, Silent) else "ready",
                                   "" if isinstance(result, Silent) else result)
        return result

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
            prompt, persona_prompt(app.persona_repository.learned_persona())), self.attempt_timeout)
        reply, _, _ = take_markers(strip_reasoning(reply or ""))
        if not reply.strip():
            raise EmptyComposition("empty composition")
        return reply.strip()

    def record(self, scope, text, delivery_id):
        source = self.resolve_scope(scope or {})
        if not source or not delivery_id:
            return False
        from ..persistence.outbound import record_delivery
        return record_delivery(self.app, {**source, "session": (scope or {}).get("session", "")}, text, delivery_id)


class EmptyComposition(ValueError):
    pass
