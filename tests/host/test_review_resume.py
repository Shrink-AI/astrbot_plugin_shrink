import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("astrbot.core.agent.runners.tool_loop_agent_runner")

from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor  # noqa: E402
from astrbot.core.star.context import Context  # noqa: E402
from astrbot.core.platform.astr_message_event import AstrMessageEvent  # noqa: E402
from astrbot.core.agent.tool import ToolSet  # noqa: E402
from mcp.types import CallToolResult, TextContent  # noqa: E402

from shrink.adapters.operations import Operations  # noqa: E402
from tests.test_agent_tasks import Event, create, runtime  # noqa: E402, F401
from tests.host.test_real_runner import tool  # noqa: E402
from tests.host.test_task_inputs import RecordingModel  # noqa: E402


async def test_resume_after_write_interruption_checks_effect_before_another_write(runtime, monkeypatch):
    op, _, _ = runtime
    await create(op)
    class HostEvent(Event, AstrMessageEvent):
        pass
    event = HostEvent()
    context = object.__new__(Context)
    context.__dict__.update(op.context.__dict__)
    op.context = context
    context.subagent_orchestrator.handoffs[0].agent.begin_dialogs = []
    side_effects, tools_run = [], []
    wrote = asyncio.Event()

    async def execute(tool, run_context, **kwargs):
        tools_run.append(tool.name)
        if tool.name == "write":
            side_effects.append("written")
            wrote.set()
            if len(side_effects) == 1:
                await asyncio.Event().wait()
        yield CallToolResult(content=[TextContent(type="text", text="object already exists")])
    monkeypatch.setattr(FunctionToolExecutor, "execute", staticmethod(execute))
    monkeypatch.setattr(FunctionToolExecutor, "_build_handoff_toolset",
                        lambda *args: ToolSet([tool("write"), tool("inspect")]))
    context.provider_manager = SimpleNamespace(
        get_provider_by_id=AsyncMock(return_value=RecordingModel(("write",))))
    result = await op.submit_agent(event, "researcher", "write one object")
    await asyncio.wait_for(wrote.wait(), 5)
    await op.close()
    assert op.detail(result["task_id"])["status"] == "reconciling"
    class Recovery(RecordingModel):
        async def text_chat(self, **payload):
            if not self.seen:
                text = json.dumps(payload["contexts"], default=lambda m: m.model_dump(),
                                  ensure_ascii=False)
                self.script = [("inspect",) if "结果未知" in text and "先核对" in text else ("write",),
                               "verified"]
            return await super().text_chat(**payload)
    model = Recovery()
    context.provider_manager.get_provider_by_id.return_value = model
    restored = Operations(context, op.app, op.database.root / "plugins" / "core")
    restored.deliver_agent_result = AsyncMock()
    await restored.orchestrate(event, "resume", {"task_id": result["task_id"]})
    await asyncio.wait_for(restored.running[result["task_id"]], 5)
    assert tools_run == ["write", "inspect"]
    assert side_effects == ["written"]
    assert restored.detail(result["task_id"])["status"] == "completed"
