import copy
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

pytest.importorskip("astrbot.core.provider.manager")

from astrbot.core.provider.register import provider_cls_map  # noqa: E402
from shrink.adapters.native_management import NativeManagement, redact  # noqa: E402
from .model_support import host_models, native_transport  # noqa: E402


@pytest.mark.asyncio
async def test_source_model_edit_uses_only_its_own_configuration(tmp_path, monkeypatch):
    host = await host_models(tmp_path, monkeypatch)
    try:
        native_transport(monkeypatch, host)
        native = NativeManagement(host.context)
        source = copy.deepcopy(host.config["provider_sources"])
        sibling = copy.deepcopy(host.config["provider"][1:])
        rows = await native.call("models", "list")
        first = next(row for row in rows if row["id"] == "first")
        config = first.get("config", first)
        await native.call("models", "update", {"id": "first", "config": config})
        assert set(host.config["provider"][0]) == {"id", "model", "enable", "provider_source_id"}
        unchanged = host.config["provider_sources"] == source and host.config["provider"][1:] == sibling
        assert unchanged
        assert "key" not in config and "api_base" not in config
        assert "Authorization" not in json.dumps(rows)
    finally:
        await host.providers.terminate()


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", [
    "openai_chat_completion", "openai_responses", "anthropic_chat_completion",
])
async def test_protocol_override_reloads_and_following_source_restores(tmp_path, monkeypatch, adapter):
    host = await host_models(tmp_path, monkeypatch)
    try:
        native_transport(monkeypatch, host)
        native = NativeManagement(host.context)
        config = copy.deepcopy(host.config["provider"][0])
        config["type"] = adapter
        await native.call("models", "update", {"id": "first", "config": config})
        assert type(host.providers.inst_map["first"]) is provider_cls_map[adapter].cls_type
        assert host.config["provider"][0]["type"] == adapter
        path = {"openai_chat_completion": "chat/completions",
                "openai_responses": "responses", "anthropic_chat_completion": "v1/messages"}[adapter]
        assert str(host.providers.inst_map["first"].client._prepare_url(path)) == {
            "openai_chat_completion": "https://gateway.invalid/v1/chat/completions",
            "openai_responses": "https://gateway.invalid/v1/responses",
            "anthropic_chat_completion": "https://gateway.invalid/v1/messages",
        }[adapter]
        host.providers.inst_map["first"].client.base_url = "https://gateway.invalid"
        assert str(host.providers.inst_map["first"].client._prepare_url(path)) == {
            "openai_chat_completion": "https://gateway.invalid/chat/completions",
            "openai_responses": "https://gateway.invalid/responses",
            "anthropic_chat_completion": "https://gateway.invalid/v1/messages",
        }[adapter]
        config.pop("type")
        await native.call("models", "update", {"id": "first", "config": config})
        assert type(host.providers.inst_map["first"]) is provider_cls_map["openai_chat_completion"].cls_type
        assert "type" not in host.config["provider"][0]
    finally:
        await host.providers.terminate()


def test_dashboard_protocols_match_registered_chat_adapters():
    from astrbot.core.provider.manager import ProviderManager
    from astrbot.core.provider.entities import ProviderType

    source = (Path(__file__).resolve().parents[2]
              / "web/src/pages/CapabilitiesPage.tsx").read_text("utf-8")
    expected = {"openai_chat_completion", "openai_responses", "anthropic_chat_completion"}
    import re
    block = re.search(r"const chatProtocols[^=]*=\s*(\{.*?\});", source, re.S)
    assert block, "模型编辑器缺少对话协议下拉"
    options = set(re.findall(r"^\s*(\w+):", block[1], re.M))
    assert options == expected
    manager = ProviderManager.__new__(ProviderManager)
    for adapter in options:
        manager.dynamic_import_provider(adapter)
        assert provider_cls_map[adapter].provider_type == ProviderType.CHAT_COMPLETION


