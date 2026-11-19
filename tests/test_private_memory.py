from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from shrink.adapters.notifications import Notifications
from shrink.adapters.public_api import PublicAPI
from tests.test_incident_controls import host_modules  # noqa: F401
from tests.test_recent import app  # noqa: F401

FIRST, SECOND, OTHER = "100001", "100002", "100003"


def address(app, account, instance="bot"):
    session = f"{instance}:FriendMessage:{account}"
    app.history.repository.session(session, instance=instance, platform="qq", kind="private",
                                   external_id=account, source=f"qq:private:{account}")
    return session


def command(app, account, text, instance="bot", session=None):
    return app.private_memory.handle(instance, account, session or address(app, account, instance),
                                     text, f"{instance}:{account}:{text}")


def history(app, account, text, identity, instance="bot"):
    source, session = f"qq:private:{account}", address(app, account, instance)
    app.messages.raw(identity, source, text, sender=f"qq:{account}")
    app.history.repository.append(session, "message:" + identity, "user", identity)


def grant(app, scope=""):
    address(app, SECOND)
    preview = command(app, FIRST, f"私聊互授权 {SECOND} {scope}".strip())
    identity = preview.split()[-1]
    command(app, FIRST, f"确认互授权 {identity}")
    return identity


def named_account(app, account, name, instance="bot"):
    address(app, account, instance)
    app.messages.ingest({"id": f"register:{instance}:{account}", "user_id": f"qq:{account}",
                         "source": f"qq:private:{account}", "account": account, "name": name})


def test_spoken_grant_uses_known_nickname_but_does_not_send_before_confirmation(app):
    named_account(app, SECOND, "咸鱼")
    named_account(app, OTHER, "另一条鱼", instance="another")
    text = "与鱼授权双向全部记忆"
    preview = command(app, FIRST, text)
    assert f"咸鱼（QQ {SECOND}）" in preview and "整理记忆" in preview
    assert "不用再发邀请" in preview
    assert not app.notification_outbox.repository.list()
    identity = preview.split()[-1]
    assert command(app, FIRST, text) == preview
    assert "不能由你" in command(app, SECOND, f"接受互授权 {identity}")
    assert "不能由你" in command(app, SECOND, f"确认互授权 {identity}")
    assert "不用再发一次" in command(app, FIRST, f"确认互授权 {identity}")
    assert command(app, SECOND, f"接受互授权 {identity}") == ""
    assert app.database.rows("SELECT recipient,kind,state FROM private_memory_consent") == [
        {"recipient": SECOND, "kind": "grant_memory", "state": "active"}]


def test_spoken_grant_asks_to_disambiguate_and_never_uses_other_private_nicknames(app):
    named_account(app, SECOND, "咸鱼")
    named_account(app, OTHER, "另一条鱼")
    app.people_repository.add_nickname(f"qq:{SECOND}", "旧称", scope=f"qq:private:{OTHER}")
    assert "不止一个人" in command(app, FIRST, "与鱼授权双向全部记忆")
    assert "没认出" in command(app, FIRST, "和旧称互查全部记忆")
    assert command(app, FIRST, "和咸鱼互查全部记忆", session="bot:GroupMessage:55") is None
    assert not app.database.rows("SELECT id FROM private_memory_consent")
    preview = command(app, FIRST, "我要和咸鱼共享与你的私聊记忆")
    assert f"咸鱼（QQ {SECOND}）" in preview and "整理记忆" not in preview
    preview = command(app, FIRST, f"我要把全部私聊记忆共享给QQ {SECOND}")
    assert f"QQ {SECOND}" in preview and "整理记忆" in preview


