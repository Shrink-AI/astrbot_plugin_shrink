"""Explicit provenance and a silence result that cannot become an empty reply."""
from dataclasses import dataclass
from contextvars import ContextVar
import re


# Set only around a claimed queue delivery; foreground/compatibility calls see None.
NOTIFICATION = ContextVar("notification", default=None)


@dataclass(frozen=True)
class ChatReply:
    text: str
    execution: dict | None = None


@dataclass(frozen=True)
class Silent:
    reason: str = "expression_unavailable"


SILENT = Silent()


def reference(value):
    return value if isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", value) else ""


def task_status_text(facts):
    if not isinstance(facts, dict):
        return None
    task, desktop = reference(facts.get("task_id")), facts.get("desktop_execution")
    status = facts.get("status")
    if isinstance(desktop, dict) and status in (
            "queued", "running", "waiting_external", "completed", "failed", "needs_attention", "unverified"):
        execution = desktop.get("status")
        if not isinstance(execution, str):
            return None
        if execution == "not_sent" and desktop.get("reason") == "device_offline":
            text = "电脑离线，命令没有发到电脑，Windows 任务未启动。"
        else:
            text = {
                "not_sent": "命令没有发到电脑，Windows 任务未启动。",
                "awaiting_receipt": "已尝试向电脑发送命令，但未收到执行回执，不能确认 Windows 任务是否启动或结束。",
                "failed": "电脑端回报执行失败，可按编号查询记录。",
                "submitted": "电脑端确认已接收请求；还没有最终结果。",
                "running": "电脑端最近一次回执显示正在执行；还没有最终结果。",
                "completed": "电脑端回报执行已经结束；任务目标是否达成仍以执行结果为准。",
                "unconfirmed": ("石英已受理，尚未收到 Windows 电脑端的启动或运行回执。" if
                                status in {"queued", "running", "waiting_external"} else
                                "石英已结束后台处理，但没有收到 Windows 电脑端的启动或完成回执。"),
            }.get(execution)
        if text:
            command = reference(desktop.get("receipt_id"))
            return ((f"石英任务 {task}：\n" if task else "") + text +
                    (f"\n电脑命令编号：{command}" if command else ""))
    ref = facts.get("result_ref")
    if isinstance(ref, dict) and facts.get("business_verified") is False:
        session, sequence = reference(ref.get("session_id")), ref.get("event_seq")
        if session and type(sequence) is int and sequence > 0:
            if text := fallback(status):
                return f"{text}\n电脑会话 {session}，结果编号 {sequence}。"
    return None


def fallback(status):
    if not isinstance(status, str):
        return None
    return {
        "unverified": "这一步的结果还没确认，执行记录已保留",
        "queued": "任务已接收，正在等待执行。",
        "submitted": "任务已提交，完成情况还未确认。",
        "running": "任务仍在执行，结果还未确认。",
        "waiting_external": "任务还在等待外部结果。",
        "retry_wait": "任务遇到暂时故障，已安排稍后继续。",
        "reconciling": "上一步的结果还不确定，需要先核对，执行记录已保留。",
        "cancelling": "已提交停止请求，正在等待执行端确认。",
        "completed": "任务执行已结束，具体结果暂时没整理好，记录已保留。",
        "desktop_completed": "电脑端已结束，详细结果已保存，汇报暂时未生成；任务目标是否完成仍以结果记录为准。",
        "cancelled": "任务已停止，已有执行记录已保留。",
        "waiting_user": "任务正在等待你的决定，具体问题暂时没整理好，记录已保留。",
        "failed": "任务执行失败，记录已保留。",
        "needs_attention": "任务有尚未解决的问题，记录已保留。",
        "timed_out": "请求超时，完成情况还未确认，记录已保留。",
        "interrupted": "任务执行已中断，记录已保留。",
    }.get(status)
