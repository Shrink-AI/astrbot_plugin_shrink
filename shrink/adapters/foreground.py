"""What the foreground chat turn may hold the host's session lock for.

The host runs one turn per session at a time, so anything slow here blocks everyone in the
group. Long execution belongs to background tasks, which get their tools from their own
definition and never pass through this module.
"""

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
    return [{"task_id": row["id"], "request": row["request"][:500],
             "status": row["status"], "result": row.get("result", "")[:1200]}
            for row in listing()
            if row["sender"] == str(event.get_sender_id()) and
            (row["session"] == event.unified_msg_origin or turn.private)][:4]


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
    # The host appends this when handoffs are mounted; it tells the model to use tools it no
    # longer has.
    router_prompt = (router_prompt or "").strip()
    if router_prompt and router_prompt in (req.system_prompt or ""):
        req.system_prompt = req.system_prompt.replace(router_prompt, "")
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
