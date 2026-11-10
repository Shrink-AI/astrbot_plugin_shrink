import asyncio
import hashlib
import json
import time
import base64
from types import SimpleNamespace
import yaml

from ..persistence.maintenance import MaintenanceRepository
from ..prompts import render, text as prompt_text
from .model_output import ask_json
from .tool_evidence import execution_evidence

RECOVERY = 30 * 86400
TERMINAL = {"completed", "cancelled", "failed"}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def skill_content(snapshot):
    files = dict(snapshot["files"])
    markdown = base64.b64decode(files.pop("SKILL.md")).decode("utf8")
    parts = markdown.split("---", 2)
    if len(parts) != 3 or parts[0].strip():
        return fingerprint(snapshot)
    metadata = yaml.safe_load(parts[1])
    if not isinstance(metadata, dict):
        return fingerprint(snapshot)
    metadata.pop("name", None)
    return fingerprint([metadata, parts[2].strip(), files])


class CapabilityMaintenance:
    def __init__(self, app, native, skills, archive=None):
        self.app, self.native, self.skills = app, native, skills
        self.repository = MaintenanceRepository(app.database)
        self.lock = asyncio.Lock()
        self.archive = archive
        self.inventory_errors = []

    async def inventory(self):
        errors = []
        try:
            definitions = (await self.native.call("subagents", "list")).get("agents", [])
        except Exception as exc:
            definitions = []
            errors.append({"kind": "subagent", "error": str(exc)[:200]})
        created = self.repository.created_agents()
        values = {}
        for s in self.repository.skill_inventory():
            name = str(s["id"])
            self.repository.register("skill", name, s["creator"] or "unknown",
                                     s["creator"] in {"reflection", "orchestration"})
            values[("skill", name)] = {k: s[k] for k in
                ("title", "trigger", "steps", "status", "version", "uses", "wins", "losses", "last_used")}
        for definition in definitions:
            name = definition["name"]
            self.repository.register("subagent", name, "agent_created" if name in created else "unknown",
                                     name in created, name in {"shirnk_developer", "shirnk_task_coordinator"})
            values[("subagent", name)] = definition
        if self.archive:
            try:
                linked = self.repository.linked_skill_names()
                created = self.repository.created_skills()
                inventory = (await self.native.call("skills", "list")).get("skills", [])
                for item in inventory:
                    name = item["name"]
                    if name in linked or item.get("source_type") in {"plugin", "sandbox"}:
                        continue
                    self.repository.register("native_skill", name,
                        "skill_created" if name in created else "unknown", name in created)
                    values[("native_skill", name)] = {
                        "title": item.get("display_name") or name,
                        "description": item.get("description", ""), "active": item.get("active", False),
                    }
            except Exception as exc:
                errors.append({"kind": "native_skill", "error": str(exc)[:200]})
        self.inventory_errors = errors
        return values

    def references(self, kind, name):
        tasks = self.repository.tasks()
        busy = [r for r in tasks if r["status"] not in TERMINAL or
                r.get("delivery") in {"pending", "composing", "sending", "unknown", "interrupted"} or
                str(r.get("delivery", "")).startswith("failed")]
        # Skills may be loaded dynamically by native runners; unresolved work protects them.
        if kind in {"skill", "native_skill"}:
            return [r["id"] for r in busy]
        roots = {r["id"] for r in busy}
        by_id = {r["id"]: r for r in tasks}
        todo = list(roots)
        while todo:
            row = by_id.get(todo.pop(), {})
            refs = json.loads(row.get("dependencies", "[]")) + ([row["parent"]] if row.get("parent") else [])
            for ref in refs:
                if ref not in roots:
                    roots.add(ref)
                    todo.append(ref)
        return [r["id"] for r in tasks if r["id"] in roots and r.get("agent") == name]

    async def status(self):
        values = await self.inventory()
        items = []
        for row in self.repository.list():
            value = values.get((row["kind"], row["name"]), {})
            items.append({**{k: v for k, v in row.items() if k != "snapshot"},
                          "title": value.get("title", row["name"]),
                          "references": self.references(row["kind"], row["name"]),
                          "evidence": {k: value[k] for k in ("uses", "wins", "losses", "last_used") if k in value},
                          "recover_until": row["retired"] + RECOVERY if row["retired"] else None})
        return {"items": items, "history": self.repository.history(),
                "last_run": self.app.state.get("capability_maintenance", {}),
                "inventory_errors": self.inventory_errors,
                "suggestion": self.app.state.get("skill_retirement_suggestion", {})}

    async def action(self, kind, name, action):
        async with self.lock:
            await self.inventory()
            row = self.repository.get(kind, name)
            if not row:
                raise ValueError("未找到能力")
            if action in {"protect", "unprotect", "adopt"}:
                fields = {"managed": 1, "origin": "admin_adopted"} if action == "adopt" else {
                    "protected": int(action == "protect")}
                self.repository.update(kind, name, **fields)
            elif action == "restore":
                if row["state"] not in {"retired", "retiring", "restoring"} or not row["snapshot"]:
                    raise ValueError("没有可恢复的快照")
                self.repository.update(kind, name, state="restoring")
                await self.restore(row)
            else:
                raise ValueError("不支持的维护操作")
            self.repository.audit(kind, name, action, "管理员操作")
        return await self.status()

    async def change_agent(self, name, expected, replacement):
        async with self.app.operations.agent_config_lock:
            config = await self.native.call("subagents", "list")
            current = next((a for a in config.get("agents", []) if a["name"] == name), None)
            if current != expected:
                raise ValueError("子代理定义已变化，停止维护")
            config["agents"] = [a for a in config.get("agents", []) if a["name"] != name]
            if replacement is not None:
                config["agents"].append(replacement)
            await self.native.call("subagents", "update", config)

    async def retire(self, row, value, reason):
        kind, name = row["kind"], row["name"]
        row = self.repository.get(kind, name)
        if self.references(kind, name) or row["protected"] or not row["managed"]:
            return False
        if kind == "skill":
            async with self.skills.lock:
                current = self.app.skill_repository.get(int(name))
                if not current or current["version"] != value["version"]:
                    raise ValueError("经验已变化，停止维护")
                inspected = await self.skills.inspect(int(name))
                if self.app.skill_repository.get(int(name))["version"] != value["version"]:
                    raise ValueError("经验在维护期间发生变化")
                if inspected["conflict"] or (inspected["native"]["exists"] and
                    inspected["native"]["content"] != self.skills.document(current, inspected["name"])):
                    raise ValueError("原生 Skill 有修改冲突")
                self.repository.update(kind, name, state="retiring",
                    snapshot=json.dumps(value, ensure_ascii=False), version=fingerprint(value),
                    reason=reason, retired=time.time())
                self.app.skill_repository.revise(int(name), reason, origin="maintenance", status="retired")
            await self.skills.sync()
        elif kind == "native_skill":
            async with self.skills.lock:
                snapshot = await self.archive.snapshot(name)
                if snapshot != value:
                    raise ValueError("Skill 已变化，停止本次淘汰")
                self.repository.update(kind, name, state="retiring",
                    snapshot=json.dumps(snapshot), version=fingerprint(snapshot),
                    reason=reason, retired=time.time())
                await self.archive.retire(name, snapshot)
        else:
            self.repository.update(kind, name, state="retiring",
                snapshot=json.dumps(value, ensure_ascii=False), version=fingerprint(value),
                reason=reason, retired=time.time())
            await self.change_agent(name, value, None)
        self.repository.update(kind, name, state="retired")
        self.repository.audit(kind, name, "retired", reason)
        return True

    async def restore(self, row):
        value = json.loads(row["snapshot"])
        if row["kind"] == "skill":
            async with self.skills.lock:
                inspected = await self.skills.inspect(int(row["name"]))
                if inspected["conflict"] or inspected["local"]["status"] != "retired" or any(
                    inspected["local"][key] != value[key] for key in ("title", "trigger", "steps")):
                    raise ValueError("Skill 已被修改，需人工核对版本")
                self.app.skill_repository.revise(int(row["name"]), "恢复夜间淘汰", origin="admin",
                                                status="active")
            await self.skills.sync()
        elif row["kind"] == "native_skill":
            async with self.skills.lock:
                await self.archive.restore(row["name"], value)
        else:
            current = (await self.native.call("subagents", "list")).get("agents", [])
            found = next((a for a in current if a["name"] == row["name"]), None)
            if found != value:
                await self.change_agent(row["name"], None, value)
        self.repository.update(row["kind"], row["name"], state="active", retired=None,
                               snapshot="", version="", protected=1, reason="已恢复并保护")

    async def purge(self, row):
        kind, name = row["kind"], row["name"]
        if row["protected"] or self.references(kind, name):
            return False
        if kind == "skill":
            async with self.skills.lock:
                current = self.app.skill_repository.get(int(name))
                if current:
                    inspected = await self.skills.inspect(int(name))
                    original = json.loads(row["snapshot"])
                    if inspected["conflict"] or current["status"] != "retired" or any(
                        current[k] != original[k] for k in ("title", "trigger", "steps")):
                        raise ValueError("淘汰后内容变化，拒绝清除")
                    if inspected["native"]["exists"] and (
                        inspected["native"]["active"] or inspected["native"]["content"] !=
                        self.skills.document(current, inspected["name"])):
                        raise ValueError("原生内容已变化，拒绝清除")
                    if inspected["native"]["exists"]:
                        await self.native.call("skills", "delete", {"name": inspected["name"]}, True)
                    self.repository.purge_skill(int(name))
        elif kind == "native_skill":
            async with self.skills.lock:
                await self.archive.purge(name, json.loads(row["snapshot"]))
        else:
            definitions = (await self.native.call("subagents", "list")).get("agents", [])
            if any(a["name"] == name for a in definitions):
                raise ValueError("代理已重新出现，拒绝清除")
        self.repository.update(kind, name, state="purged", snapshot="", version="")
        self.repository.audit(kind, name, "purged", "恢复期已过且无引用")
        return True

    async def reflect_orchestration(self):
        """Use the existing experience pipeline, retaining no private task parameters in lessons."""
        for task in self.repository.review_tasks():
            tools = []
            verified = False
            for event in self.repository.task_events(task["id"]):
                try:
                    result = json.loads(event["result"])
                except (TypeError, ValueError):
                    continue
                evidence = execution_evidence(result.get("result"))
                verified |= bool(evidence and evidence["status"] == "completed")
                tools.append({"name": event["action"].removeprefix("tool_end:"),
                    "args": "已省略私人任务参数",
                    "result": ("工具报错" if result.get("error") else
                               evidence["status"] if evidence else "工具已返回，效果未独立核验")})
            if not verified:
                self.repository.reviewed(task["id"], None)
                continue
            scope = self.app.outbound.resolve_scope({"session": task["session"], "sender": task["sender"]})
            if not scope:
                continue
            turn = SimpleNamespace(source=scope["source"], user_id=scope["user_id"],
                id="orchestration:" + task["id"], text="后台子代理任务的工具调用编排和核验步骤",
                created=task["started"])
            episode = self.app.skills.record(turn, tools,
                "任务已返回，工具有完成回执；只提炼可复用的派工顺序和核验方法，不推断具体业务目标均已成功。",
                "ok", [])
            self.repository.reviewed(task["id"], episode)
        await self.app.skills.reflect_due()

    async def run(self):
        async with self.lock:
            run_id = self.app.runs.start("capability_maintenance")
            stats = {"retired": 0, "purged": 0, "retained": 0, "errors": []}
            try:
                values = await self.inventory()
                stats["errors"].extend(self.inventory_errors)
                unavailable = {e["kind"] for e in self.inventory_errors}
                native_snapshots = {}
                if self.archive and "native_skill" not in unavailable:
                    for kind, name in values:
                        if kind == "native_skill":
                            try:
                                native_snapshots[name] = await self.archive.snapshot(name)
                            except Exception as exc:
                                stats["errors"].append({"kind": kind, "name": name, "error": str(exc)[:200]})
                for row in self.repository.list():
                    kind, name = row["kind"], row["name"]
                    try:
                        if kind in unavailable:
                            continue
                        if not row["managed"] or row["protected"] or self.references(kind, name):
                            stats["retained"] += 1
                            continue
                        if row["state"] == "restoring":
                            await self.restore(row)
                            continue
                        if row["state"] == "retiring":
                            # An interrupted mutation must be reconciled, not guessed or repeated.
                            value = values.get((kind, name))
                            if kind == "skill" and value and value["status"] == "retired":
                                original = json.loads(row["snapshot"])
                                if any(value[k] != original[k] for k in ("title", "trigger", "steps")):
                                    raise ValueError("淘汰中内容发生变化，等待核对")
                                await self.skills.sync()
                                self.repository.update(kind, name, state="retired")
                            elif kind == "subagent" and value is None:
                                self.repository.update(kind, name, state="retired")
                            elif kind == "native_skill":
                                expected = json.loads(row["snapshot"])
                                current = await self.archive.snapshot(name)
                                if current["files"] != expected["files"]:
                                    raise ValueError("Skill 内容发生变化，等待核对")
                                await self.archive.retire(name, current)
                                self.repository.update(kind, name, state="retired")
                            else:
                                raise ValueError("上次淘汰未完成，保留现场等待核对")
                            continue
                        if row["state"] == "retired":
                            if time.time() - row["retired"] >= RECOVERY:
                                stats["purged"] += int(await self.purge(row))
                            continue
                        if row["state"] != "active":
                            continue
                        value = values.get((kind, name))
                        if value is None:
                            continue
                        reason = ""
                        if kind == "skill" and value["status"] == "retired":
                            reason = "已由经验质量复盘停用"
                        if kind == "skill" and not reason:
                            equivalent = [(other_name, other) for (other_kind, other_name), other in values.items()
                                if other_kind == "skill" and other_name != name and other["status"] == "active"
                                and all(" ".join(other[k].split()) == " ".join(value[k].split())
                                        for k in ("trigger", "steps"))]
                            if equivalent:
                                survivor = min([name, *[n for n, _ in equivalent]], key=int)
                                if survivor != name:
                                    reason = "内容与在用经验完全重复，保留经验 " + survivor
                        if kind == "subagent":
                            if row["origin"] == "agent_created":
                                created = self.repository.created_agents().get(name)
                                if not created or json.loads(created) != value:
                                    stats["retained"] += 1
                                    continue
                            tasks = [t for t in self.repository.tasks() if t.get("agent") == name]
                            if tasks and all(t["status"] in TERMINAL for t in tasks):
                                # A completed task alone does not prove a reusable definition obsolete.
                                suggestion = await ask_json(self.app.expression.model,
                                    render("maintenance.review", evidence=json.dumps({
                                        "definition": value, "completed_tasks": len(tasks),
                                        "statuses": [t["status"] for t in tasks]}, ensure_ascii=False)),
                                    prompt_text("maintenance.system"), timeout=30)
                                if isinstance(suggestion, dict) and suggestion.get("temporary") is True:
                                    reason = str(suggestion.get("reason", ""))[:300]
                        if kind == "native_skill" and name in native_snapshots:
                            own = native_snapshots[name]
                            equivalents = [n for n, v in native_snapshots.items()
                                if v["active"] and n != name and skill_content(v) == skill_content(own)]
                            if equivalents and min([name, *equivalents]) != name:
                                reason = "内容与在用 Skill 完全重复，保留 " + min(equivalents)
                        if reason:
                            stats["retired"] += int(await self.retire(
                                row, native_snapshots[name] if kind == "native_skill" else value, reason))
                        else:
                            stats["retained"] += 1
                    except Exception as exc:
                        stats["errors"].append({"kind": kind, "name": name, "error": str(exc)[:200]})
                try:
                    await self.reflect_orchestration()
                except Exception as exc:
                    stats["errors"].append({"kind": "skill", "error": "编排复盘：" + str(exc)[:200]})
                self.app.state.put("capability_maintenance", {"at": time.time(), **stats})
                for kind in ("skill", "subagent"):
                    section = self.app.runs.start(kind + "_maintenance")
                    errors = [e for e in stats["errors"] if
                              (e["kind"] == "subagent") == (kind == "subagent")]
                    self.app.runs.finish(section, "failed" if errors else "success", {"errors": errors})
                self.app.runs.finish(run_id, "failed" if stats["errors"] else "success", stats)
            except BaseException:
                self.app.runs.finish(run_id, "failed", stats)
                raise
            return stats
