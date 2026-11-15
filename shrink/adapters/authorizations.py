"""Existing computer.control capability boundary; no native Agent protocol."""
from ..services.task_context import CURRENT_TASK


class AuthorizationAdapter:
    def __init__(self, app, context):
        self.app, self.context = app, context
        self.service = app.authorizations
        self.service.command = self.command
        self.service.access = self.access
        self.service.notify = self.ask
        self.service.is_admin = lambda actor: actor in {str(a) for a in context.get_config().get("admins_id", [])}

    async def command(self, provider, action, arguments, scope):
        return await self.app.plugin_bus.call("computer.control", action, arguments, provider=provider, scope=scope)

    async def access(self, body, actor):
        if not self.service.is_admin(actor) or not self.service.is_admin(body["administrator"]):
            return False
        for account in dict.fromkeys([actor, body["administrator"]]):
            devices = await self.command(body["provider"], "devices", {}, {
                "session": body["origin"], "sender": account, "admin": True})
            if not isinstance(devices, list) or not any(
                d.get("id") == body["device"] and not d.get("revoked")
                and ("access" not in d or d["access"] in {"owner", "delegated"})
                for d in devices
            ):
                return False
        return True

    async def manage(self, action, arguments, scope, input_id=""):
        if CURRENT_TASK.get():
            raise ValueError("后台同构体不能代表用户产生授权")
        if action == "list":
            return self.service.listing(scope)
        if action == "grant":
            return await self.service.grant(input_id, scope, arguments)
        if action in {"pause", "revoke"}:
            return self.service.change(input_id, scope, arguments["policy_id"],
                                       "paused" if action == "pause" else "revoked")
        if action == "answer":
            identity = arguments.get("confirmation_id")
            if not identity:
                candidates = self.service.answer_candidates(input_id, scope)
                if len(candidates) != 1:
                    for row in candidates:
                        await self.ask(row)
                    return {"state": "waiting_user", "reason": "请明确要答复的任务或确认对象",
                            "confirmations": [r["id"] for r in candidates]}
                identity = candidates[0]["id"]
            row = self.service.repository.event(identity)
            self.service.answer(input_id, scope, identity, arguments.get("task", row["body"]["task"]))
            return await self.service.process(identity, input_id)
        if action == "reconcile":
            row = self.service.repository.event(arguments["confirmation_id"])
            if not scope.get("admin") or not self.service.is_admin(str(scope["sender"])) or (
                row["body"]["administrator"], row["body"]["origin"]) != (str(scope["sender"]), scope["session"]):
                raise ValueError("确认对象不属于当前管理员")
            return await self.service.process(row["id"])
        raise ValueError("未知授权操作")

    async def receive(self, arguments, scope):
        row = self.service.receive(arguments, scope)
        result = await self.service.process(row["id"])
        await self.ask(result)
        return result

    async def ask(self, result):
        if result["state"] == "waiting_user":
            from .notifications import Notifications, notification_event
            scope = {"session": result["body"]["origin"], "sender": result["body"]["administrator"]}
            pending = [e for e in self.service.repository.events() if e["state"] == "waiting_user"
                       and e["body"]["origin"] == scope["session"]
                       and e["body"]["administrator"] == str(scope["sender"])]
            await Notifications(self.app).send_once(
                notification_event(self.context, scope["session"], str(scope["sender"])),
                "authorization-question:" + result["id"],
                {"status": "waiting_user", "confirmations": [
                    {"confirmation_id": e["id"], "task": e["body"]["task"], "approval_id": e["body"]["approval_id"],
                     "device": e["body"]["device"], "operation": e["body"]["category"],
                     "difference": e["reason"]} for e in pending]}, kind="question")
