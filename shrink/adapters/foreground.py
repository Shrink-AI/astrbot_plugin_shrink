"""What the foreground chat turn may hold the host's session lock for.

The host runs one turn per session at a time, so anything slow here blocks everyone in the
group. Long execution belongs to background tasks, which get their tools from their own
definition and never pass through this module.
"""

# Host tools that run for as long as the command does. Sandbox runtimes use the ipython name.
LONG_RUNNING = frozenset({
    "astrbot_execute_shell", "astrbot_execute_python", "astrbot_execute_ipython",
    "astrbot_shell_session",
})
# A synchronous handoff runs a whole subagent inside the turn, with no tool-call timeout.
HANDOFF_PREFIX = "transfer_to_"


def blocked(name):
    return name in LONG_RUNNING or str(name).startswith(HANDOFF_PREFIX)


def restrict(req, router_prompt=""):
    """Drop long-running tools from this request only; returns the removed names.

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
