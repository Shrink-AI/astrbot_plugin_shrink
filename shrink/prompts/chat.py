"""Chat persona, per-turn assembly and rewrite prompts. Wording lives in defaults.toml."""

from . import block, join, render, text

FORMS = ("tiny", "short", "normal", "meme", "detail")
REASONS = ("direct", "engaged", "spontaneous")


def fixed_persona():
    return text("chat.persona")


def persona_prompt(learned: str) -> str:
    learned = (learned or "").strip()
    if not learned:
        return fixed_persona()
    return f"{fixed_persona()}\n\n{text('chat.style_header')}\n{learned}"


def form_text(form):
    return text(f"chat.form.{form if form in FORMS else 'short'}")


def turn_prompt(
    *,
    people="",
    memories="",
    lessons="",
    chat="",
    expressions="",
    recent="",
    speaker="",
    addressee="",
    reason="direct",
    form="short",
    allow_skip=False,
    memes=None,
    skills="",
):
    why = text(f"chat.reason.{reason}") if reason in REASONS else ""
    tail = [render("chat.turn.current", speaker=speaker, reason=why)]
    if addressee:
        tail.append(render("chat.turn.addressee", addressee=addressee))
    tail.append(render("chat.turn.form", form=form_text(form)))
    if memes:
        tail.append(render(
            "chat.turn.memes",
            memes="；".join(f"{m['id']}={m['title']}({m['tags']})" for m in memes),
        ))
    if allow_skip:
        tail.append(text("chat.turn.skip"))
    tail.append(text("chat.tools_hint"))
    tail.append(text("chat.turn.end"))
    return join(
        block("人物", people),
        block("记忆", memories),
        block("群友提醒过你", lessons),
        block("群聊", chat),
        text("chat.turn.data_note"),
        render("chat.turn.expressions", expressions=expressions) if expressions else "",
        render("chat.turn.recent", recent=recent) if recent else "",
        block("你攒的经验", skills),
        text("chat.turn.skills") if skills else "",
        text("chat.contrast") if form != "detail" else "",
        text("chat.task_contrast"),
        "\n".join(tail),
    )


def rewrite_prompt(draft, user_text, form, recent=""):
    recent_line = render("chat.rewrite.recent", recent=recent) + "\n" if recent else ""
    return render("chat.rewrite", form=form_text(form), recent=recent_line, user=user_text, draft=draft)
