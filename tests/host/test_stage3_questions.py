"""T04 through the native runner and the real notification/outbox path."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("astrbot.core.agent.runners.tool_loop_agent_runner")

from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor  # noqa: E402
from astrbot.core.agent.tool import ToolSet  # noqa: E402
from astrbot.core.platform.astr_message_event import AstrMessageEvent  # noqa: E402
from astrbot.core.star.context import Context  # noqa: E402
from mcp.types import CallToolResult, TextContent  # noqa: E402

from shrink.adapters.agent_tasks import AgentTasks  # noqa: E402
from shrink.adapters.notifications import Notifications  # noqa: E402
from shrink.application import Application  # noqa: E402
from shrink.services.task_context import CURRENT_TASK  # noqa: E402
from tests.host.test_real_runner import tool  # noqa: E402
from tests.host.test_task_inputs import RecordingModel  # noqa: E402
from tests import test_agent_tasks  # noqa: E402

Event, create = test_agent_tasks.Event, test_agent_tasks.create
runtime = pytest.fixture(test_agent_tasks.runtime.__wrapped__)


async def test_t04_parent_merges_questions_original_creator_answer_resumes_without_repeating_steps(
        runtime, monkeypatch, tmp_path):
    op, _, _ = runtime
    real = Application(tmp_path / "notifications", AsyncMock(return_value="需要你确认：只处理桌面、保存在本机吗？"),
                       AsyncMock(), AsyncMock())
    # Tasks and queue must share a database; this app owns the whole test state.
    op.database.close()
    op.database = real.database
    op.app = real
    real.native = SimpleNamespace(call=AsyncMock())
    definitions = []
    async def native(resource, action, args=None):
        if action == "tools":
            return [{"name": "read_source", "active": True}]
        if action == "list":
            return {"agents": list(definitions)}
        definitions[:] = args["agents"]
        op.context.subagent_orchestrator.handoffs = [SimpleNamespace(provider_id=a["provider_id"],
            agent=SimpleNamespace(name=a["name"], instructions=a["system_prompt"], tools=a["tools"],
                                  begin_dialogs=[])) for a in definitions]
    real.native.call.side_effect = native
    op.init_agents()
    op.execution.states, op.execution.repository = op.task_states, op.task_states.repository
    op.execution.inputs, op.execution.clarifications = op.task_inputs, op.clarifications
    op.task_states.migrate()
    op.notifications = Notifications(real)
    op.deliver_agent_result = AgentTasks.deliver_agent_result.__get__(op)
    await create(op, "coordinator")
    await create(op, "researcher")
    class HostEvent(Event, AstrMessageEvent):
        pass
    event = HostEvent()
    context = object.__new__(Context)
    context.__dict__.update(op.context.__dict__)
    op.context = context
    sends, executed, models = [], [], {}
    async def send(chain):
        sends.append((event.unified_msg_origin, event.get_sender_id(),
                      "".join(p.text for p in chain.chain)))
        return True
    event.send = send
    real.notification_event = lambda session, sender: event
    real.outbound.resolve_scope = lambda scope: {"source": "qq:group:10", "user_id": "qq:123"}
    class Script(RecordingModel):
        async def text_chat(self, **payload):
            identity = CURRENT_TASK.get()
            row = op.detail(identity)
            text = str(payload["contexts"])
            if row["agent"] == "coordinator":
                self.script = [("dispatch",) if not self.seen else "分工已经提交"] if not self.seen else [
                    "所有分工已经核对完成" if "原用户答复" in text else "分工已经提交"]
            elif not self.seen:
                self.script = [("write_once",)]
            elif row["request"].startswith("独立"):
                self.script = ["独立分工完成"]
            elif "原用户答复" in text:
                self.script = ["按用户答复完成"]
            else:
                self.script = [("question",)]
            return await super().text_chat(**payload)
    async def provider(_):
        return models.setdefault(CURRENT_TASK.get(), Script())
    context.provider_manager = SimpleNamespace(get_provider_by_id=provider)
    async def execute(tool, run_context, **kwargs):
        identity = CURRENT_TASK.get()
        executed.append((identity, tool.name))
        if tool.name == "dispatch":
            for request in ("界面范围", "保存方式", "独立检查"):
                await op.submit_agent(event, "researcher", request)
        elif tool.name == "question":
            question = "只处理桌面吗？建议是" if op.detail(identity)["request"].startswith("界面") else "保存在本机吗？建议是"
            await op.orchestrate(event, "ask_user", {"question": question, "context": "已完成第一步"})
        yield CallToolResult(content=[TextContent(type="text", text="步骤已记录")])
    monkeypatch.setattr(FunctionToolExecutor, "execute", staticmethod(execute))
    monkeypatch.setattr(FunctionToolExecutor, "_build_handoff_toolset", lambda *args: ToolSet([
        tool(name) for name in ("dispatch", "write_once", "question")]))
    try:
        parent = (await op.submit_agent(event, "coordinator", "整合三个分工"))["task_id"]
        await asyncio.wait_for(op.running[parent], 10)
        children = [r for r in op.list() if r["parent"] == parent]
        assert op.detail(parent)["status"] == "waiting_user"
        assert {"界面范围", "保存方式", "独立检查"} == {r["request"] for r in children}
        assert next(r for r in children if r["request"].startswith("独立"))["status"] == "completed"
        assert "只处理桌面" in op.clarifications.get(parent)["question"]
        assert "保存在本机" in op.clarifications.get(parent)["question"]
        assert len(sends) == 1 and sends[0][:2] == (event.unified_msg_origin, "123")
        for sender, session in (("other", event.unified_msg_origin), ("123", "other-session")):
            other = HostEvent()
            other.unified_msg_origin = session
            other.get_sender_id = lambda: sender
            with pytest.raises(ValueError):
                await op.orchestrate(other, "resume", {"task_id": parent, "answer": "是"})
        before = list(executed)
        await op.orchestrate(event, "resume", {"task_id": parent, "answer": "是，只处理桌面并保存在本机"})
        await asyncio.wait_for(op.running[parent], 10)
        assert op.detail(parent)["status"] == "completed"
        assert all(op.detail(c["id"])["status"] == "completed" for c in children)
        assert [e for e in executed if e[1] in {"write_once", "dispatch"}] == [
            e for e in before if e[1] in {"write_once", "dispatch"}]
        assert len(sends) == 2
        assert any("原用户答复" in json.dumps(model.seen[-1]["contexts"], ensure_ascii=False)
                   for identity, model in models.items() if identity != parent)
    finally:
        await op.close()
        real.scheduling.close()
        real.database.close()
