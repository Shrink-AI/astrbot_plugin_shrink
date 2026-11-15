"""One durable queue before the existing host notification transport."""
import asyncio
import json
import time

from .notification_selection import select, URGENT, necessary
from .composition import NOTIFICATION
from .outbound_guard import unsafe
from .scheduling import Scheduling


def task_fact(task):
    return json.dumps(["task", task["id"], task["status"], task.get("finished"), task.get("step", 0)],
                      ensure_ascii=False, separators=(",", ":"))


class NotificationOutbox:
    def __init__(self, repository, receipts, inbox, decisions, error):
        self.repository, self.receipts, self.inbox, self.decisions = repository, receipts, inbox, decisions
        self.error = error
        self.transport = None
        self.record = None
        self.foreground = lambda session: False
        self.inflight = set()
        self.merge_seconds = 2
        self.scheduling = Scheduling()
        self.persona = lambda: ""

    def submit(self, scope, event_id, facts, *, kind="result", message_type="", request="", task_id="", fact_refs=(),
               fallback_text=""):
        if not event_id or not scope.get("session") or not scope.get("sender"):
            raise ValueError("通知需要稳定事件标识、原会话和创建者")
        if not isinstance(fact_refs, (tuple, list)) or not all(isinstance(ref, str) and ref for ref in fact_refs):
            raise ValueError("事实引用必须是稳定标识列表")
        if not isinstance(kind, str) or not isinstance(message_type, str):
            raise ValueError("消息类型必须是字符串")
        task_id = task_id or (facts.get("task_id", "") if isinstance(facts, dict) else "")
        scope = {**scope, "sender": str(scope["sender"])}
        refs = list(fact_refs)
        if task_id:
            if not isinstance(facts, dict):
                raise ValueError("任务通知事实须包含已持久化的状态")
            task = self.repository.task(task_id)
            if task["sender"] != scope["sender"]:
                raise ValueError("通知任务不属于当前创建者")
            scope["session"] = task["session"]
            if kind == "deployment" and isinstance(facts, dict):
                facts = {**facts, "deployment_status": facts["status"], "status": task["status"]}
            if isinstance(facts, dict) and facts.get("status") != task["status"]:
                raise ValueError("通知事实状态与已保存任务不一致")
            refs = [task_fact(task)]
            for dependency in json.loads(task.get("dependencies") or "[]"):
                child = self.repository.task(dependency)
                if (child["session"], child["sender"]) == (task["session"], task["sender"]) and child["status"] == "completed":
                    refs.append(task_fact(child))
        else:
            refs = ["external:" + str(ref) for ref in refs] or ["event:" + str(event_id)]
        identity = json.dumps([scope["session"], scope["sender"], event_id], ensure_ascii=False)
        existing = self.repository.get(identity)
        if existing:
            return existing
        with self.repository.db.transaction():
            message_type = message_type or "core." + kind
            owned = self.repository.ownership(scope["session"], scope["sender"], message_type, "core")
            owned = owned and self.repository.event_owner(identity, "core")
            incoming, _ = self.inbox.event(session=scope["session"], sender=scope["sender"], external_id=event_id,
                                          source="notification", route="notification", text=json.dumps(facts, ensure_ascii=False),
                                          metadata={"kind": kind})
            now = time.time()
            question = kind == "question" and isinstance(facts, dict) and facts.get("confirmations")
            row = self.repository.insert({
                "id": identity, "event_id": event_id, "input_id": incoming["id"],
                "session": scope["session"], "sender": scope["sender"], "message_type": message_type,
                "kind": kind, "task_id": task_id, "facts": facts, "fact_refs": refs, "request": request,
                "priority": 0 if kind in URGENT else 20 if kind in {"progress", "tool_progress"} else 10,
                "state": "pending" if owned else "suppressed", "reason": "" if owned else "compatibility_owner",
                "fallback_text": fallback_text.strip() if isinstance(fallback_text, str)
                and 0 < len(fallback_text) <= 1000 and not unsafe(
                    fallback_text, facts, self.persona(), readable_fallback=True) else "",
                "created": now, "available": now + (.2 if question else self.merge_seconds
                    if kind in {"progress", "tool_progress"}
                    and self.decisions.state.settings.decision_backend in self.decisions.backends else 0)})
            self.inbox.repository.complete([incoming["id"]])
            return row

    def lookup(self, scope, event_id, message_type, task_id=""):
        if task_id:
            task = self.repository.task(task_id)
            if task["sender"] != str(scope.get("sender", "")):
                raise ValueError("通知任务不属于当前创建者")
            scope = {**scope, "session": task["session"]}
        identity = json.dumps([scope.get("session"), str(scope.get("sender", "")), event_id], ensure_ascii=False)
        row = self.repository.get(identity)
        if not row or row["message_type"] != message_type:
            raise ValueError("通知不存在或范围不匹配")
        return row

    def operate(self, scope, event_id, message_type, action, task_id=""):
        row = self.lookup(scope, event_id, message_type, task_id)
        if (row["session"], row["sender"]) != (scope.get("session"), str(scope.get("sender", ""))):
            raise ValueError("通知操作需要原提交者范围")
        identity = row["id"]
        receipt = self.receipts.get(identity)
        if receipt and receipt["state"] in {"platform_accepted", "sending", "unknown", "silent", "cancelled"}:
            self.project(row, receipt)
            return self.repository.get(identity), False
        if action == "cancel":
            applied = not receipt and self.repository.move(
                identity, ("pending", "merging"), "cancelled", "submitter_cancelled")
        elif action == "retry":
            allowed = (identity not in self.inflight and row["state"] not in {
                "sent", "unknown", "suppressed", "cancelled"} and (
                    receipt and receipt["state"] in {"failed", "interrupted", "composing"}
                    or row["state"] == "failed" and not receipt))
            applied = bool(allowed and self.repository.move(
                identity, ("failed", "delivering"), "pending", "explicit_retry"))
        else:
            raise ValueError("未知通知操作")
        return self.repository.get(identity), bool(applied)

    def busy(self, row):
        if necessary(row["kind"]) and time.time() - row["created"] >= self.decisions.state.settings.notification_max_delay:
            return False
        return row["priority"] > 0 and (self.foreground(row["session"]) or bool(
            self.inbox.repository.pending(route="chat", session=row["session"])))

    def preflight(self, row):
        if not row["session"] or not row["sender"]:
            return False
        if row["task_id"]:
            task = self.repository.task(row["task_id"])
            if (task["session"], task["sender"]) != (row["session"], row["sender"]):
                return False
            if task_fact(task) not in row["fact_refs"]:
                return False
        if isinstance(row["facts"], dict) and row["facts"].get("confirmations") and all(
                isinstance(c, dict) and c.get("confirmation_id") for c in row["facts"]["confirmations"]):
            pending = self.repository.waiting_confirmations(row["facts"]["confirmations"])
            if not pending:
                return False
            if pending != row["facts"]["confirmations"]:
                row["facts"] = {**row["facts"], "confirmations": pending}
                self.repository.facts(row["id"], row["facts"])
        return self.repository.claim_facts(row)

    def ready_to_send(self, identity, facts):
        row = self.repository.get(identity)
        return row and not self.busy(row) and self.preflight(row) and row["facts"] == facts

    async def deliver(self, identity, *, event=None, retry=False):
        row = self.repository.get(identity)
        if not row:
            return False
        async with self.scheduling.use("urgent" if row["priority"] == 0 else "expression"):
            return await self._deliver(identity, event=event, retry=retry)

    async def _deliver(self, identity, *, event=None, retry=False):
        row = self.repository.get(identity)
        if not row or identity in self.inflight or row["state"] in {"sent", "unknown", "suppressed", "cancelled"}:
            return False
        receipt = self.receipts.get(identity)
        protected = receipt and receipt["state"] in {"platform_accepted", "sending", "unknown", "silent", "cancelled"}
        if not protected:
            if row["available"] > time.time() or self.busy(row) or not self.transport:
                return False
            if row["kind"] == "foreground":
                self.repository.state(identity, "unknown", "foreground_receipt_missing")
                return False
        self.inflight.add(identity)
        try:
            if protected:
                if receipt["state"] == "platform_accepted" and self.record:
                    self.record({"session": row["session"], "sender": row["sender"]}, receipt["text"], identity)
                self.project(row, receipt)
                return False
            if row["state"] == "failed" and not retry:
                return False
            if row["kind"] == "question":
                row = self.repository.merge_questions(row)
            decision = row["decision"] or await select(self.decisions, row)
            if self.repository.get(identity)["state"] == "cancelled":
                return False
            notify = decision["decision"]["notify"]
            if notify == "silent":
                if self.receipts.reserve(identity):
                    self.receipts.state(identity, "silent")
                self.repository.state(identity, "suppressed", decision["reason"], decision=decision)
                return False
            if notify == "merge":
                self.repository.state(identity, "merging", decision=decision)
                others = []
                for sibling in self.repository.siblings(row):
                    value = sibling["decision"] or await select(self.decisions, sibling)
                    if self.repository.get(identity)["state"] == "cancelled":
                        return False
                    if self.repository.get(sibling["id"])["state"] == "cancelled":
                        continue
                    if value["decision"]["notify"] == "merge":
                        self.repository.state(sibling["id"], "merging", decision=value)
                        others.append(sibling)
                    else:
                        self.repository.state(sibling["id"], "suppressed", value["reason"], decision=value)
                others = [r for r in others if self.repository.get(r["id"])["state"] != "cancelled"]
                row = self.repository.merge(row, others) if others else row
            if not self.preflight(row):
                self.repository.state(identity, "suppressed", "duplicate_or_stale_fact", decision=decision)
                return False
            self.repository.state(identity, "delivering", decision=decision)
            token = NOTIFICATION.set(row)
            try:
                accepted = await self.transport(row, event, retry or bool(receipt))
            finally:
                NOTIFICATION.reset(token)
            receipt = self.receipts.get(identity)
            if receipt:
                if receipt["state"] == "deferred":
                    self.repository.state(identity, "pending", "foreground_priority")
                else:
                    self.project(row, receipt)
            return accepted
        finally:
            self.inflight.discard(identity)

    def project(self, row, receipt):
        state = {"platform_accepted": "sent", "silent": "suppressed", "cancelled": "suppressed",
                 "sending": "unknown", "unknown": "unknown"}.get(receipt["state"], "failed")
        self.repository.state(row["id"], state, receipt.get("error", "") or receipt["state"])
        if row["task_id"] and row["kind"] in {"task_result", "question", "deployment"}:
            from ..persistence.task_reports import TaskReports
            TaskReports(self.repository.db).delivery(row["task_id"], receipt["state"])
        elif row["kind"] == "foreground":
            self.foreground_projection(row, receipt["state"] if receipt["state"] != "sending" else "unknown")

    def acknowledge(self, scope, task_id, receipt, fact=None):
        task = self.repository.task(task_id)
        if (task["session"], task["sender"]) != (scope["session"], str(scope["sender"])):
            raise ValueError("前台回执与任务原会话不一致")
        self.repository.acknowledge(scope["session"], [fact or task_fact(task)], receipt)

    def foreground_begin(self, scope, task_id, key, fact, text):
        task = self.repository.task(task_id)
        if (task["session"], task["sender"]) != (scope["session"], str(scope["sender"])):
            raise ValueError("前台回执与任务原会话不一致")
        with self.repository.db.transaction():
            row = self.submit(scope, "foreground:" + key,
                              {"task_reference": task_id, "fact": fact}, kind="foreground", fact_refs=[fact])
            if not self.receipts.reserve(row["id"]):
                return None
            self.repository.acknowledge(scope["session"], [fact], row["id"])
            self.repository.state(row["id"], "delivering", "foreground")
            self.receipts.state(row["id"], "sending", text)
            self.foreground_projection(row, "sending")
        return row["id"]

    def foreground_end(self, identity, accepted):
        receipt = self.receipts.get(identity)
        self.receipts.state(identity, "platform_accepted" if accepted else "unknown", receipt["text"])
        self.repository.state(identity, "sent" if accepted else "unknown", "foreground")
        self.foreground_projection(self.repository.get(identity), "platform_accepted" if accepted else "unknown")

    def foreground_projection(self, row, state):
        facts = row["facts"]
        task = self.repository.task(facts["task_reference"])
        if task["status"] in {"completed", "failed", "needs_attention", "waiting_user"} and task_fact(task) == facts["fact"]:
            from ..persistence.task_reports import TaskReports
            TaskReports(self.repository.db).delivery(task["id"], state)

    async def pump(self, urgent=None):
        for row in self.repository.ready():
            if urgent is not None and (row["priority"] == 0) != urgent:
                continue
            try:
                await self.deliver(row["id"])
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.error("notification_outbox", exc)

    async def run(self):
        closing = False
        async def lane(urgent):
            while not closing:
                await self.pump(urgent)
                await asyncio.sleep(0.25)
        workers = [asyncio.create_task(lane(urgent)) for urgent in (False, True)]
        try:
            done, _ = await asyncio.wait(workers, return_when=asyncio.FIRST_EXCEPTION)
            for worker in done:
                worker.result()
        finally:
            # Some host/provider wait_for calls consume a cancellation when their result
            # arrives concurrently. The loop must also observe an explicit close flag.
            closing = True
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
