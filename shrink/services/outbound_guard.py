"""Prevent raw execution material or prompt structure from becoming a notification."""
import json
import re

from .composition import SILENT, fallback
from ..prompts import render


def unsafe(text, facts, system=""):
    stripped = text.strip()
    try:
        if isinstance(json.loads(stripped), (dict, list)):
            return True
    except (ValueError, TypeError):
        pass
    if re.search(r"<\|(?:im_start|im_end|system|assistant)|</?(?:system|tool_result|instructions)>|"
                 r"Traceback \(most recent call last\)|^\s*(?:system|developer)\s*:", text, re.I | re.M):
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
    return any(value and (stripped == value or len(value) >= 80 and value in stripped) for value in raw(facts))


def safe_expression(text, facts, kind, system=""):
    if not unsafe(text, facts, system):
        return text
    if kind in {"progress", "tool_progress"}:
        return SILENT
    return fallback(facts.get("status") if isinstance(facts, dict) else None) or render("outbound.fallback")