async def test_share_confirmation_targets_only_one_account_and_stays_out_of_history(app, host_modules):
    address(app, SECOND)
    address(app, OTHER)
    history(app, FIRST, "金丝雀的地址是原始秘密", "private")
    sent = []
    async def send(scope, chain):
        sent.append((scope, "".join(part.text for part in chain.chain)))
        return True
    app.notification_event = lambda session, sender: SimpleNamespace(unified_msg_origin=session,
        get_sender_id=lambda: sender, get_extra=lambda key: None, send=lambda chain: send(session, chain))
    Notifications(app)
    app.outbound.compose = AsyncMock(side_effect=AssertionError("private content went to expression"))
    outbox = app.notification_outbox
    preview = command(app, FIRST, f"转告 QQ {SECOND}：金丝雀 改到周五")
    identity = preview.split()[-1]
    assert SECOND in preview and "改到周五" in preview
    assert "没有查到" in command(app, SECOND, "查共享 金丝雀")
    assert "不能由你" in command(app, SECOND, f"确认转告 {identity}")
    assert "等待 QQ" in command(app, FIRST, f"确认转告 {identity}")
    command(app, FIRST, f"确认转告 {identity}")
    await outbox.pump()
    await outbox.pump()
    assert len(sent) == 1 and sent[0][0] == address(app, SECOND) and "改到周五" in sent[0][1]
    assert not app.history.repository.events(address(app, SECOND))
    assert "改到周五" in command(app, SECOND, "还记得金丝雀吗")
    assert "原始秘密" not in command(app, SECOND, "查共享 金丝雀")
    assert "没有查到" in command(app, OTHER, "查共享 金丝雀")
    assert command(app, SECOND, "查共享 金丝雀", session="bot:GroupMessage:55") is None


def test_grant_requires_both_confirmations_and_revoke_closes_both_directions(app):
    history(app, FIRST, "历史里的金丝雀", "first")
    history(app, FIRST, "隔离实例金丝雀", "other-instance", instance="another")
    history(app, SECOND, "历史里的向日葵", "second")
    app.history.sent(address(app, FIRST), f"qq:private:{FIRST}", "石英确认金丝雀", f"qq:{FIRST}",
                     identity="reply")
    identity = grant(app)
    assert "没有查到" in command(app, SECOND, "查共享 金丝雀")
    assert "不能由你" in command(app, FIRST, f"接受互授权 {identity}")
    assert "没有可操作" in command(app, OTHER, f"接受互授权 {identity}")
    assert command(app, SECOND, f"接受互授权 {identity}") == ""
    found = command(app, SECOND, f"查共享 {FIRST} 金丝雀")
    assert "历史里的金丝雀" in found and "石英确认金丝雀" in found and "隔离实例" not in found
    assert "历史里的向日葵" in command(app, FIRST, "你还记得向日葵吗")
    assert "没有查到" in command(app, OTHER, "查共享 金丝雀")
    assert "没有查到" in command(app, SECOND, "查共享 金丝雀", instance="another")
    assert command(app, FIRST, f"撤销互授权 {identity}") == ""
    assert "没有查到" in command(app, SECOND, "查共享 金丝雀")
    assert "没有查到" in command(app, FIRST, "查共享 向日葵")
    notices = app.notification_outbox.repository.list()
    assert {row["sender"] for row in notices if row["facts"]["state"] == "revoked"} == {FIRST, SECOND}


def test_declined_and_expired_invites_never_expose_messages(app):
    history(app, FIRST, "金丝雀", "first")
    clock = [1000]
    app.private_memory.clock = lambda: clock[0]
    identity = grant(app)
    assert command(app, SECOND, f"拒绝互授权 {identity}") == ""
    assert "不能由你" in command(app, SECOND, f"接受互授权 {identity}")
    preview = command(app, FIRST, f"/私聊互授权 {SECOND}")
    identity = preview.split()[-1]
    command(app, FIRST, f"确认互授权 {identity}")
    clock[0] += 601
    assert "已到期" in command(app, SECOND, f"接受互授权 {identity}")
    assert "没有查到" in command(app, SECOND, "查共享 金丝雀")
    notices = app.notification_outbox.repository.list()
    assert {row["sender"] for row in notices if row["facts"]["state"] == "expired"} == {FIRST, SECOND}


