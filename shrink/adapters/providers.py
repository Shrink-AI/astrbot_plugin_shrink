from types import SimpleNamespace

from ..ports import ProviderUnavailable
from ..services.glossary import GlossaryService

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

    async def model(self, prompt, system="", images=None):
        provider = self.context.get_provider_by_id(self.state().settings.model_provider)
        if provider is None:
            raise ProviderUnavailable("Configured chat provider is unavailable")
        response = await provider.text_chat(
            prompt=prompt, system_prompt=system, image_urls=images or [], contexts=[]
        )
        return response.completion_text

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
