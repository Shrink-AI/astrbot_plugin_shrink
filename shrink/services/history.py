"""Role-preserving context with bounded, referenced compression."""
import asyncio
import json

from ..persistence.execution_context import checkpoint_value
from ..prompts import render, text as prompt_text


class ConversationHistory:
    def __init__(self, repository, messages, model, error):
        self.repository, self.messages = repository, messages
        self.model, self.error = model, error
        self.timeout = 3
        self.locks = {}

    def sent(self, session, source, text, sender="", reason="", *, identity):
        message_id = "sent:" + identity
        with self.repository.db.transaction():
            self.messages.raw(message_id, source, text, sender="self", is_bot=True, addressee=sender)
            self.repository.append(session, "message:" + message_id, "assistant", message_id, speaker="self")
        return message_id

    def tool_start(self, session, identity, name, arguments):
        calls = [{"id": identity, "type": "function",
                  "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}]
        self.repository.append(session, "call:" + identity, "assistant", tool_calls=calls, kind="tool_call")

    def tool_end(self, session, identity, text):
        message_id = "tool:" + identity
        with self.repository.db.transaction():
            self.messages.raw(message_id, session, text, sender="tool")
            self.repository.append(session, message_id, "tool", message_id, tool_call_id=identity)

    def revise(self, session, message_id, *, sender, identity, text=None, message_ref=None):
        originals = [r for r in self.repository.events(session) if r["message_id"] == message_id]
        if not originals or originals[0]["speaker"] != sender:
            raise ValueError("只能撤回或纠正当前会话中自己的消息")
        state = "retracted" if text is None else "corrected"
        reference = message_ref or "revision:" + identity
        with self.repository.db.transaction():
            if not message_ref:
                self.messages.raw(reference, session, text or "", sender=sender)
            self.repository.append(session, reference, "user", reference, speaker=sender,
                                   kind=state, target=message_id)
            self.repository.invalidate(session)

    def references(self, session, refs):
        allowed = {r["message_id"] for r in self.repository.events(session)}
        if not set(refs) <= allowed:
            raise ValueError("引用不属于当前会话")
        return self.messages.get_many(refs)

    def read_checkpoint(self, session, identity):
        row = self.repository.checkpoint(session, identity)
        if not row:
            raise ValueError("当前会话不存在这个压缩检查点")
        return {"checkpoint": row, "originals": self.references(session, json.loads(row["message_refs"]))}

    @staticmethod
    def render(rows):
        changes = {r["target"]: r for r in rows if r["kind"] in {"corrected", "retracted"}}
        results = {r["tool_call_id"] for r in rows if r["role"] == "tool"}
        rendered = []
        for row in rows:
            text = row["text"] or ""
            if row["message_id"] in changes:
                text = f"[消息 {row['message_id']} 已撤回或被纠正；旧内容不再作为当前事实]"
            if row["kind"] in {"corrected", "retracted"}:
                if changes[row["target"]]["id"] != row["id"]:
                    text = f"[对 {row['target']} 的此前纠正已被后续状态取代]"
                else:
                    text = f"[{row['target']} {'已撤回' if row['kind'] == 'retracted' else '纠正为'}] {text}"
            message = {"role": row["role"], "content": checkpoint_value(text)}
            if row["role"] == "user":
                message["content"] = f"[发言人 {row['speaker']} · {row['speaker_name'] or row['speaker']}]\n{text}"
            if row["tool_calls"]:
                calls = json.loads(row["tool_calls"])
                if not all(call["id"] in results for call in calls):
                    message = {"role": "user", "content": "[工具结果未知，先核对副作用，不能盲目重做] " +
                               json.dumps(calls, ensure_ascii=False)}
                else:
                    message["tool_calls"] = calls
            if row["tool_call_id"]:
                message["tool_call_id"] = row["tool_call_id"]
            rendered.append((row, message))
        ordered, index = [], 0
        while index < len(rendered):
            row, message = rendered[index]
            calls = {c["id"] for c in message.get("tool_calls", [])}
            if not calls:
                ordered.append((row, message))
                index += 1
                continue
            end = index + 1
            remaining = set(calls)
            while end < len(rendered) and remaining:
                remaining.discard(rendered[end][1].get("tool_call_id"))
                end += 1
            block = rendered[index:end]
            # Arrivals during a tool call stay in the log; providers require adjacent call/results.
            outputs = [item for item in block[1:] if item[1].get("tool_call_id") in calls]
            arrivals = [item for item in block[1:] if item[1].get("tool_call_id") not in calls]
            ordered.extend(({**r, "_block": row["id"]}, m) for r, m in [block[0], *outputs, *arrivals])
            index = end
        return ordered

    @staticmethod
    def size(message):
        return len(json.dumps(message, ensure_ascii=False))

    @staticmethod
    def tail(items, budget):
        used, cut = 0, len(items)
        for index in range(len(items) - 1, -1, -1):
            cost = ConversationHistory.size(items[index][1])
            if used + cost > budget:
                break
            used += cost
            cut = index
        # Never orphan a tool result by cutting between the call and its result.
        while cut < len(items) and items[cut][1]["role"] == "tool":
            cut += 1
        while (0 < cut < len(items) and items[cut][0].get("_block") is not None
               and items[cut][0].get("_block") == items[cut - 1][0].get("_block")):
            cut += 1
        return cut

    async def contexts(self, session, budget, *, exclude=()):
        lock = self.locks.setdefault(session, asyncio.Lock())
        async with lock:
            rows = self.repository.events(session)
            items = [(r, m) for r, m in self.render(rows) if r["message_id"] not in exclude]
            checkpoint = self.repository.checkpoint(session)
            if checkpoint:
                items = [(r, m) for r, m in items if r["id"] > checkpoint["end_event"]]
            summary = self.summary_message(checkpoint) if checkpoint else None
            size = sum(self.size(m) for _, m in items) + (self.size(summary) if summary else 0)
            if size <= budget:
                return ([summary] if summary else []) + [m for _, m in items]
            cut = self.tail(items, max(0, int(budget * .6) - 240))
            boundary = min((r["id"] for r in rows if r["message_id"] in exclude), default=float("inf"))
            cut = min(cut, next((index for index, (r, _) in enumerate(items) if r["id"] >= boundary),
                                len(items)))
            while (0 < cut < len(items) and items[cut][0].get("_block") is not None
                   and items[cut][0].get("_block") == items[cut - 1][0].get("_block")):
                cut -= 1
            covered, tail = items[:cut], items[cut:]
            if covered:
                prompt = render("history.compress", messages=json.dumps(
                    ([summary] if summary else []) + [m for _, m in covered], ensure_ascii=False))
                try:
                    result = await asyncio.wait_for(self.model(prompt, prompt_text("history.compress_system")),
                                                    self.timeout)
                    if not result or not result.strip():
                        raise ValueError("empty conversation summary")
                    latest = self.repository.events(session)
                    if any(r["id"] > rows[-1]["id"] and r["kind"] in {"corrected", "retracted"} for r in latest):
                        current = [(r, m) for r, m in self.render(latest) if r["message_id"] not in exclude]
                        return [m for _, m in current[self.tail(current, budget):]]
                    refs = list(dict.fromkeys(
                        (json.loads(checkpoint["message_refs"]) if checkpoint else []) +
                        [r["message_id"] for r, _ in covered if r["message_id"]]))
                    self.repository.compress(session, checkpoint["start_event"] if checkpoint else covered[0][0]["id"],
                                             max(r["id"] for r, _ in covered),
                                             result.strip()[:max(1, int(budget * .25))], refs)
                    summary = self.summary_message(self.repository.checkpoint(session))
                    kept = self.tail(tail, max(0, budget - self.size(summary)))
                    return [summary] + [m for _, m in tail[kept:]]
                except Exception as exc:
                    self.error("history_compression", exc)
            # Originals remain queryable even when no summary fits or the summary model fails.
            cut = self.tail(items, budget)
            return [m for _, m in items[cut:]]

    @staticmethod
    def summary_message(checkpoint):
        return {"role": "user", "content":
                f"[会话压缩检查点 {checkpoint['id']}，事件 {checkpoint['start_event']}～{checkpoint['end_event']}；"
                f"用 shrink_chat_history(keyword='checkpoint:{checkpoint['id']}') 取回原文引用和细节]\n"
                + checkpoint["summary"]}
