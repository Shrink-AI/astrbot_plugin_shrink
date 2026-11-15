"""Role-preserving context with bounded, referenced compression."""
import asyncio
import json
import re
import time
import uuid

from ..persistence.execution_context import checkpoint_value
from ..prompts import render, text as prompt_text
from .scheduling import in_lane
from .models import for_source


class ConversationHistory:
    def __init__(self, repository, messages, model, error):
        self.repository, self.messages = repository, messages
        self.model, self.error = model, error
        self.timeout = 180
        self.retry_delays = (2, 4)
        self.spawn = self.scheduling = None
        self.worker = None
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
        text = json.dumps(message, ensure_ascii=False)
        cjk = sum("\u4e00" <= ch <= "\u9fff" for ch in text)
        emoji = len(re.findall("[\u200d\u2600-\u27bf\ufe00-\ufe0f\U0001f000-\U0001faff]", text))
        return max(1, int(cjk * .6 + emoji * 2 + (len(text) - cjk - emoji) * .3))

    @staticmethod
    def tail(items, budget):
        used, cut = 0, len(items)
        for index in range(len(items) - 1, -1, -1):
            cost = ConversationHistory.size(items[index][1])
            if used + cost > budget and cut < len(items):
                break
            used += cost
            cut = index
        # Keep the newest complete block, including a single oversized message.
        while 0 < cut < len(items) and items[cut][1]["role"] == "tool":
            cut -= 1
        while (0 < cut < len(items) and items[cut][0].get("_block") is not None
               and items[cut][0].get("_block") == items[cut - 1][0].get("_block")):
            cut -= 1
        return cut

    async def contexts(self, session, budget, *, exclude=(), retain_ratio=.65):
        # No await between reading and persisting the boundary: concurrent requests on
        # the owning event loop see the same committed window, without a model lock.
        rows = self.repository.events(session)
        items = [(r, m) for r, m in self.render(rows) if r["message_id"] not in exclude]
        checkpoint = self.repository.window(session)
        if checkpoint:
            items = [(r, m) for r, m in items if r["id"] > checkpoint["end_event"]]
        # Reserve the same summary capacity before and after it lands, so its arrival
        # cannot itself trigger another boundary change.
        reserved = checkpoint["summary_tokens"] if checkpoint else 0
        if sum(self.size(m) for _, m in items) + reserved >= budget:
            cut = self.tail(items, max(1, int(budget * retain_ratio)))
            boundary = min((r["id"] for r in rows if r["message_id"] in exclude), default=float("inf"))
            cut = min(cut, next((i for i, (r, _) in enumerate(items) if r["id"] >= boundary), len(items)))
            while (0 < cut < len(items) and items[cut][0].get("_block") is not None
                   and items[cut][0].get("_block") == items[cut - 1][0].get("_block")):
                cut -= 1
            covered, tail = items[:cut], items[cut:]
            if covered:
                refs = list(dict.fromkeys(
                    (json.loads(checkpoint["message_refs"]) if checkpoint else []) +
                    [r["message_id"] for r, _ in covered if r["message_id"]]))
                checkpoint = self.repository.reserve(
                    session, checkpoint["start_event"] if checkpoint else covered[0][0]["id"],
                    max(r["id"] for r, _ in covered), refs, budget, max(1, min(4000, int(budget * .2))))
                items = tail
                self.schedule()
        return ([self.summary_message(checkpoint)] if checkpoint else []) + [m for _, m in items]

    def start(self):
        self.repository.recover_summaries(len(self.retry_delays) + 1)
        self.schedule()

    def schedule(self):
        if self.spawn and (self.worker is None or self.worker.done()) and self.repository.pending():
            self.worker = self.spawn(self.summarize_pending())

    @in_lane("maintenance")
    async def summarize_pending(self):
        if self.scheduling:
            async with self.scheduling.use("maintenance"):
                await self._summarize_pending()
        else:
            await self._summarize_pending()

    @staticmethod
    def summary_input_limit(checkpoint):
        # A full checkpoint window covers every allowed retention ratio without
        # depending on later settings changes; allow summary and rendering overhead.
        budget = checkpoint["window_budget"]
        return max(32000, budget + checkpoint["summary_tokens"] + max(4000, (budget + 9) // 10))

    def compression_prompt(self, checkpoint, previous, covered, system):
        input_limit = self.summary_input_limit(checkpoint)
        allowance = max(1, checkpoint["summary_tokens"] - self.size(self.summary_message(
            {**checkpoint, "summary": ""})))
        # Only block boundaries are candidates: never separate a tool call/result,
        # even when a single block exceeds the entire summary input allowance.
        starts = [i for i, (row, _) in enumerate(covered)
                  if i == 0 or row.get("_block", row["id"]) != covered[i - 1][0].get(
                      "_block", covered[i - 1][0]["id"])] + [len(covered)]
        prefix = [self.summary_message(previous)] if previous else []

        def build(index):
            omitted = index > 0 or (previous is not None and not prefix)
            material = ([{"role": "user", "content":
                         "[前面还有未包含的更早记录；可通过本检查点的原文引用取回。]"}] if omitted else [])
            material += prefix + [m for _, m in covered[starts[index]:]]
            return render("history.compress", messages=json.dumps(material, ensure_ascii=False),
                          max_tokens=allowance)

        def fits(prompt):
            return (self.size({"role": "user", "content": prompt}) +
                    self.size({"role": "system", "content": system})) <= input_limit

        full = build(0)
        if fits(full):
            return full
        # Prefer recent originals if an older summary would leave no room for the
        # newest complete block. Its originals remain in this checkpoint's refs.
        if prefix and not fits(build(max(0, len(starts) - 2))):
            prefix = []
        low, high = 0, len(starts) - 1
        while low < high:
            middle = (low + high) // 2
            if fits(build(middle)):
                high = middle
            else:
                low = middle + 1
        prompt = build(low)
        if not fits(prompt):
            raise ValueError("history compression template exceeds input budget")
        return prompt

    async def _summarize_pending(self):
        while pending := self.repository.pending():
            checkpoint = pending[0]
            delay = checkpoint["summary_next_run"] - time.time()
            if delay > 0:
                await asyncio.sleep(min(delay, 4))
                continue
            claim = uuid.uuid4().hex
            if not self.repository.claim_summary(checkpoint["id"], claim):
                continue
            try:
                previous = self.repository.previous_summary(checkpoint)
                rows = self.repository.events(checkpoint["session"])
                covered = [(r, m) for r, m in self.render(rows)
                           if (previous["end_event"] if previous else checkpoint["start_event"] - 1)
                           < r["id"] <= checkpoint["end_event"]]
                system = prompt_text("history.compress_system")
                prompt = self.compression_prompt(checkpoint, previous, covered, system)
                result = await asyncio.wait_for(
                    for_source(self.model, checkpoint["session"])(prompt, system), self.timeout)
                if not result or not result.strip():
                    raise ValueError("empty conversation summary")
                text = result.strip()
                low, high = 0, len(text)
                while low < high:
                    middle = (low + high + 1) // 2
                    if self.size(self.summary_message({**checkpoint, "summary": text[:middle]})) <= (
                            checkpoint["summary_tokens"]):
                        low = middle
                    else:
                        high = middle - 1
                self.repository.finish_summary(checkpoint["id"], claim, summary=text[:low])
            except Exception as exc:
                self.error("history_compression", exc)
                attempt = checkpoint["summary_attempts"]
                retry = attempt < len(self.retry_delays)
                self.repository.finish_summary(
                    checkpoint["id"], claim, state="pending" if retry else "failed",
                    next_run=time.time() + self.retry_delays[attempt] if retry else 0)

    @staticmethod
    def summary_message(checkpoint):
        return {"role": "user", "content":
                f"[会话压缩检查点 {checkpoint['id']}，事件 {checkpoint['start_event']}～{checkpoint['end_event']}；"
                f"用 shrink_chat_history(keyword='checkpoint:{checkpoint['id']}') 取回原文引用和细节]\n"
                + checkpoint["summary"]}
