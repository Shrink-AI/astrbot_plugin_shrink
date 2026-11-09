"""Directed learning: the admin teaches, the model proposes structured changes."""

from . import block, join, render, text

PROPOSE = "teach.propose.system"
REVIEW = "teach.review.system"


def propose_prompt(*, people, memories, history, message, today):
    return join(
        render("teach.propose.today", today=today),
        block("已知人物", people),
        block("相关记忆", memories),
        block("之前的对话", history),
        block("管理员刚才说", message),
        text("common.data_note"),
        text("teach.propose.output"),
        text("teach.propose.types"),
        text("teach.propose.rules"),
    )


def review_prompt(items):
    return join(
        text("teach.review.task"),
        block("待确认", items),
        text("teach.review.output"),
    )
