import base64
import copy
import io
import json

import pytest
from PIL import Image

pytest.importorskip("astrbot.core.provider.manager")

from astrbot.core.provider.entities import LLMResponse  # noqa: E402
from shrink.adapters.decision_backends import HaikuDecisionBackend  # noqa: E402
from shrink.adapters.providers import AstrBotProviders  # noqa: E402
from shrink.persistence.database import Database, StateRepository  # noqa: E402
from .model_support import host_models  # noqa: E402


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", [
    "openai_chat_completion", "openai_responses", "anthropic_chat_completion",
])
async def test_neutral_history_images_and_decisions_use_real_adapter_conversion(tmp_path, monkeypatch, protocol):
    host = await host_models(tmp_path, monkeypatch)
    database = Database(tmp_path / "shrink")
    state = StateRepository(database)
    try:
        entry = copy.deepcopy(host.config["provider"][0])
        entry["type"] = protocol
        await host.service.update_provider("first", entry)
        provider = host.providers.inst_map["first"]
        captured = []

        async def query(payloads, tools, **kwargs):
            captured.append(copy.deepcopy(payloads))
            return LLMResponse(role="assistant", completion_text='{"ok":true}')

        monkeypatch.setattr(provider, "_query", query)
        image = io.BytesIO()
        Image.new("RGB", (1, 1)).save(image, format="PNG")
        image_uri = "data:image/png;base64," + base64.b64encode(image.getvalue()).decode()
        history = [
            {"role": "user", "content": "查一下"},
            {"role": "assistant", "content": "", "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "lookup", "arguments": "{}"},
            }]},
            {"role": "tool", "tool_call_id": "call_1", "content": "查到了"},
            {"role": "user", "content": [
                {"type": "text", "text": "看这张图"},
                {"type": "image_url", "image_url": {"url": image_uri}},
            ]},
        ]
        response = await provider.text_chat(prompt="继续", contexts=copy.deepcopy(history))
        assert response.completion_text == '{"ok":true}'
        payload = captured[-1]
        serialized = json.dumps(payload, ensure_ascii=False)
        assert serialized.count("call_1") == 2 and "查到了" in serialized
        if protocol == "openai_responses":
            assert payload["store"] is False
            assert {"function_call", "function_call_output"} <= {
                item["type"] for item in payload["input"]}
            assert "input_image" in serialized
        elif protocol == "anthropic_chat_completion":
            assert '"tool_use"' in serialized and '"tool_result"' in serialized
            assert '"image"' in serialized
        else:
            assert payload["messages"][1]["tool_calls"][0]["id"] == "call_1"
            assert '"image_url"' in serialized
        state.put("settings", {**state.settings.model_dump(),
                               "model_provider": "first", "decision_provider": "first"})
        backend = HaikuDecisionBackend(host.context, state)
        assert await backend({"event": {"text": "test"}}) == '{"ok":true}'
        bridge = AstrBotProviders(host.context, lambda: state)
        for purpose in ("learning", "summary", "compression"):
            assert await bridge.for_purpose(purpose)("test") == '{"ok":true}'
    finally:
        database.close()
        await host.providers.terminate()


@pytest.mark.asyncio
async def test_resuming_as_messages_does_not_send_responses_state_as_a_signature(tmp_path, monkeypatch):
    from shrink.adapters.task_provider import resume_contexts

    host = await host_models(tmp_path, monkeypatch)
    try:
        entry = copy.deepcopy(host.config["provider"][0])
        entry["type"] = "anthropic_chat_completion"
        await host.service.update_provider("first", entry)
        provider = host.providers.inst_map["first"]
        state = json.dumps({"type": "openai_responses_reasoning", "items": [
            {"type": "reasoning", "id": "reasoning_1", "encrypted_content": "opaque-state"},
        ]})
        saved = [{"role": "assistant", "content": [
            {"type": "think", "think": "分析", "encrypted": state},
            {"type": "text", "text": "已查到资料"},
        ], "tool_calls": [{"id": "call_1", "type": "function",
                          "function": {"name": "lookup", "arguments": "{}"}}]},
                 {"role": "tool", "tool_call_id": "call_1", "content": "回执"}]
        original = copy.deepcopy(saved)
        _, unsafe_payload = provider._prepare_payload(copy.deepcopy(saved))
        assert unsafe_payload[0]["content"][0]["signature"] == state
        restored = resume_contexts(saved, provider)
        _, payload = provider._prepare_payload(restored)
        serialized = json.dumps(payload, ensure_ascii=False)
        assert "openai_responses_reasoning" not in serialized
        assert '"thinking"' not in serialized
        assert "已查到资料" in serialized and serialized.count("call_1") == 2
        assert saved == original
        # A task continuing on Responses must retain its own replay state.
        entry["type"] = "openai_responses"
        await host.service.update_provider("first", entry)
        assert resume_contexts(saved, host.providers.inst_map["first"]) == saved
    finally:
        await host.providers.terminate()
