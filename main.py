from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

from .shrink.prompts import text as prompt_text, tool_doc
from .shrink.adapters.host import PluginHost
from .shrink.adapters.tool_notices import ToolNoticeHooks


@register("astrbot_plugin_shrink", "Shrink", "石英：人格、记忆、学习和表情", "0.7.0")
class ShrinkPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.host = PluginHost(
            context,
            StarTools.get_data_dir("astrbot_plugin_shrink"),
            Path(__file__).parent / "static",
            self.__module__,
        )

    async def initialize(self):
        from astrbot.core.star.star import star_registry
        from astrbot.core.provider.register import llm_tools
        from .shrink.adapters.tool_prompts import owned

        required = {"shirnk_plugin_workbench": ["action"],
                    "shrink_develop": ["request"],
                    "shrink_orchestrate": [],
                    "shrink_native_manage": ["resource", "action"],
                    "shrink_plugin_capability": ["action"],
                    "shrink_curate_material": ["title", "source", "content"],
                    "shrink_chat_attachments": ["action"]}
        for tool in llm_tools.func_list:
            if tool.name in required and owned(tool, self.__module__):
                tool.parameters["required"] = required[tool.name]

        from .shrink.adapters.tool_prompts import push
        push(llm_tools.func_list, self.__module__)
        self.host.application.prompts.listeners.append(
            lambda key: push(llm_tools.func_list, self.__module__) if key.startswith("tool.") else None)
        await self.host.start({star.name for star in star_registry if star.activated})
        logger.info("Shrink ready: unified conversation and dashboard on port 6186")

    def plugin_capabilities(self):
        return self.host.public_api.manifest()

    async def invoke_capability(self, request):
        return await self.host.public_api.invoke(request)

    async def terminate(self):
        await self.host.close()

    @filter.event_message_type(filter.EventMessageType.ALL, priority=-100)
    async def receive(self, event: AstrMessageEvent):
        await self.host.conversation.receive(event)

    @filter.on_llm_request(priority=-100)
    async def enrich(self, event, req):
        from .shrink.services.task_context import CURRENT_TASK
        if CURRENT_TASK.get():
            return
        await self.host.conversation.enrich(event, req)
        if event.is_admin():
            req.system_prompt = (req.system_prompt or "") + "\n" + prompt_text("tasks.admin_hint")

    @filter.on_using_llm_tool()
    async def before_tool(self, event, tool, tool_args):
        await ToolNoticeHooks.notify_before_tool(self, event, tool, tool_args)

    @filter.llm_tool(name="shirnk_plugin_workbench")
    @tool_doc("shirnk_plugin_workbench", action="string", name="string", path="string", content="string")
    async def plugin_workbench(self, event: AstrMessageEvent, action: str = "guide",
                               name: str = "", path: str = "", content: str = ""):
        return await self.host.operations.work(event, action, name, path, content)

    @filter.llm_tool(name="shrink_curate_material")
    @tool_doc("shrink_curate_material", title="string", source="string", content="string")
    async def curate_material(self, event: AstrMessageEvent, title: str, source: str, content: str):
        import json
        if not event.is_admin():
            return '{"ok":false,"error":"仅管理员可导入项目资料"}'
        platform = event.get_platform_name()
        if platform in {"aiocqhttp", "qq_official", "cron"}:
            platform = "qq"
        account = str(event.get_sender_id())
        group = str(event.get_group_id() or "")
        scope = f"{platform}:group:{group}" if group else f"{platform}:private:{account}"
        try:
            result = await self.host.application.materials.ingest(
                title, source, content, scope, f"{platform}:{account}")
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc)[:1000]}, ensure_ascii=False)

    @filter.llm_tool(name="shrink_develop")
    @tool_doc("shrink_develop", request="string")
    async def develop(self, event: AstrMessageEvent, request: str):
        import json
        try:
            return json.dumps(await self.host.operations.submit(event, request), ensure_ascii=False)
        except ValueError as exc:
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)

    @filter.llm_tool(name="shrink_orchestrate")
    @tool_doc("shrink_orchestrate", action="string", arguments="string")
    async def orchestrate(self, event: AstrMessageEvent, action: str = "guide", arguments: str = "{}"):
        import json
        try:
            if not isinstance(action, str) or not action.strip():
                raise ValueError("action必须是非空字符串；先用guide查看用法")
            parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
            result = await self.host.operations.orchestrate(event, action, parsed)
            self.host.operations.record("orchestrate:" + action, result=json.dumps(result, ensure_ascii=False))
            return json.dumps(result, ensure_ascii=False)
        except (ValueError, TypeError) as exc:
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)

    @filter.llm_tool(name="shrink_reload")
    async def reload_core(self, event: AstrMessageEvent, mode: str = "hot"):
        """管理员要求重载时使用。hot 仅重载石英核心；cold 重启整个 AstrBot，会暂时断开 QQ。异步执行，提交不等于重载成功，结果由统一表达链路回传。其他任务运行时拒绝。

        Args:
            mode(string): hot 或 cold；默认 hot。只有用户要求重启 AstrBot 时选择 cold。
        """
        import json
        from .shrink.services.task_context import CURRENT_TASK
        try:
            result = await self.host.operations.deployments.reload_core(event, mode, CURRENT_TASK.get() or "")
            self.host.operations.record("reload:" + mode, result=json.dumps(result))
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)

    @filter.llm_tool(name="shrink_native_manage")
    @tool_doc("shrink_native_manage", resource="string", action="string", arguments="string", confirmed="boolean")
    async def native_manage(self, event: AstrMessageEvent, resource: str, action: str,
                            arguments: str = "{}", confirmed: bool = False):
        import json
        if not event.is_admin():
            return '{"ok":false,"error":"仅管理员可管理AstrBot"}'
        try:
            result = await self.host.operations.manage_native(resource, action, json.loads(arguments), confirmed)
            self.host.operations.record("native:" + action, resource, result=json.dumps(result, ensure_ascii=False))
            return json.dumps({"ok": True, "data": result}, ensure_ascii=False, default=str)
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc)[:1500]}, ensure_ascii=False)

    @filter.llm_tool(name="shrink_plugin_capability")
    async def plugin_capability(self, event: AstrMessageEvent, action: str, capability: str = "",
                                operation: str = "", arguments: str = "{}", provider: str = ""):
        """发现并使用已启用插件的公开能力。先 list 获取版本、操作和参数，再 call；遵循用户授权和原会话范围。现有原生工具可直接使用，无需重复调用。

        Args:
            action(string): list 或 call。
            capability(string): list 返回的能力名称。
            operation(string): 能力支持的操作。
            arguments(string): JSON 对象参数。
            provider(string): 多个提供者时指定 list 返回的 provider。
        """
        import json
        if not event.is_admin():
            return '{"ok":false,"error":"仅管理员可调用插件能力"}'
        try:
            bus = self.host.application.plugin_bus
            if action == 'list':
                return json.dumps(bus.catalog(), ensure_ascii=False)
            if action != 'call':
                raise ValueError('action 必须是 list 或 call')
            result = await bus.call(capability, operation, json.loads(arguments), provider=provider or None,
                                    scope={'session': event.unified_msg_origin,
                                           'sender': str(event.get_sender_id()), 'admin': True,
                                           'message_id': str(event.message_obj.message_id)})
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc)[:1000]}, ensure_ascii=False)

    @filter.on_llm_response(priority=-100)
    async def response(self, event, response):
        from .shrink.services.task_context import CURRENT_TASK
        if CURRENT_TASK.get():
            return
        await self.host.conversation.response(event, response)

    @filter.on_decorating_result(priority=-100)
    async def decorate(self, event):
        from .shrink.services.task_context import CURRENT_TASK
        if CURRENT_TASK.get():
            return
        await self.host.conversation.decorate(event)

    @filter.after_message_sent()
    async def sent(self, event):
        await self.host.conversation.sent(event)

    @filter.on_llm_tool_respond()
    async def tool_used(self, event, tool, tool_args, tool_result):
        self.host.conversation.tool_used(event, tool, tool_args, tool_result)

    @filter.llm_tool(name="shrink_chat_history")
    @tool_doc("shrink_chat_history", keyword="string", person="string", days="number")
    async def chat_history(
        self, event: AstrMessageEvent, keyword: str = "", person: str = "", days: float = 3
    ):
        return await self.host.conversation.chat_history(event, keyword, person, days)

    @filter.llm_tool(name="shrink_chat_attachments")
    @tool_doc("shrink_chat_attachments", action="string", keyword="string", attachment_id="string")
    async def chat_attachments(self, event: AstrMessageEvent, action: str = "list",
                               keyword: str = "", attachment_id: str = ""):
        import json
        turn = event.get_extra("shrink_turn")
        if not turn:
            return "请在原聊天会话中查看附件。"
        try:
            archive = self.host.application.attachments
            if action == "list":
                return json.dumps(archive.list(turn.source, keyword), ensure_ascii=False)
            row, path = archive.file(attachment_id, turn.source)
            if action == "read":
                if path.suffix.lower() not in {".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".py", ".log"}:
                    return "此附件不是可直接读取的文本，可发送副本。"
                with path.open("r", encoding="utf-8-sig") as file:
                    text = file.read(24001)
                return json.dumps({"name": row["name"], "content": text[:24000],
                                   "truncated": len(text) > 24000,
                                   "source": f"attachment:{attachment_id}"}, ensure_ascii=False)
            if action == "send":
                from astrbot.api.message_components import File
                from astrbot.core.message.message_event_result import MessageChain
                await event.send(MessageChain([File(name=row["name"], file=str(path))]))
                return "已调用当前会话的文件发送接口；这是保存的附件副本。"
            return "不支持的操作。"
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc)[:500]}, ensure_ascii=False)

    @filter.llm_tool(name="shrink_lookup")
    @tool_doc("shrink_lookup", query="string")
    async def lookup(self, event: AstrMessageEvent, query: str):
        return await self.host.conversation.lookup(event, query)

    @filter.llm_tool(name="shrink_find_meme")
    @tool_doc("shrink_find_meme", keyword="string")
    async def find_meme(self, event: AstrMessageEvent, keyword: str):
        return await self.host.conversation.find_meme(event, keyword)

    @filter.llm_tool(name="shrink_run_python")
    @tool_doc("shrink_run_python", code="string")
    async def run_python(self, event: AstrMessageEvent, code: str):
        return await self.host.conversation.run_python(event, code)

    @filter.llm_tool(name="shrink_remember")
    @tool_doc("shrink_remember", text="string")
    async def remember(self, event: AstrMessageEvent, text: str):
        return await self.host.conversation.remember(event, text)
