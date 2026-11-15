import copy
import secrets
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from shrink.adapters.native_management import NativeManagement, preserve_secrets, redact


def test_secret_roundtrip_preserves_existing_values():
    original = {"url": "https://example.test", "headers": {"Authorization": "secret"}, "key": ["abc"]}
    shown = redact(original)
    assert "secret" not in str(shown) and "abc" not in str(shown)
    shown["url"] = "https://new.example.test"
    result = preserve_secrets(shown, original)
    assert result["headers"] == original["headers"]
    assert result["key"] == ["abc"]
    assert result["url"] == "https://new.example.test"
    with pytest.raises(ValueError):
        preserve_secrets({"key": "[已隐藏]"}, {})


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["save", "delete", "toggle"])
@pytest.mark.parametrize("current,incoming", [
    ({"main_enable": False}, True), ({"main_enable": False}, None),
    ({"main_enable": True}, False), ({}, True), (None, True),
])
async def test_subagent_updates_preserve_host_main_enable(monkeypatch, operation, current, incoming):
    host = {"dashboard": {"username": "admin", "jwt_secret": "test-secret"}}
    if current is not None:
        host["subagent_orchestrator"] = current
    original_host = copy.deepcopy(host)
    expected = (current or {}).get("main_enable", False)
    context = SimpleNamespace(get_config=lambda: host)
    client = AsyncMock()
    client.request.return_value = httpx.Response(
        200, json={"status": "ok", "data": {}},
        request=httpx.Request("POST", "http://localhost/api/subagent/config"))
    manager = AsyncMock()
    manager.__aenter__.return_value = client
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: manager)
    monkeypatch.setitem(sys.modules, "jwt", SimpleNamespace(encode=lambda *a, **kw: "token"))
    agents = [] if operation == "delete" else [{"name": "worker", "enabled": operation != "toggle"}]
    arguments = {"agents": agents, "router_system_prompt": "existing"}
    if incoming is not None:
        arguments["main_enable"] = incoming
    untouched = copy.deepcopy(arguments)
    await NativeManagement(context).call("subagents", "update", arguments)
    method, url = client.request.call_args.args
    written = client.request.call_args.kwargs["json"]
    assert method == "POST" and url.endswith("/api/subagent/config")
    assert written == {**arguments, "main_enable": expected}
    assert arguments == untouched
    assert host == original_host


def test_subagent_editor_does_not_force_main_enable():
    source = (Path(__file__).resolve().parents[1]
              / "web/src/pages/CapabilitiesPage.tsx").read_text("utf-8")
    assert "main_enable:" not in source


@pytest.fixture
def model_native(monkeypatch):
    source = {"id": "source", "type": "openai_chat_completion",
              "provider_type": "chat_completion", "api_base": "https://gateway.invalid/v1",
              "key": [secrets.token_urlsafe(32)], "timeout": 120}
    entries = [{"id": "first", "provider_source_id": "source", "model": "first", "enable": True},
               {"id": "sibling", "provider_source_id": "source", "model": "sibling", "enable": True}]
    host = {"provider": entries, "provider_sources": [source],
            "dashboard": {"username": "admin", "jwt_secret": secrets.token_urlsafe(32)}}
    writes = []

    async def request(method, url, **kwargs):
        if url.endswith("/provider/list"):
            data = [{**source, **entry} for entry in host["provider"]]
        else:
            writes.append(copy.deepcopy(kwargs["json"]))
            data = {}
        return httpx.Response(200, json={"status": "ok", "data": data},
                              request=httpx.Request(method, url))

    client, manager = AsyncMock(), AsyncMock()
    client.request.side_effect = request
    manager.__aenter__.return_value = client
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: manager)
    monkeypatch.setitem(sys.modules, "jwt", SimpleNamespace(encode=lambda *a, **kw: "token"))
    class ModelFixture:
        def __iter__(self):
            return iter((NativeManagement(SimpleNamespace(get_config=lambda: host)), host, writes))

        def __repr__(self):
            return "<isolated model configuration>"

    return ModelFixture()


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", [
    None, "openai_chat_completion", "openai_responses", "anthropic_chat_completion",
    "googlegenai_chat_completion", "openrouter_chat_completion",
])
async def test_model_protocol_and_source_fields_roundtrip(model_native, protocol):
    native, host, writes = model_native
    if protocol:
        host["provider"][0]["type"] = protocol
    original = copy.deepcopy(host)
    rows = await native.call("models", "list")
    first = rows[0]
    assert first["effective_type"] == (protocol or "openai_chat_completion")
    config = first["config"]
    assert set(config) == set(host["provider"][0])
    await native.call("models", "update", {"id": "first", "config": config})
    written = writes[-1]["config"]
    unchanged = written == original["provider"][0] and host == original
    assert unchanged
    assert "key" not in written and "api_base" not in written and "timeout" not in written


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", [None, "openai_chat_completion", "openai_responses", "anthropic_chat_completion"])
async def test_model_protocol_override_is_written_or_cleared(model_native, protocol):
    native, host, writes = model_native
    host["provider"][0]["type"] = "anthropic_chat_completion"
    config = copy.deepcopy(host["provider"][0])
    config.pop("type")
    if protocol:
        config["type"] = protocol
    await native.call("models", "update", {"id": "first", "config": config})
    assert writes[-1]["config"].get("type") == protocol


@pytest.mark.asyncio
async def test_merged_model_payload_does_not_copy_source_fields(model_native):
    native, host, writes = model_native
    merged = redact({**host["provider_sources"][0], **host["provider"][0]})
    merged["type"] = "openai_responses"
    await native.call("models", "update", {"id": "first", "config": merged})
    assert set(writes[-1]["config"]) == {
        "id", "provider_source_id", "model", "enable", "type",
    }


@pytest.mark.asyncio
async def test_model_own_hidden_credentials_roundtrip(model_native):
    native, host, writes = model_native
    host["provider"][0].pop("provider_source_id")
    host["provider"][0].update(
        type="openai_chat_completion", key=[secrets.token_urlsafe(32)],
        custom_headers={"X-Api-Key": secrets.token_urlsafe(32)},
    )
    config = redact(host["provider"][0])
    assert config["custom_headers"] == "[已隐藏]"
    await native.call("models", "update", {"id": "first", "config": config})
    unchanged = writes[-1]["config"] == host["provider"][0]
    assert unchanged
