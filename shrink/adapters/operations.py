"""Native AstrBot developer agent with durable, inspectable task state."""
import asyncio
import json
import time
import uuid
from pathlib import Path

import httpx

from ..services.workbench import Workbench
from .deployment import Deployments
from .agent_tasks import AgentTasks, AwaitingUserInput
from .workbench_runtime import AstrBotWorkbenchRuntime
from .admin_events import admin_event, admin_candidates
from ..services.task_context import CURRENT_TASK
from ..prompts import text as prompt_text



class Operations(AgentTasks):
    def __init__(self, context, application, plugin_root):
        self.context, self.app = context, application
        self.database = application.database
        self.workbench = Workbench(application.state.root / "workbench",
                                   Path(plugin_root).parent, self.test_plugin, AstrBotWorkbenchRuntime(),
                                   lambda: prompt_text('tasks.workbench_guide'))
        self.running = {}
        self.events = {}
        self.deployments = Deployments(self)
        self.database.connection.executescript("""
        CREATE TABLE IF NOT EXISTS operation_tasks(
          id TEXT PRIMARY KEY, request TEXT NOT NULL, session TEXT NOT NULL,
          sender TEXT NOT NULL, status TEXT NOT NULL, started REAL NOT NULL,
          finished REAL, result TEXT NOT NULL DEFAULT '', failures INTEGER NOT NULL DEFAULT 0,
          provider TEXT NOT NULL, parent TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS operation_events(
          id INTEGER PRIMARY KEY, task_id TEXT NOT NULL, action TEXT NOT NULL,
          plugin TEXT NOT NULL DEFAULT '', path TEXT NOT NULL DEFAULT '',
          result TEXT NOT NULL, created REAL NOT NULL);
        """)
        self.init_agents()
        with self.database.transaction() as con:
            con.execute("UPDATE operation_tasks SET delivery='interrupted' WHERE delivery IN ('pending','composing')")
            con.execute("UPDATE operation_tasks SET delivery='unknown' WHERE delivery='sending'")
            con.execute("UPDATE operation_tasks SET status='interrupted',finished=? "
                        "WHERE status IN ('queued','running','waiting','testing','deploying')", (time.time(),))

    def event_for(self, session=''):
        return admin_event(self.context, session)

    def admin_candidates(self):
        return admin_candidates(self.context)

    async def manage_native(self, resource, action, arguments=None, confirmed=False):
        if resource == "subagents" and action == "update":
            async with self.agent_config_lock:
                return await self.app.native.call(resource, action, arguments, confirmed)
        if resource == "skills" and action in {"create", "write", "update", "delete"}:
            async with self.app.native_skills.lock:
                return await self.app.native.call(resource, action, arguments, confirmed)
        return await self.app.native.call(resource, action, arguments, confirmed)

    def list(self):
        return self.database.rows("SELECT * FROM operation_tasks ORDER BY started DESC LIMIT 100")

    def detail(self, task_id):
        rows = self.database.rows("SELECT * FROM operation_tasks WHERE id=?", (task_id,))
        if not rows:
            raise ValueError("任务不存在")
        return {**rows[0], "events": self.database.rows(
            "SELECT * FROM operation_events WHERE task_id=? ORDER BY id", (task_id,))}

    def record(self, action, plugin="", path="", result=""):
        task_id = CURRENT_TASK.get()
        with self.database.transaction() as con:
            con.execute("INSERT INTO operation_events(task_id,action,plugin,path,result,created) "
                        "VALUES(?,?,?,?,?,?)",
                        (task_id, action, plugin, path, str(result)[:40000], time.time()))

    async def test_plugin(self, files):
        async with httpx.AsyncClient(timeout=65) as client:
            response = await client.post(self.app.state.settings.sandbox_url.rstrip("/") + "/test-plugin",
                                         json={"files": files})
            response.raise_for_status()
            return response.json()

    async def work(self, event, action="guide", name="", path="", content=""):
        if not event.is_admin():
            return json.dumps({"ok": False, "error": "仅管理员可操作插件"})
        task_id = CURRENT_TASK.get()
        if task_id and action in {"write", "test", "deploy"}:
            row = self.detail(task_id)
            if row["failures"] >= 3:
                return json.dumps({"ok": False, "error": "已达3轮修复上限，保留现场等待续做"})
        try:
            if action in {"deploy", "rollback"} and self.workbench.name(name) == "astrbot_plugin_shrink":
                result = json.dumps(await self.deployments.submit(name, event, task_id, action), ensure_ascii=False)
                self.record(action, name, path, result)
                return result
            result = await self.workbench.perform(
                action, name, path, content, self.context._star_manager)
            if action == "status" and self.workbench.name(name) == "astrbot_plugin_shrink":
                value = json.loads(result)
                try:
                    self.deployments.rollback_source(self.workbench.name(name))
                    value["rollback_available"] = True
                except ValueError:
                    pass
                result = json.dumps(value, ensure_ascii=False)
            if action == "test" and not json.loads(result).get("ok") and task_id:
                with self.database.transaction() as con:
                    con.execute("UPDATE operation_tasks SET failures=failures+1 WHERE id=?", (task_id,))
            if action == "deploy":
                config = self.context.get_config(umo=event.unified_msg_origin)
                enabled = list(config.get("plugin_set", ["*"]))
                plugin = self.workbench.name(name)
                if "*" not in enabled and plugin not in enabled:
                    config["plugin_set"] = [*enabled, plugin]
                    await config.save_config_async()
                result = json.dumps({**json.loads(result), "tools": [
                    t.name for t in self.context.get_llm_tool_manager().func_list
                    if plugin in str(t.handler_module_path)
                ]}, ensure_ascii=False)
        except Exception as exc:
            result = json.dumps({"ok": False, "error": str(exc)[:2000]}, ensure_ascii=False)
        self.record(action, name, path, result)
        return result

    async def submit(self, event, request, parent=""):
        if not event.is_admin():
            raise ValueError("仅管理员可发起开发任务")
        if not request.strip() or len(request) > 16000:
            raise ValueError("任务内容为空或过长")
        # One developer owns the drafts at a time; avoid concurrent overwrite.
        if any(not t.done() and self.detail(tid)['kind'] == 'development'
               for tid, t in self.running.items()):
            raise ValueError("已有开发任务运行，请等待完成或先取消")
        task_id = uuid.uuid4().hex
        provider = "shrink/gpt-6.1-sol"
        with self.database.transaction() as con:
            con.execute("INSERT INTO operation_tasks(id,request,session,sender,status,started,provider,parent)"
                        " VALUES(?,?,?,?,?,?,?,?)",
                        (task_id, request, event.unified_msg_origin, event.get_sender_id(),
                         "queued", time.time(), provider, parent))
        self.events[task_id] = event
        self.running[task_id] = asyncio.create_task(self._run(task_id, event, request, provider))
        if not CURRENT_TASK.get():
            event.set_extra('shrink_background_task', task_id)
        return {"task_id": task_id, "status": "queued", "deadline_seconds": None,
                "completed": False,
                "execution": {"version": 1, "status": "submitted", "task_id": task_id}}

    async def _run(self, task_id, event, request, provider):
        token = CURRENT_TASK.set(task_id)
        status, result = "failed", ""
        try:
            with self.database.transaction() as con:
                con.execute("UPDATE operation_tasks SET status='running' WHERE id=?", (task_id,))
            orchestrator = getattr(self.context, "subagent_orchestrator", None)
            handoff = next((h for h in getattr(orchestrator, "handoffs", [])
                            if h.agent.name == "shirnk_developer"), None)
            if handoff is None:
                raise ValueError("AstrBot原生shirnk_developer子代理未配置")
            result = await self.invoke_agent(handoff, event, request)
            # Agent prose is not proof that it deployed anything.
            evidence = self.database.rows(
                "SELECT result FROM operation_events WHERE task_id=? AND action IN ('deploy','rollback')", (task_id,))
            deployed = any(json.loads(x["result"]).get("activated") or json.loads(x["result"]).get("restored")
                           for x in evidence)
            pending = any(json.loads(x["result"]).get("pending") for x in evidence)
            status = "awaiting_deployment" if pending else "completed" if deployed else "needs_attention"
        except AwaitingUserInput:
            status = "awaiting_input"
            question = self.clarifications.get(task_id)
            result = json.dumps({"status": status, "question": question["question"],
                                 "context": question["context"]}, ensure_ascii=False)
        except asyncio.CancelledError:
            status, result = "cancelled", "开发任务已取消，保留草稿和已有执行记录。"
        except TimeoutError:
            status, result = "timed_out", "开发任务的一次请求超时，草稿和执行记录已保留，请核对后续做。"
        except Exception as exc:
            result = f"{type(exc).__name__}: {str(exc)[:2000]}"
        finally:
            with self.database.transaction() as con:
                con.execute("UPDATE operation_tasks SET status=?,result=?,finished=? WHERE id=?",
                            (status, result, time.time(), task_id))
            CURRENT_TASK.reset(token)
            self.question_signals.pop(task_id, None)
        if status not in {"cancelled", "awaiting_deployment"}:
            try:
                from astrbot.api.message_components import Plain
                from astrbot.core.message.message_event_result import MessageChain
                scope = {"session": event.unified_msg_origin, "sender": str(event.get_sender_id())}
                reply = await self.app.outbound.compose(result, request=request, kind="development",
                                                       instruction="实际状态：" + status, scope=scope)
                accepted = await asyncio.wait_for(
                    self.context.send_message(event.unified_msg_origin, MessageChain([Plain(reply)])), 30)
                if not accepted:
                    raise RuntimeError("开发结果发送未找到匹配平台；结果保留在后台任务详情")
                self.app.outbound.record(scope, reply, "development:" + task_id)
            except Exception as exc:
                self.app.tasks.error("developer_result_delivery", exc)

    async def cancel(self, task_id):
        task = self.running.get(task_id)
        if not task or task.done():
            raise ValueError("任务未在运行")
        task.cancel()
        await task
        return {"id": task_id, "status": "cancelled"}

    async def cancel_owned(self, event):
        rows = [r for r in self.list() if r["session"] == event.unified_msg_origin
                and r["sender"] == str(event.get_sender_id())
                and r["status"] in {"running", "queued", "waiting", "awaiting_input"}]
        for row in rows:
            task = self.running.get(row["id"])
            if task and not task.done():
                task.cancel()
        for row in rows:
            task = self.running.get(row["id"])
            if task:
                await asyncio.gather(task, return_exceptions=True)
            with self.database.transaction() as con:
                con.execute("UPDATE operation_tasks SET status='cancelled',delivery='cancelled',"
                            "finished=? WHERE id=?", (time.time(), row["id"]))
        return [r["id"] for r in rows]

    async def close(self):
        await self.deployments.close()
        tasks = [task for task in self.running.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
