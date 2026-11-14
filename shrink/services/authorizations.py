"""Persistent authorization policy. Device protocol execution is an injected boundary."""
import asyncio
import json
import re
import time


CATEGORIES = {"read": "读取", "execute": "执行", "write": "写入",
              "delete_data": "删除业务数据", "grant_permission": "新增权限"}


class AuthorizationService:
    def __init__(self, repository, inbox):
        self.repository, self.inbox = repository, inbox
        self.access = None
        self.command = None
        self.is_admin = lambda actor: False
        self.notify = None
        self.inflight = set()

    def trusted(self, identity, scope):
        if not scope.get("admin") or not self.is_admin(str(scope.get("sender", ""))):
            raise ValueError("需要当前管理员身份")
        row = self.inbox.get(identity)
        if not row:
            raise ValueError("授权需要原始输入引用")
        if row["source"] == "webui":
            if row["metadata"].get("path") != "/api/v1/authorizations/action":
                raise ValueError("不是授权管理入口")
            data = json.loads(row["text"])
            if data.get("session") != scope["session"]:
                raise ValueError("授权原会话不匹配")
        elif row["source"] == "platform":
            if (row["sender"], row["session"]) != (str(scope["sender"]), scope["session"]) or not row["message_id"]:
                raise ValueError("授权输入身份不匹配")
        else:
            raise ValueError("工具、记忆及模型转述不能作为授权来源")
        return row

    def task_scope(self, task, origin, administrator):
        if not task:
            return
        db = self.repository.db
        if not db.rows("SELECT name FROM sqlite_master WHERE name='operation_tasks'"):
            raise ValueError("任务不存在")
        rows = db.rows("SELECT * FROM operation_tasks WHERE id=?", (task,))
        if not rows or (rows[0]["session"], rows[0]["sender"]) != (origin, administrator):
            raise ValueError("任务与原会话、创建者不一致")
        if rows[0]["status"] in {"completed", "failed", "cancelled", "cancelling"}:
            raise ValueError("任务已结束或正在停止")

    async def grant(self, identity, scope, specification):
        source = self.trusted(identity, scope)
        required = ("device", "provider", "execution_session")
        if not all(isinstance(specification.get(k), str) and specification[k] for k in required):
            raise ValueError("需要设备、提供者和具体执行会话")
        categories = specification.get("categories", [])
        if not categories or not isinstance(categories, list) or not set(categories) <= CATEGORIES.keys():
            raise ValueError("无效操作类别")
        actor = str(scope["sender"])
        administrator = specification.get("administrator", actor)
        if not self.is_admin(administrator):
            raise ValueError("被授权人不是当前管理员")
        future = bool(specification.get("future", False))
        task = specification.get("task", "")
        self.task_scope(task, scope["session"], administrator)
        expires = specification.get("expires")
        if expires is not None and (not isinstance(expires, (int, float)) or expires <= time.time()):
            raise ValueError("有效期必须在未来")
        if source["source"] == "webui":
            submitted = json.loads(source["text"])
            if submitted.get("action") != "grant" or submitted.get("arguments") != specification:
                raise ValueError("策略与原始表单不一致")
            if future and submitted.get("future_instruction") != "未来同类任务":
                raise ValueError("扩大范围需要明确未来同类任务")
        else:
            # Scope is accepted only through an explicit grammar, never model paraphrases.
            text = source["text"].strip()
            expected = "授权 " + specification["device"] + " " + specification["execution_session"] + " " + ",".join(categories)
            suffix = " 未来同类任务" if future else ""
            if text != expected + suffix or administrator != actor or expires is not None:
                raise ValueError("请明确授权范围：授权 设备ID 执行会话ID read,execute；可追加 未来同类任务")
        body = {**specification, "administrator": administrator, "origin": scope["session"]}
        if self.repository.blocked(body) or not self.access or not await self.access(body, actor):
            raise ValueError("设备访问权未确认；策略不能授予设备权限")
        values = {**body, "grantor": actor, "input_id": identity, "task": task, "future": future,
                  "expires": expires, "categories": sorted(set(categories))}
        existing = next((p for p in self.repository.policies(actor) if p["input_id"] == identity), None)
        return existing or self.repository.grant(values)

    def change(self, identity, scope, policy_id, state):
        source = self.trusted(identity, scope)
        if state not in {"paused", "revoked"}:
            raise ValueError("仅支持暂停或撤销；恢复须重新明确授权")
        policy = self.repository.policy(policy_id)
        if not policy or policy["grantor"] != str(scope["sender"]):
            raise ValueError("只能修改本人授予的策略")
        if source["source"] == "platform" and source["text"].strip() != (
            ("暂停授权 " if state == "paused" else "撤销授权 ") + policy_id
        ):
            raise ValueError("请明确要暂停或撤销的策略")
        if source["source"] == "webui":
            data = json.loads(source["text"])
            if data.get("action") != {"paused": "pause", "revoked": "revoke"}[state] or data.get("arguments") != {"policy_id": policy_id}:
                raise ValueError("策略操作与原始表单不一致")
        self.repository.change(policy_id, state, str(scope["sender"]), identity)
        return self.repository.policy(policy_id)

    def revoke_scope(self, identity, scope):
        from .turn_control import is_revocation
        source = self.trusted(identity, scope)
        if source["source"] != "platform" or not is_revocation(source["text"]):
            raise ValueError("撤销必须来自本轮用户明确指令")
        for policy in self.repository.policies(str(scope["sender"])):
            if policy["grantor"] == str(scope["sender"]) and policy["origin"] == scope["session"]:
                self.repository.change(policy["id"], "revoked", str(scope["sender"]), identity)

    def receive(self, body, scope):
        required = {"provider", "device", "execution_session", "approval_id", "category", "expires"}
        if not required <= body.keys() or body["category"] not in CATEGORIES:
            raise ValueError("确认事件缺少具体设备、执行会话、确认对象、类别或有效期")
        if not all(isinstance(body[k], str) and body[k] for k in required - {"expires", "category"}):
            raise ValueError("确认标识无效")
        if not isinstance(body["expires"], (int, float)):
            raise ValueError("确认有效期必须是时间戳")
        if not scope.get("admin") or not self.is_admin(str(scope.get("sender", ""))):
            raise ValueError("确认事件需要可信管理员范围")
        value = {k: body[k] for k in required}
        value.update(task=str(body.get("task", "")), administrator=str(scope["sender"]), origin=scope["session"],
                     summary=str(body.get("summary", ""))[:2000])
        self.task_scope(value["task"], value["origin"], value["administrator"])
        identity = json.dumps([value[k] for k in ("provider", "device", "execution_session", "approval_id")],
                              ensure_ascii=False, separators=(",", ":"))
        with self.repository.db.transaction():
            incoming, _ = self.inbox_service.event(
                session=value["origin"], sender=value["administrator"], external_id=identity,
                source="confirmation", text=json.dumps(value, ensure_ascii=False), route="confirmation")
            return self.repository.receive(identity, incoming["id"], value)

    def policy_for(self, body):
        relevant = [p for p in self.repository.policies() if p["provider"] == body["provider"]
                    and p["administrator"] == body["administrator"]
                    and p["origin"] == body["origin"]
                    and (p["execution_session"] == body["execution_session"] or p["future"])]
        for policy in reversed(relevant):
            if policy["device"] != body["device"]:
                continue
            if policy["state"] != "active" or (policy["expires"] is not None and policy["expires"] <= time.time()):
                continue
            if not policy["future"] and policy["task"] != body["task"]:
                continue
            if body["category"] in policy["categories"]:
                return policy, ""
        if any(p["device"] != body["device"] or p["state"] != "active" or
               (p["expires"] is not None and p["expires"] <= time.time()) for p in relevant):
            return None, "revoked_expired_or_device_mismatch"
        return None, "needs_authorization:" + CATEGORIES[body["category"]]

    async def process(self, identity, answer_input=None):
        if identity in self.inflight:
            return self.repository.event(identity)
        self.inflight.add(identity)
        try:
            event = self.repository.event(identity)
            body = event["body"]
            scope = {"session": body["origin"], "sender": body["administrator"], "admin": True}
            if event["state"] == "confirmed":
                return event
            if event["operation_id"]:
                return await self.reconcile(event, scope)
            policy, reason = self.policy_for(body)
            if answer_input:
                self.answer(answer_input, scope, identity, body["task"])
            if body["expires"] <= time.time() or self.repository.blocked(body) or not self.is_admin(body["administrator"]):
                self.repository.update(identity, "held", "expired_or_access_revoked")
            elif not answer_input and not policy:
                self.repository.update(identity, "held" if reason == "revoked_expired_or_device_mismatch" else "waiting_user", reason)
            elif not self.access or not await self.access(body, body["administrator"]):
                self.repository.update(identity, "held", "device_access_denied")
            else:
                # Recheck after the awaited access lookup: a concurrent revocation wins.
                current, _ = self.policy_for(body)
                if self.repository.blocked(body) or (not answer_input and (not current or current["id"] != policy["id"]
                                                                          or current["version"] != policy["version"])):
                    self.repository.update(identity, "held", "authorization_changed")
                else:
                    self.task_scope(body["task"], body["origin"], body["administrator"])
                    operation = self.repository.reserve(identity, policy if not answer_input else None, answer_input)
                    if operation:
                        try:
                            await self.command(body["provider"], "approve", {
                                "device_id": body["device"], "session_id": body["execution_session"],
                                "approval_id": body["approval_id"], "allow": True, "request_id": operation,
                                "authorization": {"policy_id": policy["id"] if policy else "",
                                                  "version": policy["version"] if policy else 0,
                                                  "confirmation_id": identity, "expires": body["expires"]}}, scope)
                        except (Exception, asyncio.CancelledError) as exc:
                            self.repository.update(identity, "unknown", type(exc).__name__)
                            if isinstance(exc, asyncio.CancelledError):
                                raise
                        return await self.reconcile(self.repository.event(identity), scope)
            return self.repository.event(identity)
        finally:
            self.inflight.discard(identity)

    def answer(self, input_id, scope, confirmation_id, task):
        source = self.trusted(input_id, scope)
        event = self.repository.event(confirmation_id)
        body = event["body"]
        if (body["origin"], body["administrator"], body["task"]) != (scope["session"], str(scope["sender"]), task):
            raise ValueError("答复与任务或确认对象不一致")
        if source["source"] == "webui":
            data = json.loads(source["text"])
            if data.get("action") != "answer" or data.get("arguments") != {"confirmation_id": confirmation_id, "task": task}:
                raise ValueError("答复表单与确认对象不一致")
        elif source["text"].strip() != "同意 " + body["approval_id"]:
            raise ValueError("答复须明确：同意 确认对象ID")
        if source["source"] == "platform" and any(
            e["id"] != confirmation_id and e["body"]["approval_id"] == body["approval_id"]
            and e["body"]["origin"] == body["origin"] and e["state"] != "confirmed"
            for e in self.repository.events()
        ):
            raise ValueError("确认对象ID不唯一，请在授权管理中选择具体任务与确认对象")
        reused = [r for r in self.repository.events() if r["answer_input"] == input_id and r["id"] != confirmation_id]
        if reused:
            raise ValueError("答复已经绑定另一确认对象")
        return event

    async def reconcile(self, event, scope):
        body = event["body"]
        try:
            receipt = await self.command(body["provider"], "result", {
                "device_id": body["device"], "request_id": event["operation_id"]}, scope)
            result = receipt.get("result") or {}
            confirmed = (receipt.get("state") == "completed" and result.get("ok") is True
                         and result.get("approval_id") == body["approval_id"]
                         and result.get("session_id") == body["execution_session"] and result.get("allow") is True)
            self.repository.update(event["id"], "confirmed" if confirmed else "unknown", receipt=receipt)
            if confirmed:
                self.inbox.complete([event["input_id"]])
        except Exception as exc:
            self.repository.update(event["id"], "unknown", type(exc).__name__)
        return self.repository.event(event["id"])

    async def recover(self):
        for event in self.repository.events():
            if event["state"] in {"pending", "issuing", "unknown", "waiting_user"}:
                try:
                    result = await self.process(event["id"])
                except Exception as exc:
                    self.repository.update(event["id"], "held", "recovery_check_failed:" + type(exc).__name__)
                    continue
                if result["state"] == "waiting_user" and self.notify:
                    await self.notify(result)

    def execution_allowed(self, identity, operation_id):
        event = self.repository.event(identity)
        body = event["body"]
        if (event["operation_id"] != operation_id or event["state"] not in {"issuing", "unknown"}
                or body["expires"] <= time.time() or self.repository.blocked(body)
                or not self.is_admin(body["administrator"])):
            return False
        try:
            self.task_scope(body["task"], body["origin"], body["administrator"])
        except ValueError:
            return False
        if event["answer_input"]:
            return True
        policy, _ = self.policy_for(body)
        return bool(policy and policy["id"] == event["policy"] and policy["version"] == event["policy_version"])

    def listing(self, scope):
        if not scope.get("admin") or not self.is_admin(str(scope.get("sender", ""))):
            raise ValueError("需要管理员身份")
        actor = str(scope["sender"])
        return {"policies": self.repository.policies(actor),
                "confirmations": [e for e in self.repository.events() if e["body"]["administrator"] == actor],
                "audit": self.repository.db.rows("SELECT * FROM authorization_audit WHERE actor=? ORDER BY id DESC", (actor,))}
