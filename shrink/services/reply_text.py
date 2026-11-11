"""Pure post-processing of model replies: strip leaked reasoning, narration and AI-ish polish."""

import re

THINK_TAGS = r"think|thinking|thought|thoughts|reasoning|analysis"
THINK_BLOCK = re.compile(rf"<({THINK_TAGS})>.*?</\1>", re.S | re.I)
THINK_CLOSE = re.compile(rf"^.*</({THINK_TAGS})>", re.S | re.I)
THINK_OPEN = re.compile(rf"<({THINK_TAGS})>.*$", re.S | re.I)
REASON_LABEL = re.compile(
    r"^\s*(?:🤔\s*)?(?:思考过程|思维过程|推理过程|思考|推理|分析|内心独白|心理活动|"
    r"thinking|thoughts?|reasoning|analysis)\s*[:：]",
    re.I,
)
ANSWER_LABEL = re.compile(
    r"(?:^|\n)\s*(?:最终回复|最终回答|正式回复|回复|回答|输出|final answer|answer|reply|response)\s*[:：]\s*",
    re.I,
)
REASON_WORDS = re.compile(r"思考过程|思维过程|推理过程|内心独白")
ENGLISH_PLAN = re.compile(
    r"\b(keep (it )?(silent|short|minimal)|the user|i should|i will|i'll|respond with|"
    r"let me|my response|in character|as (an? )?(ai|assistant)|minimal reply)\b",
    re.I,
)
META = re.compile(
    r"^\s*(?:用户|群友|对方)\s*\S{0,16}?(?:发了|说了|在问|问了|提到|分享了|想要|的意思是|的消息)|"
    r"^\s*(?:本轮|这轮|根据(?:参考数据|系统提示|记忆|资料|上下文)|作为(?:石英|AI|助手))|"
    r"^\s*(?:我应该|我需要|我要回|回复应该|需要回复)"
)
SKIP = re.compile(r"\[\[\s*skip\s*]]", re.I)
MEME = re.compile(r"\[\[\s*meme\s*:\s*(\d+)\s*]]", re.I)
ANY_MARKER = re.compile(r"\[\[[^\]]*]]")
SENTENCE = re.compile(r"[^。！？!?\n]+[。！？!?…~]*")


PROMPT_LEAK = re.compile(
    r"(?:本轮要求[：:]|这次回[：:]|让我核实字数|数一下字数|非常贴合石英的语气|看提示[：:])")


def strip_prompt_deliberation(text):
    # Inspect prose only: examples inside code fences are user-visible content.
    parts = re.split(r"(```.*?```)", text or "", flags=re.S)
    for index, part in enumerate(parts):
        if part.startswith("```"):
            continue
        matches = list(PROMPT_LEAK.finditer(part))
        if len({match.group() for match in matches}) < 2:
            continue
        prefix = part[:matches[0].start()]
        prefix = re.sub(r"(?:^|\n)\s*等等[！!：:]*\s*$", "", prefix)
        return ("".join(parts[:index]) + prefix).strip()
    return text or ""


def has_cjk(text):
    return bool(re.search(r"[一-鿿]", text or ""))


def strip_reasoning(text):
    """Remove reasoning the model wrote into the visible answer."""
    if not text:
        return ""
    text = strip_prompt_deliberation(text)
    text = THINK_BLOCK.sub("", text)
    if THINK_CLOSE.search(text):
        text = THINK_CLOSE.sub("", text)
    text = THINK_OPEN.sub("", text)
    if REASON_LABEL.search(text):
        answers = list(ANSWER_LABEL.finditer(text))
        if answers:
            text = text[answers[-1].end():]
        else:
            paragraphs = re.split(r"\n\s*\n", text)
            text = "\n\n".join(p for p in paragraphs if not REASON_LABEL.search(p))
    parts = re.split(r"(```.*?```)", text, flags=re.S)
    for index, part in enumerate(parts):
        if part.startswith("```"):
            continue  # Code is content, never reasoning.
        kept = []
        for line in part.split("\n"):
            if REASON_LABEL.search(line):
                continue
            sentences = SENTENCE.findall(line) or [line]
            kept.append(
                "".join(
                    s for s in sentences if not REASON_WORDS.search(s) and not ENGLISH_PLAN.search(s)
                )
            )
        parts[index] = "\n".join(kept)
    return re.sub(r"\n{3,}", "\n\n", "".join(parts)).strip()


def looks_like_meta(text):
    """Narration about the conversation instead of a message to it."""
    text = (text or "").strip()
    if not text:
        return False
    return bool(META.search(text) or REASON_LABEL.search(text) or REASON_WORDS.search(text))


def drop_meta_sentences(text):
    sentences = SENTENCE.findall(text or "")
    if not sentences:
        return text or ""
    kept = [s for s in sentences if not looks_like_meta(s)]
    return "".join(kept).strip()


def take_markers(text):
    """Returns (clean_text, skip, meme_id)."""
    text = text or ""
    skip = bool(SKIP.search(text))
    match = MEME.search(text)
    meme_id = int(match.group(1)) if match else None
    return ANY_MARKER.sub("", text).strip(), skip, meme_id


def humanize(text):
    """Remove assistant polish that real group chat rarely has."""
    text = (text or "").strip()
    if not text:
        return text
    text = re.sub(r"^(?:哈哈+[，,]\s*)(?=\S{4,})", "", text)
    text = re.sub(r"[！!]{2,}", "！", text)
    text = re.sub(r"[~～]{2,}", "~", text)
    text = re.sub(r"[。.]+$", "", text) if not text.endswith("...") else text
    text = re.sub(r"\s+", " ", text) if "\n" not in text else text
    return text.strip()


def split_two(text, minimum=2):
    """Split one chat line into at most two natural bubbles; None when it should stay whole."""
    text = (text or "").strip()
    if len(text) < 8 or "\n" in text or "```" in text:
        return None
    middle = len(text) / 2
    best = None
    for match in re.finditer(r"[，,！!？?]\s*|\s+", text):
        cut = match.end()
        head, tail = text[: match.start() + (1 if text[match.start()] in "！!？?" else 0)], text[cut:]
        head, tail = head.strip().rstrip("，,"), tail.strip()
        if len(head) >= minimum and len(tail) >= minimum:
            score = abs(cut - middle)
            if best is None or score < best[0]:
                best = (score, head, tail)
    return (best[1], best[2]) if best else None


def opener(text):
    return re.sub(r"[\s，,。！!？?~～]", "", text or "")[:2]


def ending(text):
    match = re.search(r"(是吧|属于是|了属于是|好吧|吧|呢|啊|了)[\s。！!？?~～]*$", text or "")
    return match.group(1) if match else ""


def repeats_recent(text, recent):
    """True when a reply reuses the opener or a stock ending of the last few replies."""
    if not text or not recent:
        return False
    head, tail = opener(text), ending(text)
    for previous in recent[:3]:
        if head and len(head) == 2 and head == opener(previous):
            return True
        if tail in ("是吧", "属于是", "了属于是") and tail == ending(previous):
            return True
    return False
