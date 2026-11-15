import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from shrink.config import Settings
from shrink.persistence.database import Database, StateRepository
from shrink.ports import ProviderUnavailable


class HostConfig(dict):
    def save_config(self):
        pass


@pytest.fixture
def model_setup(tmp_path):
    from shrink.adapters.model_settings import ModelSettings

    database = Database(tmp_path)
    state = StateRepository(database)
    def config(provider):
        return HostConfig(
            agent_runner={"runner_type": "local", "config": {
                "model": {"provider_id": provider, "fallback_provider_ids": [], "request_max_retries": 5},
                "compression": {"provider_id": "", "keep_recent_ratio": 0.15}}},
            provider_settings={"default_image_caption_provider_id": "", "web_search": True},
        )
    profiles = {"default": config("main"), "group": config("group-main")}
    context = SimpleNamespace(
        astrbot_config_mgr=SimpleNamespace(
            confs=profiles, abconf_data={"group": {"name": "群配置"}}),
        get_config=lambda umo=None: profiles["group" if umo else "default"],
    )
    binding = ModelSettings(context, state)
    yield binding, profiles, state, context
    database.close()


@pytest.mark.asyncio
async def test_model_settings_use_only_host_storage_and_refresh_from_either_side(model_setup):
    from shrink.config import HOST_MODEL_FIELDS

    binding, profiles, state, _ = model_setup
    binding.migrate()
    updated = Settings(**{**state.settings.model_dump(), "model_provider": "new",
                          "fallback_providers": ["backup-two", "backup-one"],
                          "compression_provider": "compress", "image_caption_provider": "vision"})
    await binding.save(updated, "group")
    assert binding.read("group") == {
        "model_provider": "new", "fallback_providers": ["backup-two", "backup-one"],
        "compression_provider": "compress", "image_caption_provider": "vision",
    }
    assert binding.read()["model_provider"] == "main"
    profiles["group"]["agent_runner"]["config"]["model"]["provider_id"] = "from-host"
    assert binding.read("group")["model_provider"] == "from-host"
    assert not (set(state.get("settings")) & HOST_MODEL_FIELDS)
    assert profiles["group"]["provider_settings"]["web_search"] is True


@pytest.mark.parametrize("legacy", ["", "legacy-main"])
def test_model_migration_preserves_actual_chat_selection_once(model_setup, legacy):
    binding, profiles, state, _ = model_setup
    state.put("settings", {**state.settings.model_dump(), "model_provider": legacy})
    before = copy.deepcopy(profiles)
    binding.migrate()
    for profile in profiles:
        expected = legacy or before[profile]["agent_runner"]["config"]["model"]["provider_id"]
        assert binding.read(profile)["model_provider"] == expected
    profiles["group"]["agent_runner"]["config"]["model"]["provider_id"] = "later-change"
    binding.migrate()
    assert binding.read("group")["model_provider"] == "later-change"
    assert state.settings.model_provider == (legacy or "main")


def test_projected_host_fields_are_not_written_back_to_sqlite(model_setup):
    from shrink.config import HOST_MODEL_FIELDS

    binding, profiles, state, _ = model_setup
    binding.migrate()
    state.put("settings", {**state.settings.model_dump(), "learning_provider": "learning"})
    assert not (set(state.get("settings")) & HOST_MODEL_FIELDS)
    profiles["default"]["agent_runner"]["config"]["model"]["provider_id"] = "changed"
    assert state.settings.model_provider == "changed"
    assert state.settings.learning_provider == "learning"


@pytest.mark.asyncio
@pytest.mark.parametrize("purpose,field", [("learning", "learning_provider"), ("summary", "summary_provider")])
@pytest.mark.parametrize("failure", ["missing", "request"])
async def test_background_purpose_falls_back_visibly_to_chat_model(model_setup, purpose, field, failure):
    from shrink.adapters.providers import AstrBotProviders

    binding, _, state, context = model_setup
    binding.migrate()
    state.put("settings", {**state.settings.model_dump(), field: "unavailable"})
    primary = SimpleNamespace(text_chat=AsyncMock(return_value=SimpleNamespace(completion_text="ok")))
    unavailable = None if failure == "missing" else SimpleNamespace(
        text_chat=AsyncMock(side_effect=RuntimeError("provider failure")))
    context.get_provider_by_id = lambda name: {"main": primary, "unavailable": unavailable}.get(name)
    bridge = AstrBotProviders(context, lambda: state)
    bridge.model_settings = binding
    assert await bridge.for_purpose(purpose)("test") == "ok"
    status = state.get("model_status")[purpose]
    assert status["status"] == "fallback" and status["using"] == "main"