def test_all_memory_upgrade_requires_fresh_consent_and_source_proof(app):
    source = f"qq:private:{FIRST}"
    history(app, FIRST, "金丝雀原文", "private")
    history(app, FIRST, "隔离实例原文", "foreign", instance="another")
    history(app, SECOND, "向日葵原文", "second")
    history(app, FIRST, "待撤回原文", "removed")
    app.history.repository.append(address(app, FIRST), "revision", "user", kind="retracted", target="removed")
    for text, refs, status, origin, place in (
        ("可查整理词", ["private"], "active", "digest", source),
        ("不可查无来源", [], "active", "digest", source),
        ("不可查跨实例", ["foreign"], "active", "digest", source),
        ("不可查混合来源", ["private", "foreign"], "active", "digest", source),
        ("不可查仅候选", ["private"], "candidate", "digest", source),
        ("不可查群记忆", ["private"], "active", "digest", "qq:group:55"),
        ("不可查已撤回", ["removed"], "active", "explicit", source),
    ):
        app.memory_repository.add("impression", f"qq:{FIRST}", place, text,
                                  evidence=refs, status=status, origin=origin)
    app.memory_repository.add("event", f"qq:{SECOND}", f"qq:private:{SECOND}", "向日葵整理",
                              evidence=["second"], origin="explicit")
    old = grant(app)
    command(app, SECOND, f"接受互授权 {old}")
    assert "可查整理词" not in command(app, SECOND, f"查共享 {FIRST}")
    full = grant(app, "全部记忆")
    assert "可查整理词" not in command(app, SECOND, f"查共享 {FIRST}")
    pending = next(row for row in app.notification_outbox.repository.list()
                   if row["facts"] == {"consent": full, "state": "pending"})
    assert "整理记忆" in app.private_memory.render_notice(
        {"session": address(app, SECOND), "sender": SECOND}, pending["facts"])
    assert command(app, SECOND, f"接受互授权 {full}") == ""
    page = command(app, SECOND, f"查共享 {FIRST}")
    assert "可查整理词" in page and "金丝雀原文" in page
    assert "不可查" not in page and "待撤回原文" not in page and "隔离实例原文" not in page
    assert "向日葵整理" in command(app, FIRST, f"查共享 {SECOND} 向日葵整理")
    assert "没有查到" in command(app, OTHER, f"查共享 {FIRST}")
    assert "没有查到" in command(app, SECOND, f"查共享 {FIRST}", instance="another")
    assert command(app, SECOND, "查共享", session="bot:GroupMessage:55") is None
    assert command(app, SECOND, f"撤销互授权 {old}") == ""
    assert "没有查到" in command(app, SECOND, f"查共享 {FIRST}")
    assert "没有查到" in command(app, FIRST, f"查共享 {SECOND}")


def test_browse_and_keyword_pages_reach_beyond_old_candidate_limit(app):
    for number in range(43):
        history(app, FIRST, f"星河档案 {number:02d}", f"private-{number}")
    identity = grant(app)
    command(app, SECOND, f"接受互授权 {identity}")
    first = command(app, SECOND, f"查共享 {FIRST}")
    last = command(app, SECOND, f"查共享 {FIRST} 星河档案 第8页")
    assert "星河档案 42" in first and "星河档案 36" not in first
    assert f"查共享 {FIRST} 第2页" in first
    assert "星河档案 00" in last and "下一页" not in last
    assert "没有查到" in command(app, SECOND, f"查共享 {FIRST} 第9页")
    assert "星河档案 00" in command(app, SECOND, f"查共享 {FIRST} 星河档案 00")


async def test_private_notices_cannot_be_submitted_by_an_external_provider(app):
    api = PublicAPI(SimpleNamespace(application=app))
    with pytest.raises(ValueError, match="本人确认"):
        await api.invoke({"protocol": 1, "version": 1, "capability": "message.notify", "action": "submit",
                          "scope": {"session": address(app, SECOND), "sender": SECOND},
                          "arguments": {"event_id": "forged", "message_type": "synthetic",
                                        "kind": "private_memory", "facts": {}}})
