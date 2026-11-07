from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

from .shrink.adapters.host import PluginHost


@register("astrbot_plugin_shrink", "Shrink", "石英：人格、记忆、学习和表情", "0.6.0")
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

        await self.host.start({star.name for star in star_registry if star.activated})
        logger.info("Shrink ready: unified conversation and dashboard on port 6186")

    async def terminate(self):
        await self.host.close()

    @filter.event_message_type(filter.EventMessageType.ALL, priority=-100)
    async def receive(self, event: AstrMessageEvent):
        await self.host.conversation.receive(event)

    @filter.on_llm_request(priority=-100)
    async def enrich(self, event, req):
        await self.host.conversation.enrich(event, req)

    @filter.on_llm_response(priority=-100)
    async def response(self, event, response):
        await self.host.conversation.response(event, response)

    @filter.on_decorating_result(priority=-100)
    async def decorate(self, event):
        await self.host.conversation.decorate(event)

    @filter.after_message_sent()
    async def sent(self, event):
        await self.host.conversation.sent(event)

    @filter.on_llm_tool_respond()
    async def tool_used(self, event, tool, tool_args, tool_result):
        self.host.conversation.tool_used(event, tool, tool_args, tool_result)

    @filter.llm_tool(name="shrink_chat_history")
    async def chat_history(
        self, event: AstrMessageEvent, keyword: str = "", person: str = "", days: float = 3
    ):
        """翻这个群（或这段私聊）以前的聊天记录。用于“之前谁说过/发过…”“昨天聊了啥”“把那个链接再发一下”这类问题。三个参数都可以留空。

        Args:
            keyword(string): 要找的词，比如“链接”“gamejam”；留空就按时间看
            person(string): 只看某个群友说的，填名字或外号；留空看所有人
            days(number): 往前翻几天，默认 3，最多 30；看最近几小时可以填小数
        """
        return await self.host.conversation.chat_history(event, keyword, person, days)

    @filter.llm_tool(name="shrink_lookup")
    async def lookup(self, event: AstrMessageEvent, query: str):
        """查你记得的关于某个人或某件事的东西：这个人是谁、外号、你对TA的了解，或者以前聊过的某件事。

        Args:
            query(string): 人名、外号，或者要回忆的事情
        """
        return await self.host.conversation.lookup(event, query)

    @filter.llm_tool(name="shrink_find_meme")
    async def find_meme(self, event: AstrMessageEvent, keyword: str):
        """在你的表情库里按关键词找一张表情，找到就跟在回复后面发出去。

        Args:
            keyword(string): 表情的意思或情绪，比如“无语”“开心”“猫”
        """
        return await self.host.conversation.find_meme(event, keyword)

    @filter.llm_tool(name="shrink_run_python")
    async def run_python(self, event: AstrMessageEvent, code: str):
        """在隔离的沙箱里运行一段 Python 3 代码并返回输出，用来算数、换算、日期计算、统计。沙箱不能联网、不能读写文件之外的东西，最多跑 10 秒，可以用 numpy。结果要用 print 打印出来。

        Args:
            code(string): 完整的 Python 代码
        """
        return await self.host.conversation.run_python(event, code)

    @filter.llm_tool(name="shrink_remember")
    async def remember(self, event: AstrMessageEvent, text: str):
        """在用户明确要求记住某件事时保存有来源的记忆。

        Args:
            text(string): 用户明确要求记住的事实，不添加推断。
        """
        return await self.host.conversation.remember(event, text)
