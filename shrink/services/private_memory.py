import asyncio
import hashlib
import re
import time

from .recent import ASKED, private_match, topic_terms

NOTICE_KIND = "private_memory"
CONFIRM_SECONDS = 600
ACCOUNT = r"[1-9]\d{4,14}"
CONFIRMATION = re.compile(r"(确认转告|确认互授权|接受互授权|拒绝互授权|撤销互授权)\s+([0-9a-f]{12})")
SHARE = re.compile(r"(?:请|帮我)?转告\s*(?:QQ\s*)?(" + ACCOUNT + r")\s*[:：]\s*(.+)", re.S | re.I)
INVITE = re.compile(r"私聊互授权\s+(?:QQ\s*)?(" + ACCOUNT + r")", re.I)
SEARCH = re.compile(r"查共享(?:\s+(" + ACCOUNT + r"))?(?:\s+(.+))?", re.S)
NAMED_SEARCH = re.compile(r"(?:你和|关于|查找?|查询)\s*(?:QQ\s*)?(" + ACCOUNT + r")\s*的私聊", re.I)
COMMAND = re.compile(r"^(?:转告|私聊互授权|确认转告|确认互授权|接受互授权|拒绝互授权|撤销互授权|查共享)")
STATES = {"active": "已双向授权", "declined": "已拒绝，未开放私聊",
          "revoked": "已撤销，双方都不能再检索对方私聊", "expired": "已到期，未开放私聊"}


def excerpt(text, terms, limit=220):
    value = text.lower()
    hit = min((position for term in terms if (position := value.find(term)) >= 0), default=0)
    start = max(0, hit - 45) if hit >= limit - 40 else 0
    return ("…" if start else "") + text[start:start + limit] + ("…" if len(text) > start + limit else "")


