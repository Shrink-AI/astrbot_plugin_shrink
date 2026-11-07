"""What 石英 can look up on its own during a reply: chat history, people and memories, stickers,
and a Python sandbox. Every answer is plain text sized for a model's tool result."""

import asyncio
import datetime
import json
import time
import urllib.error
import urllib.request

from ..config import BOT_NAME
from .memory import Query

HISTORY_LINES = 18
HISTORY_DAYS_MAX = 30
LINE_LIMIT = 200
OUTPUT_LIMIT = 3000


def _stamp(ts):
    value = datetime.datetime.fromtimestamp(ts)
    return f"{value.month}-{value.day} {value:%H:%M}"


def _clip(text, limit=LINE_LIMIT):
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= limit else text[:limit] + "…"


class ToolService:
    def __init__(self, *, messages, digests, people, memory, memes, state, error):
        self.messages, self.digests, self.people = messages, digests, people
        self.memory, self.memes, self.state, self.error = memory, memes, state, error

    # ---- people ------------------------------------------------------------------------------------
    def resolve(self, name, source):
        """uids a name may refer to here: exact names first, then partial and global matches."""
        name = (name or "").strip().lstrip("@").strip()
        if not name:
            return []
        if name.isdigit():
            uid = f"qq:{name}"
            return [uid] if self.people.repository.get(uid) else []
        if name in (BOT_NAME, "你", "你自己"):
            return []
        book = self.people.address_book(source)
        exact = [uid for n, uid in book.items() if n == name]
        if exact:
            return list(dict.fromkeys(exact))
        nickname = self.people.repository.nickname_by_name(source, name)
        if nickname:
            return [nickname["user_id"]]
        partial = [uid for n, uid in book.items() if name in n or (len(n) >= 2 and n in name)]
        if partial:
            return list(dict.fromkeys(partial))[:3]
        return [r["id"] for r in self.people.repository.search(q=name, limit=3)]

    def _line(self, row):
        if row["is_bot"]:
            speaker = f"{BOT_NAME}（你）"
        else:
            speaker = row.get("person_name") or row["speaker_name"] or row["user_id"]
        text = _clip(row["text"]) + (" [图片]" if row.get("has_image") else "")
        return f"[{_stamp(row['created'])}] {speaker}: {text.strip() or '[消息]'}"

    # ---- chat history ------------------------------------------------------------------------------
    def history(self, source, keyword="", person="", days=3):
        try:
            days = float(days or 3)
        except (TypeError, ValueError):
            days = 3
        days = min(HISTORY_DAYS_MAX, max(0.05, days))
        since = time.time() - days * 86400
        keyword = (keyword or "").strip()[:40]
        uid = ""
        notes = []
        if person:
            found = self.resolve(person, source)
            if not found:
                return f"群里没找到叫“{person}”的人。"
            uid = found[0]
            if len(found) > 1:
                notes.append(
                    "“" + person + "”可能是：" + "、".join(self.people.display(u) for u in found)
                    + f"，下面按{self.people.display(uid)}查"
                )
        rows = self.messages.search(source, keyword, uid, since, HISTORY_LINES * 2)
        rows = [r for r in rows if not (keyword and r["is_bot"])]  # Echoes of itself add nothing.
        lines = []
        if keyword:
            hits = rows[:6]
            # Neighbours explain a hit (“那个链接” is often the line right before or after).
            context = self.messages.around([r["id"] for r in hits], before=1, after=1, span=600)
            for r in context:
                lines.append(("→ " if r["cited"] else "   ") + self._line(r))
            for r in rows[6:HISTORY_LINES]:
                lines.append("→ " + self._line(r))
        else:
            lines = [self._line(r) for r in reversed(rows[:HISTORY_LINES])]
        summaries = []
        if not keyword and not uid:
            for digest in self.digests.overlapping(source, since, 6):
                summaries.append(
                    f"（{_stamp(digest['start'])}～{_stamp(digest['end'])} 摘要）{digest['summary']}"
                )
        if not lines and not summaries:
            span = f"最近{days:g}天" if days >= 1 else f"最近{round(days * 24)}小时"
            what = f"提到“{keyword}”的" if keyword else ""
            who = self.people.display(uid) if uid else ""
            return f"{span}没有{who}{what}消息。"
        parts = notes + summaries
        if lines:
            parts.append(("（→ 是命中的消息）\n" if keyword else "") + "\n".join(lines))
        return "\n".join(parts)[:OUTPUT_LIMIT]

    # ---- people and memories -----------------------------------------------------------------------
    async def lookup(self, query, *, source, speaker, private):
        query = (query or "").strip()[:100]
        if not query:
            return "要查谁或什么？"
        uids = self.resolve(query, source) if len(query) <= 16 else []
        uids += [u for u in self.people.mentioned(query, source) if u not in uids]
        uids = uids[:2]
        parts = []
        for uid in uids:
            card = self.people.card(uid, "这个人", source)
            if card:
                parts.append(card + f"（QQ {uid.split(':')[-1]}）")
            said = self.messages.search(source, "", uid, 0, 4)
            if said:
                parts.append("TA最近在这儿说过：\n" + "\n".join(self._line(r) for r in reversed(said)))
        rendered, rows = await self.memory.recall_for(
            Query(text=query, source=source, speaker=speaker, mentioned=uids, private=private),
            budget=1500, limit=10,
        )
        if rendered:
            parts.append(rendered)
        if not parts:
            return f"没有关于“{query}”的记忆。"
        return "\n".join(parts)[:OUTPUT_LIMIT]

    # ---- stickers ----------------------------------------------------------------------------------
    def find_meme(self, keyword):
        """Best enabled sticker for a keyword; title matches beat tag matches, fresh beats stale."""
        if not self.state.settings.meme_enabled:
            return None
        keyword = (keyword or "").strip()[:30]
        if not keyword:
            return None
        rows = self.memes.repository.search(keyword, enabled=1)
        if not rows:
            rows = [
                r for word in keyword.replace("，", " ").split()
                for r in self.memes.repository.search(word, enabled=1)
            ]
        if not rows:
            return None
        rows.sort(key=lambda r: (keyword not in r["title"], r["last_sent"]))
        return rows[0]

    # ---- python sandbox ----------------------------------------------------------------------------
    async def run_python(self, code):
        code = code or ""
        if not code.strip():
            return "代码是空的。"
        if len(code) > 8000:
            return "代码太长了，精简一下再跑。"
        url = self.state.settings.sandbox_url.rstrip("/") + "/run"
        body = json.dumps({"code": code}).encode()

        def call():
            request = urllib.request.Request(
                url, body, {"Content-Type": "application/json"}, method="POST"
            )
            with urllib.request.urlopen(request, timeout=25) as response:
                return json.load(response)

        try:
            result = await asyncio.to_thread(call)
        except urllib.error.HTTPError as exc:
            if exc.code == 503:
                return "沙箱正忙，等一下再跑。"
            self.error("sandbox", exc)
            return "沙箱拒绝了这段代码。"
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self.error("sandbox", exc)
            return "沙箱现在连不上，没法跑代码。"
        out = []
        if result.get("stdout"):
            out.append(result["stdout"])
        if result.get("stderr"):
            out.append("stderr:\n" + result["stderr"])
        if result.get("timeout"):
            out.append(f"超时了（{result.get('limit', 10)} 秒），被终止。")
        elif result.get("exit") not in (0, None):
            out.append(f"退出码 {result['exit']}")
        if not out:
            out.append("（没有输出，记得用 print 打印结果）")
        return "\n".join(out)[:OUTPUT_LIMIT]
