"""How 石英 says things: reply form, prompt assembly and post-processing."""

import asyncio
import random
import re
from dataclasses import dataclass

from ..prompts.chat import persona_prompt, rewrite_prompt, turn_prompt
from ..text_rules import grams
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


def choose_form(text, random_value, meme_available=False, meme_roll=1.0):
    if wants_detail(text):
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

    async def finish(self, raw, *, user_text, form, reason, recent, context=None, execution=None):
        """Clean a model reply. Never returns reasoning, narration or markers."""
        text = strip_reasoning(raw or "")
        text, skip, meme_id = take_markers(text)
        if skip and reason in ("engaged", "spontaneous"):
            return Finished("", skip=True)
        text = drop_meta_sentences(text) if looks_like_meta(text) else text
        if form == "tool_result":
            if getattr(self, "outbound", None):
                facts = {"answer": text, "execution": execution} if execution else text
                text = await self.outbound.compose(facts, request=user_text, kind="tool_result",
                                                   context=context, recent=recent)
            return Finished(text.strip(), meme_id=meme_id)
        if form == "detail":
            return Finished(text.strip(), meme_id=meme_id)
        flat = re.sub(r"\s+", " ", text).strip()
        limit = FORM_LIMIT.get(form, HARD_LIMIT)
        if flat and (len(flat) > max(limit * 2, HARD_LIMIT) or repeats_recent(flat, recent)):
            flat = await self.rewrite(flat, user_text, form, recent)
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
        """Keep whole sentences within the hard limit; never cut a fact mid-sentence."""
        for sentence in re.findall(r"[^。！？!?\n]+[。！？!?]?", text):
            sentence = sentence.strip()
            if 0 < len(sentence) <= HARD_LIMIT and not looks_like_meta(sentence):
                return sentence
        return ""
