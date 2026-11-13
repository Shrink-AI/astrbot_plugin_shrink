"""Durable supplements through AstrBot 4.29.4's real model/tool loop."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("astrbot.core.agent.runners.tool_loop_agent_runner")

from astrbot.core.agent.hooks import BaseAgentRunHooks  # noqa: E402
from astrbot.core.agent.run_context import ContextWrapper  # noqa: E402
from astrbot.core.agent.tool import ToolSet  # noqa: E402
from astrbot.core.provider.entities import ProviderRequest  # noqa: E402

from shrink.adapters.task_runner import task_runner  # noqa: E402
from tests.host.test_real_runner import Model, Tools, drive, tool  # noqa: E402
from tests.test_agent_tasks import create, runtime  # noqa: E402, F401
from tests.test_task_inputs import message, stored_task  # noqa: E402


class RecordingModel(Model):
    async def text_chat(self, **payload):
        # The host mutates contexts after each call; keep what the provider actually saw.
        response = await super().text_chat(**payload)
        self.seen[-1] = {**payload, "contexts": json.loads(json.dumps(
            payload["contexts"], default=lambda value: value.model_dump()))}
        return response

    async def text_chat_stream(self, **payload):
        yield await self.text_chat(**payload)


async def build(op, model, *, restored=False):
    runner = task_runner(op.task_inputs, "task")
    contexts = op.task_inputs.repository.context("task") if restored else []
    await runner.reset(
        provider=model, request=ProviderRequest(
            prompt=None if restored else "original", contexts=contexts or [],
            func_tool=ToolSet([tool("work")])),
        run_context=ContextWrapper(context=SimpleNamespace()),
        tool_executor=Tools, agent_hooks=BaseAgentRunHooks(), streaming=False)
    return runner


@pytest.fixture(autouse=True)
def tools():
    Tools.ran, Tools.gates, Tools.cancelled = [], {}, []


async def started():
    async def wait():
        while not Tools.ran:
            await asyncio.sleep(0)
    await asyncio.wait_for(wait(), 5)


def words(payload):
    return str(payload["contexts"])


async def test_input_enters_after_tool_finishes_before_next_model_once(runtime):
    op, event, _ = runtime
    stored_task(op)
    Tools.gates["work"] = asyncio.Event()
    model = RecordingModel(("work",), ("work",), "finished")
    runner = await build(op, model)
    run = asyncio.create_task(drive(runner))
    await started()
    receipt = await op.orchestrate(message(event), "append_input", {
        "task_id": "task", "text": "SUPPLEMENT"})
    assert receipt["status"] == "received"
    assert "SUPPLEMENT" not in str(runner.run_context.messages)
    Tools.gates["work"].set()
    await asyncio.wait_for(run, 5)
    assert Tools.ran == ["work", "work"] and not Tools.cancelled
    assert "SUPPLEMENT" not in words(model.seen[0])
    second = model.seen[1]["contexts"]
    assert [m["role"] if isinstance(m, dict) else m.role for m in second][-3:] == [
        "assistant", "tool", "user"]
    assert words(model.seen[1]).count("SUPPLEMENT") == 1
    assert words(model.seen[2]).count("SUPPLEMENT") == 1
    row = op.detail("task")["inputs"][0]
    assert row["received"] <= row["entered"] <= row["processed"]
    assert (await op.orchestrate(event, "append_input", {
        "task_id": "task", "text": "duplicate"}))["status"] == "processed"


async def test_model_error_then_restart_restores_entered_and_only_adds_unconsumed(runtime):
    op, event, _ = runtime
    stored_task(op)
    op.task_inputs.append("task", event.unified_msg_origin, "123", "m1", "ENTERED")
    class Error(RecordingModel):
        async def text_chat(self, **payload):
            from astrbot.core.provider.entities import LLMResponse
            return LLMResponse(role="err", completion_text="offline")
    runner = await build(op, Error())
    from shrink.services.task_errors import TaskFailure
    with pytest.raises(TaskFailure):
        await drive(runner)
    row = op.detail("task")["inputs"][0]
    assert row["entered"] is not None and row["processed"] is None
    op.task_inputs.append("task", event.unified_msg_origin, "123", "m2", "RECEIVED")
    # Re-open SQLite and rebuild the service, as a new process would.
    from shrink.persistence.database import Database
    from shrink.persistence.task_inputs import TaskInputsRepository
    from shrink.services.task_inputs import TaskInputs
    db = Database(op.database.root)
    op.task_inputs = TaskInputs(TaskInputsRepository(db))
    try:
        model = RecordingModel("done")
        restored = await build(op, model, restored=True)
        await drive(restored)
        assert words(model.seen[0]).count("ENTERED") == 1
        assert words(model.seen[0]).count("RECEIVED") == 1
        assert op.task_inputs.pending("task") == []
        again = RecordingModel("still done")
        await drive(await build(op, again, restored=True))
        assert words(again.seen[0]).count("ENTERED") == 1
        assert words(again.seen[0]).count("RECEIVED") == 1
    finally:
        db.close()


async def test_cancelled_task_keeps_pending_input_out_of_runner(runtime):
    op, event, _ = runtime
    stored_task(op)
    Tools.gates["work"] = asyncio.Event()
    model = RecordingModel(("work",), "should not run")
    runner = await build(op, model)
    run = asyncio.create_task(drive(runner))
    await started()
    op.task_inputs.append("task", event.unified_msg_origin, "123", "m1", "SUPPLEMENT")
    op.task_states.cancel("task")
    Tools.gates["work"].set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(run, 5)
    assert len(model.seen) == 1 and "SUPPLEMENT" not in str(runner.run_context.messages)
    assert op.detail("task")["inputs"][0]["entered"] is None


async def test_input_arriving_during_final_reply_uses_same_runner(runtime):
    op, event, _ = runtime
    stored_task(op)
    class Incoming(RecordingModel):
        async def text_chat(self, **payload):
            response = await super().text_chat(**payload)
            if len(self.seen) == 1:
                op.task_inputs.append("task", event.unified_msg_origin, "123", "m1", "LAST_INPUT")
            return response
    model = Incoming("first", "updated")
    runner = await build(op, model)
    await drive(runner)
    assert len(model.seen) == 2 and "LAST_INPUT" in words(model.seen[1])
    assert runner.get_final_llm_resp().completion_text == "updated"
    assert not op.task_inputs.pending("task")


async def test_failed_tool_is_not_repeated_to_deliver_input(runtime, monkeypatch):
    op, event, _ = runtime
    stored_task(op)
    async def failed(tool, run_context, **kwargs):
        Tools.ran.append(tool.name)
        op.task_inputs.append("task", event.unified_msg_origin, "123", "m1", "AFTER_ERROR")
        raise RuntimeError("tool failed")
        yield
    monkeypatch.setattr(Tools, "execute", failed)
    model = RecordingModel(("work",), "handled")
    await drive(await build(op, model))
    assert Tools.ran == ["work"] and len(model.seen) == 2
    assert words(model.seen[1]).count("AFTER_ERROR") == 1
    assert "tool failed" in words(model.seen[1])
    assert op.task_inputs.pending("task") == []


async def test_operations_invoke_uses_real_task_runner(runtime, monkeypatch):
    from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
    from astrbot.core.star.context import Context
    from astrbot.core.platform.astr_message_event import AstrMessageEvent
    from tests.test_agent_tasks import Event
    op, event, _ = runtime
    await create(op)
    class HostEvent(Event, AstrMessageEvent):
        pass
    event = HostEvent()
    context = object.__new__(Context)
    context.__dict__.update(op.context.__dict__)
    op.context = context
    op.context.subagent_orchestrator.handoffs[0].agent.begin_dialogs = []
    model = RecordingModel(("work",), "finished")
    op.context.provider_manager = SimpleNamespace(get_provider_by_id=AsyncMock(return_value=model))
    monkeypatch.setattr(FunctionToolExecutor, "_build_handoff_toolset",
                        lambda *args: ToolSet([tool("work")]))
    monkeypatch.setattr(FunctionToolExecutor, "execute", Tools.execute)
    Tools.gates["work"] = asyncio.Event()
    result = await op.submit_agent(event, "researcher", "original")
    while not Tools.ran:
        if op.running[result["task_id"]].done():
            pytest.fail(str(op.detail(result["task_id"])))
        await asyncio.sleep(0)
    await op.orchestrate(message(event), "append_input", {
        "task_id": result["task_id"], "text": "PRODUCTION_INPUT"})
    Tools.gates["work"].set()
    await asyncio.wait_for(op.running[result["task_id"]], 10)
    assert op.detail(result["task_id"])["status"] == "completed"
    assert Tools.ran == ["work"] and "PRODUCTION_INPUT" in words(model.seen[1])
    assert op.detail(result["task_id"])["inputs"][0]["processed"] is not None
    assert op.task_inputs.repository.context(result["task_id"]) is None


async def test_native_steps_checkpoint_once_without_new_input(runtime, monkeypatch):
    op, _, _ = runtime
    stored_task(op)
    save = AsyncMock(wraps=op.task_inputs.repository.checkpoint_async)
    monkeypatch.setattr(op.task_inputs.repository, "checkpoint_async", save)
    model = RecordingModel(("work",), ("work",), ("work",), "done")
    await drive(await build(op, model))
    assert len(model.seen) == 4
    assert save.await_count == 5  # Initial context, then one checkpoint per completed step.
