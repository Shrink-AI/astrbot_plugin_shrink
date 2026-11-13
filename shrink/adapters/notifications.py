"""The only entry point for new user-facing progress notifications."""
import asyncio
import json
from functools import partial
from types import SimpleNamespace
from ..persistence.notifications import NotificationRepository
from ..services.composition import Silent


class Notifications:
    def __init__(self, app):
        self.app = app
        self.repository = NotificationRepository(app.database)
        if not hasattr(app, '_notification_inflight'):
            app._notification_inflight = set()
        self.inflight = app._notification_inflight

    async def send_once(self, event, key, facts, *, request="", kind="progress", retry=False):
        scope = {"session": event.unified_msg_origin, "sender": str(event.get_sender_id())}
        identity = json.dumps([scope["session"], scope["sender"], key], ensure_ascii=False)
        if identity in self.inflight or not self.repository.reserve(identity, retry=retry):
            return False
        self.inflight.add(identity)
        sending = False
        try:
            text = await self.app.outbound.compose(
                facts, request=request, kind=kind, scope=scope)
            if isinstance(text, Silent):
                self.repository.state(identity, "silent")
                return False
            if event.get_extra("shrink_cancelled"):
                self.repository.state(identity, "cancelled")
                return False
            from astrbot.api.message_components import Plain
            from astrbot.core.message.message_event_result import MessageChain
            chain = MessageChain([Plain(text)])
            chain.use_markdown(False)
            self.repository.state(identity, "sending", text)
            sending = True
            accepted = await asyncio.wait_for(event.send(chain), 30)
            if accepted is False:
                raise RuntimeError("结果发送未确认")
            self.repository.state(identity, "platform_accepted", text)
            self.app.outbound.record(scope, text, identity)
            return True
        except BaseException as exc:
            # A recording failure after acceptance must never make the send retryable.
            state = "unknown" if sending else "interrupted" if isinstance(exc, asyncio.CancelledError) else "failed"
            self.repository.fail(identity, state, type(exc).__name__)
            if isinstance(exc, asyncio.CancelledError):
                raise
            self.app.tasks.error("notification", exc)
            return False
        finally:
            self.inflight.discard(identity)


def notification_event(context, session, sender):
    return SimpleNamespace(unified_msg_origin=session, get_sender_id=lambda: sender,
                           get_extra=lambda key: None,
                           send=partial(context.send_message, session))
