"""How 石英 says things: reply form, prompt assembly and post-processing."""

import asyncio
import random
import re
from dataclasses import dataclass

from ..prompts.chat import persona_prompt, rewrite_prompt, turn_prompt
from ..prompts import text as prompt_text
from ..text_rules import grams
from .composition import ChatReply, Silent
from .outbound_guard import unsafe
from .turn_control import unsupported_claim
from .scheduling import in_lane, measure_expression, EXPRESSION_CALLS
from .reply_text import (
    drop_meta_sentences,
    humanize,
    looks_like_meta,
    repeats_recent,
    split_two,
    strip_reasoning,
    take_markers,
)

FORM_WEIGHTS = (("tiny", 0.4), ("short", 0.4), ("normal", 0.2))
FORM_LIMIT = {"tiny": 12, "short": 30, "normal": 60, "meme": 12}
HARD_LIMIT = 60
RESULT_LIMIT = 8000


def wants_detail(text):
    if re.search(r"一句话|简短|别展开|不用解释|不要解释", text or ""):
        return False
    return bool(
        re.search(
            r"详细|展开|解释|分析一下|分析下|步骤|教程|怎么配置|怎么实现|如何实现|"
            r"列(一下|下|个|出|全)|清单|明细|"
            r"为什么.{0,15}(报错|失败|不能|异常)|列出|对比|比较|"
            r"(写|给|生成|提供|贴).{0,12}(代码|脚本|程序|文章|方案)|```|"
            r"\b(code|explain|debug|step.by.step)\b",
            text or "",
            re.I,
        )
    )


def allows_tool_quotes(text):
    return wants_detail(text) or bool(re.search(
        r"详情|(写|给|生成|提供|贴|看).{0,12}(代码|日志|配置|工具.{0,6}(内容|结果))",
        text or ""))


def choose_form(text, random_value, meme_available=False, meme_roll=1.0):
    if wants_detail(text) or re.search(r"测试过|查到了|查一下|查下|报错|权限|离线|授权|重载|部署|进度|同步|安装|配置|提交|推送", text or ""):
        return "detail"
    if meme_available and meme_roll < 0.12:
        return "meme"
    acc = 0.0
    for form, weight in FORM_WEIGHTS:
        acc += weight
        if random_value < acc:
            return form
    return "short"


ASKS = re.compile(r"[?？]|吗|呢|哪|谁|什么|啥|怎么|几")
REACTION_LIMIT = 6


def settle_meme(text, *, explicit, reason):
    """People answer with words or a sticker, rarely both. Returns (text, keep_sticker).

    A short reaction becomes the sticker alone; anything that carries content (an answer to
    someone calling 石英, a question, a longer line) stays as words and the sticker is dropped.
    Someone asking for a sticker gets both."""
    text = (text or "").strip()
    if not text or explicit:
        return text, True
    if reason != "direct" and len(text) <= REACTION_LIMIT and not ASKS.search(text):
        return "", True
    return text, False


@dataclass
class Finished:
    text: str
    second: str = ""
    skip: bool = False
    meme_id: int | None = None
    model_calls: int = 0


