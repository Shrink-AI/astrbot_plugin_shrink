"""Re-enter pending platform messages through the host's existing event queue."""
from astrbot.core.cron.events import CronMessageEvent
from astrbot.core.platform.message_session import MessageSession


class RecoveredMessage(CronMessageEvent):
    def __init__(self, context, row):
        metadata = row["metadata"]
        session = MessageSession.from_str(row["session"])
        super().__init__(context=context, session=session, message=row["text"],
                         message_type=session.message_type)
        self.message_obj.message_id = row["external_id"]
        self.message_obj.sender.user_id = row["sender"]
        self.message_obj.sender.nickname = metadata.get("name", row["sender"])
        self.message_obj.self_id = metadata.get("self_id", "")
        self.message_obj.group_id = metadata.get("group", "")
        self.message_obj.timestamp = row["received"]
        self.platform_meta.name = metadata.get("platform", "qq")
        self.is_at_or_wake_command = bool(metadata.get("wake"))
        self.role = "admin" if row["sender"] in {
            str(value) for value in context.get_config(umo=row["session"]).get("admins_id", [])
        } else "member"
        self.set_extra("shrink_recovered_input", row["id"])

async def recover_inputs(context, inbox):
    async def enqueue(row):
        await context.get_event_queue().put(RecoveredMessage(context, row))
    await inbox.recover("chat", enqueue)
