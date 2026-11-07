"""Style learning: how real group members reply, plus a bank of their actual phrases."""

import asyncio
import json
import re
import time

from ..growth import style_decision
from ..persistence.database import StateRepository
from ..persistence.learning import LearningRepository
from ..persistence.messages import MessageRepository
from ..ports import ChatModel, ErrorSink
from ..prompts.knowledge import LEARNING, learning_prompt
from ..text_rules import INJECTION, INSULT
from .model_output import ask_json
from .persona import PersonaService

MAX_EXPRESSIONS = 10
ROAST = re.compile(r"是吧$|属于是|笑死.{0,4}了|装啥|抓包|菜就|典中典|难绷.{0,2}$")


class LearningService:
    def __init__(
        self,
        repository: LearningRepository,
        messages: MessageRepository,
        state: StateRepository,
        persona: PersonaService,
        expressions,
        model: ChatModel,
        error: ErrorSink,
    ):
        self.repository, self.messages, self.state = repository, messages, state
        self.persona, self.expressions, self.model, self.error = persona, expressions, model, error
        self.learning_lock = asyncio.Lock()

    def preview_batch(self, cfg):
        batch, seen = [], set()
        for item in self.messages.recent():
            key = (item["user_id"], item["text"].strip())
            if key in seen or INJECTION.search(item["text"]) or item["text"].lstrip().startswith(
                ("/", "!", "！")
            ):
                continue
            seen.add(key)
            batch.append(item)
            if len(batch) >= min(120, cfg.learning_batch_size):
                break
        batch.reverse()
        return batch

    def check_expression(self, item, by_short):
        if not isinstance(item, dict):
            return None, "format"
        phrase = str(item.get("phrase") or "").strip()
        situation = str(item.get("situation") or "").strip()
        ids = item.get("evidence_ids")
        if not isinstance(ids, list) or not ids or not all(str(i) in by_short for i in ids):
            return None, "evidence"
        if not 2 <= len(phrase) <= 20 or not 2 <= len(situation) <= 30:
            return None, "length"
        rows = [by_short[str(i)] for i in ids]
        if not any(phrase in r["text"] for r in rows):
            return None, "not_verbatim"
        if INSULT.search(phrase) or ROAST.search(phrase) or not re.search(r"[一-鿿A-Za-z]", phrase):
            return None, "filtered"
        return {
            "phrase": phrase,
            "situation": situation,
            "evidence": [r["id"] for r in rows],
            "scope": rows[0]["source"],
        }, None

    async def learn(self, force=False, preview=False):
        if self.learning_lock.locked():
            return {"status": "running"}
        async with self.learning_lock:
            cfg = self.state.settings
            if not cfg.learning_enabled and not force and not preview:
                return {"status": "paused"}
            batch = self.preview_batch(cfg) if preview else self.messages.pending(cfg.learning_batch_size)
            if len(batch) < (1 if force or preview else cfg.learning_min_messages):
                return {"status": "waiting", "samples": len(batch)}
            initial_version = self.state.get("active_persona")
            detail = {
                "mode": "preview" if preview else "apply",
                "base_version": initial_version,
                "original_style": self.persona.repository.learned_persona(),
                "candidate": None,
                "decision": "pending",
                "reason": "正在提取",
                "new_version": None,
                "expressions_eligible": 0,
                "expressions_added": 0,
                "expressions_skipped": 0,
                "sync": "not_attempted",
                "evidence": [],
                "sample_ids": [m["id"] for m in batch],
                "provider": cfg.model_provider,
                "prompt": LEARNING.tag,
            }
            run_id = self.repository.start(batch, detail)
            phase = "model"
            try:
                by_short = {f"s{i}": dict(m) for i, m in enumerate(batch, 1)}
                samples = "\n".join(
                    f"{key} [{m['speaker_name'] or m['user_id']}] {m['text'][:200]}"
                    for key, m in by_short.items()
                )
                try:
                    result = await ask_json(
                        self.model, learning_prompt(detail["original_style"], samples),
                        LEARNING.system, timeout=90,
                    )
                except ValueError:
                    phase = "parse"
                    raise
                phase = "parse"
                if not isinstance(result, dict):
                    raise ValueError("invalid learning object")
                by_id = {m["id"]: m for m in batch}
                style = result.get("style")
                raw_evidence = result.get("evidence_ids")
                evidence = (
                    [by_short[str(x)]["id"] if str(x) in by_short else x for x in raw_evidence]
                    if isinstance(raw_evidence, list)
                    else raw_evidence
                )
                detail["candidate"] = style[:2000] if isinstance(style, str) else None
                detail["candidate_length"] = len(style) if isinstance(style, str) else None
                decision, reason = style_decision(
                    style, evidence, by_id, detail["original_style"],
                    self.persona.repository.rejected(),
                )
                detail.update(decision=decision, reason=reason)
                if isinstance(evidence, list):
                    detail["evidence"] = [
                        by_id[x] for x in dict.fromkeys(
                            x for x in evidence if isinstance(x, str) and x in by_id
                        )
                    ]
                phase = "expressions"
                items = result.get("expressions") if isinstance(result.get("expressions"), list) else []
                for item in items[:MAX_EXPRESSIONS]:
                    normalized, problem = self.check_expression(item, by_short)
                    if problem:
                        detail["expressions_skipped"] += 1
                        continue
                    detail["expressions_eligible"] += 1
                    if not preview and self.expressions.add(
                        normalized["scope"], normalized["situation"], normalized["phrase"],
                        normalized["evidence"],
                    ):
                        detail["expressions_added"] += 1
                if decision == "eligible":
                    if preview:
                        detail.update(decision="would_apply", reason="候选通过，预演未应用")
                    else:
                        phase = "sync"
                        version = await self.persona.set_persona(
                            style, detail["evidence"], "自主学习", expected_version=initial_version
                        )
                        if version is None:
                            detail.update(
                                decision="version_conflict",
                                reason="分析期间人格已被修改，保留当前版本",
                            )
                        else:
                            detail.update(
                                decision="applied", reason="新表达习惯已保存并同步",
                                new_version=version, sync="synced",
                            )
                if not preview:
                    self.messages.mark_learned(batch)
                    self.state.put("last_learning", time.time())
                status = "success"
            except asyncio.CancelledError:
                detail.update(decision="interrupted", reason="任务被中断，未消费样本")
                self.repository.finish(run_id, "interrupted", detail["reason"], detail)
                raise
            except Exception as exc:
                if not preview:
                    self.error("learning", exc)
                detail.update(
                    decision="failed",
                    reason={
                        "model": "模型调用失败",
                        "parse": "模型返回格式无效",
                        "expressions": "表达库保存失败",
                        "sync": "人格应用失败，检查同步状态",
                    }.get(phase, "学习失败"),
                    error_type=type(exc).__name__,
                )
                if phase == "sync":
                    detail["sync"] = self.state.get("persona_sync", {}).get("status", "failed")
                status = "failed"
            summary = ("预演未应用：" if preview else "") + detail["reason"]
            self.repository.finish(run_id, status, summary, json.loads(json.dumps(detail, default=str)))
            return {"status": status, "samples": len(batch), "run_id": run_id}
