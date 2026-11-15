import logging
import time
from types import SimpleNamespace

from ..ports import ProviderUnavailable
from ..services.glossary import GlossaryService
from .model_settings import ModelSettings

logger = logging.getLogger(__name__)
PURPOSES = {"learning": "学习", "summary": "总结", "compression": "历史压缩", "chat": "后台"}

# Mirrors AstrBot's provider → builtin tool mapping (astr_main_agent._apply_web_search_tools).
SEARCH_TOOLS = {
    "tavily": "TavilyWebSearchTool",
    "bocha": "BochaWebSearchTool",
    "brave": "BraveWebSearchTool",
    "firecrawl": "FirecrawlWebSearchTool",
    "baidu_ai_search": "BaiduWebSearchTool",
    "exa": "ExaWebSearchTool",
    "anysearch": "AnySearchWebSearchTool",
    "deepseek": "DeepSeekWebSearchTool",
    "serpapi": "SerpApiWebSearchTool",
}


class AstrBotProviders:
    def __init__(self, context, state):
        self.context, self.state = context, state
        self.umos = {}
        self.model_settings = None
        self.session_for_source = lambda source: ""

    def bind_settings(self):
        self.model_settings = ModelSettings(self.context, self.state())
        self.model_settings.migrate()
        return self.model_settings

    def for_purpose(self, purpose):
        def bind(source=""):
            async def call(prompt, system="", images=None):
                return await self.call_model(purpose, source, prompt, system, images)
            call.for_source = bind
            return call
        return bind()

    async def model(self, prompt, system="", images=None):
        return await self.call_model("chat", "", prompt, system, images)

    async def call_model(self, purpose, source, prompt, system="", images=None):
        state = self.state()
        cfg = state.settings
        session = self.umos.get(source) or self.session_for_source(source) or source
        host = (self.model_settings.for_session(session) if self.model_settings else {
            "model_provider": cfg.model_provider, "fallback_providers": cfg.fallback_providers,
            "compression_provider": cfg.compression_provider,
        })
        selected = {"learning": cfg.learning_provider, "summary": cfg.summary_provider,
                    "compression": host["compression_provider"]}.get(purpose, "")
        primary = host["model_provider"]
        if not primary and self.model_settings and hasattr(self.context, "get_using_provider_async"):
            current = await self.context.get_using_provider_async(umo=session or None)
            if current:
                primary = current.meta().id
        candidates = list(dict.fromkeys([selected or primary, primary, *host["fallback_providers"]]))
        for index, identity in enumerate(candidates):
            provider = self.context.get_provider_by_id(identity)
            if provider is None or not callable(getattr(provider, "text_chat", None)):
                continue
            try:
                response = await provider.text_chat(
                    prompt=prompt, system_prompt=system, image_urls=images or [], contexts=[])
                if getattr(response, "role", "") == "err":
                    raise ProviderUnavailable("模型返回错误")
                if not response.completion_text:
                    raise ProviderUnavailable("模型没有返回正文")
            except Exception:
                continue
            self.model_status(purpose, selected or primary, identity, "fallback" if index else "ok")
            return response.completion_text
        self.model_status(purpose, selected or primary, "", "unavailable")
        raise ProviderUnavailable(f"{PURPOSES[purpose]}模型和备用模型均不可用，请在设置页检查模型")

    def model_status(self, purpose, selected, using, status):
        state = self.state()
        statuses = state.get("model_status", {})
        previous = statuses.get(purpose, {})
        current = {"selected": selected, "using": using, "status": status}
        if any(previous.get(key) != value for key, value in current.items()):
            statuses[purpose] = {**current, "at": time.time()}
            state.put("model_status", statuses)
            if status != "ok":
                logger.warning("%s模型%s，当前使用：%s", PURPOSES[purpose],
                               "已回退" if status == "fallback" else "不可用", using or "无")

    async def embed(self, text):
        provider = self.context.get_provider_by_id(self.state().settings.embedding_provider)
        if provider is None:
            raise ProviderUnavailable("Configured embedding provider is unavailable")
        return await provider.get_embedding(text)

    async def search(self, query, source=""):
        """Use the host's configured web search tool; empty when search is off or unavailable."""
        from astrbot.core.tools import web_search_tools

        umo = self.umos.get(source)
        config = self.context.get_config(umo=umo) if umo else self.context.get_config()
        settings = config.get("provider_settings", {})
        if not settings.get("web_search", False):
            return []
        name = SEARCH_TOOLS.get(settings.get("websearch_provider", "tavily"))
        tool_class = getattr(web_search_tools, name or "", None)
        if tool_class is None:
            return []
        tool = self.context.get_llm_tool_manager().get_builtin_tool(tool_class)
        properties = (tool.parameters or {}).get("properties", {})
        kwargs = {"query": query[:128]}
        for key in ("count", "max_results", "num", "limit"):
            if key in properties:
                kwargs[key] = 5
                break
        # The tools only read the event's origin and the star context's config.
        wrapper = SimpleNamespace(
            context=SimpleNamespace(
                event=SimpleNamespace(unified_msg_origin=umo or ""), context=self.context
            )
        )
        raw = await tool.call(wrapper, **kwargs)
        return GlossaryService.parse_results(raw if isinstance(raw, str) else str(raw))

    async def sync_persona(self, prompt):
        await self.context.persona_manager.update_persona(
            self.state().settings.persona_id, system_prompt=prompt, begin_dialogs=[]
        )
        actual = await self.context.persona_manager.get_persona(self.state().settings.persona_id)
        if actual is None or actual.system_prompt != prompt:
            raise RuntimeError("Persona synchronization could not be verified")

    def providers(self):
        return {
            "chat": [p.meta().id for p in self.context.get_all_providers()],
            "embedding": [p.meta().id for p in self.context.get_all_embedding_providers()],
        }

    async def original_persona(self):
        original = await self.context.persona_manager.get_persona(self.state().settings.persona_id)
        return original.system_prompt if original else ""
