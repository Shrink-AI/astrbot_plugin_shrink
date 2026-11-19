"""Prevent raw execution material or prompt structure from becoming a notification."""
import json
import re

from .composition import SILENT, fallback, task_status_text
from ..prompts import render
from .reply_text import PROMPT_LEAK


def unsafe(text, facts, system="", *, detail=False, readable_fallback=False):
    stripped = text.strip()
    try:
        if isinstance(json.loads(stripped), (dict, list)):
            return True
    except (ValueError, TypeError):
        pass
    prose = re.sub(r"```.*?```", "", text, flags=re.S) if detail else text
    if PROMPT_LEAK.search(prose) or (not detail and re.search(r'\{\s*\\?"[^"\n]+\\?"\s*:', text)):
        return True
    if re.search(r"<\|(?:im_start|im_end|system|assistant)|</?(?:system|tool_result|instructions|facts|context|recent)>|"
                 r"^\s*(?:system|developer)\s*:", text, re.I | re.M):
        return True
    if not detail and re.search(r"Traceback \(most recent call last\)", text, re.I):
        return True
    if any(len(line.strip()) > 20 and line.strip() in stripped for line in system.splitlines()):
        return True
    def raw(value):
        if isinstance(value, str):
            yield value.strip()
        elif isinstance(value, dict):
            for entry in value.values():
                yield from raw(entry)
        elif isinstance(value, list):
            for entry in value:
                yield from raw(entry)
    return not detail and any(
        value and ((stripped == value)
                   or (not readable_fallback and len(value) >= 80 and value in stripped))
        for value in raw(facts))


def safe_expression(text, facts, kind, system=""):
    canonical = task_status_text(facts)
    if canonical and text == canonical:
        return canonical
    if not unsafe(text, facts, system):
        return text
    if kind in {"progress", "tool_progress"}:
        return SILENT
    return canonical or fallback(facts.get("status") if isinstance(facts, dict) else None) or render("outbound.fallback")
