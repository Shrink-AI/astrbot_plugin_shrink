"""Sticker/text pairing and in-flight reply merging, from cases seen in the group."""
from shrink.services.debounce import Debouncer
from shrink.services.expression import settle_meme


def test_short_reaction_becomes_sticker_only():
    assert settle_meme("好嘞", explicit=False, reason="engaged") == ("", True)
    assert settle_meme("那再等等", explicit=False, reason="spontaneous") == ("", True)


def test_content_keeps_words_and_drops_sticker():
    assert settle_meme("哪家的", explicit=False, reason="engaged") == ("哪家的", False)
    assert settle_meme("节点送中了？", explicit=False, reason="spontaneous") == ("节点送中了？", False)
    assert settle_meme("好", explicit=False, reason="direct") == ("好", False)
    assert settle_meme("华莱士正价随便凑个十块倒是不难", explicit=False, reason="engaged")[1] is False


def test_requested_sticker_or_sticker_alone_is_kept():
    assert settle_meme("给你", explicit=True, reason="direct") == ("给你", True)
    assert settle_meme("", explicit=False, reason="engaged") == ("", True)


def test_followup_while_writing_takes_over_older_reply():
    d = Debouncer()
    key = ("group", "yinkui")
    first = d.begin(key, "华莱士", "direct")
    second = d.begin(key, "全国通用", "engaged")
    assert first.superseded and not d.commit(first)
    assert second.text == "华莱士\n全国通用" and second.reason == "direct"
    assert d.commit(second)
    d.done(key, first)
    assert d.replies[key] is second
    d.done(key, second)
    assert not d.replies


def test_reply_that_did_work_or_is_sending_is_not_taken_over():
    d = Debouncer()
    key = ("group", "eicy")
    first = d.begin(key, "帮我记一条", "direct")
    first.committed = True
    second = d.begin(key, "顺便看看还有啥", "direct")
    assert not first.superseded and second.text == "顺便看看还有啥"


def test_other_senders_do_not_interfere():
    d = Debouncer()
    a = d.begin(("group", "a"), "一", "engaged")
    b = d.begin(("group", "b"), "二", "engaged")
    assert d.commit(a) and d.commit(b)
