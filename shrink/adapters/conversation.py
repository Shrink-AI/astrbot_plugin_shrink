"""AstrBot event hooks ⇄ Turn. Logic lives in the services; this only translates and wires."""

import asyncio
import hashlib
import random
import re
import time
from pathlib import Path

from astrbot.api import logger
from astrbot.api.message_components import (
    At,
    BaseMessageComponent,
    ComponentType,
    Image,
    Plain,
    Reply,
)
from astrbot.core.message.message_event_result import MessageChain

from ..config import BOT_NAME, fixed_persona
from ..prompts import render
from ..prompts import text as prompt_text
from ..services.expression import choose_form, settle_meme, wants_detail
from ..services.memes import MAX_IMAGE_BYTES
from ..services.memory import Query
from ..services.participation import should_quote
from ..services.perception import SELF, Turn, address
from ..services.reply_text import drop_meta_sentences, humanize, strip_reasoning
from ..services.skills import tool_step
from ..services.debounce import Debouncer
from ..services.task_context import CURRENT_TASK
from ..services.task_reporting import execution_report
from ..services.turn_control import is_stop_request, is_task_followup, is_revocation
from ..services.turn_trace import TurnTrace
from ..services.decisions import DecisionContext, guidance as decision_guidance
from ..services.scheduling import in_lane, LANE
from ..persistence.turn_traces import TurnTraceRepository
from .foreground import active_runner, release_follow_ups, request_stop, restrict
from .notifications import Notifications

REPLY_REASONS = {"direct", "engaged", "spontaneous"}
# The foreground has a short budget; authorized tool work continues in native subagents.
REPLY_DEADLINE = None
# People rarely attach a sticker to most messages; only sometimes offer the sticker list.
MEME_OFFER = 0.35
EXPLICIT_MEME = re.compile(r"表情|反应图|来张图|发张图")
# Tools that only shape a chat reply; using them does not turn the answer into a task result.
CHAT_TOOLS = {"shrink_find_meme"}


class QQSticker(BaseMessageComponent):
    # The Image adapter drops sub_type; a native segment preserves QQ sticker semantics.
    type: ComponentType = ComponentType.Image
    file: str
    sub_type: int = 1
    summary: str = "[表情]"


