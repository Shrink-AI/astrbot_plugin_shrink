"""Notification value is decided separately from expression and transport."""
import asyncio
import json
import time


QUIET = {"tool_start", "tool_end", "file_read", "log", "auto_approval", "recoverable_error",
         "still_running", "internal_handoff"}
URGENT = {"cancelled", "revocation", "question", "blocker"}


async def select(decisions, row):
    started = time.monotonic()
    kind = row["kind"]
    backend, version, fallback, reason = "rules", "1", False, "important_result"
    notify = "action" if kind in URGENT else "normal"
    if kind in QUIET:
        notify, reason = "silent", "quiet_process"
    elif kind in {"progress", "tool_progress"}:
        backend = decisions.state.settings.decision_backend
        model = decisions.backends.get(backend)
        version = getattr(model, "version", "unavailable")
        event = decisions.inbox.get(row["input_id"])
        payload = {"event": {"id": event["id"], "session": row["session"], "source": "notification",
                             "sender": row["sender"], "text": json.dumps(row["facts"], ensure_ascii=False)[:4000]},
                   "candidates": {"tasks": [], "topics": [], "people": [], "capabilities": []},
                   "rules": {"purpose": "notification_value", "allowed_notify": ["silent", "merge"],
                             "direct": False, "private": False, "may_create_task": False, "fixed_task": ""}}
        try:
            from .decisions import DecisionContext, validate_decision
            raw = await asyncio.wait_for(model(payload), decisions.state.settings.decision_timeout)
            value = validate_decision(raw, event, payload["candidates"], DecisionContext())
            if value.notify not in {"silent", "merge"}:
                raise ValueError("进展不能提升为关键通知")
            notify, reason = value.notify, "notification_value"
        except Exception:
            notify, reason, fallback = "silent", "notification_unavailable", True
    result = {"decision": {"intent": "event", "route": "silent" if notify == "silent" else "reply",
                          "conversation": row["session"], "topic": None, "task": row["task_id"] or None,
                          "evidence": [row["input_id"]], "capability_candidates": [], "notify": notify,
                          "confidence": 1.0 if backend == "rules" else 0.0},
              "backend": backend, "version": version, "fallback": fallback, "reason": reason,
              "rule_direct": backend == "rules", "elapsed": time.monotonic() - started,
              "confidence_kind": "rule" if backend == "rules" else "uncompared"}
    result["id"] = decisions.repository.save(row["input_id"], {"facts": row["fact_refs"]}, result)
    return result
