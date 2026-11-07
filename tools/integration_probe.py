"""Verify real AstrBot hooks and host lifecycle using temporary data and fake providers."""

import asyncio
import importlib
import sqlite3
import sys
import tempfile
import time
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(sys.argv[1]).resolve()
sys.path[:0] = [str(ROOT.parent), str(ROOT), str(ROOT / "tools")]


async def main():
    from astrbot.core.provider.entities import LLMResponse, ProviderRequest
    from astrbot.api.message_components import At
    from growth_style_probe import Event

    # Import the entry point as AstrBot does, including its package-relative dependencies.
    sys.path[:0] = [str(ROOT.parent), str(ROOT)]
    module = importlib.import_module(ROOT.name + ".main")
    host_module = importlib.import_module(ROOT.name + ".shrink.adapters.host")
    runtime = importlib.import_module(ROOT.name + ".shrink.runtime")
    persona_module = importlib.import_module(ROOT.name + ".shrink.config")
    native = SimpleNamespace(system_prompt="original")

    class PersonaManager:
        async def get_persona(self, persona_id):
            return native

        async def update_persona(self, persona_id, system_prompt, begin_dialogs):
            native.system_prompt = system_prompt

    class Provider:
        def meta(self):
            return SimpleNamespace(id="probe-model")

        async def text_chat(self, **kwargs):
            return LLMResponse(role="assistant", completion_text="好，试试。")

        async def get_embedding(self, text):
            return [1.0, 0.0]

    provider = Provider()
    context = SimpleNamespace(
        persona_manager=PersonaManager(),
        get_provider_by_id=lambda key: provider,
        get_all_providers=lambda: [provider],
        get_all_embedding_providers=lambda: [],
    )
    with tempfile.TemporaryDirectory(prefix="shrink-host-probe-") as temp:
        data = Path(temp)
        plugin = module.ShrinkPlugin.__new__(module.ShrinkPlugin)
        plugin.host = host_module.PluginHost(context, data, ROOT / "static", module.__name__)
        app = plugin.host.application
        app.state.put("last_learning_check", time.time())
        with patch.object(
            host_module,
            "DashboardServer",
            partial(runtime.DashboardServer, host="127.0.0.1", port=0),
        ):
            await plugin.initialize()
            try:
                assert plugin.host.dashboard.server.started
                assert native.system_prompt == persona_module.persona_prompt(
                    app.persona_repository.learned_persona()
                )
                event = Event("石英，记住我喜欢酸辣粉", "remember")
                await plugin.receive(event)
                assert event.get_extra("shrink_reply_reason") == "direct"
                request = ProviderRequest(prompt=event.text, system_prompt="工具权限原文")
                await plugin.enrich(event, request)
                assert request.system_prompt.startswith("工具权限原文")
                assert "石英" in request.system_prompt
                response = LLMResponse(role="assistant", completion_text="嗯，记住了。")
                await plugin.response(event, response)
                from astrbot.core.message.message_event_result import MessageEventResult

                event.result = MessageEventResult().message(response.completion_text)
                await plugin.decorate(event)
                await event.send(event.result)
                await plugin.sent(event)
                assert len(event.sent) == 1
                assert "已保存" in await plugin.remember(event, "喜欢酸辣粉")
                duplicate = Event(event.text, "remember")
                await plugin.receive(duplicate)
                assert not duplicate.get_extra("shrink_reply")
                other = Event("发语音", "other")
                other.get_messages = lambda: [At(qq="12345")]
                await plugin.receive(other)
                assert other.get_extra("shrink_reply_reason") == "addressed_other"
                followup = Event("再说一句", "followup")
                followup.is_at_or_wake_command = False
                await plugin.receive(followup)
                assert followup.get_extra("shrink_reply_reason") == "other_conversation_followup"
                assert not followup.sent
            finally:
                await plugin.terminate()
                await plugin.terminate()
            assert not app.tasks.jobs
            with sqlite3.connect(data / "shrink.db") as connection:
                assert connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1
            restored = host_module.PluginHost(context, data, ROOT / "static", module.__name__)
            await restored.start(set())
            assert restored.application.persona_repository.learned_persona()
            await restored.close()
        conflict = host_module.PluginHost(context, data, ROOT / "static", module.__name__)
        try:
            await conflict.start({"astrbot_plugin_livingmemory"})
        except RuntimeError:
            assert conflict.application.closed
        else:
            raise AssertionError("Conflicting plugin was accepted")
    print(
        "INTEGRATION_OK hooks, memory, audience, dedup, startup, shutdown, restart, conflict cleanup; external_sends=0"
    )

if __name__ == "__main__":
    asyncio.run(main())
