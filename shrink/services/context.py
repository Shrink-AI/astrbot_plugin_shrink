"""Working memory: a budgeted conversation window, like a harness managing its context."""

import datetime
import json
from dataclasses import dataclass, field

from ..config import BOT_NAME
from ..text_rules import grams
from .recent import RecentInteractions

GAP_SECONDS = 20 * 60
MIN_LINES = 6
GAP_MIN_LINES = 10
LINE_LIMIT = 120


@dataclass
class Window:
    text: str = ""
    participants: list = field(default_factory=list)
    start: float = 0.0
    lines: int = 0
    texts: list = field(default_factory=list)


def _clock(ts):
    return datetime.datetime.fromtimestamp(ts).strftime("%H:%M")


def _date(ts):
    value = datetime.datetime.fromtimestamp(ts)
    return f"{value.month}月{value.day}日 {value:%H:%M}"


class ContextService:
    def __init__(self, state, messages, digests, people, recent=None):
        self.state, self.messages, self.digests, self.people = state, messages, digests, people
        self.recent = recent or RecentInteractions(messages)

    def line(self, row, current_id=None):
        if row["is_bot"]:
            speaker = f"{BOT_NAME}（你）"
        else:
            speaker = row.get("person_name") or row["speaker_name"] or row["user_id"]
            if row.get("speaker_kind") == "bot":
                speaker += "（机器人）"
        target = row.get("addressee") or ""
        arrow = ""
        if target == "self":
            arrow = " →对你"
        elif target:
            arrow = " →对" + self.people.display(target)
        text = (row["text"] or "").replace("\n", " ")
        if len(text) > LINE_LIMIT:
            text = text[:LINE_LIMIT] + "…"
        if row.get("has_image"):
            text = (text + " [图片]").strip()
        mark = "（当前）" if current_id and row["id"] == current_id else ""
        return f"[{_clock(row['created'])}] {speaker}{arrow}: {text or '[消息]'}{mark}"

    def window(self, turn, *, same_source_only=False, recent_only=False):
        settings = self.state.settings
        budget = int(settings.context_budget * 0.6)
        rows = self.messages.window(turn.source, limit=200, before=turn.created + 1)
        digested_until = self.digests.last_end(turn.source)
        picked, used, previous = [], 0, None
        for row in rows:
            line = self.line(row, turn.id)
            if len(picked) >= MIN_LINES:
                if used + len(line) > budget:
                    break
                if previous and previous - row["created"] > GAP_SECONDS and len(picked) >= GAP_MIN_LINES:
                    break
                if row["created"] <= digested_until:
                    break
            picked.append((row, line))
            used += len(line)
            previous = row["created"]
        picked.reverse()
        participants = list(
            dict.fromkeys(r["user_id"] for r, _ in picked if not r["is_bot"])
        )
        start = picked[0][0]["created"] if picked else turn.created
        sections = []
        earlier = [] if recent_only else self.digests.before(turn.source, start, 2)
        for digest in reversed(earlier):
            sections.append(f"（更早 {_date(digest['start'])}～{_clock(digest['end'])}，摘要）{digest['summary']}")
        query = grams(turn.text + " " + " ".join(self.people.display(u) for u in participants[-3:]))
        old = [] if recent_only else self.digests.search(query, exclude=[d["id"] for d in earlier], limit=2,
                                  source=turn.source)
        for digest in old:
            if digest["end"] < start - 3600:
                sections.append(f"（之前聊过，{_date(digest['start'])}）{digest['summary']}")
        if not (same_source_only or recent_only):
            name = turn.name or self.people.display(turn.user_id)
            found = self.recent.find(turn.user_id, turn.source, turn.text, name=name)
            sections.extend(self.recent.render(found, name))
        sections.append("（最近）")
        sections.extend(line for _, line in picked)
        texts = [r["text"] for r, _ in picked if r["id"] != turn.id and r["text"]][-3:]
        return Window("\n".join(sections), participants, start, len(picked), texts)

    def recent_bot(self, source, limit=5):
        return self.messages.recent_bot(source, limit)

    @staticmethod
    def mentions_json(mentions):
        return json.dumps(mentions, ensure_ascii=False)
