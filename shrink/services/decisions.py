"""Rules own identity and authority; a replaceable classifier only selects known references."""
import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..text_rules import grams
from .recent import ASKED, PRIVATE, is_private
from .turn_control import is_stop_request, is_revocation, is_task_followup

logger = logging.getLogger(__name__)

class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    intent: Literal["chat", "query", "task", "supplement", "stop", "authorization", "event"]
    route: Literal["reply", "create_task", "attach_task", "ask", "silent", "stop"]
    conversation: str
    topic: str | None
    task: str | None
    evidence: list[str] = Field(min_length=1, max_length=32)
    capability_candidates: list[str]
    notify: Literal["silent", "merge", "normal", "action"]
    confidence: float = Field(ge=0, le=1)


@dataclass
class DecisionContext:
    private: bool = False
    direct: bool = False
    admin: bool = False
    participation: str = ""
    foreground: int = 0
    fixed_task: str = ""


def capabilities(text, catalog, limit=5):
    """Ranking suggests a small shortlist; it never removes tools from the actual catalog."""
    wanted = set(grams(text))
    scored = []
    for item in catalog:
        if not item.get("active", True):
            continue
        score = len(wanted & set(grams(item["name"] + " " + item.get("description", ""))))
        if score:
            scored.append((score, item["name"]))
    return [name for _, name in sorted(scored, key=lambda pair: (-pair[0], pair[1]))[:limit]]


def decision_payload(raw):
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        decoder = json.JSONDecoder()
        objects, offset = [], 0
        while (start := raw.find("{", offset)) >= 0:
            try:
                value, offset = decoder.raw_decode(raw, start)
            except json.JSONDecodeError:
                raise error
            objects.append(value)
        if len(objects) != 1:
            raise error
        return objects[0]


def validate_decision(raw, event, candidates, context):
    data = decision_payload(raw)
    allowed = {event["id"]}
    for group in ("tasks", "topics", "people"):
        allowed.update(item["id"] for item in candidates[group])
    if isinstance(data, dict):
        evidence = data.get("evidence")
        if isinstance(evidence, list) and len(evidence) <= 32 and all(isinstance(item, str) for item in evidence):
            # Keep the schema bound while reserving a reference to the actual input.
            evidence = [item for item in evidence if item in allowed and item != event["id"]]
            data = {**data, "evidence": [event["id"], *evidence[:31]]}
    value = Decision.model_validate(data)
    if value.conversation != event["session"]:
        raise ValueError("模型不能覆盖会话")
    tasks = {item["id"]: item for item in candidates["tasks"]}
    if value.task is not None and value.task not in tasks:
        raise ValueError("任务不在候选中")
    if context.fixed_task and value.task != context.fixed_task:
        raise ValueError("模型不能覆盖已有任务关联")
    if value.topic is not None and value.topic not in {item["id"] for item in candidates["topics"]}:
        raise ValueError("话题不在候选中")
    names = {item["name"] for item in candidates["capabilities"]}
    value.capability_candidates = [name for name in value.capability_candidates if name in names]
    if value.route == "stop":
        raise ValueError("停止只能由规则确认")
    if value.route in {"create_task", "attach_task"} and not context.admin:
        raise ValueError("模型不能授予任务权限")
    if value.route == "attach_task" and (
        not value.task or tasks[value.task]["status"] not in {"queued", "running", "waiting_external", "retry_wait"}
    ):
        raise ValueError("补充目标必须是可接收输入的已有任务")
    if value.intent == "supplement" and value.route == "create_task":
        raise ValueError("补充要求不能新建任务")
    if value.task and value.route == "create_task":
        raise ValueError("已有任务关联不能新建任务")
    if value.route == "silent" and (context.private or context.direct):
        raise ValueError("明确呼叫不能静默")
    if value.route == "silent":
        value.notify = "silent"
    return value