def test_hidden_custom_headers_are_not_returned():
    assert redact({"custom_headers": {"X-Api-Key": object()}}) == {"custom_headers": "[已隐藏]"}


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["default", "group"])
async def test_model_selections_roundtrip_through_host_save_and_real_pipeline(tmp_path, monkeypatch, profile):
    from astrbot.core.astr_main_agent import _get_compress_provider, _get_fallback_chat_providers
    from astrbot.core.config.astrbot_config import AstrBotConfig
    from astrbot.core.pipeline.process_stage.method.agent_sub_stages.internal import InternalAgentSubStage
    from astrbot.dashboard.services.config_service import ConfigProfileService
    from shrink.adapters.model_settings import ModelSettings
    from shrink.api.system import router_for
    from shrink.persistence.database import Database, StateRepository

    host = await host_models(tmp_path, monkeypatch)
    group = AstrBotConfig(str(tmp_path / "group.json"), copy.deepcopy(dict(host.config)))
    host.profiles.confs["group"] = group
    host.profiles.abconf_data["group"] = {"name": "群配置", "path": "group.json"}
    host.profiles.ucr.umop_to_conf_id["test:GroupMessage:group"] = "group"
    database = Database(tmp_path / "shrink")
    state = StateRepository(database)
    binding = ModelSettings(host.context, state)
    binding.migrate()
    host.context.conversation_manager = SimpleNamespace()
    stages = {}

    async def reload_pipeline(identity):
        stage = InternalAgentSubStage()
        await stage.initialize(SimpleNamespace(
            astrbot_config=host.profiles.confs[identity],
            plugin_manager=SimpleNamespace(context=host.context),
        ))
        stages[identity] = stage

    lifecycle = SimpleNamespace(
        astrbot_config_mgr=host.profiles, astrbot_config=host.config,
        umop_config_router=host.profiles.ucr, db=None,
        reload_pipeline_scheduler=reload_pipeline,
    )
    profile_service = ConfigProfileService(lifecycle, runtime={})
    # Exercise the same host service called by the authenticated native save path,
    # while forbidding HTTP transport entirely.
    original_request = httpx.AsyncClient.request

    async def request(client, method, url, **kwargs):
        if url.endswith("/api/config/astrbot/update"):
            body = kwargs["json"]
            await profile_service.update_profile(body["conf_id"], body["config"])
            return httpx.Response(200, json={"status": "ok"},
                                  request=httpx.Request(method, url))
        return await original_request(client, method, url, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "request", request)
    binding.persist = NativeManagement(host.context).save_model_profile
    app = FastAPI()
    app.include_router(router_for(state, None, None, lambda: {
        "chat": ["first", "second", "third"], "embedding": [""],
    }, model_settings=binding))
    try:
        await reload_pipeline(profile)
        before = copy.deepcopy(dict(host.profiles.confs["group" if profile == "default" else "default"]))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://local",
        ) as client:
            settings = (await client.get(f"/api/v1/settings?profile={profile}")).json()["settings"]
            settings.update(model_provider="second", fallback_providers=["third", "first"],
                            compression_provider="third", image_caption_provider="first",
                            learning_provider="third", summary_provider="first")
            response = await client.put(f"/api/v1/settings?profile={profile}", json=settings)
            assert response.status_code == 200, response.text
            session = "test:GroupMessage:group" if profile == "group" else "test:GroupMessage:default"
            assert (await host.context.get_using_provider_async(umo=session)).meta().id == "second"
            cfg = stages[profile].main_agent_cfg
            assert cfg.fallback_provider_ids == ["third", "first"]
            assert cfg.llm_compress_provider_id == "third"
            assert cfg.provider_settings["default_image_caption_provider_id"] == "first"
            fallbacks = _get_fallback_chat_providers(
                host.providers.inst_map["second"], host.context, cfg.fallback_provider_ids)
            assert [provider.meta().id for provider in fallbacks] == ["third", "first"]
            assert (await _get_compress_provider(cfg, host.context)).meta().id == "third"
            sibling_unchanged = dict(host.profiles.confs["group" if profile == "default" else "default"]) == before
            assert sibling_unchanged
            host_config = copy.deepcopy(dict(host.profiles.confs[profile]))
            host_config["agent_runner"]["config"]["model"].update(
                provider_id="third", fallback_provider_ids=["second", "first"])
            host_config["agent_runner"]["config"]["compression"]["provider_id"] = "second"
            host_config["provider_settings"]["default_image_caption_provider_id"] = "second"
            await profile_service.update_profile(profile, host_config)
            current = (await client.get(f"/api/v1/settings?profile={profile}")).json()["settings"]
            assert current["model_provider"] == "third"
            assert current["fallback_providers"] == ["second", "first"]
            assert current["compression_provider"] == current["image_caption_provider"] == "second"
            assert (await host.context.get_using_provider_async(umo=session)).meta().id == "third"
            assert stages[profile].main_agent_cfg.llm_compress_provider_id == "second"
            assert "model_provider" not in state.get("settings")
    finally:
        database.close()
        await host.providers.terminate()


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", ["", "second"])
async def test_upgrade_retains_the_previously_selected_reply_model(tmp_path, monkeypatch, legacy):
    from astrbot.core.astr_main_agent import _select_provider
    from shrink.adapters.model_settings import ModelSettings
    from shrink.persistence.database import Database, StateRepository

    host = await host_models(tmp_path, monkeypatch)
    database = Database(tmp_path / "shrink")
    state = StateRepository(database)
    state.put("settings", {**state.settings.model_dump(), "model_provider": legacy})
    selected_before = legacy or "first"
    try:
        binding = ModelSettings(host.context, state)
        binding.migrate()
        event = SimpleNamespace(get_extra=lambda name: binding.for_session()["model_provider"],
                                unified_msg_origin="test:GroupMessage:1")
        assert (await _select_provider(event, host.context)).meta().id == selected_before
        host.config["agent_runner"]["config"]["model"]["provider_id"] = "third"
        binding.migrate()
        assert (await _select_provider(event, host.context)).meta().id == "third"
    finally:
        database.close()
        await host.providers.terminate()
