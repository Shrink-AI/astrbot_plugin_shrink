"""Constrained facade over AstrBot's own authenticated dashboard services."""
import json
import time
import io
import re
import zipfile

import httpx


ROUTES = {
    "plugins": {
        "list": ("GET", "/api/plugin/get"),
        "reload": ("POST", "/api/plugin/reload"),
        "enable": ("POST", "/api/plugin/on"),
        "disable": ("POST", "/api/plugin/off"),
    },
    "mcp": {
        "list": ("GET", "/api/tools/mcp/servers"),
        "create": ("POST", "/api/tools/mcp/add"),
        "update": ("POST", "/api/tools/mcp/update"),
        "delete": ("POST", "/api/tools/mcp/delete"),
        "test": ("POST", "/api/tools/mcp/test"),
    },
    "skills": {
        "create": ("POST", "/api/skills/upload"),
        "list": ("GET", "/api/skills"),
        "files": ("GET", "/api/skills/files"),
        "read": ("GET", "/api/skills/file"),
        "write": ("POST", "/api/skills/file"),
        "update": ("POST", "/api/skills/update"),
        "delete": ("POST", "/api/skills/delete"),
    },
    "subagents": {
        "list": ("GET", "/api/subagent/config"),
        "update": ("POST", "/api/subagent/config"),
        "tools": ("GET", "/api/subagent/available-tools"),
    },
    "reminders": {"list": ("GET", "/api/cron/jobs")},
    "tools": {"list": ("GET", "/api/tools/list")},
    "knowledge": {
        "list": ("GET", "/api/kb/list"),
        "create": ("POST", "/api/kb/create"),
        "update": ("POST", "/api/kb/update"),
        "delete": ("POST", "/api/kb/delete"),
        "documents": ("GET", "/api/kb/document/list"),
        "document": ("GET", "/api/kb/document/get"),
        "chunks": ("GET", "/api/kb/chunk/list"),
        "delete_document": ("POST", "/api/kb/document/delete"),
        "retrieve": ("POST", "/api/kb/retrieve"),
    },
    "models": {
        "list": ("GET", "/api/config/provider/list"),
        "schema": ("GET", "/api/config/provider/template"),
        "create": ("POST", "/api/config/provider/new"),
        "update": ("POST", "/api/config/provider/update"),
        "delete": ("POST", "/api/config/provider/delete"),
        "test": ("GET", "/api/config/provider/check_one"),
    },
}


