"""Every model prompt, by key. Defaults ship in defaults.toml; the admin's edits override them.

Code only assembles: it picks which pieces apply this time and fills ``{{name}}`` placeholders.
The wording itself is data, editable on the dashboard's prompt page."""

import hashlib
import re
import tomllib
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")

CATALOG = {
    key: {**entry, "text": entry["text"].strip("\n")}
    for key, entry in tomllib.loads(
        Path(__file__).with_name("defaults.toml").read_text(encoding="utf-8")
    ).items()
}
_overrides: dict[str, str] = {}
# Unsaved edits being tried out; visible only to the task that set them, never to live chat.
_drafts: ContextVar[dict | None] = ContextVar("prompt_drafts", default=None)


def placeholders(text):
    return sorted(set(PLACEHOLDER.findall(text or "")))


def text(key):
    drafts = _drafts.get()
    if drafts and key in drafts:
        return drafts[key]
    return _overrides.get(key, CATALOG[key]["text"])


@contextmanager
def drafting(drafts):
    """Use ``drafts`` (key -> text) instead of the saved prompts inside this block."""
    token = _drafts.set(dict(drafts or {}))
    try:
        yield
    finally:
        _drafts.reset(token)


def render(key, **values):
    """The prompt with its placeholders filled; every placeholder must be given."""
    template = text(key)
    missing = [n for n in placeholders(template) if n not in values]
    if missing:
        raise KeyError(f"{key} needs {missing}")
    return PLACEHOLDER.sub(lambda m: str(values[m.group(1)]), template)


def tag(key):
    """Recorded in run diagnostics so a result can be traced to the exact wording used."""
    digest = hashlib.sha1(text(key).encode()).hexdigest()[:8]
    return f"{key}@{'custom' if key in _overrides else 'default'}:{digest}"


TOOL_PARAMS = re.compile(r"^\s*参数\s*[：:]\s*$", re.M)
PARAM_LINE = re.compile(r"^\s*(\w+)\s*[：:]\s*(.*?)\s*$")


def tool_parts(value):
    """Split a tool text into its description and {parameter: description}.

    Raises ValueError when the parameter section can't be read."""
    pieces = TOOL_PARAMS.split(value or "", maxsplit=1)
    description = pieces[0].strip()
    params = {}
    if len(pieces) == 2:
        for line in pieces[1].splitlines():
            if not line.strip():
                continue
            match = PARAM_LINE.match(line)
            if not match:
                raise ValueError(f"看不懂这一行：{line.strip()[:40]}（应该是“参数名：说明”）")
            params[match.group(1)] = match.group(2)
    return description, params


def check_tool(key, value):
    try:
        description, params = tool_parts(value)
    except ValueError as exc:
        return str(exc)
    _, expected = tool_parts(CATALOG[key]["text"])
    problems = []
    if not description:
        problems.append("工具说明不能为空")
    missing, extra = set(expected) - set(params), set(params) - set(expected)
    if missing:
        problems.append("缺少参数 " + "、".join(sorted(missing)))
    if extra:
        problems.append("工具没有参数 " + "、".join(sorted(extra)))
    empty = [n for n, d in params.items() if n in expected and not d]
    if empty:
        problems.append("参数说明不能为空：" + "、".join(empty))
    return "；".join(problems)


def check(key, value):
    """Why ``value`` can't replace prompt ``key``; empty when it can."""
    if key not in CATALOG:
        return "没有这个提示词"
    value = (value or "").strip("\n")
    if not value.strip():
        return "内容不能为空"
    if key.startswith("tool."):
        problem = check_tool(key, value)
        if problem:
            return problem
    expected = set(placeholders(CATALOG[key]["text"]))
    found = set(placeholders(value))
    problems = []
    if expected - found:
        problems.append("缺少 " + "、".join("{{%s}}" % n for n in sorted(expected - found)))
    if found - expected:
        problems.append("多了程序不认识的 " + "、".join("{{%s}}" % n for n in sorted(found - expected)))
    return "；".join(problems)


def apply(overrides):
    """Install the admin's saved versions (key -> text), ignoring keys no longer in the catalog."""
    _overrides.clear()
    _overrides.update({k: v for k, v in (overrides or {}).items() if k in CATALOG})


def overridden(key):
    return key in _overrides


def tool_doc(name, /, **types):
    """Decorator for an AstrBot ``llm_tool`` handler, placed under ``@filter.llm_tool``: writes
    the docstring AstrBot parses from prompt ``tool.<name>``. Parameter types stay in code.
    AstrBot reads the docstring once at import; later edits are pushed to the registered tool."""
    description, params = tool_parts(text(f"tool.{name}"))
    if list(params) != list(types):
        raise ValueError(f"tool.{name} parameters {list(params)} != {list(types)}")

    def decorate(func):
        lines = [description, "", "Args:"]
        lines += [f"    {p}({kind}): {params[p]}" for p, kind in types.items()]
        func.__doc__ = "\n".join(lines)
        return func

    return decorate


def block(name, body):
    """Wrap untrusted material so the model can tell data from instructions."""
    body = (body or "").strip()
    return f"<{name}>\n{body}\n</{name}>" if body else ""


def join(*parts):
    return "\n\n".join(p for p in parts if p and p.strip())
