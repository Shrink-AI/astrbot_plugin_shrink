"""Reflection: turn one tool-using episode into reusable know-how, or revise what exists."""

from . import block, join, render, text

REFLECT = "skills.reflect.system"
OUTCOMES = ("ok", "timeout", "error")


def reflect_prompt(*, question, steps, answer, outcome, duration, follow_up, skills):
    said = text(f"skills.outcome.{outcome}") if outcome in OUTCOMES else outcome
    return join(
        text("skills.reflect.intro"),
        text("skills.authoring"),
        block("请求", question),
        block("经过", steps or "（没用工具，直接回答）"),
        block("石英的回答", answer or "（没有回答）"),
        render("skills.reflect.result", outcome=said, duration=f"{duration:.0f}"),
        block("之后的聊天", follow_up or "（之后没人接话）"),
        block("现有经验", skills or "（还没有相关经验）"),
        text("skills.reflect.task"),
    )
