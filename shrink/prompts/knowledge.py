"""Glossary, consolidation, learning and meme prompts."""

from . import block, join, render, text

GLOSSARY = "glossary.system"
PROFILE = "profile.system"
GROUP = "group.system"
LEARNING = "learning.system"
MEMES = "memes.system"


def glossary_prompt(term, usage, results):
    return join(
        f"词：{term}",
        block("群里的用法", usage),
        block("搜索结果", results),
        text("common.data_note"),
        text("glossary.output"),
    )


def profile_prompt(name, aliases, memories, stats, lines):
    return join(
        f"人物：{name}" + (f"（也叫：{aliases}）" if aliases else ""),
        block("记忆", memories),
        block("和石英的往来", stats),
        block("最近对石英说过的话", lines),
        text("common.data_note"),
        text("profile.output"),
    )


def group_prompt(name, digests):
    return join(
        f"群：{name}",
        block("最近的聊天摘要", digests),
        text("common.data_note"),
        text("group.output"),
    )


def learning_prompt(original_style, samples):
    return join(
        render("learning.task", original_style=original_style or "（空）"),
        block("样本", samples),
        text("common.data_note"),
    )
