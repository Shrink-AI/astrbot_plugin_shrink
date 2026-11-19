"""What the current speaker recently said in other conversations, straight from stored messages.

Available the moment a message is ingested: no summary, embedding or model call is involved.
The scope is the speaker's own words and the bot's replies to them. Their private chat is used
only inside that private chat, or when they themselves ask about it.
"""
import datetime
import re
import time
from dataclasses import dataclass, field

from ..config import BOT_NAME
from ..text_rules import grams

AUTO_SECONDS = 24 * 3600
ASKED_SECONDS = 7 * 86400
SCAN = 400
REPLY_GAP = 300
LINE_LIMIT = 160
# The speaker points at something said earlier or somewhere else.
ASKED = re.compile(
    r"刚才|刚刚|之前|上次|早上|昨天|另一个群|别的群|其他群|那个群|隔壁群|别处|"
    r"还记得|记得.{0,12}[吗嘛么]|说过|聊过|问过|提过|讲过|打过招呼|私聊|私下|私信|小窗")
PRIVATE = re.compile(r"私聊|私下|私信|小窗|单独(跟|和|找)你")
PRIVATE_FILLER = re.compile(
    r"刚才|刚刚|之前|上次|那个|这个|还记得|记得|说过|说的|聊过|聊的|问过|提过|跟你|和你|"
    r"继续|什么|多少|的是|的事|这件事|告诉|有没有|可以|知道|一下|原文|内容|关键词|"
    r"私聊|私下|私信|小窗|单独|查找|检索")


def topic_terms(request):
    return {term for term in grams(PRIVATE_FILLER.sub(" ", request or ""), n=2, limit=48)
            if len(term) >= 2}


def private_match(request, text):
    value = (text or "").lower()
    return not value.startswith("[原消息已") and any(term in value for term in topic_terms(request))


@dataclass
class Recent:
    lines: list = field(default_factory=list)
    asked: bool = False
    found: int = 0       # matching records in the window, shown or not
    capped: bool = False  # the scan limit was reached; `found` is a lower bound
    since: float = 0.0
    shown: int = 0

    @property
    def omitted(self):
        return max(0, self.found - self.shown)


def is_private(source):
    return ":private:" in (source or "")


def where(row):
    if is_private(row["source"]):
        return "私聊"
    name = row.get("source_name") or row["source"].rsplit(":", 1)[-1]
    return name if name.endswith("群") else name + "群"


def stamp(ts, now):
    value = datetime.datetime.fromtimestamp(ts)
    if value.date() == datetime.datetime.fromtimestamp(now).date():
        return f"{value:%H:%M}"
    return f"{value.month}月{value.day}日 {value:%H:%M}"


class RecentInteractions:
    def __init__(self, messages):
        self.messages = messages

    def find(self, user_id, source, text, *, name="", request=None, now=None,
             lines=None, budget=None, decision=None):
        """Pick the records that bear on `text`. `request` is what the person actually wrote;
        only that can widen the search or open their private chat, never a model's paraphrase."""
        now = now or time.time()
        request = text if request is None else request
        explicit_private = bool(PRIVATE.search(request or ""))
        asked = explicit_private or (
                 bool(decision["decision"]["topic"]) if decision and not decision["fallback"]
                 and decision.get("reason") != "decision_disabled"
                 else bool(ASKED.search(request or "")))
        since = now - (ASKED_SECONDS if asked else AUTO_SECONDS)
        rows = self.candidates(user_id, source, since, request, SCAN)
        result = Recent(asked=asked, found=sum(1 for r in rows if not r["is_bot"]),
                        capped=len(rows) >= SCAN, since=since)
        if not rows:
            return result
        lines = lines or (20 if asked else 6)
        budget = budget or (1600 if asked else 600)
        wanted = set(grams(text or ""))
        rows.sort(key=lambda r: r["created"])
        own = [r for r in rows if not r["is_bot"]]
        scored = sorted(
            own, key=lambda r: (len(wanted & set(grams(r["text"]))), r["created"]), reverse=True)
        if not asked:
            # Unprompted, only what touches the topic plus the latest couple of lines.
            latest = {r["id"] for r in own[-2:]}
            scored = [r for r in scored
                      if r["id"] in latest or wanted & set(grams(r["text"]))]
        picked, used = {}, 0
        for row in scored:
            if len(picked) >= lines:
                break
            group = [row] + self.replies(rows, row, user_id)
            cost = sum(min(len(r["text"] or ""), LINE_LIMIT) + 24 for r in group)
            if picked and used + cost > budget:
                continue
            used += cost
            picked.update((r["id"], r) for r in group)
        chosen = sorted(picked.values(), key=lambda r: r["created"])
        result.shown = sum(1 for r in chosen if not r["is_bot"])
        result.lines = [self.line(r, name, now) for r in chosen]
        return result

    def candidates(self, user_id, source, since, request, limit):
        rows = self.messages.elsewhere(user_id, source, since, limit, include_private=False)
        if not is_private(source) and PRIVATE.search(request or ""):
            platform, _, account = user_id.partition(":")
            own_private = f"{platform}:private:{account}"
            selected = [row for row in self.messages.private_matches(
                own_private, user_id, since, sorted(topic_terms(request)), limit)
                        if private_match(request, row["text"])]
            rows.extend(selected)
        return rows

    @staticmethod
    def replies(rows, row, user_id):
        """The bot's recorded reply to this message, if it sent one."""
        for other in rows:
            if (other["is_bot"] and other["source"] == row["source"]
                    and other.get("addressee") == user_id
                    and 0 <= other["created"] - row["created"] <= REPLY_GAP):
                return [other]
        return []

    @staticmethod
    def line(row, name, now):
        speaker = f"{BOT_NAME}（你）回复" if row["is_bot"] else (
            name or row.get("speaker_name") or row["user_id"])
        text = (row["text"] or "").replace("\n", " ")
        if len(text) > LINE_LIMIT:
            text = text[:LINE_LIMIT] + "…"
        if row.get("has_image"):
            text = (text + " [图片]").strip()
        return f"  {where(row)} [{stamp(row['created'], now)}] {speaker}: {text or '[消息]'}"

    def render(self, result, name):
        """Context lines for the reply; empty when there is nothing worth saying."""
        if not result.lines:
            if result.asked:
                return ["（查了别处的记录：最近没有查到 " + name + " 在其他会话的发言。"
                        "没查到只说明这里没有记录，不能断言没发生，也不要假装记得。）"]
            return []
        note = ("私聊只列与问题相关的发言，不推断当时是否回复" if
                any(line.startswith("  私聊 [") for line in result.lines) else
                "没列出你的回复就是当时没有回复")
        head = f"（{name} 最近在别处说过的，来自原始记录；{note}）"
        out = [head, *result.lines]
        if result.omitted:
            count = f"至少 {result.omitted}" if result.capped else str(result.omitted)
            out.append(f"  （同一时段还有 {count} 条没列出；没列出不等于没说过，"
                       "需要时用 shrink_lookup 按内容再查）")
        return out
