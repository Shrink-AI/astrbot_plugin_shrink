"""One pre-tool notice per foreground request, never per internal tool call."""
from ..services.task_context import CURRENT_TASK


class ToolNoticeHooks:
    async def notify_before_tool(self, event, tool, tool_args):
        if CURRENT_TASK.get() or not event.get_extra("shrink_reply"):
            return
        if event.get_extra("shrink_cancelled"):
            raise RuntimeError("用户已停止本轮任务")
        if event.get_extra("shrink_tool_notice"):
            return
        name = getattr(tool, "name", "")
        # Background submission returns immediately; its normal reply owns acceptance.
        # Do not send a second "starting work" message before that receipt.
        if name in {"shrink_find_meme", "shrink_develop", "shrink_orchestrate", "shrink_reload"}:
            return
        if event.get_extra("shrink_background_task"):
            return
        # Set before awaiting, so parallel tool calls share one notice.
        event.set_extra("shrink_tool_notice", True)
        turn = event.get_extra("shrink_turn")
        if not turn:
            return
        await self.host.conversation.notifications.send_once(
            event, turn.id + ":tools",
            {"phase": "before_tool", "executed": False,
             "fact": "准备开始核查当前请求，工具尚未返回；不要声称已经派工或完成。"},
            request=turn.text, kind="progress")
