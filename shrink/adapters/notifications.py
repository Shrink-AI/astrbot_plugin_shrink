"""The only entry point for new user-facing progress notifications."""
import asyncio
import json
from ..persistence.notifications import NotificationRepository


class Notifications:
    def __init__(self, app):
        self.app = app
        self.repository = NotificationRepository(app.database)

    async def send_once(self, event, key, facts, *, request="", kind="progress"):
        scope = {"session": event.unified_msg_origin, "sender": str(event.get_sender_id())}
        identity = json.dumps([scope["session"], scope["sender"], key], ensure_ascii=False)
        if not self.repository.reserve(identity):
            return False
        sending = False
        try:
            text = await self.app.outbound.compose(
                facts, request=request, kind=kind, scope=scope)
            if event.get_extra("shrink_cancelled"):
                self.repository.state(identity, "cancelled")
                return False
            from astrbot.api.message_components import Plain
            from astrbot.core.message.message_event_result import MessageChain
            chain = MessageChain([Plain(text)])
            chain.use_markdown(False)
            self.repository.state(identity, "sending", text)
            sending = True
            await asyncio.wait_for(event.send(chain), 30)
            self.repository.state(identity, "platform_accepted", text)
            self.app.outbound.record(scope, text, identity)
            return True
        except BaseException as exc:
            # A recording failure after acceptance must never make the send retryable.
            self.repository.fail(identity, "unknown" if sending else "failed", type(exc).__name__)
            if isinstance(exc, asyncio.CancelledError):
                raise
            self.app.tasks.error("notification", exc)
            return False

