"""Native AstrBot developer agent with durable, inspectable task state."""
import asyncio
import json
from pathlib import Path

import httpx

from ..services.workbench import Workbench
from .deployment import Deployments
from .agent_tasks import AgentTasks
from .workbench_runtime import AstrBotWorkbenchRuntime
from .admin_events import admin_event, admin_candidates
from .notifications import Notifications
from ..services.task_context import CURRENT_TASK
from ..prompts import text as prompt_text
from ..services.scheduling import shared_lock


class Operations(AgentTasks):
    def __init__(self, context, application, plugin_root):
        self.context, self.app = context, application
        self.database = application.database
        self.notifications = Notifications(application)
        self.workbench = Workbench(application.state.root / "workbench",
                                   Path(plugin_root).parent, self.test_plugin, AstrBotWorkbenchRuntime(),
                                   lambda: prompt_text('tasks.workbench_guide'))
        self.events = {}
        self.handoffs = {}
        self.deployments = Deployments(self)
        self.init_agents()
        from ..services.task_execution import TaskExecution
        self.execution = TaskExecution(self.task_states, self.task_inputs, self.clarifications,
                                       self.execute_task, self.deliver_completed, self.app.tasks.error,
                                       getattr(self.app, "scheduling", None))
        self.running = self.execution.running
        self.task_states.migrate()
        self.task_states.recover()

    def event_for(self, session=''):
        return admin_event(self.context, session)

    def task_event(self, row):
        from .admin_events import task_event
        return task_event(self.context, row)

    def admin_candidates(self):
        return admin_candidates(self.context)

    async def manage_native(self, resource, action, arguments=None, confirmed=False):
        if resource == "subagents" and action == "update":
            async with shared_lock(self.agent_config_lock):
                return await self.app.native.call(resource, action, arguments, confirmed)
        if resource == "skills" and action in {"create", "write", "update", "delete"}:
            async with shared_lock(self.app.native_skills.lock):
                return await self.app.native.call(resource, action, arguments, confirmed)
        return await self.app.native.call(resource, action, arguments, confirmed)

    @property
    def agent_slots(self):
        return self.execution.slots

    @agent_slots.setter
    def agent_slots(self, value):
        self.execution.slots = value

    def list(self):
        return self.task_states.repository.list()[:100]

    def detail(self, task_id):
        repository = self.task_states.repository
        return {**repository.task(task_id), "inputs": self.task_inputs.repository.all(task_id),
                "attempts": repository.attempts(task_id), "events": repository.events(task_id)}

    def record(self, action, plugin="", path="", result=""):
        self.task_states.repository.record(CURRENT_TASK.get(), action, plugin, path, result)

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
                self.task_states.repository.update(task_id, failures=self.detail(task_id)["failures"] + 1)
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

    def require_developer_slot(self, excluding=""):
        self.task_states.capacity("development", excluding)
        if any(task_id != excluding and not task.done() and self.detail(task_id)['kind'] == 'development'
               for task_id, task in self.running.items()):
            raise ValueError("已有开发任务运行，请等待完成或先取消")

    async def submit(self, event, request, parent=""):
        if not event.is_admin():
            raise ValueError("仅管理员可发起开发任务")
        if not request.strip() or len(request) > 16000:
            raise ValueError("任务内容为空或过长")
        self.require_developer_slot()
        task_id = self.task_states.repository.create(
            request, event.unified_msg_origin, str(event.get_sender_id()), "shrink/gpt-6.1-sol",
            kind="development", agent="shirnk_developer", parent=parent)
        self.events[task_id] = event
        self.execution.launch(task_id)
        if not CURRENT_TASK.get():
            event.set_extra('shrink_background_task', task_id)
        return {"task_id": task_id, "status": "queued", "deadline_seconds": None,
                "completed": False,
                "execution": {"version": 1, "status": "submitted", "task_id": task_id}}

    async def cancel(self, task_id):
        return await self.execution.cancel(task_id)

    async def cancel_owned(self, event):
        from ..services.task_states import ACTIVE
        from ..persistence.task_reports import TaskReports
        rows = [r for r in self.list() if r["session"] == event.unified_msg_origin
                and r["sender"] == str(event.get_sender_id()) and r["status"] in ACTIVE]
        for row in rows:
            await self.cancel(row["id"])
            TaskReports(self.database).delivery(row["id"], "cancelled")
        return [r["id"] for r in rows]

    async def start(self):
        await self.execution.start()
        await self.deployments.start()

    async def close(self):
        await self.deployments.close()
        await self.execution.close()
