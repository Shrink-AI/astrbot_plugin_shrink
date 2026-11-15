"""Explicit provenance and a silence result that cannot become an empty reply."""
from dataclasses import dataclass
from contextvars import ContextVar


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
        "cancelled": "任务已停止，已有执行记录已保留。",
        "waiting_user": "任务正在等待你的决定，具体问题暂时没整理好，记录已保留。",
        "failed": "任务执行失败，记录已保留。",
        "needs_attention": "任务有尚未解决的问题，记录已保留。",
        "timed_out": "请求超时，完成情况还未确认，记录已保留。",
        "interrupted": "任务执行已中断，记录已保留。",
    }.get(status)
