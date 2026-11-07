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

from ..config import BOT_NAME, FIXED_PERSONA, persona_prompt
from ..services.expression import choose_form, wants_detail
from ..services.memes import MAX_IMAGE_BYTES
from ..services.memory import Query
from ..services.perception import SELF, Turn, address
from ..services.reply_text import drop_meta_sentences, humanize, strip_reasoning
from ..services.skills import tool_step

REPLY_REASONS = {"direct", "engaged", "spontaneous"}
# The host's provider timeout does not bound a stalled relay connection, and a reply that
# arrives minutes late is worse than none: give up on the whole turn (searches included).
REPLY_DEADLINE = 100
GAVE_UP = "查半天没查出来，网页一直打不开"
NO_TURN = "这个工具只能在群聊回复里用。"
# People rarely attach a sticker to most messages; only sometimes offer the sticker list.
MEME_OFFER = 0.35
EXPLICIT_MEME = re.compile(r"表情|反应图|来张图|发张图")


class QQSticker(BaseMessageComponent):
    # The Image adapter drops sub_type; a native segment preserves QQ sticker semantics.
    type: ComponentType = ComponentType.Image
    file: str
    sub_type: int = 1
    summary: str = "[表情]"


class ConversationAdapter:
    def __init__(self, app, plugin_module, umos=None):
        self.app = app
        self.plugin_module = plugin_module
        self.umos = umos if umos is not None else {}

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

    async def receive(self, event):
        app = self.app
        if str(event.get_sender_id()) == str(event.get_self_id()):
            event.call_llm = True
            return
        # Other command handlers retain ownership; slash commands are never learned.
        handlers = event.get_extra("activated_handlers", [])
        if any(
            any(
                type(f).__name__ in {"CommandFilter", "CommandGroupFilter", "RegexFilter"}
                for f in h.event_filters
            )
            for h in handlers
            if h.handler_module_path != self.plugin_module
        ):
            return
        raw = str(getattr(event.message_obj, "message_str", event.get_message_str()))
        if raw.lstrip().startswith(("/", "!", "！")):
            return
        platform = event.get_platform_name()
        platform = "qq" if platform in {"aiocqhttp", "qq_official"} else platform
        account = str(event.get_sender_id())
        uid = f"{platform}:{account}"
        group = str(event.get_group_id() or "")
        source = f"{platform}:group:{group}" if group else f"{platform}:private:{account}"
        raw_id = str(event.message_obj.message_id or "")
        if not raw_id:
            raw_id = hashlib.sha256(
                f"{uid}:{source}:{getattr(event.message_obj, 'timestamp', '')}:{raw}".encode()
            ).hexdigest()
        message_id = f"{platform}:{event.get_platform_id()}:{source}:{raw_id}"
        turn = self.build_turn(event, platform, uid, source, group, message_id)
        person = app.people_repository.get(uid)
        turn.speaker_bot = bool(person and person["kind"] == "bot")
        turn.addressee, turn.addressee_name = address(turn, app.people.address_book(source))
        if not app.messages.ingest(turn.record()):
            event.call_llm = True
            return
        if turn.umo:
            self.umos[source] = turn.umo
        event.set_extra("shrink_turn", turn)
        cfg = app.state.settings
        if cfg.collect_enabled and group in cfg.collect_groups:
            for comp in event.get_messages():
                if isinstance(comp, Image) and app.memes.pending < 8:
                    app.memes.pending += 1
                    app.tasks.spawn(self.collect_image(comp, source))
        turn.reason = app.participation.decide(turn, random.random())
        event.set_extra("shrink_reply_reason", turn.reason)
        if turn.reason in REPLY_REASONS:
            logger.info(f"Shrink reply: {turn.reason} source={source}")
            event.is_at_or_wake_command = True
            event.set_extra("enable_streaming", False)
            event.set_extra("enable_reasoning", False)
            event.set_extra("selected_provider", cfg.model_provider)
            event.set_extra("shrink_reply", True)
        else:
            logger.debug(f"Shrink silent: {turn.reason} source={source}")
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
    async def enrich(self, event, req):
        turn = event.get_extra("shrink_turn")
        if not turn or not event.get_extra("shrink_reply"):
            return
        app = self.app
        self.arm_watchdog(event)
        window = app.context.window(turn)
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
            chat=window.text,
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
        if FIXED_PERSONA not in (req.system_prompt or ""):
            req.system_prompt = (req.system_prompt or "") + "\n" + persona_prompt(
                app.persona_repository.learned_persona()
            )
        req.system_prompt += "\n\n" + prompt
        # The plugin owns conversation context; host history may carry leaked or stale replies.
        req.contexts = []
        event.set_extra("shrink_form", form)
        event.set_extra("shrink_meme_choices", [r["id"] for r in choices])
        event.set_extra("shrink_meme_explicit", explicit)
        event.set_extra("shrink_examples", example_ids)
        event.set_extra("shrink_skills", [s["id"] for s in skills])
        event.set_extra("shrink_tools", [])

    # ---- watchdog --------------------------------------------------------------------------------
    def arm_watchdog(self, event):
        """Cancel the event's pipeline task if no final answer arrives in time."""
        if event.get_extra("shrink_watchdog"):
            return
        task = asyncio.current_task()
        if task is None:
            return
        handle = asyncio.get_running_loop().call_later(REPLY_DEADLINE, self.expire, event, task)
        event.set_extra("shrink_watchdog", handle)

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
            self.app.tasks.spawn(self.send_gave_up(event, turn))

    async def send_gave_up(self, event, turn):
        chain = MessageChain([Plain(GAVE_UP)])
        chain.use_markdown(False)
        await asyncio.wait_for(event.send(chain), 30)
        self.app.messages.record_bot(turn.source, GAVE_UP, turn.user_id, turn.reason)

    # ---- response --------------------------------------------------------------------------------
    async def response(self, event, response):
        if not event.get_extra("shrink_reply") or response.tools_call_name:
            return
        app = self.app
        turn = event.get_extra("shrink_turn")
        self.disarm_watchdog(event)
        event.set_extra("shrink_final_response", True)
        if getattr(response, "reasoning_content", None):
            response.reasoning_content = ""
        if response.role == "err":
            app.tasks.error("chat", RuntimeError())
            response.completion_text = "刚刚没接上，等会儿再试试" if turn.reason == "direct" else ""
            self.record_episode(event, "", "error")
            return
        finished = await app.expression.finish(
            response.completion_text or "",
            user_text=turn.text,
            form=event.get_extra("shrink_form", "short"),
            reason=turn.reason,
            recent=app.messages.recent_bot(turn.source, 3),
        )
        event.set_extra("shrink_finished", finished)
        self.record_episode(event, finished.text if not finished.skip else "[没有回复]", "ok")
        if finished.meme_id:
            event.set_extra("shrink_selected_meme", finished.meme_id)
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
        if (finished and finished.skip) or self.stale(turn):
            logger.info(f"Shrink dropped reply: {'skip' if finished and finished.skip else 'stale'}")
            result.chain = []
            return
        # on_llm_response is authoritative; this also covers nonstandard runner output.
        detail = event.get_extra("shrink_form") == "detail" or wants_detail(turn.text)
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
            if event.get_extra("shrink_selected_meme"):
                # An empty result skips after_message_sent, so sticker-only replies send here.
                event.set_extra("shrink_sent", True)
                await self.send_meme(event)
            return
        result.chain = ([Plain(text)] if text else []) + others
        result.use_markdown(False)
        event.set_extra("shrink_text", text)

    async def sent(self, event):
        if not event.get_extra("shrink_reply") or event.get_extra("shrink_sent"):
            return
        event.set_extra("shrink_sent", True)
        app, turn = self.app, event.get_extra("shrink_turn")
        text = event.get_extra("shrink_text", "")
        if turn and text:
            app.messages.record_bot(turn.source, text, turn.user_id, turn.reason)
        finished = event.get_extra("shrink_finished")
        if turn and finished and finished.second:
            await asyncio.sleep(random.uniform(1.0, 2.0))
            try:
                chain = MessageChain([Plain(finished.second)])
                chain.use_markdown(False)
                await asyncio.wait_for(event.send(chain), 30)
                app.messages.record_bot(turn.source, finished.second, turn.user_id, turn.reason)
            except Exception as exc:
                app.tasks.error("second_bubble", exc)
        ids = event.get_extra("shrink_examples", [])
        if ids:
            app.expressions.used(ids)
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
            await asyncio.wait_for(event.send(chain), 30)
            app.memes.sent(meme_id, turn.source)
            app.messages.record_bot(turn.source, "[表情]", turn.user_id, turn.reason)
        except Exception as exc:
            app.tasks.error("meme_send", exc)

    # ---- experience ------------------------------------------------------------------------------
    def tool_used(self, event, tool, args, result):
        tools = event.get_extra("shrink_tools")
        if tools is None or not event.get_extra("shrink_reply"):
            return
        content = getattr(result, "content", None) or []
        text = " ".join(str(getattr(c, "text", "") or "") for c in content) or str(result or "")
        tools.append(tool_step(getattr(tool, "name", str(tool)), args, text))

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
    async def remember(self, event, text: str):
        turn = event.get_extra("shrink_turn")
        if not turn or not re.search(r"记住|记一下|记着|别忘|remember", turn.text, re.I):
            return "本轮没有明确要求记忆，请不要声称已经记住。"
        if not text or len(text) > 1000:
            return "记忆长度无效。"
        memory_id, status = await self.app.memory.remember(turn.record(), text)
        if memory_id:
            self.app.tasks.spawn(self.app.memory.index_memory(memory_id))
        if status == "candidate":
            return "已暂存，涉及其他人的内容需要管理员确认后才会使用。"
        return "已保存，附有用户账号、来源和原话。"

    async def chat_history(self, event, keyword="", person="", days=3):
        turn = event.get_extra("shrink_turn")
        if not turn:
            return NO_TURN
        return self.app.tools.history(turn.source, keyword, person, days)

    async def lookup(self, event, query):
        turn = event.get_extra("shrink_turn")
        if not turn:
            return NO_TURN
        return await self.app.tools.lookup(
            query, source=turn.source, speaker=turn.user_id, private=turn.private
        )

    async def find_meme(self, event, keyword):
        turn = event.get_extra("shrink_turn")
        if not turn:
            return NO_TURN
        row = self.app.tools.find_meme(keyword)
        if not row:
            return f"表情库里没有和“{keyword}”对得上的表情，别发了，用文字回就行。"
        event.set_extra("shrink_selected_meme", row["id"])
        event.set_extra("shrink_meme_found", row["id"])
        return f"找到了「{row['title']}」，会跟在你这句话后面发出去。文字可以很短，也可以不写。"

    async def run_python(self, event, code):
        if not event.get_extra("shrink_turn"):
            return NO_TURN
        return await self.app.tools.run_python(code)
