import asyncio
import hashlib
import re
import time

from .recent import ASKED, private_match, topic_terms

NOTICE_KIND = "private_memory"
CONFIRM_SECONDS = 600
PAGE_SIZE = 6
ACCOUNT = r"[1-9]\d{4,14}"
CONFIRMATION = re.compile(r"(确认转告|确认互授权|接受互授权|拒绝互授权|撤销互授权)\s+([0-9a-f]{12})")
SHARE = re.compile(r"(?:请|帮我)?转告\s*(?:QQ\s*)?(" + ACCOUNT + r")\s*[:：]\s*(.+)", re.S | re.I)
INVITE = re.compile(r"私聊互授权\s+(?:QQ\s*)?(" + ACCOUNT + r")(?:\s+(全部记忆))?", re.I)
GRANT_REQUEST = re.compile(
    r"^(?:我想|我要|我需要|我希望|请|帮我|能不能|可以)(?:帮我)?\s*"
    r"(?:(?:和|跟|与).{1,35}?(?:共享|分享|互查|互通)(?=.{0,35}(?:私聊|记忆))"
    r"|(?:把|将).{0,35}?(?:私聊|记忆).{0,20}?(?:共享|分享)(?:给|给到)"
    r"|(?:开通|开启|申请)?\s*(?:双向授权|互授权))"
)
QQ_TARGET = re.compile(r"(?<![A-Za-z0-9])QQ\s*[：:]?\s*(" + ACCOUNT + r")(?!\d)", re.I)
SEARCH = re.compile(r"查共享(?:\s+(" + ACCOUNT + r"))?(?:\s+(.+))?", re.S)
PAGE = re.compile(r"(?:^|\s+)第\s*([1-9]\d{0,8})\s*页\s*$")
NAMED_SEARCH = re.compile(r"(?:你和|关于|查找?|查询)\s*(?:QQ\s*)?(" + ACCOUNT + r")\s*的私聊", re.I)
COMMAND = re.compile(r"^(?:转告|私聊互授权|确认转告|确认互授权|接受互授权|拒绝互授权|撤销互授权|查共享)")
STATES = {"active": "已双向授权", "declined": "已拒绝，未开放本次申请的范围",
          "revoked": "已撤销，双方都不能再检索对方私聊",
          "cancelled": "已撤回本次邀请", "expired": "已到期，未开放本次申请的范围"}
SCOPES = {
    "grant": "双方与这个石英实例的私聊原文（含历史）",
    "grant_memory": "双方与这个石英实例的私聊原文（含历史）及有同一私聊来源的有效整理记忆",
}
INVITE_HELP = "请发「私聊互授权 对方QQ号 全部记忆」；只互查私聊原文就省略「全部记忆」。双方确认后才生效。"
SEARCH_HELP = "用法：查共享 [QQ号] [关键词] [第N页]。不写关键词可浏览已共享的记录。"


def excerpt(text, terms, limit=220):
    value = text.lower()
    hit = min((position for term in terms if (position := value.find(term)) >= 0), default=0)
    start = max(0, hit - 45) if hit >= limit - 40 else 0
    return ("…" if start else "") + text[start:start + limit] + ("…" if len(text) > start + limit else "")


