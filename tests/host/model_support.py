"""Real host configuration and provider services, with network access forbidden."""
import copy
import secrets
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

pytest.importorskip("astrbot.core.provider.manager")

from astrbot.core.astrbot_config_mgr import AstrBotConfigManager  # noqa: E402
from astrbot.core.config.astrbot_config import AstrBotConfig  # noqa: E402
from astrbot.core.config.default import DEFAULT_CONFIG  # noqa: E402
from astrbot.core.provider import manager as manager_module  # noqa: E402
from astrbot.core.provider.manager import ProviderManager  # noqa: E402
from astrbot.core.star.context import Context  # noqa: E402
from astrbot.core.umop_config_router import UmopConfigRouter  # noqa: E402
from astrbot.dashboard.services.config_service import ProviderConfigService  # noqa: E402


async def host_models(tmp_path, monkeypatch):
    config = AstrBotConfig(str(tmp_path / "host.json"), copy.deepcopy(DEFAULT_CONFIG))
    config["provider_sources"] = [{
        "id": "source", "type": "openai_chat_completion", "provider_type": "chat_completion",
        "api_base": "https://gateway.invalid/v1", "key": [secrets.token_urlsafe(32)],
        "custom_headers": {"Authorization": secrets.token_urlsafe(32)}, "timeout": 120,
    }]
    config["provider"] = [
        {"id": name, "provider_source_id": "source", "model": name, "enable": True}
        for name in ("first", "second", "third")
    ]
    config["agent_runner"]["config"]["model"]["provider_id"] = "first"
    config["dashboard"]["jwt_secret"] = secrets.token_urlsafe(32)
    config.save_config()
    router = UmopConfigRouter(None)
    manager = AstrBotConfigManager(config, router, None)
    manager.abconf_data = {}
    providers = ProviderManager(manager, None, SimpleNamespace(default_persona="default"))
    monkeypatch.setattr(manager_module, "astrbot_config", config)
    monkeypatch.setattr(manager_module, "sp", SimpleNamespace(get_async=AsyncMock(return_value=None)))
    context = Context.__new__(Context)
    context._config = config
    context.astrbot_config_mgr = manager
    context.provider_manager = providers
    service = ProviderConfigService(SimpleNamespace(
        astrbot_config=config, provider_manager=providers,
    ))

    original_send = httpx.AsyncClient.send

    async def forbid_send(client, *args, **kwargs):
        if isinstance(client._transport, httpx.ASGITransport):
            return await original_send(client, *args, **kwargs)
        raise AssertionError("Host model tests must not send network requests")

    monkeypatch.setattr(httpx.AsyncClient, "send", forbid_send)
    for entry in config["provider"]:
        await providers.load_provider(entry)
    return SimpleNamespace(config=config, profiles=manager, context=context,
                           providers=providers, service=service)


def native_transport(monkeypatch, host):
    async def request(client, method, url, **kwargs):
        if url.endswith("/provider/list"):
            data = host.service.list_providers_for_dashboard_types(
                kwargs["params"]["provider_type"])
        elif url.endswith("/provider/update"):
            await host.service.update_provider_from_dashboard_payload(kwargs["json"])
            data = None
        elif url.endswith("/provider/new"):
            await host.service.create_provider_from_dashboard_payload(kwargs["json"])
            data = None
        elif url.endswith("/provider/template"):
            data = host.service.get_provider_schema()
        else:
            raise AssertionError("Unexpected local host service path")
        return httpx.Response(200, json={"status": "ok", "data": data},
                              request=httpx.Request(method, url))

    monkeypatch.setattr(httpx.AsyncClient, "request", request)
