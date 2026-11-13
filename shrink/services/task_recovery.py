"""Recover tool outcomes without interpreting a missing receipt as a failed write."""
import json


def reconciliation(events):
    pending = []
    for event in events:
        action = event["action"]
        if not action.startswith(("tool_start:", "tool_end:")):
            continue
        phase, name = action.split(":", 1)
        try:
            payload = json.loads(event["result"])
        except (ValueError, TypeError):
            payload = event["result"]
        if phase == "tool_start":
            pending.append({"event_id": event["id"], "attempt_id": event.get("attempt_id", ""),
                            "tool": name, "arguments": payload})
        else:
            arguments = payload.get("arguments") if isinstance(payload, dict) else None
            match = next((row for row in pending
                          if row["tool"] == name and row["arguments"] == arguments
                          and row["attempt_id"] == event.get("attempt_id", "")), None)
            if match:
                pending.remove(match)
    if not pending:
        return ""
    return ("中断恢复：以下工具已开始但没有结束回执，结果未知。先核对副作用，不重复写入。"
            "先用只读查询核对目标对象和操作回执；不能把缺少工具结果当成未执行。"
            "无法核对时保留未知并询问用户，不能盲目重跑。\n"
            + json.dumps(pending, ensure_ascii=False))