class Decisions:
    def __init__(self, repository, inbox, messages, state):
        self.repository, self.inbox, self.messages, self.state = repository, inbox, messages, state
        self.backends = {}

    def preprocess(self, event, context, candidates):
        if event["processed"] is not None or event["route"] == "excluded":
            return self.rules(event, context, candidates)
        bindings = self.repository.binding(event["id"])
        if len(bindings) == 1 and not context.fixed_task:
            context.fixed_task = bindings[0]
        if (context.direct or context.private) and (
            is_stop_request(event["text"]) or is_revocation(event["text"])
        ):
            return None, ""
        if len(bindings) > 1:
            value = self.base(event, context, candidates)
            value.intent, value.route, value.notify = "supplement", "ask", "action"
            return value, "multiple_bindings"
        if context.fixed_task:
            return None, ""
        return self.rules(event, context, candidates)

    def recall(self, event, context, catalog):
        text = event["text"] or ""
        meta = event["metadata"]
        source = meta.get("source", event["session"])
        speaker = meta.get("platform", "") + ":" + event["sender"]
        rows = self.messages.elsewhere(
            speaker, source, time.time() - 7 * 86400, 100,
            include_private=is_private(source) or bool(PRIVATE.search(text)))
        local = self.messages.window(source, 20)
        people = [{"id": speaker}]
        for row in local:
            if not row["is_bot"] and row["user_id"] not in {person["id"] for person in people}:
                people.append({"id": row["user_id"], "name": row.get("person_name", "")})
        wanted = set(grams(text))
        rows.sort(key=lambda row: (len(wanted & set(grams(row["text"] or ""))), row["created"]), reverse=True)
        topics = [{"id": row["id"], "source": row["source"], "text": (row["text"] or "")[:240]}
                  for row in rows[:8] if not row["is_bot"]]
        tasks = self.repository.tasks(event["session"], event["sender"])
        tasks = [{**item, "request": item["request"][:500]} for item in tasks]
        bindings = self.repository.binding(event["id"])
        if len(bindings) == 1 and not context.fixed_task:
            context.fixed_task = bindings[0]
        shortlist = set(capabilities(text, catalog, self.state.settings.decision_capability_limit * 3))
        return {"tasks": tasks, "topics": topics, "people": people[:8],
                "capabilities": [{"name": item["name"], "description": item.get("description", "")[:300]}
                                 for item in catalog if item.get("active", True) and item["name"] in shortlist]}

    @staticmethod
    def base(event, context, candidates):
        addressed = context.private or context.direct
        topic = candidates["topics"][0]["id"] if ASKED.search(event["text"] or "") and candidates["topics"] else None
        return Decision(intent="query" if topic else "chat", route="reply" if addressed else "silent",
                        conversation=event["session"], topic=topic, task=None, evidence=[event["id"]],
                        capability_candidates=[], notify="normal" if addressed else "silent", confidence=0.0)

    def rules(self, event, context, candidates):
        decision = self.base(event, context, candidates)
        if event["processed"] is not None or event["route"] == "excluded":
            return decision.model_copy(update={"route": "silent", "notify": "silent"}), "duplicate"
        text = event["text"] or ""
        if (context.direct or context.private) and (is_stop_request(text) or is_revocation(text)):
            decision.intent = "authorization" if is_revocation(text) else "stop"
            active = [t for t in candidates["tasks"] if t["status"] not in {"completed", "failed", "cancelled"}]
            if not context.admin:
                decision.route, decision.notify = "ask", "action"
                return decision, "stop_permission"
            if is_revocation(text):
                decision.route, decision.notify = "stop", "normal"
                return decision, "revocation"
            targets = len(active) + context.foreground
            decision.route = "stop" if targets == 1 else "ask"
            decision.task = active[0]["id"] if len(active) == 1 and not context.foreground else None
            decision.notify = "normal" if decision.route == "stop" else "action"
            return decision, "stop_target" if targets == 1 else "stop_ambiguous"
        if context.fixed_task:
            target = next((t for t in candidates["tasks"] if t["id"] == context.fixed_task), None)
            if target is None:
                raise ValueError("已有任务关联不属于当前身份或会话")
            decision.intent, decision.task = "supplement", target["id"]
            decision.route = "attach_task" if context.admin and target["status"] in {
                "queued", "running", "waiting_external", "retry_wait"} else "ask"
            decision.notify = "merge" if decision.route == "attach_task" else "action"
            return decision, "binding"
        if context.participation in {"group_blacklisted", "bot_quiet", "addressed_other", "other_conversation_followup"}:
            decision.route, decision.notify = "silent", "silent"
            return decision, context.participation
        if context.participation == "quiet" and not (context.direct or context.private):
            return decision, "participation_budget"
        if re.fullmatch(r"\s*(?:你好|您好|在吗|谢谢|好的|收到|hi|hello)[？?！!。，, ]*", text, re.I):
            if not self.state.settings.decision_enabled and context.participation in {"engaged", "spontaneous"}:
                return None, ""
            return decision, "simple_chat"
        return None, ""

    async def decide(self, identity, context=None, catalog=()):
        started = time.monotonic()
        context = context or DecisionContext()
        event = self.inbox.get(identity)
        if not event:
            raise ValueError("决策输入必须已持久接收")
        candidates = {"tasks": [], "topics": [], "people": [], "capabilities": []}
        decision, reason = self.preprocess(event, context, candidates)
        if decision is None:
            candidates = self.recall(event, context, catalog)
            decision, reason = self.rules(event, context, candidates)
        direct = decision is not None
        fallback = False
        backend_name, version, confidence_kind = "rules", "1", "rule"
        if not direct and not self.state.settings.decision_enabled:
            decision = self.base(event, context, candidates)
            decision.route, decision.notify = "reply", "normal"
            backend_name, version, confidence_kind = "main_llm", "deferred", "unscored"
            reason = "decision_disabled"
        elif not direct:
            backend_name = self.state.settings.decision_backend
            backend = self.backends.get(backend_name)
            version = getattr(backend, "version", "unavailable")
            confidence_kind = "probability" if backend_name == "laya" else "self_report"
            payload = {"event": {"id": event["id"], "session": event["session"], "sender": event["sender"],
                                 "source": event["source"], "text": (event["text"] or "")[:4000],
                                 "text_truncated": len(event["text"] or "") > 4000},
                       "candidates": candidates,
                       "rules": {"direct": context.direct, "private": context.private,
                                 "may_create_task": context.admin, "fixed_task": context.fixed_task}}
            try:
                if backend is None:
                    raise ValueError("backend_unavailable")
                scheduled = self.scheduling.wrap(backend, "decision") if hasattr(self, "scheduling") else backend
                raw = await asyncio.wait_for(scheduled(payload), self.state.settings.decision_timeout)
                decision = validate_decision(raw, event, candidates, context)
                reason = "validated"
            except Exception as exc:
                fallback, reason = True, "timeout" if isinstance(exc, TimeoutError) else "invalid_or_unavailable"
                logger.warning(
                    "Shrink decision_failure input=%s session=%s backend=%s error=%s timeout=%.2f",
                    identity, event["session"], backend_name, type(exc).__name__,
                    self.state.settings.decision_timeout)
                decision = self.base(event, context, candidates)
                if is_task_followup(event["text"]):
                    decision.intent = "supplement"
        limit = self.state.settings.decision_capability_limit
        decision.capability_candidates = list(dict.fromkeys(decision.capability_candidates))[:limit] or capabilities(
            event["text"] or "", candidates["capabilities"], limit)
        result = {"decision": decision.model_dump(), "backend": backend_name, "version": version,
                  "confidence_kind": confidence_kind, "fallback": fallback, "reason": reason,
                  "rule_direct": direct, "elapsed": time.monotonic() - started}
        result["id"] = self.repository.save(identity, candidates, result)
        logger.info(
            "Shrink decision input=%s session=%s sender=%s enabled=%s participation=%s "
            "private=%s direct=%s backend=%s route=%s reason=%s fallback=%s task=%s "
            "topics=%d tasks=%d elapsed=%.3f",
            identity, event["session"], event["sender"], self.state.settings.decision_enabled,
            context.participation, context.private, context.direct, backend_name,
            decision.route, reason, fallback, decision.task,
            len(candidates["topics"]), len(candidates["tasks"]), result["elapsed"])
        return result

    def append_target(self, identity, task_id, session, sender):
        event = self.inbox.get(identity)
        if not event or (event["session"], event["sender"]) != (session, sender):
            raise ValueError("补充要求的输入身份不匹配")
        bindings = self.repository.binding(identity)
        if bindings and task_id not in bindings:
            raise ValueError("不能覆盖输入已有任务关联")
        selected = self.repository.latest(identity)
        selected_task = selected["task"] if selected else None
        if selected_task and task_id and selected_task != task_id:
            raise ValueError("补充目标与本轮核对的任务关联不一致")
        task_id = task_id or selected_task
        if not task_id:
            raise ValueError("请明确要补充的原任务")
        if not self.repository.owned(task_id, session, sender):
            raise ValueError("任务不属于当前会话和创建者")
        if selected and selected["route"] == "ask" and not selected_task:
            raise ValueError("请先澄清补充要求的目标任务")
        return task_id

    def select_capabilities(self, result, text, catalog):
        allowed = {item["name"] for item in catalog if item.get("active", True)}
        limit = self.state.settings.decision_capability_limit
        selected = [name for name in result["decision"]["capability_candidates"] if name in allowed]
        selected.extend(capabilities(text, catalog, limit))
        result["decision"]["capability_candidates"] = list(dict.fromkeys(selected))[:limit]
        self.repository.capability_selection(result["id"], catalog, result["decision"])
        return result["decision"]["capability_candidates"]


def guidance(result):
    value = result["decision"]
    if result["reason"] == "decision_disabled":
        return ("本轮未调用独立判断模型。请结合当前会话历史和用户原话，自行判断意图、"
                "是否需要回应及是否需要派工；群聊是否可不回复仍按本轮要求。"
                "补充任务时先核对原会话、创建者和目标，不猜测关联，不扩大授权；长执行交后台。")
    if result["reason"] in {"stop_ambiguous", "stop_permission"}:
        return "只询问要停止的具体目标；本轮不调用任何执行工具，也不要宣称已停止。"
    action = {"attach_task": "补充要求追加到原任务，使用 shrink_orchestrate append_input，不新建任务。",
              "create_task": "通过现有编排入口提交后台任务，主会话不等待执行。",
              "ask": "先澄清目标，不猜测任务关联。"}.get(value["route"], "")
    return (f"决策路由：{value['route']}；任务：{value['task'] or '未指定'}；话题：{value['topic'] or '未指定'}。\n"
            + action + "\n工具候选：" + "、".join(value["capability_candidates"])
            + "。这是候选子集；未命中或未列出不代表不可用，可继续通过已挂载工具或 "
            "shrink_native_manage tools / shrink_orchestrate tools 发现；长执行仍交后台。")
