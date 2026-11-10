from pathlib import Path
import time

from ..api.app import create_app
from ..application import Application
from ..config import persona_prompt
from ..prompts import text as prompt_text
from ..runtime import DashboardServer
from .conversation import ConversationAdapter
from .providers import AstrBotProviders
from .operations import Operations
from .native_management import NativeManagement
from .knowledge import KnowledgeBridge
from .native_skills import NativeSkills
from .skill_archive import SkillArchive
from .public_api import PublicAPI
from .plugin_protocol import CapabilityClient
from ..services.maintenance import CapabilityMaintenance


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
        def host_quotes(umo):
            try:
                settings = context.get_config(umo=umo or None)["platform_settings"]
                return bool(settings.get("reply_with_quote"))
            except Exception:
                return False

        self.conversation = ConversationAdapter(
            self.application, plugin_module, umos=self.bridge.umos, host_quotes=host_quotes
        )
        self.dashboard = None
        self.static_dir = static_dir
        self.operations = Operations(context, self.application, static_dir.parent)
        self.native = NativeManagement(context, self.application)
        self.application.operations = self.operations
        self.application.native = self.native
        self.native_skills = NativeSkills(self.application, self.native)
        self.application.native_skills = self.native_skills
        self.application.maintenance = CapabilityMaintenance(
            self.application, self.native, self.native_skills, SkillArchive(self.native))
        self.application.consolidation.capabilities = self.application.maintenance.run
        self.knowledge = KnowledgeBridge(context, self.application)
        self.application.knowledge = self.knowledge
        self.application.memory.knowledge = self.knowledge
        self.application.digests.resolve = lambda rows: self.knowledge.resolve(rows, "digests")
        self.application.plugin_bus = CapabilityClient()
        self.public_api = PublicAPI(self)
        self.application.outbound.resolve_scope = self.resolve_scope

    def resolve_scope(self, scope):
        umo = str(scope.get('session', ''))
        sender = str(scope.get('sender', ''))
        if not umo or not sender:
            return None
        from astrbot.core.platform.message_session import MessageSession
        try:
            parsed = MessageSession.from_str(umo)
            platform = self.bridge.context.get_platform_inst(parsed.platform_name)
            if platform is None:
                return None
            name = platform.meta().name
            name = 'qq' if name in ('aiocqhttp', 'qq_official') else name
            private = 'friend' in str(parsed.message_type).lower()
            source = next((key for key, value in self.bridge.umos.items() if value == umo),
                          f"{name}:{'private' if private else 'group'}:{parsed.session_id}")
            if private and str(parsed.session_id) != sender:
                return None
            return {'source': source, 'user_id': f'{name}:{sender}'}
        except (ValueError, AttributeError):
            return None

    async def start(self, active_plugins):
        app = self.application
        try:
            if not app.state.get("active_persona"):
                app.state.put("original_persona", await self.bridge.original_persona())
                app.persona_repository.add_version(prompt_text("persona.initial"), [], "初始人格")
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
            app.tasks.spawn(self.knowledge.start())
            app.tasks.spawn(self.native_skills.start())
            app.tasks.spawn(self.operations.deployments.start())
        except BaseException:
            await self.close()
            raise

    async def close(self):
        # Stop accepting admin mutations before cancelling work or closing SQLite.
        try:
            if self.dashboard:
                await self.dashboard.close()
        finally:
            try:
                await self.knowledge.close()
                await self.native_skills.close()
                await self.operations.close()
            finally:
                await self.application.close()