def redact(value):
    if isinstance(value, dict):
        return {k: ("[已隐藏]" if any(term in k.lower() for term in
                ("password", "secret", "token", "api_key", "authorization", "cookie"))
                or k.lower() in {"key", "env", "headers"} else redact(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


class NativeManagement:
    def __init__(self, context, app=None):
        self.context, self.app = context, app

    async def call(self, resource, action, arguments=None, confirmed=False):
        if resource == "catalog":
            return {k: list(v) for k, v in ROUTES.items()}
        route = ROUTES.get(resource, {}).get(action)
        if route is None:
            raise ValueError("该原生管理操作尚未接入；使用catalog查看实际支持的操作")
        if action in {"delete", "delete_document"} and not confirmed:
            raise ValueError("删除必须来自管理员明确指令，并传confirmed=true")
        args = arguments or {}
        if not isinstance(args, dict):
            raise ValueError("操作参数必须是JSON对象")
        if resource == "models" and action == "list":
            args = {"provider_type": "chat_completion,embedding,rerank,stt,tts", **args}
        if resource == "mcp" and action in {"update", "test"} and "config" in args:
            old = self.context.get_llm_tool_manager().load_mcp_config().get("mcpServers", {}).get(
                args.get("oldName") or args.get("name"), {})
            args = {**args, "config": preserve_secrets(args["config"], old)}
        if resource == "models" and action == "update":
            old = next((p for p in self.context.get_config().get("provider", [])
                        if p.get("id") == args.get("id")), {})
            args = {**args, "config": preserve_secrets(args.get("config", {}), old)}
        if resource == "knowledge" and self.app:
            links = self.app.database.rows("SELECT * FROM knowledge_links WHERE kb_id=?",
                                          (args.get("kb_id", ""),))
            if links and action == "delete":
                raise ValueError("这是石英正在使用的记忆库，请先在记忆页整理或撤销关联内容")
            if links and action == "update":
                helper = await self.context.kb_manager.get_kb(args["kb_id"])
                if args.get("kb_name", helper.kb.kb_name) != helper.kb.kb_name:
                    raise ValueError("石英管理的知识库名称用于检索关联，请保留")
                if args.get("embedding_provider_id", helper.kb.embedding_provider_id) != helper.kb.embedding_provider_id:
                    raise ValueError("已有记忆的向量模型更换需要重新索引，请通过开发任务执行迁移")
            if action == "delete_document":
                link = next((x for x in links if x["doc_id"] == args.get("doc_id")), None)
                if link:
                    if link["kind"] == "memories":
                        self.app.memory_repository.delete_memory(link["row_id"])
                    else:
                        with self.app.database.transaction() as con:
                            con.execute("DELETE FROM digests WHERE id=?", (link["row_id"],))
                    await self.app.knowledge.sync()
                    return {"removed_from_core": True, "pending": self.app.knowledge.status()["pending"]}
        if resource == "plugins" and action in {"reload", "disable"}:
            if args.get("name") == "astrbot_plugin_shrink":
                raise ValueError("核心重载需经独立部署执行器")
        import jwt
        dashboard = self.context.get_config()["dashboard"]
        token = jwt.encode({"username": dashboard["username"], "exp": time.time() + 60},
                           dashboard["jwt_secret"], algorithm="HS256")
        method, path = route
        async with httpx.AsyncClient(timeout=45, trust_env=False) as client:
            upload = None
            if resource == "skills" and action == "create":
                name = str(args.get("name", ""))
                if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", name):
                    raise ValueError("Skill标识只允许英文字母、数字、下划线和连字符；中文标题可写在正文中")
                content = args.get("content", "")
                if not isinstance(content, str) or not content.startswith("---") or len(content) > 100000:
                    raise ValueError("需要带name和description元数据的SKILL.md，最多100000字")
                payload = io.BytesIO()
                with zipfile.ZipFile(payload, "w") as archive:
                    archive.writestr(f"{name}/SKILL.md", content)
                upload = {"file": (name + ".zip", payload.getvalue(), "application/zip")}
            response = await client.request(
                method, f"http://127.0.0.1:{dashboard.get('port', 6185)}{path}",
                headers={"Authorization": "Bearer " + token},
                params=args if method == "GET" else None,
                json=args if method != "GET" and not upload else None,
                files=upload,
            )
            response.raise_for_status()
            result = response.json()
            if result.get("status") != "ok":
                raise ValueError(str(redact(result))[:2000])
            data = result.get("data")
            if self.app and resource == "skills" and action == "list":
                labels = {r["name"]: r["title"] for r in self.app.database.rows(
                    "SELECT n.name,s.title FROM native_skill_links n JOIN skills s ON s.id=n.id")}
                for skill in data.get("skills", []):
                    if skill["name"] in labels:
                        skill["display_name"] = labels[skill["name"]]
            return redact(data)


def preserve_secrets(value, previous):
    if value == "[已隐藏]":
        if previous is None:
            raise ValueError("该凭据没有已有值，请重新填写")
        return previous
    if isinstance(value, dict):
        previous = previous if isinstance(previous, dict) else {}
        return {k: preserve_secrets(v, previous.get(k)) for k, v in value.items()}
    if isinstance(value, list):
        old = previous if isinstance(previous, list) else []
        return [preserve_secrets(v, old[i] if i < len(old) else None) for i, v in enumerate(value)]
    return value