class PrivateMemory:
    def __init__(self, database, outbox, clock=time.time):
        self.db, self.outbox, self.clock = database, outbox, clock
        with self.db.transaction() as con:
            con.execute("""CREATE TABLE IF NOT EXISTS private_memory_consent(
                id TEXT PRIMARY KEY, instance TEXT NOT NULL, sender TEXT NOT NULL, recipient TEXT NOT NULL,
                kind TEXT NOT NULL, state TEXT NOT NULL, text TEXT NOT NULL, expires REAL NOT NULL,
                changed REAL NOT NULL)""")
            con.execute("""CREATE INDEX IF NOT EXISTS private_memory_active ON
                private_memory_consent(instance,state,sender,recipient)""")
            con.execute("""CREATE INDEX IF NOT EXISTS private_memory_revision ON
                conversation_events(target) WHERE target!=''""")
            con.execute("""CREATE INDEX IF NOT EXISTS private_memory_evidence ON
                conversation_events(message_id) WHERE message_id IS NOT NULL""")

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
            kind = "grant_memory" if match[2] else "grant"
            return self.propose(instance, owner, match[1], kind, "", input_identity)
        if match := CONFIRMATION.fullmatch(text):
            return self.transition(instance, owner, match[2], match[1])
        if match := SEARCH.fullmatch(text):
            query = (match[2] or "").strip()
            page = 1
            if marker := PAGE.search(query):
                page = int(marker[1])
                query = query[:marker.start()].strip()
            return self.search(instance, owner, query, match[1], page=page) or "没有查到匹配的共享记录。"
        if GRANT_REQUEST.search(text) or text in {"双向授权", "互授权"}:
            targets = set(QQ_TARGET.findall(text))
            if len(targets) == 1:
                scope = ("grant_memory" if re.search(r"(?:全部|所有|全量)(?:的)?(?:私聊)?记忆", text)
                         else "grant")
                return self.propose(instance, owner, targets.pop(), scope, "", input_identity)
            return INVITE_HELP
        if match := NAMED_SEARCH.search(text):
            query = (text[:match.start()] + " " + text[match.end():]).strip(" ：:，,？?。")
            if not topic_terms(query):
                return f"发「查共享 {match[1]}」可浏览；也可以在后面加关键词。"
            return self.search(instance, owner, query, match[1], page=1) or "没有查到匹配的共享记录。"
        if COMMAND.match(text):
            return f"{INVITE_HELP}\n{SEARCH_HELP}\n转告用「转告 QQ号：内容」；确认或撤销需附编号。"
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
        return (f"邀请 QQ {recipient} 互查{SCOPES[kind]}。仅在各自与我的私聊中可查。"
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
            elif account == sender and state == "draft" and action == "确认互授权" and row["kind"] in SCOPES:
                exists = con.execute(
                    "SELECT id FROM private_memory_consent WHERE instance=? AND state IN ('pending','active') "
                    "AND (?='grant' OR kind='grant_memory') "
                    "AND ((sender=? AND recipient=?) OR (sender=? AND recipient=?))",
                    (instance, row["kind"], sender, recipient, recipient, sender)).fetchone()
                if exists:
                    return f"该账号已有待确认或已生效的互授权：{exists['id']}。"
                target, recipients = "pending", (recipient,)
                response = f"已登记邀请 {identity}，等对方接受后才会开放。"
            elif account == recipient and state == "pending" and action in {"接受互授权", "拒绝互授权"}:
                target = "active" if action == "接受互授权" else "declined"
                recipients, response = (sender, recipient), ""
            elif state in {"pending", "active"} and action == "撤销互授权":
                target = "revoked" if state == "active" else "cancelled"
                recipients, response = (sender, recipient), ""
            else:
                return "该编号已处理、已到期，或不能由你执行这项操作。"
            con.execute("UPDATE private_memory_consent SET state=?,changed=?,expires=? WHERE id=?",
                        (target, now, now + CONFIRM_SECONDS if target == "pending" else 0, identity))
            if target == "revoked":
                con.execute(
                    "UPDATE private_memory_consent SET state='revoked',changed=?,expires=0 "
                    "WHERE instance=? AND state IN ('active','pending') AND kind IN ('grant','grant_memory') "
                    "AND ((sender=? AND recipient=?) OR (sender=? AND recipient=?))",
                    (now, instance, sender, recipient, recipient, sender))
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
                if row["kind"] in SCOPES:
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
            return (f"QQ {row['sender']} 邀请你互查{SCOPES[row['kind']]}。仅在各自与我的私聊中可查。"
                    f"接受才会双向开放，之后可撤销。\n接受互授权 {row['id']}\n拒绝互授权 {row['id']}")
        if state in STATES:
            other = row["recipient"] if owner == row["sender"] else row["sender"]
            tail = (f"：{SCOPES[row['kind']]}\n发「查共享 {other}」浏览，或加关键词搜索。"
                    f"\n撤销互授权 {row['id']}" if state == "active" else "")
            return (f"与 QQ {other} 的互授权（{row['id']}）：{STATES[state]}{tail}"
                    if state != "active" or row["state"] == "active" else "")
        return ""

    def search(self, instance, owner, query, peer=None, *, page=None):
        query = query[:200]
        terms = sorted(topic_terms(query))
        if page is not None and query:
            terms = list(dict.fromkeys([query.lower(), *terms]))
        if not terms and page is None:
            return None
        page_number = page or 1
        keywords = {f"term{index}": term for index, term in enumerate(terms)}
        score = " + ".join(f"(instr(lower(text),:{key})>0)" for key in keywords) or "1"
        params = dict(owner=owner, instance=instance, peer=peer, now=self.clock(),
                      limit=PAGE_SIZE + 1, offset=(page_number - 1) * PAGE_SIZE, **keywords)
        rows = self.db.rows(f"""
            WITH granted AS (
                SELECT CASE WHEN sender=:owner THEN recipient ELSE sender END AS account,
                       kind='grant_memory' AS include_memory
                FROM private_memory_consent
                WHERE instance=:instance AND state='active' AND kind IN ('grant','grant_memory')
                      AND (sender=:owner OR recipient=:owner)
            ), sessions AS (
                SELECT s.id,s.source,g.account,max(g.include_memory) AS include_memory
                FROM granted g JOIN conversation_sessions s
                  ON s.id=:instance || ':FriendMessage:' || g.account
                WHERE (:peer IS NULL OR g.account=:peer) AND s.instance=:instance AND s.platform='qq'
                      AND s.kind='private' AND s.external_id=g.account AND s.source='qq:private:' || g.account
                GROUP BY s.id,s.source,g.account
            ), private_text AS (
                SELECT DISTINCT m.id,m.created,m.text,m.is_bot,s.account
                FROM sessions s JOIN conversation_events e ON e.session=s.id
                JOIN messages m ON m.id=e.message_id AND m.source=s.source
                WHERE e.kind IN ('message','corrected') AND
                      ((e.role='assistant' AND m.is_bot=1) OR
                       (e.role='user' AND m.is_bot=0 AND m.user_id='qq:' || s.account))
                      AND NOT EXISTS (SELECT 1 FROM conversation_events revision
                          WHERE revision.target!='' AND revision.target=m.id
                                AND revision.kind IN ('retracted','corrected'))
            ), memory AS (
                SELECT m.*,s.account,
                       CASE WHEN json_valid(m.evidence) THEN m.evidence ELSE '[]' END AS refs
                FROM sessions s JOIN memories m ON m.source=s.source
                WHERE s.include_memory=1 AND m.status='active' AND m.origin IN ('explicit','digest')
                      AND (m.valid_until IS NULL OR m.valid_until>:now)
            ), records AS (
                SELECT id,changed AS created,text,sender AS account,'转告' AS label
                FROM private_memory_consent
                WHERE instance=:instance AND state='shared' AND recipient=:owner
                      AND (:peer IS NULL OR sender=:peer)
                UNION ALL
                SELECT id,created,text,account,CASE WHEN is_bot THEN '私聊 · 石英回复' ELSE '私聊 · 对方' END
                FROM private_text
                UNION ALL
                SELECT id,updated,text,account,'整理记忆' FROM memory
                WHERE json_array_length(refs)>0 AND NOT EXISTS (
                    SELECT 1 FROM json_each(refs) AS ref
                    WHERE NOT EXISTS (SELECT 1 FROM private_text p WHERE p.account=memory.account AND p.id=ref.value)
                      OR EXISTS (SELECT 1 FROM conversation_events other
                          WHERE other.message_id=ref.value
                                AND other.session!=:instance || ':FriendMessage:' || memory.account)
                )
            ), scored AS (
                SELECT *,({score}) AS score FROM records WHERE trim(text)!=''
            )
            SELECT text,account,label FROM scored WHERE score>0
            ORDER BY score DESC,created DESC,account,label,id
            LIMIT :limit OFFSET :offset
            """, params)
        if not rows:
            return None
        header = f"共享记录 · 第 {page_number} 页（仅私聊可查）" if page is not None else "共享记录（仅私聊可查）"
        lines = [header, *(f"QQ {row['account']} {row['label']}：{excerpt(row['text'], terms)}"
                           for row in rows[:PAGE_SIZE])]
        if len(rows) > PAGE_SIZE:
            next_query = " ".join(part for part in ("查共享", peer, query, f"第{page_number + 1}页") if part)
            lines.append(f"还有记录，下一页：{next_query}")
        return "\n".join(lines)