class PrivateMemory:
    def __init__(self, database, messages, outbox, clock=time.time):
        self.db, self.messages, self.outbox, self.clock = database, messages, outbox, clock
        with self.db.transaction() as con:
            con.execute("""CREATE TABLE IF NOT EXISTS private_memory_consent(
                id TEXT PRIMARY KEY, instance TEXT NOT NULL, sender TEXT NOT NULL, recipient TEXT NOT NULL,
                kind TEXT NOT NULL, state TEXT NOT NULL, text TEXT NOT NULL, expires REAL NOT NULL,
                changed REAL NOT NULL)""")
            con.execute("""CREATE INDEX IF NOT EXISTS private_memory_active ON
                private_memory_consent(instance,state,sender,recipient)""")

    def address(self, instance, account):
        rows = self.db.rows(
            "SELECT id FROM conversation_sessions WHERE instance=? AND platform='qq' "
            "AND kind='private' AND external_id=? AND source=? AND id=?",
            (instance, account, "qq:private:" + account, f"{instance}:FriendMessage:{account}"))
        return {"session": rows[0]["id"], "sender": account} if rows else None

    def handle(self, instance, owner, session, text, input_identity):
        if not instance or not re.fullmatch(ACCOUNT, owner) or session != f"{instance}:FriendMessage:{owner}":
            return None
        text = re.sub(r"^(?:石英[，,：:\s]+)?/", "", text.strip(), count=1).strip()
        text = re.sub(r"^石英[，,：:\s]+", "", text, count=1)
        if match := SHARE.fullmatch(text):
            return self.propose(instance, owner, match[1], "share", match[2].strip(), input_identity)
        if match := INVITE.fullmatch(text):
            return self.propose(instance, owner, match[1], "grant", "", input_identity)
        if match := CONFIRMATION.fullmatch(text):
            return self.transition(instance, owner, match[2], match[1])
        if match := SEARCH.fullmatch(text):
            query = (match[2] or "").strip()
            if not query:
                return "用法：查共享 [QQ号] 关键词"
            return self.search(instance, owner, query, match[1]) or "没有查到匹配的共享记录。"
        if match := NAMED_SEARCH.search(text):
            query = (text[:match.start()] + " " + text[match.end():]).strip(" ：:，,？?。")
            if not topic_terms(query):
                return "用法：查共享 " + match[1] + " 关键词"
            return self.search(instance, owner, query, match[1]) or "没有查到匹配的共享记录。"
        if COMMAND.match(text):
            return "用法：转告 QQ号：内容 / 私聊互授权 QQ号 / 查共享 [QQ号] 关键词。确认或撤销时请附上对应编号。"
        if len(text) <= 160 and ASKED.search(text):
            return self.search(instance, owner, text)
        return None

    def propose(self, instance, sender, recipient, kind, text, input_identity):
        if recipient == sender or not self.address(instance, recipient):
            return "目标 QQ 不可用。请核对账号，并请对方先在这个石英实例发一条私聊。"
        if kind == "share" and not 0 < len(text) <= 1000:
            return "转告正文需为 1～1000 字。"
        now = self.clock()
        identity = hashlib.sha256(input_identity.encode()).hexdigest()[:12]
        with self.db.transaction() as con:
            con.execute("INSERT OR IGNORE INTO private_memory_consent VALUES(?,?,?,?,?,'draft',?,?,?)",
                        (identity, instance, sender, recipient, kind, text, now + CONFIRM_SECONDS, now))
        if kind == "share":
            return f"将只向 QQ {recipient} 转告以下内容：\n{text}\n\n如无误，10 分钟内回复：确认转告 {identity}"
        return (f"邀请 QQ {recipient} 互查双方与这个石英实例的私聊文本，包括历史。"
                f"对方也接受后才会开放，任一方可撤销。\n10 分钟内回复：确认互授权 {identity}")

    def notice(self, row, account, state):
        scope = self.address(row["instance"], account)
        if scope:
            self.outbox.submit(
                scope, f"private-memory:{row['id']}:{state}", {"consent": row["id"], "state": state},
                kind=NOTICE_KIND)

    def transition(self, instance, account, identity, action):
        now = self.clock()
        self.expire(now)
        with self.db.transaction() as con:
            found = con.execute("SELECT * FROM private_memory_consent WHERE instance=? AND id=?",
                                (instance, identity)).fetchone()
            if not found or account not in (found["sender"], found["recipient"]):
                return "没有可操作的共享或授权。"
            row = dict(found)
            state, sender, recipient = row["state"], row["sender"], row["recipient"]
            if account == sender and state == "draft" and action == "确认转告" and row["kind"] == "share":
                target, recipients = "shared", (recipient,)
                response = f"已登记转告 {identity}，等待 QQ 平台受理。"
            elif account == sender and state == "draft" and action == "确认互授权" and row["kind"] == "grant":
                exists = con.execute(
                    "SELECT id FROM private_memory_consent WHERE instance=? AND state IN ('pending','active') "
                    "AND ((sender=? AND recipient=?) OR (sender=? AND recipient=?))",
                    (instance, sender, recipient, recipient, sender)).fetchone()
                if exists:
                    return f"该账号已有待确认或已生效的互授权：{exists['id']}。"
                target, recipients = "pending", (recipient,)
                response = f"已登记邀请 {identity}，等对方接受后才会开放。"
            elif account == recipient and state == "pending" and action in {"接受互授权", "拒绝互授权"}:
                target = "active" if action == "接受互授权" else "declined"
                recipients, response = (sender, recipient), ""
            elif state in {"pending", "active"} and action == "撤销互授权":
                target, recipients, response = "revoked", (sender, recipient), ""
            else:
                return "该编号已处理、已到期，或不能由你执行这项操作。"
            con.execute("UPDATE private_memory_consent SET state=?,changed=?,expires=? WHERE id=?",
                        (target, now, now + CONFIRM_SECONDS if target == "pending" else 0, identity))
            for other in recipients:
                self.notice(row, other, target)
            return response

    def expire(self, now=None):
        now = self.clock() if now is None else now
        with self.db.transaction() as con:
            rows = con.execute("SELECT * FROM private_memory_consent WHERE state IN ('draft','pending') "
                               "AND expires<=?", (now,)).fetchall()
            for found in rows:
                row = dict(found)
                con.execute("UPDATE private_memory_consent SET state='expired',text='',changed=? WHERE id=?",
                            (now, row["id"]))
                if row["kind"] == "grant":
                    for account in ((row["sender"], row["recipient"]) if row["state"] == "pending"
                                    else (row["sender"],)):
                        self.notice(row, account, "expired")

    async def run(self):
        while True:
            self.expire()
            await asyncio.sleep(30)

    def render_notice(self, scope, facts):
        rows = self.db.rows("SELECT * FROM private_memory_consent WHERE id=?", (facts["consent"],))
        if not rows:
            return ""
        row = rows[0]
        owner, state = str(scope["sender"]), facts["state"]
        if (owner not in (row["sender"], row["recipient"]) or self.address(row["instance"], owner) != scope):
            return ""
        if state == "shared" and row["state"] == "shared" and owner == row["recipient"]:
            return f"QQ {row['sender']} 请我转告你：\n{row['text']}"
        if (state == "pending" and row["state"] == "pending" and owner == row["recipient"]
                and row["expires"] > self.clock()):
            return (f"QQ {row['sender']} 邀请你互查双方与石英的整个私聊文本历史。"
                    f"接受才会双向开放，之后可撤销。\n接受互授权 {row['id']}\n拒绝互授权 {row['id']}")
        if state in STATES:
            other = row["recipient"] if owner == row["sender"] else row["sender"]
            tail = f"\n撤销互授权 {row['id']}" if state == "active" else ""
            return (f"与 QQ {other} 的互授权（{row['id']}）：{STATES[state]}{tail}"
                    if state != "active" or row["state"] == "active" else "")
        return ""

    def search(self, instance, owner, query, peer=None):
        terms = sorted(topic_terms(query))
        if not terms:
            return None
        matches = " OR ".join("instr(lower(text),?)" for _ in terms)
        shares = self.db.rows(
            "SELECT sender,text,changed AS created FROM private_memory_consent WHERE instance=? "
            "AND state='shared' AND recipient=? AND (? IS NULL OR sender=?) "
            f"AND ({matches}) ORDER BY changed DESC LIMIT 40", (instance, owner, peer, peer, *terms))
        grants = self.db.rows(
            "SELECT CASE WHEN sender=? THEN recipient ELSE sender END AS peer "
            "FROM private_memory_consent WHERE instance=? AND state='active' AND (sender=? OR recipient=?)",
            (owner, instance, owner, owner))
        hits = [(row["created"], f"QQ {row['sender']} 转告", row["text"]) for row in shares
                if private_match(query, row["text"])]
        for grant in grants:
            other = grant["peer"]
            if peer and other != peer:
                continue
            address = self.address(instance, other)
            if not address:
                continue
            rows = self.db.rows(
                "SELECT m.* FROM conversation_events e JOIN messages m ON m.id=e.message_id "
                "WHERE e.session=? AND m.source=? AND (e.role='assistant' AND m.is_bot=1 "
                "OR e.role='user' AND m.user_id=?) AND e.kind IN ('message','corrected') "
                f"AND ({matches}) ORDER BY e.id DESC LIMIT 40",
                (address["session"], "qq:private:" + other, "qq:" + other, *terms))
            for row in self.messages.current(rows):
                if private_match(query, row["text"]):
                    speaker = "石英回复" if row["is_bot"] else "对方"
                    hits.append((row["created"], f"QQ {other} 私聊 · {speaker}", row["text"]))
        ranked = sorted(hits, key=lambda entry: (
            sum(term in entry[2].lower() for term in terms), entry[0]), reverse=True)
        return ("共享记录（仅你与石英的私聊可查）：\n" + "\n".join(
            f"{label}：{excerpt(text, terms)}" for _, label, text in ranked[:6])
                if ranked else None)
