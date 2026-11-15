"""One persona-aware composition path; delivery remains owned by the caller."""
import asyncio
import json
from types import SimpleNamespace
import time

import httpx

from ..persistence.compositions import Compositions
from .composition import ChatReply, SILENT, Silent, fallback, NOTIFICATION
from .notification_selection import necessary

from ..config import persona_prompt
from ..prompts import render, text as prompt_text
from .reply_text import strip_reasoning, take_markers
from .scheduling import LANE, in_lane, EXPRESSION_CALLS


class Outbound:
    def __init__(self, app):
        self.app = app
        self.resolve_scope = lambda scope: None
        self.repository = Compositions(app.database)
        self.attempt_timeout = 15

    async def compose(self, facts, *, request="", kind="result", instruction="", scope=None,
                      context=None, recent=None, recovery=False, required=True, detail=False):
        chat = facts if isinstance(facts, ChatReply) else None
        detail = bool(chat is not None and detail)
        if chat is not None:
            facts = {"answer": chat.text, "execution": chat.execution}
        identity = None if chat is not None else self.repository.save(facts, request, kind, scope)
        result = None
        notification = NOTIFICATION.get()
        plugin_fallback = ""
        if chat is None and notification and necessary(kind) and (
                notification["facts"] == facts and notification["kind"] == kind and
                (notification["session"], notification["sender"]) ==
                ((scope or {}).get("session"), str((scope or {}).get("sender", "")))):
            from .outbound_guard import unsafe
            candidate = notification["fallback_text"]
            if candidate and not unsafe(candidate, facts, self.persona(), readable_fallback=True):
                plugin_fallback = candidate
        for attempt in range(1 if chat is not None else 2):
            try:
                result = await self._compose(facts, request=request, kind=kind, instruction=instruction,
                                             scope=scope, context=context, recent=recent, detail=detail)
                break
            except Exception as exc:
                self.app.tasks.error("outbound_composition", exc)
                if chat is None and attempt == 0 and isinstance(exc, (TimeoutError, ConnectionError, OSError,
                                                     httpx.TransportError, EmptyComposition)):
                    continue
                if chat is not None:
                    clean, _, _ = take_markers(strip_reasoning(chat.text))
                    from .outbound_guard import unsafe
                    result = fallback((chat.execution or {}).get("status")) or SILENT
                    if recovery and not required:
                        result = SILENT
                    elif not recovery and clean.strip() and not unsafe(
                            chat.text, chat.execution or {}, self.persona(), detail=detail):
                        result = clean.strip()
                elif kind in {"progress", "tool_progress"}:
                    result = SILENT
                else:
                    known = facts.get("status") if isinstance(facts, dict) else None
                    result = plugin_fallback or fallback(known) or render("outbound.fallback")
                break
        if isinstance(result, str):
            from .outbound_guard import safe_expression, unsafe
            if chat:
                if unsafe(result, chat.execution or {}, self.persona(), detail=detail):
                    result = (fallback((chat.execution or {}).get("status")) or SILENT) if required else SILENT
            else:
                if plugin_fallback and unsafe(plugin_fallback, facts, self.persona(), readable_fallback=True):
                    plugin_fallback = ""
                result = (plugin_fallback if plugin_fallback and unsafe(result, facts, self.persona())
                          else safe_expression(result, facts, kind, self.persona()))
        if identity is not None:
            self.repository.finish(identity, "silent" if isinstance(result, Silent) else "ready",
                                   "" if isinstance(result, Silent) else result)
        return result

    def persona(self):
        return persona_prompt(self.app.persona_repository.learned_persona())

    def evidence(self, scope, request, facts, context=None, recent=None):
        app = self.app
        source = self.resolve_scope(scope or {}) if context is None else None
        recent = list(recent or [])
        evidence = {}
        if context is not None:
            evidence["context"] = context
        elif source:
            user = source["user_id"]
            turn = SimpleNamespace(source=source["source"], user_id=user, text=request,
                                   created=time.time(), id="", private=":private:" in source["source"])
            evidence["context"] = self.turn_context(
                app.context.window(turn, same_source_only=True).text,
                [app.people.card(user, "接收者", source["source"])],
                examples=app.expression.examples(source["source"], request or str(facts))[0])
            recent = app.messages.recent_bot(source["source"], 3)
        return evidence, recent

    @staticmethod
    def turn_context(chat, people, memories="", examples=""):
        return {"chat": chat, "people": people, "memories": memories, "examples": examples}

    async def _compose(self, facts, *, request, kind, instruction, scope, context, recent, detail=False):
        app = self.app
        evidence, recent = self.evidence(scope, request, facts, context, recent)
        prompt = prompt_text("outbound.contract") + "\n" + render(
                        "outbound.compose", facts=json.dumps(facts, ensure_ascii=False),
                        request=json.dumps(request, ensure_ascii=False), kind=kind,
                        instruction=json.dumps(instruction, ensure_ascii=False),
                        context=json.dumps(evidence, ensure_ascii=False),
                        recent=json.dumps(recent, ensure_ascii=False))
        lane = LANE.get() if LANE.get() in {"chat", "urgent"} else "expression"
        async def invoke(*args):
            counter = EXPRESSION_CALLS.get()
            if counter is not None:
                counter[0] += 1
            return await app.expression.model(*args)
        model = app.scheduling.wrap(invoke, "model")
        reply = await asyncio.wait_for(in_lane(lane)(model)(prompt, self.persona()), self.attempt_timeout)
        from .outbound_guard import unsafe
        if unsafe(reply or "", {}, self.persona(), detail=detail):
            raise UnsafeComposition("unsafe composition")
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


class UnsafeComposition(ValueError):
    pass