class ConversationAdapter:
    def __init__(self, app, plugin_module, umos=None, host_quotes=None, router_prompt=None, catalog=None):
        self.app = app
        self.plugin_module = plugin_module
        self.umos = umos if umos is not None else {}
        # The host's own reply_with_quote setting quotes every reply after our hook runs.
        self.host_quotes = host_quotes or (lambda umo: False)
        # The host's instruction to delegate through handoff tools the foreground no longer has.
        self.router_prompt = router_prompt or (lambda: "")
        self.decision_catalog = catalog or (lambda: [])
        self.debouncer = Debouncer()
        self.notifications = Notifications(app)
        self.traces = getattr(app, "turn_traces", None) or TurnTraceRepository(app.database)
        self.active_turns = {}
        if getattr(app, 'notification_outbox', None):
            app.notification_outbox.foreground = lambda session: any(
                key[0] == session and any(not task.done() and not event.get_extra('shrink_sent')
                    and not event.get_extra('shrink_cancelled') for event, task in turns)
                for key, turns in self.active_turns.items())

    # ---- receive ---------------------------------------------------------------------------------
    def build_turn(self, event, platform, uid, source, group, message_id):
        self_id = str(event.get_self_id())
        mentions, quote, reply_sender, has_image = [], {}, "", False
        for comp in event.get_messages():
            if isinstance(comp, At):
                qq = str(comp.qq)
                if qq == self_id:
                    mentions.append({"id": SELF, "name": BOT_NAME})
                elif qq.isdigit():
                    mentions.append({"id": f"{platform}:{qq}", "name": str(comp.name or "")})
            elif isinstance(comp, Reply):
                sender = str(comp.sender_id or "")
                quote = {
                    "message_id": str(comp.id),
                    "user_id": sender,
                    "name": str(getattr(comp, "sender_nickname", "") or ""),
                    "text": str(getattr(comp, "message_str", "") or "")[:200],
                }
                reply_sender = SELF if sender == self_id else (f"{platform}:{sender}" if sender else "")
            elif isinstance(comp, Image):
                has_image = True
        return Turn(
            id=message_id,
            platform=platform,
            account=str(event.get_sender_id()),
            user_id=uid,
            name=event.get_sender_name() or "",
            source=source,
            source_name=group,
            text=event.get_message_str() or "",
            created=time.time(),
            private=event.is_private_chat(),
            mentions=mentions,
            reply_to=quote.get("message_id", ""),
            reply_sender=reply_sender,
            quote=quote,
            has_image=has_image,
            wake=bool(getattr(event, "is_at_or_wake_command", False)),
            umo=str(getattr(event, "unified_msg_origin", "") or ""),
        )

    @in_lane("chat")
    async def receive(self, event):
        app = self.app
        arrived = time.monotonic()
        if str(event.get_sender_id()) == str(event.get_self_id()):
            event.call_llm = True
            return
        # Other command handlers retain ownership; slash commands are never learned.
        handlers = event.get_extra("activated_handlers", [])
        handled_elsewhere = any(
            any(
                type(f).__name__ in {"CommandFilter", "CommandGroupFilter", "RegexFilter"}
                for f in h.event_filters
            )
            for h in handlers
            if h.handler_module_path != self.plugin_module
        )
        raw = str(getattr(event.message_obj, "message_str", event.get_message_str()))
        handled_elsewhere = handled_elsewhere or raw.lstrip().startswith(("/", "!", "！"))
        platform = event.get_platform_name()
        platform = "qq" if platform in {"aiocqhttp", "qq_official"} else platform
        account = str(event.get_sender_id())
        uid = f"{platform}:{account}"
        group = str(event.get_group_id() or "")
        source = f"{platform}:group:{group}" if group else f"{platform}:private:{account}"
        notice = getattr(event.message_obj, "raw_message", None)
        if isinstance(notice, dict) and notice.get("notice_type") in {"group_recall", "friend_recall"}:
            target = f"{platform}:{event.get_platform_id()}:{source}:{notice['message_id']}"
            session = str(event.unified_msg_origin)
            incoming, fresh = app.inbox.event(
                session=session, sender=account, external_id="recall:" + target,
                source="platform", route="revision", text="撤回消息 " + target)
            if fresh and app.messages.get_many([target]):
                app.history.revise(session, target, sender=uid, identity=incoming["id"])
                app.inbox.repository.complete_message(target)
            app.inbox.repository.complete([incoming["id"]])
            event.stop_event()
            return
        raw_id = str(event.message_obj.message_id or "")
        if not raw_id:
            raw_id = hashlib.sha256(
                f"{uid}:{source}:{getattr(event.message_obj, 'timestamp', '')}:{raw}".encode()
            ).hexdigest()
        if handled_elsewhere:
            app.inbox.exclude(session=str(event.unified_msg_origin), sender=account, external_id=raw_id)
            return
        message_id = f"{platform}:{event.get_platform_id()}:{source}:{raw_id}"
        turn = self.build_turn(event, platform, uid, source, group, message_id)
        person = app.people_repository.get(uid)
        turn.speaker_bot = bool(person and person["kind"] == "bot")
        turn.addressee, turn.addressee_name = address(turn, app.people.address_book(source))
        session = turn.umo or turn.source
        app.history.repository.session(
            session, instance=str(event.get_platform_id()), platform=platform,
            kind="private" if turn.private else "group", external_id=account if turn.private else group,
            source=source)
        correction = re.match(r"^(?:更正|纠正|改为|改成)[：:，, ]*(.+)", turn.text, re.S)
        target = f"{platform}:{event.get_platform_id()}:{source}:{turn.reply_to}" if turn.reply_to else ""
        original = app.messages.get_many([target]) if correction and target else []
        correcting = bool(original and original[0]["user_id"] == uid)
        restored = event.get_extra("shrink_recovered_input")
        if restored:
            saved = app.inbox.repository.get(restored)
            if not saved or saved["processed"] is not None:
                event.call_llm = True
                return
            turn.id, turn.text = saved["message_id"], saved["text"]
        else:
            saved, fresh = app.inbox.receive(
                turn.record(),
                session=session, sender=account, external_id=raw_id,
                route="chat",
                source="timer" if event.get_platform_name() == "cron" else "platform",
                conversational=not correcting,
                metadata={"platform": platform, "instance": str(event.get_platform_id()),
                          "group": group, "name": turn.name, "private": turn.private,
                          "admin": event.is_admin(), "wake": turn.wake, "source": source,
                          "reply_to": turn.reply_to,
                          "reply_text": next((str(getattr(c, "message_str", "") or "")
                                              for c in event.get_messages() if isinstance(c, Reply)
                                              and str(c.sender_id or "") == str(event.get_self_id())), ""),
                          "self_id": str(event.get_self_id())})
            if not fresh:
                event.set_extra("shrink_reply", False)
                event.call_llm = True
                return
        if correcting and not restored:
            app.history.revise(session, target, sender=uid, identity=saved["id"],
                               text=turn.text, message_ref=saved["message_id"])
        event.set_extra("shrink_input_id", saved["id"])
        event.set_extra("shrink_input_ids", [saved["id"]])
        if turn.umo:
            self.umos[source] = turn.umo
        app.attachments.capture(turn, list(event.get_messages()))
        event.set_extra("shrink_turn", turn)
        trace = TurnTrace(turn.id, turn.umo, turn.source, turn.user_id, "")
        trace.begin = trace.mark = arrived
        trace.enter("decision")
        event.set_extra("shrink_trace", trace)
        prior = [entry for entry in self.active_turns.get((session, turn.user_id), []) if not entry[1].done()]
        self.arm_watchdog(event)
        reason = app.participation.decide(turn, random.random())
        decision = await app.decisions.decide(saved["id"], DecisionContext(
            private=turn.private, direct=turn.addressee == SELF or (turn.wake and not turn.addressee),
            admin=event.is_admin(), participation=reason, foreground=bool(prior)), self.decision_catalog())
        trace.enter("debounce")
        event.set_extra("shrink_decision", decision)
        if decision["decision"]["route"] == "stop":
            await self.stop_requested(event, turn)
            self.close_trace(event, "stopped")
            return
        cfg = app.state.settings
        if cfg.collect_enabled and group in cfg.collect_groups:
            for comp in event.get_messages():
                if isinstance(comp, Image) and app.memes.pending < 8:
                    app.memes.pending += 1
                    app.tasks.spawn(self.collect_image(comp, source))
        turn.reason = app.participation.reviewed(reason, decision)
        trace.reason = turn.reason
        burst_key = (turn.umo or turn.source, turn.user_id)
        if not restored and cfg.debounce_enabled and turn.addressee in ("", SELF) and (
            turn.reason in REPLY_REASONS or burst_key in self.debouncer.pending
        ):
            burst = await self.debouncer.collect(
                burst_key, turn.text, list(event.get_messages()), turn.reason,
                quiet=cfg.debounce_seconds, maximum=cfg.debounce_max_seconds,
            )
            if burst is None:
                self.close_trace(event, "debounced")
                event.stop_event()
                return
            turn.text = "\n".join(burst.texts)
            if turn.reason not in REPLY_REASONS:
                turn.reason = burst.reason
            event.message_str = turn.text
            event.message_obj.message_str = turn.text
            event.message_obj.message = [Plain(turn.text)] + [
                c for c in burst.components if not isinstance(c, Plain)
            ]
            event.set_extra("shrink_merged_messages", len(burst.texts))
        if turn.reason in REPLY_REASONS:
            pending = app.inbox.repository.pending("chat", session, account)
            event.set_extra("shrink_input_ids", [r["id"] for r in pending if r["claimed"] is None
                                                or r["id"] == saved["id"]])
        if turn.reason in REPLY_REASONS:
            running = [entry for entry in self.active_turns.get(burst_key, []) if entry[0] is not event]
            reply = self.debouncer.begin(burst_key, turn.text, turn.reason)
            if reply.text != turn.text:
                # An older reply to this sender was still being written; this turn answers both.
                turn.text, turn.reason = reply.text, reply.reason
                logger.info(f"Shrink merged in-flight reply source={source}")
            if running:
                event.set_extra("shrink_followup_owner", running[-1][0])
            for previous, _ in running:
                if self.withdrawn(previous):
                    event.set_extra("shrink_input_ids", list(dict.fromkeys(
                        (previous.get_extra("shrink_input_ids") or []) +
                        (event.get_extra("shrink_input_ids") or []))))
                self.bind_runner(previous)
                if cfg.cooperative_stop and self.withdrawn(previous) and request_stop(previous):
                    # The older run would only produce a reply that is dropped; end it now so
                    # this message does not wait behind it for the session lock.
                    logger.info(f"Shrink stopped superseded run source={source}")
            event.set_extra("shrink_inflight", (burst_key, reply))
            self.track(event, burst_key, reply, turn, arrived)
            # Registered from arrival, so a stop also reaches turns still queued for the lock.
            self.arm_watchdog(event)
        event.set_extra("shrink_reply_reason", turn.reason)
        if turn.reason in REPLY_REASONS:
            logger.info(f"Shrink reply: {turn.reason} source={source}")
            event.is_at_or_wake_command = True
            event.set_extra("enable_streaming", False)
            event.set_extra("enable_reasoning", False)
            binding = getattr(app, "model_settings", None)
            selected = (binding.for_session(event.unified_msg_origin)["model_provider"]
                        if binding else cfg.model_provider)
            event.set_extra("selected_provider", selected)
            event.set_extra("shrink_reply", True)
        else:
            self.close_trace(event, "silent")
            logger.debug(f"Shrink silent: {turn.reason} source={source}")
            if not decision["fallback"]:
                app.inbox.repository.complete([saved["id"]], route="chat")
            event.call_llm = True

    async def collect_image(self, comp, source):
        memes = self.app.memes
        try:
            path = Path(await asyncio.wait_for(comp.convert_to_file_path(), 20))
            if path.stat().st_size > MAX_IMAGE_BYTES:
                memes.pending -= 1
                return
            await memes.collect(path.read_bytes(), source)
        except Exception as exc:
            memes.pending -= 1
            self.app.tasks.error("image_download", exc)

    # ---- enrich ----------------------------------------------------------------------------------
    async def stop_requested(self, event, turn):
        key = (turn.umo or turn.source, turn.user_id)
        for previous, task in list(self.active_turns.get(key, [])):
            if previous is event:
                continue
            previous.set_extra("shrink_cancelled", True)
            self.disarm_watchdog(previous)
            if not (self.app.state.settings.cooperative_stop and request_stop(previous)):
                # No run to tell yet: the turn is still queued or preparing its context, where
                # enrich also checks the flag in case the cancellation lands inside a hook.
                task.cancel()
        self.debouncer.pending.pop(key, None)
        self.debouncer.replies.pop(key, None)
        if is_revocation(turn.text) and getattr(self.app, "authorizations", None):
            self.app.authorizations.revoke_scope(event.get_extra("shrink_input_id"), {
                "session": event.unified_msg_origin, "sender": str(event.get_sender_id()), "admin": event.is_admin()})
        decision = event.get_extra("shrink_decision")
        target = decision["decision"]["task"] if decision else None
        if target:
            self.app.operations.owned_task(event, target)
            cancelled = [await self.app.operations.cancel(target)]
        elif not decision or decision["reason"] == "revocation":
            cancelled = await self.app.operations.cancel_owned(event)
        else:
            cancelled = []
        await self.notifications.send_once(
            event, turn.id + ":stop",
            {"status": "cancelled", "cancelled_tasks": len(cancelled),
             "fact": "已停止当前会话中你交办的运行任务；保留执行记录，不再自动续做。"},
            request=turn.text, kind="cancelled")
        if getattr(self.app, "inbox", None):
            self.app.inbox.repository.complete(
                [r["id"] for r in self.app.inbox.repository.pending("chat", turn.umo or turn.source,
                                                                  str(event.get_sender_id()))],
                route="chat")
        event.stop_event()

    # ---- turn lifecycle --------------------------------------------------------------------------
    def track(self, event, key, reply, turn, arrived):
        """Tie the reply record and timing to the task that processes this event.

        The host may end an event without calling any later hook (consumed as a follow-up by an
        older run, cancelled, failed). Cleanup therefore hangs on the task itself; it compares
        by identity, so an old event never clears a newer one's record.
        """
        trace = event.get_extra("shrink_trace") or TurnTrace(turn.id, turn.umo, turn.source, turn.user_id, turn.reason)
        trace.enter("follow_up")
        event.set_extra("shrink_trace", trace)
        task = asyncio.current_task()
        if task is None:
            return

        def finished(_):
            self.debouncer.done(key, reply)
            runner = event.get_extra("shrink_runner")
            if runner is not None:
                # A run that died mid-step leaves queued follow-ups waiting forever.
                release_follow_ups(runner)
            consumed = event.get_extra("_follow_up_captured")
            if consumed and getattr(self.app, "inbox", None):
                identities = event.get_extra("shrink_input_ids") or []
                owner = event.get_extra("shrink_followup_owner")
                if owner and owner.get_extra("shrink_final_response"):
                    self.app.inbox.repository.complete(identities, route="chat")
                elif owner:
                    owner.set_extra("shrink_input_ids", list(dict.fromkeys(
                        (owner.get_extra("shrink_input_ids") or []) + identities)))
                    self.app.inbox.repository.claim(identities)
            self.close_trace(event, "follow_up" if consumed else "ended")
        task.add_done_callback(finished)

    @staticmethod
    def bind_runner(event):
        if event.get_extra("shrink_runner") is None:
            runner = active_runner(event)
            if runner is not None:
                event.set_extra("shrink_runner", runner)

    @staticmethod
    def enter(event, phase):
        trace = event.get_extra("shrink_trace")
        if trace is not None:
            trace.enter(phase)

    @staticmethod
    def outcome(event, value):
        trace = event.get_extra("shrink_trace")
        if trace is not None and not trace.outcome:
            trace.outcome = value

    def close_trace(self, event, outcome):
        previous = event.get_extra("shrink_previous_lane")
        if previous is not None:
            event.set_extra("shrink_previous_lane", None)
            LANE.set(previous)
        slot = event.get_extra("shrink_chat_slot")
        if slot is not None:
            event.set_extra("shrink_chat_slot", None)
            slot.release()
        trace = event.get_extra("shrink_trace")
        if trace is None or trace.closed:
            return
        trace.task_id = event.get_extra("shrink_background_task") or ""
        record = trace.finish(outcome)
        phases = " ".join(f"{k}={v:.2f}" for k, v in record["phases"].items())
        logger.info(
            f"Shrink turn run={record['run_id']} input={record['input_id']} umo={record['umo']} "
            f"sender={record['sender']} outcome={record['outcome']} total={record['total']:.2f}s "
            f"{phases} model_calls={record['model_calls']} tools={','.join(record['tools'])} "
            f"task={record['task_id']}")
        try:
            self.traces.save(record)
        except Exception as exc:
            self.app.tasks.error("turn_trace", exc)

    def waiting(self, event):
        """The host is about to queue for the session lock."""
        if event.get_extra("shrink_reply"):
            self.enter(event, "lock")

    def withdrawn(self, event):
        """A newer message from the same sender answers this one too, and nothing was done yet."""
        reply = self.inflight(event)
        return bool(reply and reply.superseded and not self.did_work(event))

    def abandon(self, event):
        """End a turn nobody is waiting for before the host starts a run for it."""
        outcome = ("stopped" if event.get_extra("shrink_cancelled")
                   else "superseded" if self.withdrawn(event) else "")
        if outcome:
            self.outcome(event, outcome)
            event.stop_event()
        return bool(outcome)

    def tool_started(self, event, tool, args=None):
        """Record that a tool is about to run. Synchronous: it must precede any wait, because a
        tool that has started may have side effects whether or not its result ever arrives."""
        if CURRENT_TASK.get() or not event.get_extra("shrink_reply"):
            return
        name = getattr(tool, "name", str(tool))
        started = event.get_extra("shrink_started_tools")
        if started is None:
            started = []
            event.set_extra("shrink_started_tools", started)
        started.append(name)
        if getattr(self.app, "history", None):
            trace = event.get_extra("shrink_trace")
            identity = f"{trace.run_id if trace else event.get_extra('shrink_turn').id}:{len(started)}"
            event.set_extra("shrink_tool_call_id", identity)
            self.app.history.tool_start(event.unified_msg_origin, identity, name, args or {})
        reply = self.inflight(event)
        if reply is not None and name not in CHAT_TOOLS:
            # Work has begun on the user's behalf; its result must be reported, not withdrawn.
            reply.committed = True
        self.bind_runner(event)
        trace = event.get_extra("shrink_trace")
        if trace is not None:
            if trace.phase != "tool":
                # The host reports only the final model response to plugins; a round that
                # asked for tools shows up here, once per round however many tools it calls.
                trace.model_calls += 1
            trace.tool(name)
            logger.info(f"Shrink tool start run={trace.run_id} tool={name}")

    async def enrich(self, event, req):
        if event.get_extra("shrink_reply") and event.get_extra("shrink_previous_lane") is None:
            event.set_extra("shrink_previous_lane", LANE.get())
            LANE.set("chat")
        try:
            await self.prepare(event, req)
        except asyncio.CancelledError:
            # The host swallows whatever a hook raises and would go on to call the model with
            # a half-built request. Stopping the event is what actually ends the turn.
            if event.get_extra("shrink_cancelled"):
                self.abandon(event)
            raise

    async def prepare(self, event, req):
        cron = event.get_extra("cron_job")
        if cron and not event.get_extra("shrink_timer_input"):
            payload = event.get_extra("cron_payload") or {}
            incoming, fresh = self.app.inbox.event(
                session=str(event.unified_msg_origin),
                sender=str(payload.get("sender_id") or "scheduler"),
                external_id=f"timer:{cron['id']}:{cron['run_started_at']}", source="timer", route="timer",
                text=event.get_message_str(), metadata={"job_id": cron["id"]})
            event.set_extra("shrink_timer_input", incoming["id"])
            if not fresh and incoming["processed"] is not None:
                event.stop_event()
                return
            self.app.inbox.repository.claim([incoming["id"]])
        turn = event.get_extra("shrink_turn")
        if not turn or not event.get_extra("shrink_reply"):
            return
        app = self.app
        self.enter(event, "context")
        if getattr(app, "history", None) and app.history.repository.revised(turn.id):
            event.stop_event()
            return
        if self.abandon(event):
            # Stopping here makes the host leave before it registers a runner.
            return
        if getattr(app, "scheduling", None) and event.get_extra("shrink_chat_slot") is None:
            self.enter(event, "chat_queue")
            slot = app.scheduling.slots["chat"]
            await slot.acquire()
            event.set_extra("shrink_chat_slot", slot)
            task = asyncio.current_task()
            if task is not None:
                task.add_done_callback(lambda _: self.close_trace(event, "ended"))
            self.enter(event, "context")
        removed = restrict(req, self.router_prompt())
        if removed:
            logger.debug(f"Shrink foreground tools withheld: {','.join(removed)}")
        self.arm_watchdog(event)
        decision = event.get_extra("shrink_decision")
        catalog = [{"name": t.name, "description": t.description or ""}
                   for t in getattr(getattr(req, "func_tool", None), "tools", []) if getattr(t, "active", True)]
        if decision:
            app.decisions.select_capabilities(decision, turn.text, catalog)
            if decision["reason"] in {"stop_ambiguous", "stop_permission"}:
                for tool in list(getattr(getattr(req, "func_tool", None), "tools", [])):
                    req.func_tool.remove_tool(tool.name)
        window = app.context.window(turn, same_source_only=True, recent_only=is_task_followup(turn.text, decision))
        session = turn.umo or turn.source
        identities = event.get_extra("shrink_input_ids") or []
        app.inbox.repository.claim(identities)
        mentioned = [
            u for u in dict.fromkeys(
                [m["id"] for m in turn.mentions if m["id"] != SELF]
                + ([turn.addressee] if turn.addressee not in ("", SELF, "other") else [])
                + app.people.mentioned(turn.text, turn.source)
            )
            if u != turn.user_id
        ]
        query = Query(
            text=" ".join([turn.text, *window.texts]),
            source=turn.source,
            speaker=turn.user_id,
            mentioned=mentioned,
            window=window.participants,
            private=turn.private,
        )
        memories, _ = await app.memory.recall_for(query)
        skills = await app.skills.relevant(turn.text)
        cards = [app.people.card(turn.user_id, "发言人", turn.source)]
        cards += [app.people.card(u, "提到的", turn.source) for u in mentioned[:3]]
        cards.append(app.people.group_card(turn.source))
        explicit = bool(EXPLICIT_MEME.search(turn.text))
        choices = (
            app.memes.choices(turn.source, explicit)
            if explicit or random.random() < MEME_OFFER
            else []
        )
        form = choose_form(turn.text, random.random(), bool(choices), random.random())
        examples, example_ids = ("", []) if form == "detail" else app.expression.examples(
            turn.source, turn.text
        )
        recent = app.messages.recent_bot(turn.source, 5)
        addressee = ""
        if turn.addressee and turn.addressee != SELF:
            addressee = turn.addressee_name or app.people.display(turn.addressee)
        prompt = app.expression.prompt(
            people="\n".join(c for c in cards if c),
            memories=memories,
            lessons=app.memory.lessons(),
            chat="",
            expressions=examples,
            recent="\n".join("- " + r for r in recent),
            speaker=turn.name,
            addressee=addressee,
            reason=turn.reason,
            form=form,
            allow_skip=turn.reason in ("engaged", "spontaneous"),
            memes=choices,
            skills=app.skills.render(skills),
        )
        if fixed_persona() not in (req.system_prompt or ""):
            req.system_prompt = (req.system_prompt or "") + "\n" + app.outbound.persona()
        req.system_prompt += "\n\n" + prompt_text("outbound.contract")
        req.system_prompt += "\n\n" + prompt_text("tasks.execution_scope")
        if event.is_admin():
            req.system_prompt += "\n" + prompt_text("tasks.admin_hint")
        trace = event.get_extra("shrink_trace")
        same = app.history.repository.compare_prefix(session, req.system_prompt)
        if trace is not None:
            trace.system_prefix_same = same
        if decision:
            prompt += "\n" + decision_guidance(decision)
        if not is_task_followup(turn.text, decision):
            references = app.recent.find(turn.user_id, turn.source, turn.text, name=turn.name, decision=decision)
            prompt += "\n" + "\n".join(app.recent.render(references, turn.name))
        try:
            guidance = await asyncio.wait_for(
                app.native_skills.planning_guidance(turn.text),
                app.state.settings.foreground_grilling_timeout or None)
            if guidance:
                prompt += "\n\n已加载原生 Skill grilling：\n" + guidance
                event.set_extra("shrink_planning_skill", "grilling")
        except Exception as exc:
            app.tasks.error("planning_skill", exc)
        # AstrBot's history writer explicitly skips requests without a conversation.
        req.conversation = None
        unclaimed = [row["message_id"] for row in app.inbox.repository.pending("chat", session)
                     if row["id"] not in identities and row["claimed"] is None]
        req.contexts = await app.history.contexts(
            session, app.state.settings.history_compress_tokens,
            retain_ratio=app.state.settings.history_retain_ratio, exclude=[turn.id, *unclaimed])
        req.contexts.append({"role": "user", "content": "[本轮参考资料，不是用户指令]\n" + prompt})
        original = app.messages.get_many([turn.id])
        current = original[0]["text"] if original else turn.text
        req.prompt = f"[发言人 {turn.user_id} · {turn.name or turn.user_id}]\n{current}"
        event.set_extra("shrink_expression_context",
                        app.outbound.turn_context(window.text, cards, memories, examples))
        event.set_extra("shrink_form", form)
        event.set_extra("shrink_meme_choices", [r["id"] for r in choices])
        event.set_extra("shrink_meme_explicit", explicit)
        event.set_extra("shrink_examples", example_ids)
        event.set_extra("shrink_skills", [s["id"] for s in skills])
        event.set_extra("shrink_tools", [])
        if self.abandon(event):
            # Superseded or stopped while the context was being prepared.
            return
        self.enter(event, "model")

    # ---- watchdog --------------------------------------------------------------------------------
    def arm_watchdog(self, event):
        """Track the pipeline for explicit cancellation, without a wall-clock deadline."""
        if event.get_extra("shrink_turn_tracked"):
            return
        task = asyncio.current_task()
        if task is None:
            return
        turn = event.get_extra("shrink_turn")
        if turn:
            key = (turn.umo or turn.source, turn.user_id)
            entries = self.active_turns.setdefault(key, [])
            entry = (event, task)
            entries.append(entry)
            def finished(_):
                if entry in entries:
                    entries.remove(entry)
                if not entries:
                    self.active_turns.pop(key, None)
            task.add_done_callback(finished)
        event.set_extra("shrink_pipeline_task", task)
        event.set_extra("shrink_turn_tracked", True)

    @staticmethod
    def disarm_watchdog(event):
        handle = event.get_extra("shrink_watchdog")
        if handle:
            handle.cancel()

    def expire(self, event, task):
        if task.done() or event.get_extra("shrink_final_response"):
            return
        turn = event.get_extra("shrink_turn")
        logger.warning(
            f"Shrink reply gave up after {REPLY_DEADLINE}s source={turn.source if turn else ''}"
        )
        self.app.tasks.error("chat_timeout", TimeoutError())
        task.cancel()
        self.record_episode(event, "", "timeout")
        if turn and turn.reason in ("direct", "engaged"):
            self.app.tasks.spawn(self.continue_or_report(event, turn, task))

    async def continue_or_report(self, event, turn, task):
        # Wait for cancellation to unwind before continuing uncertain side effects.
        try:
            await asyncio.wait_for(asyncio.shield(task), 15)
        except asyncio.CancelledError:
            pass
        except TimeoutError:
            await self.send_gave_up(event, turn)
            return
        except Exception:
            pass
        if event.get_extra("shrink_cancelled") or is_stop_request(turn.text):
            return
        operations = getattr(self.app, "operations", None)
        if operations and event.is_admin():
            try:
                existing = event.get_extra("shrink_background_task")
                if existing:
                    row = operations.detail(existing)
                    fact = f"后台任务{existing}实际状态：{row['status']}，主会话等待结束不会取消后台执行。"
                else:
                    await self.send_gave_up(event, turn)
                    return
                await self.notifications.send_once(
                    event, turn.id + ":timeout", fact, request=turn.text, kind="accepted")
                return
            except Exception as exc:
                self.app.tasks.error("background_continuation", exc)
        await self.send_gave_up(event, turn)

    async def send_gave_up(self, event, turn):
        from ..services.execution_status import timeout_reply
        await self.notifications.send_once(
            event, turn.id + ":timeout-result",
            timeout_reply(event.get_extra("shrink_tools"), REPLY_DEADLINE),
            request=turn.text, kind="timeout")

    # ---- response --------------------------------------------------------------------------------
    @in_lane("chat")
    async def response(self, event, response):
        timer = event.get_extra("shrink_timer_input")
        if timer and not response.tools_call_name:
            self.app.inbox.repository.complete([timer], route="timer")
        if not event.get_extra("shrink_reply"):
            return
        trace = event.get_extra("shrink_trace")
        if trace is not None:
            trace.model_calls += 1
        self.bind_runner(event)
        if response.tools_call_name:
            return
        if getattr(self.app, "inbox", None) and not self.withdrawn(event):
            self.app.inbox.repository.complete(event.get_extra("shrink_input_ids") or [], route="chat")
        if event.get_extra("shrink_cancelled"):
            response.completion_text = ""
            self.outcome(event, "stopped")
            return
        if self.withdrawn(event):
            # This reply will not be sent; do not spend expression calls on it inside the lock.
            response.completion_text = ""
            if response.result_chain is not None:
                response.result_chain.chain = []
            self.outcome(event, "superseded")
            logger.info("Shrink dropped reply: a newer message from the same sender took over")
            return
        self.enter(event, "expression")
        try:
            await self.final_response(event, response)
        finally:
            self.enter(event, "send")

    async def final_response(self, event, response):
        app = self.app
        turn = event.get_extra("shrink_turn")
        self.disarm_watchdog(event)
        event.set_extra("shrink_final_response", True)
        if getattr(response, "reasoning_content", None):
            response.reasoning_content = ""
        if response.role == "err":
            app.tasks.error("chat", RuntimeError())
            response.completion_text = prompt_text("reply.error") if turn.reason == "direct" else ""
            self.record_episode(event, "", "error")
            return
        form = event.get_extra("shrink_form", "short")
        if self.did_work(event):
            # Tool-backed answers carry results, not just conversational flavor.
            form = "tool_result"
            event.set_extra("shrink_form", form)
        execution = None
        task = None
        task_id = event.get_extra("shrink_background_task")
        if task_id:
            try:
                task = app.operations.owned_task(event, task_id)
                execution = {**execution_report(task), "delegated_to_subagent": True,
                             "request": task["request"], "reports_back": not bool(task["parent"])}
            except ValueError:
                pass
        steps = event.get_extra("shrink_tools") or []
        if not execution and steps:
            execution = {"status": "unverified", "tool_steps": steps,
                         "note": "以下是工具记录；模型草稿不是执行证据，单步完成不代表整个请求完成"}
        elif execution and steps:
            execution["tool_steps"] = steps
        if task is not None:
            from ..services.notification_outbox import task_fact
            event.set_extra('shrink_reported_task', task_id)
            event.set_extra('shrink_reported_fact', task_fact(task))
        finished = await app.expression.finish(
            response.completion_text or "",
            user_text=turn.text,
            form=form,
            reason=turn.reason,
            recent=app.messages.recent_bot(turn.source, 3),
            context=event.get_extra("shrink_expression_context"),
            execution=execution,
        )
        trace = event.get_extra("shrink_trace")
        if trace is not None:
            trace.expression_model_calls += finished.model_calls
        if finished.meme_id:
            event.set_extra("shrink_selected_meme", finished.meme_id)
        if event.get_extra("shrink_selected_meme") and form != "tool_result" and not finished.skip:
            text, keep = settle_meme(
                finished.text, explicit=event.get_extra("shrink_meme_explicit", False),
                reason=turn.reason,
            )
            if text != finished.text:
                finished.text, finished.second = text, ""
            if not keep:
                event.set_extra("shrink_selected_meme", None)
        event.set_extra("shrink_finished", finished)
        self.record_episode(event, finished.text if not finished.skip else "[没有回复]", "ok")
        response.completion_text = finished.text
        if response.result_chain is not None:
            response.result_chain.chain = (
                [Plain(finished.text)] if finished.text.strip() else []
            ) + [c for c in response.result_chain.chain if not isinstance(c, Plain)]

    def stale(self, turn):
        settings = self.app.state.settings
        return (
            turn.reason == "spontaneous"
            and time.time() - turn.created > settings.stale_seconds
            and self.app.messages.count_since(turn.source, turn.created) >= 3
        )

    async def decorate(self, event):
        if not event.get_extra("shrink_reply"):
            return
        result = event.get_result()
        if result is None:
            return
        if not event.get_extra("shrink_final_response") or event.get_extra("shrink_sent"):
            result.chain = []
            return
        turn = event.get_extra("shrink_turn")
        finished = event.get_extra("shrink_finished")
        if not self.debouncer.commit(self.inflight(event)) and not self.did_work(event):
            logger.info("Shrink dropped reply: a newer message from the same sender took over")
            self.outcome(event, "superseded")
            self.release(event)
            result.chain = []
            return
        if (finished and finished.skip) or self.stale(turn):
            logger.info(f"Shrink dropped reply: {'skip' if finished and finished.skip else 'stale'}")
            self.outcome(event, "skip" if finished and finished.skip else "stale")
            self.release(event)
            result.chain = []
            return
        # on_llm_response is authoritative; this also covers nonstandard runner output.
        detail = event.get_extra("shrink_form") in {"detail", "tool_result"} or wants_detail(turn.text)
        text = " ".join(
            t for t in (strip_reasoning(c.text) for c in result.chain if isinstance(c, Plain)) if t
        ).strip()
        if not detail:
            text = humanize(drop_meta_sentences(text))
            if len(text) > 60:
                text = self.app.expression.trim(text)
        others = [c for c in result.chain if not isinstance(c, Plain)]
        visible = [c for c in others if not isinstance(c, (At, Reply))]
        if not text and not visible:
            result.chain = []
            self.release(event)
            if event.get_extra("shrink_selected_meme"):
                # An empty result skips after_message_sent, so sticker-only replies send here.
                event.set_extra("shrink_sent", True)
                self.close_trace(event, "sent")
                self.after_turn(self.send_meme(event))
            return
        result.chain = ([Plain(text)] if text else []) + others
        if text and self.quotes(event, turn, result.chain):
            result.chain.insert(0, Reply(id=event.message_obj.message_id))
        result.use_markdown(False)
        event.set_extra("shrink_text", text)
        self.watch_delivery(event)

    def watch_delivery(self, event):
        if event.get_extra("shrink_observing_delivery"):
            return
        event.set_extra("shrink_observing_delivery", True)
        original = event.send

        async def send(chain):
            box = getattr(self.app, 'notification_outbox', None)
            fact = event.get_extra('shrink_reported_fact')
            text = ''.join(c.text for c in chain.chain if isinstance(c, Plain))
            identity = None
            if box and fact and text == event.get_extra('shrink_text'):
                turn = event.get_extra('shrink_turn')
                identity = box.foreground_begin(
                    {'session': event.unified_msg_origin, 'sender': str(event.get_sender_id())},
                    event.get_extra('shrink_reported_task'), turn.id + ':reply', fact, text)
                if identity is None:
                    return False
            accepted = False
            previous = getattr(event, "_has_send_oper", None)
            if previous is not None:
                event._has_send_oper = False
            try:
                result = await original(chain)
                accepted = result is not False and (previous is None or event._has_send_oper)
            finally:
                if identity:
                    box.foreground_end(identity, accepted)
                if previous is not None:
                    event._has_send_oper = previous or event._has_send_oper
            if accepted:
                accepted = event.get_extra("shrink_accepted_texts", [])
                accepted.append("".join(c.text for c in chain.chain if isinstance(c, Plain)))
                event.set_extra("shrink_accepted_texts", accepted)
            return result
        event.send = send

    @staticmethod
    def did_work(event):
        names = [s.get("name") for s in event.get_extra("shrink_tools") or []]
        names += event.get_extra("shrink_started_tools") or []
        return any(name not in CHAT_TOOLS for name in names)

    @staticmethod
    def inflight(event):
        entry = event.get_extra("shrink_inflight")
        return entry[1] if entry else None

    def release(self, event):
        entry = event.get_extra("shrink_inflight")
        if entry:
            self.debouncer.done(*entry)

    def quotes(self, event, turn, chain):
        if any(isinstance(c, Reply) for c in chain) or not event.message_obj.message_id:
            return False
        if self.host_quotes(turn.umo):
            return False
        return should_quote(turn, self.app.messages.count_since(turn.source, turn.created))

    async def sent(self, event):
        if not event.get_extra("shrink_reply") or event.get_extra("shrink_sent"):
            return
        text = event.get_extra("shrink_text", "")
        if getattr(self.app, "history", None) and text not in event.get_extra("shrink_accepted_texts", []):
            self.close_trace(event, "send_unknown")
            self.release(event)
            return
        event.set_extra("shrink_sent", True)
        self.close_trace(event, "sent")
        self.release(event)
        app, turn = self.app, event.get_extra("shrink_turn")
        text = event.get_extra("shrink_text", "")
        if turn and text:
            if getattr(app, "history", None):
                app.history.sent(turn.umo or turn.source, turn.source, text, turn.user_id, turn.reason,
                                 identity=turn.id + ":reply")
            else:
                app.messages.record_bot(turn.source, text, turn.user_id, turn.reason)
        ids = event.get_extra("shrink_examples", [])
        if ids:
            app.expressions.used(ids)
        finished = event.get_extra("shrink_finished")
        if turn and ((finished and finished.second) or event.get_extra("shrink_selected_meme")):
            self.after_turn(self.send_extras(event))

    def after_turn(self, coroutine):
        """Run outside the host's session lock, which this hook still holds."""
        try:
            self.app.tasks.spawn(coroutine)
        except RuntimeError:
            # Shutting down; the spawner has already closed the coroutine.
            pass

    async def send_extras(self, event):
        """The second bubble and the sticker; each is recorded only once the platform took it."""
        app, turn = self.app, event.get_extra("shrink_turn")
        finished = event.get_extra("shrink_finished")
        if finished and finished.second:
            await asyncio.sleep(random.uniform(1.0, 2.0))
            try:
                chain = MessageChain([Plain(finished.second)])
                chain.use_markdown(False)
                accepted = await asyncio.wait_for(event.send(chain), 30)
                if accepted is False:
                    return
                app.history.sent(turn.umo or turn.source, turn.source, finished.second, turn.user_id,
                                 turn.reason, identity=turn.id + ":second")
            except Exception as exc:
                app.tasks.error("second_bubble", exc)
        await self.send_meme(event)

    async def send_meme(self, event):
        app = self.app
        meme_id = event.get_extra("shrink_selected_meme")
        if not meme_id or event.get_extra("shrink_meme_attempted"):
            return
        event.set_extra("shrink_meme_attempted", True)
        turn = event.get_extra("shrink_turn")
        path = app.memes.select(
            meme_id,
            turn.source,
            event.get_extra("shrink_meme_choices", []),
            event.get_extra("shrink_meme_explicit", False),
            searched=meme_id == event.get_extra("shrink_meme_found"),
        )
        if not path:
            return
        try:
            image = Image.fromFileSystem(str(path))
            component = image
            if event.get_platform_name() == "aiocqhttp":
                component = QQSticker(file="base64://" + await image.convert_to_base64())
            chain = MessageChain([component])
            chain.use_markdown(False)
            accepted = await asyncio.wait_for(event.send(chain), 30)
            if accepted is False:
                return
            app.memes.sent(meme_id, turn.source)
            app.history.sent(turn.umo or turn.source, turn.source, "[表情]", turn.user_id,
                             turn.reason, identity=turn.id + ":meme")
        except Exception as exc:
            app.tasks.error("meme_send", exc)

    # ---- experience ------------------------------------------------------------------------------
    def tool_used(self, event, tool, args, result):
        if CURRENT_TASK.get():
            import json
            operations = getattr(self.app, "operations", None)
            if operations:
                content = getattr(result, "content", None) or []
                text = " ".join(str(getattr(c, "text", "") or "") for c in content) or str(result or "")
                operations.record("tool:" + getattr(tool, "name", str(tool)),
                                  result=json.dumps({"arguments": args, "result": text}, ensure_ascii=False, default=str))
            return
        tools = event.get_extra("shrink_tools")
        if tools is None or not event.get_extra("shrink_reply"):
            return
        name = getattr(tool, "name", str(tool))
        reply = self.inflight(event)
        if reply is not None and name not in CHAT_TOOLS:
            # Work was done on the user's behalf; its result must be reported, not withdrawn.
            reply.committed = True
        content = getattr(result, "content", None) or []
        text = " ".join(str(getattr(c, "text", "") or "") for c in content) or str(result or "")
        identity = event.get_extra("shrink_tool_call_id")
        if identity and getattr(self.app, "history", None):
            self.app.history.tool_end(event.unified_msg_origin, identity, text)
        tools.append(tool_step(getattr(tool, "name", str(tool)), args, text))
        self.enter(event, "model")

    def record_episode(self, event, answer, outcome):
        turn = event.get_extra("shrink_turn")
        if not turn or event.get_extra("shrink_episode") is not None:
            return
        try:
            episode = self.app.skills.record(
                turn, event.get_extra("shrink_tools") or [], answer, outcome,
                event.get_extra("shrink_skills") or [],
            )
            event.set_extra("shrink_episode", episode or 0)
        except Exception as exc:
            self.app.tasks.error("episode", exc)

    # ---- tools -----------------------------------------------------------------------------------
    def tool_turn(self, event):
        task_id = CURRENT_TASK.get()
        if not task_id:
            return event.get_extra("shrink_turn")
        # Background and resumed dashboard events need not contain the original Turn.
        row = self.app.operations.owned_task(event, task_id)
        scope = self.app.outbound.resolve_scope(
            {"session": row["session"], "sender": row["sender"]})
        if not scope:
            return None
        from types import SimpleNamespace
        return SimpleNamespace(source=scope["source"], user_id=scope["user_id"],
                               private=":private:" in scope["source"])

    async def remember(self, event, text: str):
        turn = event.get_extra("shrink_turn")
        if not turn or not re.search(r"记住|记一下|记着|别忘|remember", turn.text, re.I):
            return prompt_text("toolret.remember_unasked")
        if not text or len(text) > 1000:
            return "记忆长度无效。"
        memory_id, status = await self.app.memory.remember(turn.record(), text)
        if memory_id:
            self.app.tasks.spawn(self.app.memory.index_memory(memory_id))
        if status == "candidate":
            return prompt_text("toolret.remember_candidate")
        return prompt_text("toolret.remember_saved")

    async def chat_history(self, event, keyword="", person="", days=3):
        turn = self.tool_turn(event)
        if not turn:
            return prompt_text("toolret.no_turn")
        if keyword.startswith("checkpoint:"):
            import json
            result = self.app.history.read_checkpoint(event.unified_msg_origin, int(keyword.split(":", 1)[1]))
            return json.dumps(result, ensure_ascii=False)
        return self.app.tools.history(turn.source, keyword, person, days)

    async def lookup(self, event, query):
        turn = self.tool_turn(event)
        if not turn:
            return prompt_text("toolret.no_turn")
        own = event.get_extra("shrink_turn")
        # Only the person's own words in this turn can widen the search; a background task
        # works for its owner's scope and gets no such request.
        request = own.text if own is turn and own else ""
        decision = event.get_extra("shrink_decision") if own is turn else None
        return await self.app.tools.lookup(
            query, source=turn.source, speaker=turn.user_id, private=turn.private,
            request=request, **({"decision": decision} if decision is not None else {}),
        )

    async def find_meme(self, event, keyword):
        turn = event.get_extra("shrink_turn")
        if not turn:
            return prompt_text("toolret.no_turn")
        row = self.app.tools.find_meme(keyword)
        if not row:
            return render("toolret.meme_missing", keyword=keyword)
        event.set_extra("shrink_selected_meme", row["id"])
        event.set_extra("shrink_meme_found", row["id"])
        return render("toolret.meme_found", title=row['title'])

    async def run_python(self, event, code):
        if not self.tool_turn(event):
            return prompt_text("toolret.no_turn")
        return await self.app.tools.run_python(code)
