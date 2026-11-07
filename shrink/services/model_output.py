import asyncio
import json
import re

from .reply_text import strip_reasoning

RETRY_HINT = "\n\n上次的输出不是合法 JSON。这次只输出 JSON 本身，不要任何解释或代码围栏。"


def parse_json(text):
    text = strip_reasoning(text or "") if "<" in (text or "") else (text or "")
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(text)
    except ValueError:
        # Tolerate prose around a single JSON value; never guess at broken JSON.
        match = re.search(r"(\{.*\}|\[.*\])", text, re.S)
        if not match:
            raise
        return json.loads(match.group(1))


async def ask_json(model, prompt, system="", timeout=90, images=None):
    """Call the model for JSON; retry once with an explicit reminder, then give up."""
    last = None
    for attempt in range(2):
        text = await asyncio.wait_for(
            model(prompt + (RETRY_HINT if attempt else ""), system, images)
            if images
            else model(prompt + (RETRY_HINT if attempt else ""), system),
            timeout,
        )
        try:
            return parse_json(text)
        except ValueError as exc:
            last = exc
    raise last
