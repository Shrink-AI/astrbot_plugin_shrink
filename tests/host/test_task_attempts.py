import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("astrbot.core.agent.runners.tool_loop_agent_runner")

from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor  # noqa: E402
from astrbot.core.star.context import Context  # noqa: E402
from astrbot.core.platform.astr_message_event import AstrMessageEvent  # noqa: E402
from astrbot.core.agent.tool import ToolSet  # noqa: E402
from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial  # noqa: E402
from astrbot.core.provider.sources.request_retry import retry_provider_request  # noqa: E402
from astrbot.core.exceptions import EmptyModelOutputError  # noqa: E402

from shrink.adapters.task_provider import task_provider  # noqa: E402
from tests.test_agent_tasks import Event, create, runtime  # noqa: E402, F401
from tests.host.test_task_inputs import RecordingModel  # noqa: E402
from tests.host.test_real_runner import Tools, tool  # noqa: E402


def host(op, model, monkeypatch):
    class HostEvent(Event, AstrMessageEvent):
        pass
    event = HostEvent()
    context = object.__new__(Context)
    context.__dict__.update(op.context.__dict__)
    op.context = context
    context.subagent_orchestrator.handoffs[0].agent.begin_dialogs = []
    context.provider_manager = SimpleNamespace(get_provider_by_id=AsyncMock(return_value=model))
    monkeypatch.setattr(FunctionToolExecutor, "_build_handoff_toolset",
                        lambda *args: ToolSet([tool("write")]))
    monkeypatch.setattr(FunctionToolExecutor, "execute", Tools.execute)
    Tools.ran, Tools.gates, Tools.cancelled = [], {}, []
    return event


async def test_real_runner_retries_only_failed_model_step_not_completed_write(runtime, monkeypatch):
    op, _, _ = runtime
    await create(op)
    calls = []
    class Flaky(RecordingModel):
        async def text_chat(self, **payload):
            calls.append(payload)
            assert payload["request_max_retries"] == 1
            if len(calls) == 2:
                raise TimeoutError("connection lost")
            return await super().text_chat(**payload)
    model = Flaky(("write",), "verified")
    event = host(op, model, monkeypatch)
    identity = (await op.submit_agent(event, "researcher", "write once"))["task_id"]
    await asyncio.wait_for(op.running[identity], 5)
    row = op.detail(identity)
    assert row["status"] == "retry_wait" and len(calls) == 2 and Tools.ran == ["write"]
    assert row["attempts"][0]["error_class"] == "network"
    await op.execution.tick(row["next_run"])
    await asyncio.wait_for(op.running[identity], 5)
    assert Tools.ran == ["write"] and len(calls) == 3
    assert any((m["role"] if isinstance(m, dict) else m.role) == "tool" for m in calls[-1]["contexts"])
    assert op.detail(identity)["status"] == "completed"
    assert len(op.detail(identity)["attempts"]) == 2


async def test_real_runner_empty_output_does_not_retry_inside_execution_slot(runtime, monkeypatch):
    op, _, _ = runtime
    await create(op)
    calls = []
    class Empty(RecordingModel):
        async def text_chat(self, **payload):
            calls.append(payload)
            raise EmptyModelOutputError("empty")
    event = host(op, Empty(), monkeypatch)
    identity = (await op.submit_agent(event, "researcher", "work"))["task_id"]
    await asyncio.wait_for(op.running[identity], 5)
    assert len(calls) == 1 and op.agent_slots._value == 3
    row = op.detail(identity)
    assert row["status"] == "retry_wait"
    assert row["attempts"][0]["error_class"] == "output_format"


async def test_real_runner_blank_response_cannot_reset_retry_budget(runtime, monkeypatch):
    from astrbot.core.provider.entities import LLMResponse
    op, _, _ = runtime
    await create(op)
    calls = []
    class Blank(RecordingModel):
        async def text_chat(self, **payload):
            calls.append(payload)
            return LLMResponse(role="assistant", completion_text="")
    event = host(op, Blank(), monkeypatch)
    identity = (await op.submit_agent(event, "researcher", "work"))["task_id"]
    for index in range(4):
        await asyncio.wait_for(op.running[identity], 5)
        row = op.detail(identity)
        if index < 3:
            assert row["status"] == "retry_wait"
            await op.execution.tick(row["next_run"])
    assert row["status"] == "needs_attention"
    assert len(calls) == 4 and len(row["attempts"]) == 4
    assert Tools.ran == []


@pytest.mark.parametrize("streaming", [False, True])
async def test_native_openai_outer_loop_and_transport_do_not_multiply_retries(streaming):
    provider = object.__new__(ProviderOpenAIOfficial)
    provider.api_keys = ["one", "two", "three"]
    provider.client = SimpleNamespace(api_key="")
    provider._prepare_chat_payload = AsyncMock(return_value=({}, []))
    transport_calls = []
    class RateLimit(Exception):
        status_code = 429
    async def transport():
        transport_calls.append(True)
        raise RateLimit("429")
    async def query(payloads, tools, *, request_max_retries, **kwargs):
        return await retry_provider_request("test", transport, max_attempts=request_max_retries)
    async def stream(payloads, tools, *, request_max_retries, **kwargs):
        yield await query(payloads, tools, request_max_retries=request_max_retries)
    provider._query, provider._query_stream = query, stream
    scoped = task_provider(provider)
    with pytest.raises(RateLimit):
        if streaming:
            async for _ in scoped.text_chat_stream(request_max_retries=1):
                pass
        else:
            await scoped.text_chat(request_max_retries=1)
    assert len(transport_calls) == 1
    assert "_handle_api_error" not in provider.__dict__


def test_sdk_retry_override_does_not_mutate_foreground_clients():
    from openai import AsyncOpenAI
    from anthropic import AsyncAnthropic
    for sdk in (AsyncOpenAI, AsyncAnthropic):
        client = sdk(api_key="fake-test-key", max_retries=2)
        provider = SimpleNamespace(client=client)
        scoped = task_provider(provider)
        assert scoped.client.max_retries == 0 and client.max_retries == 2


async def test_gemini_sdk_and_inner_repair_budget_are_scoped(monkeypatch):
    from google.genai import types
    from astrbot.core.provider.sources.gemini_source import ProviderGoogleGenAI
    from shrink.services.task_errors import TaskFailure
    provider = object.__new__(ProviderGoogleGenAI)
    prepare = AsyncMock(side_effect=lambda *a, **k: types.GenerateContentConfig())
    monkeypatch.setattr(ProviderGoogleGenAI, "_prepare_query_config", prepare)
    scoped = task_provider(provider)
    config = await scoped._prepare_query_config()
    assert config.http_options.retry_options.attempts == 1
    with pytest.raises(TaskFailure) as exc:
        await scoped._prepare_query_config()
    assert exc.value.category == "output_format"
    assert "_prepare_query_config" not in provider.__dict__