@pytest.mark.asyncio
async def test_background_model_exhaustion_is_visible_and_raises(model_setup):
    from shrink.adapters.providers import AstrBotProviders

    binding, _, state, context = model_setup
    binding.migrate()
    context.get_provider_by_id = lambda name: None
    bridge = AstrBotProviders(context, lambda: state)
    bridge.model_settings = binding
    with pytest.raises(ProviderUnavailable, match="学习"):
        await bridge.for_purpose("learning")("test")
    assert state.get("model_status")["learning"]["status"] == "unavailable"


@pytest.mark.asyncio
async def test_history_compression_uses_session_profile_and_compression_provider(model_setup):
    from shrink.adapters.providers import AstrBotProviders

    binding, profiles, state, context = model_setup
    binding.migrate()
    profiles["group"]["agent_runner"]["config"]["compression"]["provider_id"] = "group-compress"
    seen = []

    class Provider:
        async def text_chat(self, **kwargs):
            seen.append(kwargs)
            return SimpleNamespace(completion_text="summary")

    context.get_provider_by_id = lambda name: Provider() if name == "group-compress" else None
    bridge = AstrBotProviders(context, lambda: state)
    bridge.model_settings = binding
    assert await bridge.for_purpose("compression").for_source("test:GroupMessage:1")("prompt") == "summary"
    assert len(seen) == 1


def test_external_runner_is_preserved_during_upgrade(model_setup):
    binding, profiles, state, _ = model_setup
    state.put("settings", {**state.settings.model_dump(), "model_provider": "legacy-main"})
    profiles["group"]["agent_runner"] = {"runner_type": "dify", "config": {"app_id": "external"}}
    before = copy.deepcopy(profiles["group"])
    binding.migrate()
    assert profiles["group"] == before
    assert binding.read()["model_provider"] == "legacy-main"


@pytest.mark.asyncio
@pytest.mark.parametrize("purpose", ["learning", "summary"])
async def test_unavailable_purpose_records_failure_without_consuming_samples(tmp_path, purpose):
    from shrink.application import Application
    from shrink.adapters.providers import AstrBotProviders

    context = SimpleNamespace(get_provider_by_id=lambda name: None)
    bridge = AstrBotProviders(context, lambda: app.state)
    app = Application(
        tmp_path, bridge.model, AsyncMock(return_value=None), AsyncMock(),
        learning_model=bridge.for_purpose("learning"), summary_model=bridge.for_purpose("summary"),
    )
    try:
        app.messages.ingest({
            "id": "sample", "platform": "qq", "account": "100", "user_id": "qq:100",
            "name": "成员", "source": "qq:group:1", "source_name": "测试群",
            "text": "今天下午一起讨论项目吧", "created": 1,
        })
        if purpose == "learning":
            result = await app.learning.learn(force=True)
            assert app.messages.pending(10)
        else:
            result = await app.digest.digest("qq:group:1", force=True)
            assert app.messages.undigested("qq:group:1", 10, 10)
        assert result["status"] == "failed"
        assert app.state.get("model_status")[purpose]["status"] == "unavailable"
    finally:
        await app.close()


def test_source_profile_can_be_resolved_after_restart_and_ambiguity_is_not_guessed(tmp_path):
    from shrink.persistence.history import HistoryRepository

    database = Database(tmp_path)
    try:
        history = HistoryRepository(database)
        history.session("test:GroupMessage:1", instance="test", source="qq:group:1")
        assert history.session_for_source("qq:group:1") == "test:GroupMessage:1"
        history.session("other:GroupMessage:1", instance="other", source="qq:group:1")
        assert history.session_for_source("qq:group:1") == ""
    finally:
        database.close()