class ExpressionService:
    def __init__(self, state, personas, expressions, model, error):
        self.state, self.personas, self.expressions = state, personas, expressions
        self.model, self.error = model, error
        self.random = random.random

    def examples(self, source, text, limit=4):
        rows = self.expressions.usable(source)
        if not rows:
            return "", []
        query = set(grams(text, limit=60))
        scored = sorted(
            rows,
            key=lambda r: (
                -len(query & set(grams(r["situation"] + r["phrase"], limit=60))),
                r["uses"],
            ),
        )
        chosen = scored[:limit]
        return "\n".join(f"- {r['situation']}：{r['phrase']}" for r in chosen), [
            r["id"] for r in chosen
        ]

    def prompt(self, **parts):
        return turn_prompt(**parts)

    @in_lane("chat")
    @measure_expression
    async def finish(self, raw, *, user_text, form, reason, recent, context=None, execution=None):
        """Clean a model reply. Never returns reasoning, narration or markers."""
        text = strip_reasoning(raw or "")
        text, skip, meme_id = take_markers(text)
        if skip and reason in ("engaged", "spontaneous"):
            return Finished("", skip=True)
        text = drop_meta_sentences(text) if looks_like_meta(text) else text
        detail = form in {"tool_result", "detail"}
        explicit_detail = form == "detail" or allows_tool_quotes(user_text)
        system = persona_prompt(self.personas.learned_persona())
        steps = (execution or {}).get("tool_steps", [])
        material = execution or {}
        if execution and execution.get("desktop_execution"):
            from .composition import task_status_text
            verified_status = task_status_text(execution)
            if verified_status:
                return Finished(verified_status)

        def invalid(value):
            return (unsafe(value, material, system, detail=explicit_detail)
                    or unsupported_claim(value, steps, execution))

        if reason == "direct" and not meme_id and not humanize(text).strip():
            revised = await self.rewrite("", user_text, form, recent)
            text = humanize(revised).strip() if not invalid(revised) else ""
            if not text:
                from .composition import fallback
                text = fallback((execution or {}).get("status")) or prompt_text("reply.error").strip()
            return Finished(text, meme_id=meme_id)

        # Inspect raw material before cleaning too: stripping a leaked prompt must not make
        # its surviving prefix look like an approved answer.
        recovered = invalid(raw or "") or invalid(text)
        if recovered:
            if getattr(self, "outbound", None):
                unsupported = unsupported_claim(raw, steps, execution)
                facts = ChatReply("" if unsupported else text,
                                  execution or ({"status": "unverified"} if unsupported else None))
                text = await self.outbound.compose(
                    facts, request=user_text, kind="execution_status" if unsupported
                    else "tool_result", context=context, recent=recent, recovery=True,
                    required=reason == "direct", detail=explicit_detail,
                    instruction=("只说明可确认的状态与缺少的回执；自然、简短、温和，不责备用户；"
                                 "没有记录不能推断未执行，也不能声称完成，不承诺自动重试。")
                    if unsupported else "")
                if isinstance(text, Silent):
                    return Finished("", skip=True)
            else:
                text = await self.rewrite(text, user_text, form, recent)
            text = strip_reasoning(text or "").strip()
            if invalid(text):
                from .composition import fallback
                text = (fallback((execution or {}).get("status")) or "") if reason == "direct" else ""
                if invalid(text):
                    text = ""
            if not text:
                return Finished("", skip=True)
            meme_id = None
        if detail:
            return Finished(text[:RESULT_LIMIT].rstrip("。"), meme_id=meme_id)
        flat = re.sub(r"\s+", " ", text).strip()
        if not recovered and (len(flat) > max(FORM_LIMIT.get(form, HARD_LIMIT) * 2, HARD_LIMIT)
                              or repeats_recent(flat, recent)):
            revised = await self.rewrite(flat, user_text, form, recent)
            if not invalid(revised):
                flat = revised
        if len(flat) > HARD_LIMIT:
            flat = self.trim(flat)
        flat = humanize(flat)
        second = ""
        settings = self.state.settings
        if form in ("short", "normal") and len(flat) > 12:
            if self.random() < settings.split_probability:
                parts = split_two(flat)
                if parts:
                    flat, second = parts
        return Finished(flat, second=second, meme_id=meme_id)

    async def rewrite(self, text, user_text, form, recent):
        try:
            counter = EXPRESSION_CALLS.get()
            if counter is not None:
                counter[0] += 1
            revised = await asyncio.wait_for(
                self.model(
                    rewrite_prompt(text, user_text, form, " / ".join(recent[:3])),
                    persona_prompt(self.personas.learned_persona()),
                ),
                30,
            )
            revised = strip_reasoning(revised)
            revised, _, _ = take_markers(revised)
            revised = re.sub(r"\s+", " ", revised).strip()
            if 0 < len(revised) <= HARD_LIMIT and not looks_like_meta(revised):
                return revised
        except Exception as exc:
            self.error("rewrite", exc)
        return text

    @staticmethod
    def trim(text):
        """Prefer a whole sentence; an unbroken sentence still obeys the hard limit."""
        for sentence in re.findall(r"[^。！？!?\n]+[。！？!?]?", text):
            sentence = sentence.strip()
            if 0 < len(sentence) <= HARD_LIMIT and not looks_like_meta(sentence):
                return sentence
        prefix = text[:HARD_LIMIT]
        boundaries = list(re.finditer(r"[，、；：\s]", prefix))
        if boundaries:
            clause = prefix[:boundaries[-1].start()].rstrip()
            if clause:
                return clause
        return prefix
