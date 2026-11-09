"""Turn a chat segment into grounded memories, then verify each claim against its evidence."""

from . import block, join, render, text

EXTRACT = "digest.extract.system"
VERIFY = "digest.verify.system"


def extract_prompt(*, group, people, known, glossary, lines):
    return join(
        render("digest.extract.task", group=group),
        block("已知人物", people),
        block("已有记忆", known),
        block("已知黑话", glossary),
        block("群聊", lines),
        text("common.data_note"),
        text("digest.extract.schema"),
        text("digest.extract.rules"),
        text("digest.extract.example"),
    )


def verify_prompt(items):
    return join(
        text("digest.verify.task"),
        block("待核对", items),
        text("common.data_note"),
        text("digest.verify.output"),
    )
