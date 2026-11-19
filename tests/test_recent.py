"""Cross-conversation awareness comes from stored messages alone (plan 5.2-5.4, C04-C09)."""
import time

import pytest

from shrink.application import Application
from shrink.services.perception import Turn

A, B, DM = "qq:group:111", "qq:group:222", "qq:private:9000000001"
ME, OTHER = "qq:9000000001", "qq:9000000003"
NAMES = {ME: "成员甲", OTHER: "成员丙"}


async def offline(*args, **kwargs):
    raise AssertionError("recent interactions must not need a model, embedding or search")


@pytest.fixture
def app(tmp_path):
    value = Application(tmp_path / "data", offline, offline, offline)
    yield value
    value.database.close()


def say(app, key, uid, text, source, ago=60, addressee=""):
    assert app.messages.ingest({
        "id": key, "platform": "qq", "account": uid.split(":")[-1], "user_id": uid,
        "name": NAMES[uid], "source": source, "source_name": "", "text": text,
        "created": time.time() - ago, "addressee": addressee, "mentions": []})


def ask(app, text, uid=ME, source=B):
    turn = Turn(id="now", platform="qq", account=uid.split(":")[-1], user_id=uid,
                name=NAMES[uid], source=source, source_name="", text=text, created=time.time())
    return app.context.window(turn).text


def test_greeting_in_one_group_is_known_in_another_at_once(app):
    say(app, "g1", ME, "石英早上好呀", A, ago=5, addressee="self")
    text = ask(app, "记得我刚才打过招呼吗")
    assert "石英早上好呀" in text and "111群" in text
    # No reply was recorded, and the context says so instead of implying one.
    assert "回复:" not in text and "当时没有回复" in text
    app.messages.record_bot(A, "早呀", addressee=ME, created=time.time() - 3)
    assert "石英（你）回复: 早呀" in ask(app, "记得我刚才打过招呼吗")


def test_words_said_to_someone_else_keep_their_real_speaker(app):
    say(app, "t1", ME, "丙哥 我那个rust项目缓存占了20g", A, addressee=OTHER)
    say(app, "t2", OTHER, "我的rust项目也占了很多缓存", A)
    text = ask(app, "rust项目缓存怎么清")
    assert "成员甲: 丙哥 我那个rust项目缓存占了20g" in text
    assert "我的rust项目也占了很多缓存" not in text


def test_private_chat_is_used_only_when_its_owner_asks(app):
    say(app, "p1", ME, "我下周要去上海面试 先别告诉别人", DM, addressee="self")
    assert "上海面试" not in ask(app, "上海最近天气怎么样")
    assert "上海面试" in ask(app, "继续刚才私聊说的面试的事")
    # Someone else asking about it gets nothing of that chat.
    assert "上海面试" not in ask(app, "成员甲私聊跟你说了什么面试的事", uid=OTHER)
    # Inside the private chat itself, the person's group messages are available.
    say(app, "g2", ME, "有人知道面试要准备什么吗", A)
    assert "面试要准备什么" in ask(app, "面试的事你怎么看", source=DM)


def test_nothing_found_is_reported_as_unknown_not_as_never_happened(app):
    assert "别处" not in ask(app, "今天吃什么")
    text = ask(app, "还记得我刚才在另一个群说的吗")
    assert "没有查到" in text and "不能断言没发生" in text


def test_unrelated_chatter_is_not_poured_into_the_window(app):
    for i in range(60):
        say(app, f"n{i}", ME, f"哈哈哈第{i}次刷屏", A, ago=3000 - i)
    say(app, "k1", ME, "部署脚本的回滚参数我忘了", A, ago=4000)
    text = ask(app, "部署脚本怎么回滚")
    assert "部署脚本的回滚参数我忘了" in text
    assert text.count("刷屏") <= 2
    assert "没列出不等于没说过" in text
    asked = ask(app, "我之前在别的群说过部署脚本吗")
    assert asked.count("刷屏") <= 20 and "部署脚本的回滚参数我忘了" in asked


def test_older_records_are_reached_only_by_asking(app):
    say(app, "o1", ME, "我的猫叫团子", A, ago=3 * 86400)
    assert "团子" not in ask(app, "猫粮买哪个牌子")
    assert "团子" in ask(app, "我之前说过我的猫叫什么来着")


@pytest.mark.asyncio
async def test_lookup_reaches_other_groups_for_the_asker_only(app):
    async def nothing(*args, **kwargs):
        return "", []
    app.tools.memory = type("M", (), {"recall_for": staticmethod(nothing)})()
    say(app, "l1", ME, "周五的聚餐定在老地方", A)
    say(app, "l2", ME, "聚餐预算别超过两百 这事只跟你说", DM, addressee="self")
    found = await app.tools.lookup("聚餐", source=B, speaker=ME, private=False, request="聚餐在哪")
    assert "老地方" in found and "两百" not in found
    # The model's own query cannot open the private chat; the person's words can.
    found = await app.tools.lookup("私聊 聚餐", source=B, speaker=ME, private=False, request="聚餐在哪")
    assert "两百" not in found
    found = await app.tools.lookup("聚餐", source=B, speaker=ME, private=False,
                                   request="我私聊跟你说的聚餐预算是多少")
    assert "两百" in found
    other = await app.tools.lookup("聚餐", source=B, speaker=OTHER, private=False,
                                   request="成员甲私聊说的聚餐")
    assert "老地方" not in other and "两百" not in other


def test_private_lookup_uses_real_topic_even_after_classification_fails(app):
    say(app, "older", ME, "备份放在金丝雀服务器", DM, ago=2 * 86400)
    say(app, "secret", ME, "绝密会场地址", DM, ago=10)
    say(app, "different", ME, "备份在别的私聊，不能串档", "qq:private:another")
    found = app.recent.find(ME, B, "我私聊提过备份的位置吗", decision={
        "fallback": True, "reason": "timeout", "decision": {"topic": None}})
    assert "金丝雀服务器" in str(found.lines)
    assert "绝密会场" not in str(found.lines) and "不能串档" not in str(found.lines)
    assert not app.recent.find(ME, B, "备份", request="刚才私聊说了什么").lines
