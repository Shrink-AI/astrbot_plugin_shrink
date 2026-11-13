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
             lines=None, budget=None):
        """Pick the records that bear on `text`. `request` is what the person actually wrote;
        only that can widen the search or open their private chat, never a model's paraphrase."""
        now = now or time.time()
        request = text if request is None else request
        asked = bool(ASKED.search(request or ""))
        private_ok = is_private(source) or bool(PRIVATE.search(request or ""))
        since = now - (ASKED_SECONDS if asked else AUTO_SECONDS)
        rows = self.messages.elsewhere(user_id, source, since, SCAN, include_private=private_ok)
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
        head = f"（{name} 最近在别处说过的，来自原始记录；没列出你的回复就是当时没有回复）"
        out = [head, *result.lines]
        if result.omitted:
            count = f"至少 {result.omitted}" if result.capped else str(result.omitted)
            out.append(f"  （同一时段还有 {count} 条没列出；没列出不等于没说过，"
                       "需要时用 shrink_lookup 按内容再查）")
        return out
