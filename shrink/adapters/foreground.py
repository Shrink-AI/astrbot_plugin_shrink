"""What the foreground chat turn may hold the host's session lock for.

The host runs one turn per session at a time, so anything slow here blocks everyone in the
group. Long execution belongs to background tasks, which get their tools from their own
definition and never pass through this module.
"""
import copy
import re

FRONT_ACTIONS = {"dispatch", "list", "status", "append_input", "resume", "cancel"}
FRONT_DESCRIPTION = (
    "管理当前创建者的任务。dispatch一次提交后台同构体，request包含完整请求、来源、授权范围和验收。"
    "可选persona定义子代理的独立角色和做事方式，不复制原任务材料。"
    "list/status查状态，append_input追加原任务，cancel停止，resume仅在用户明确要求续做时使用。"
    "arguments是JSON对象；查询/续做/取消指定task_id，追加指定task_id和text。")

CHAT_TOOLS = frozenset({
    "shrink_lookup", "shrink_chat_history", "shrink_find_meme", "shrink_remember",
    "shrink_orchestrate", "shrink_develop", "shrink_authorization",
})
MAX_TOOL_CALLS = 4


def blocked(name):
    return name not in CHAT_TOOLS


def finish_tools(event):
    """Close the current batch of tool receipts before the next model call loses tools."""
    if (not event.get_extra("shrink_background_task") and
            len(event.get_extra("shrink_tools") or []) < MAX_TOOL_CALLS):
        return
    runner = event.get_extra("shrink_runner") or active_runner(event)
    if runner is None or event.get_extra("shrink_tools_finishing"):
        return
    event.set_extra("shrink_tools_finishing", True)
    original = runner._func_tool_for_provider

    def without_tools():
        # Emptying ToolSet inside on_tool_end makes the host drop the remaining results
        # of a parallel batch. This boundary runs after all tool-call IDs are paired.
        requests = [event.get_extra("shrink_foreground_request"), runner.req]
        sets = [getattr(req, "func_tool", None) for req in requests]
        sets.append(getattr(runner, "_skill_like_raw_tool_set", None))
        for toolset in sets:
            for tool in list(getattr(toolset, "tools", None) or []):
                toolset.remove_tool(tool.name)
        runner._func_tool_for_provider = original
        return original()

    runner._func_tool_for_provider = without_tools


def task_statuses(operations, event, turn):
    """A private status query may read this sender's tasks from their other chats."""
    listing = getattr(operations, "list", None)
    if not callable(listing):
        return []
    rows = [row for row in listing()
            if row["sender"] == str(event.get_sender_id()) and
            (row["session"] == event.unified_msg_origin or turn.private)]
    decision = event.get_extra("shrink_decision") or {}
    target = decision.get("decision", {}).get("task")
    if target:
        rows = [row for row in rows if row["id"] == target]
    else:
        text = getattr(turn, "text", "")
        explicit = [row for row in rows if row["id"] in text]
        if explicit:
            rows = explicit
        elif len(rows) != 1:
            from ..text_rules import grams
            query = set(grams(text))
            ranked = [(len(query & set(grams(row["request"]))), row) for row in rows]
            ranked.sort(key=lambda value: value[0], reverse=True)
            rows = ([ranked[0][1]] if ranked and ranked[0][0] >= 2 and
                    (len(ranked) == 1 or ranked[0][0] > ranked[1][0]) else [])
    summaries = []
    for row in rows[:1]:
        from ..services.task_reporting import desktop_execution, desktop_requested
        item = {"task_id": row["id"], "request": row["request"][:300],
                "status": row["status"], "result": row.get("result", "")[:1200]}
        detail = getattr(operations, "detail", None)
        if callable(detail) and desktop_requested(row["request"]):
            try:
                item["desktop_execution"] = desktop_execution(detail(row["id"]))
            except ValueError:
                item["desktop_execution"] = {"status": "unconfirmed"}
            item.pop("result", None)
        summaries.append(item)
    return summaries


def restrict(req, router_prompt=""):
    """Keep only foreground chat and dispatch tools; returns the removed names.

    The host looks tools up in the request's own set when executing a call, so a tool removed
    here cannot run even if the model names it. Tool objects and other tool sets are untouched.
    """
    toolset = getattr(req, "func_tool", None)
    tools = list(getattr(toolset, "tools", None) or [])
    removed = [t.name for t in tools if blocked(t.name)]
    for name in removed:
        toolset.remove_tool(name)
    for index, tool in enumerate(getattr(toolset, "tools", [])):
        if tool.name == "shrink_orchestrate":
            narrowed = copy.copy(tool)
            narrowed.description = FRONT_DESCRIPTION
            narrowed.parameters = {"type": "object", "properties": {
                "action": {"type": "string", "enum": sorted(FRONT_ACTIONS)},
                "arguments": {"type": "string", "description": "JSON对象"}},
                "required": ["action", "arguments"]}
            toolset.tools[index] = narrowed
    # The host appends this when handoffs are mounted; it tells the model to use tools it no
    # longer has.
    router_prompt = (router_prompt or "").strip()
    if router_prompt and router_prompt in (req.system_prompt or ""):
        req.system_prompt = req.system_prompt.replace(router_prompt, "")
    # Remove only the host-generated skill section, not arbitrary persona headings.
    req.system_prompt = re.sub(
        r"## Skills\n\nYou have specialized skills — reusable instruction bundles stored "
        r".*?7\. \*\*Failure handling\*\* — If a skill cannot be applied, state the "
        r"issue clearly and continue with the best alternative\.\n",
        "", req.system_prompt or "", flags=re.S)
    try:
        from astrbot.core.astr_main_agent import SANDBOX_MODE_PROMPT, _build_local_mode_prompt
        for instruction in (SANDBOX_MODE_PROMPT, _build_local_mode_prompt()):
            req.system_prompt = req.system_prompt.replace(instruction, "")
    except ImportError:
        pass
    return removed


def active_runner(event):
    """The host's agent runner for this event, once the host has registered it."""
    try:
        from astrbot.core.pipeline.process_stage.follow_up import _ACTIVE_AGENT_RUNNERS
    except Exception:
        return None
    runner = _ACTIVE_AGENT_RUNNERS.get(getattr(event, "unified_msg_origin", ""))
    context = getattr(getattr(runner, "run_context", None), "context", None)
    return runner if runner is not None and getattr(context, "event", None) is event else None


def release_follow_ups(runner):
    """Let messages queued on a finished runner continue as turns of their own.

    The host releases them when a run ends normally or is stopped, but not when a step raises;
    the waiting events would otherwise never proceed.
    """
    resolve = getattr(runner, "_resolve_unconsumed_follow_ups", None)
    if not callable(resolve):
        return False
    resolve()
    return True


def request_stop(event):
    """Ask the host to end this event's agent run at its next step; True when a run was told.

    Cancelling the pipeline task instead can be swallowed inside a plugin hook and leaves
    messages queued on the run waiting. The flag is set first so the host offers the run no
    further follow-ups; the run then releases the ones it already holds as it stops.
    """
    event.set_extra("agent_stop_requested", True)
    runner = event.get_extra("shrink_runner") or active_runner(event)
    stop = getattr(runner, "request_stop", None)
    if not callable(stop):
        return False
    event.set_extra("shrink_runner", runner)
    stop()
    return True
