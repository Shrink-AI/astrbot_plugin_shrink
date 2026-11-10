"""Translate an authenticated dashboard session into the host's administrator event."""

def admin_candidates(context):
    admins = [str(x) for x in context.get_config().get("admins_id", [])]
    items = []
    for platform in context.platform_manager.get_insts():
        meta = platform.meta()
        for account in admins:
            if account:
                items.append({"session": f"{meta.id}:FriendMessage:{account}",
                              "label": f"{meta.id} · 管理员 {account}"})
    return {"items": items, "reason": "" if items else "没有已连接平台或未配置管理员"}


def admin_event(context, session):
    from astrbot.core.cron.events import CronMessageEvent
    from astrbot.core.platform.message_session import MessageSession
    from astrbot.core.platform.message_type import MessageType
    admins = context.get_config().get('admins_id', [])
    if not admins:
        raise ValueError('AstrBot未配置管理员')
    if not session:
        raise ValueError('请填写接收结果的管理员私聊会话标识')
    parsed = MessageSession.from_str(session)
    if parsed.message_type != MessageType.FRIEND_MESSAGE or parsed.session_id not in [str(x) for x in admins]:
        raise ValueError('后台任务结果仅发送到已配置管理员私聊')
    event = CronMessageEvent(context=context, session=parsed, message='管理员后台任务')
    event.role = 'admin'
    return event
