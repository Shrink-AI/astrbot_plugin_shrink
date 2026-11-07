from pathlib import Path
import time

from ..api.app import create_app
from ..application import Application
from ..config import persona_prompt
from ..runtime import DashboardServer
from .conversation import ConversationAdapter
from .providers import AstrBotProviders


class PluginHost:
    """Owns startup/teardown; the AstrBot entry point only registers hooks."""

    def __init__(self, context, root, static_dir: Path, plugin_module):
        self.bridge = AstrBotProviders(context, lambda: self.application.state)
        self.application = Application(
            root,
            self.bridge.model,
            self.bridge.embed,
            self.bridge.sync_persona,
            self.bridge.search,
        )
        self.conversation = ConversationAdapter(
            self.application, plugin_module, umos=self.bridge.umos
        )
        self.dashboard = None
        self.static_dir = static_dir

    async def start(self, active_plugins):
        app = self.application
        try:
            conflicts = {
                "astrbot_plugin_group_chat_plus",
                "astrbot_plugin_livingmemory",
                "astrbot_plugin_self_learning",
            }.intersection(active_plugins)
            if conflicts:
                raise RuntimeError(
                    "Disable conflicting plugins before enabling Shrink: " + str(sorted(conflicts))
                )
            if not app.state.get("active_persona"):
                app.state.put("original_persona", await self.bridge.original_persona())
                app.persona_repository.add_version(
                    "随和，接话自然，有自己的想法，不刻意卖萌。", [], "初始人格"
                )
            await self.bridge.sync_persona(persona_prompt(app.persona_repository.learned_persona()))
            app.state.put(
                "persona_sync",
                {
                    "status": "synced",
                    "version": app.state.get("active_persona"),
                    "at": time.time(),
                },
            )
            self.dashboard = DashboardServer(
                create_app(app, self.static_dir, self.bridge.providers)
            )
            await self.dashboard.start()
            app.start()
        except BaseException:
            await self.close()
            raise

    async def close(self):
        # Stop accepting admin mutations before cancelling work or closing SQLite.
        try:
            if self.dashboard:
                await self.dashboard.close()
        finally:
            await self.application.close()
