"""Real templates and independently budgeted role history, with synthetic content."""
import json
import re

from shrink import prompts
from shrink.config import Settings
from tests.test_continuous_history import app as history_app, receive

app = history_app


def test_real_default_compression_template_contains_messages():
    value = "synthetic-history-sentinel"
    with prompts.drafting({"history.compress": prompts.CATALOG["history.compress"]["text"]}):
        rendered = prompts.render("history.compress", messages=value, max_tokens=321)
        assert value in rendered
        assert "321" in rendered and "{{" not in rendered


def test_default_templates_have_no_single_brace_placeholders():
    assert not [(key, match.group()) for key, entry in prompts.CATALOG.items()
                for match in re.finditer(r"(?<!\{)\{\w+\}(?!\})", entry["text"])]


def test_independent_token_settings_defaults():
    settings = Settings(context_budget=1000)
    assert settings.history_compress_tokens == 128000
    assert settings.history_retain_ratio == .65


async def test_long_history_appends_without_rewriting_prefix(app):
    for i in range(20):
        receive(app, str(i), f"合成内容{i}：" + "完整行程细节" * 120)
    first = await app.history.contexts("bot:GroupMessage:1", 128000)
    prefix = json.dumps(first, ensure_ascii=False)
    assert len(first) == 20
    receive(app, "new", "补充一句")
    second = await app.history.contexts("bot:GroupMessage:1", 128000)
    assert json.dumps(second[:len(first)], ensure_ascii=False) == prefix
    assert not app.history.repository.checkpoint("bot:GroupMessage:1")


async def test_threshold_retains_recent_original_tokens_and_stable_checkpoint(app):
    from shrink.services.history import ConversationHistory
    for i in range(20):
        receive(app, str(i), f"合成记录{i}：" + "行程细节" * 40)
    contexts = await app.history.contexts("bot:GroupMessage:1", 1500, retain_ratio=.65)
    checkpoint = app.history.repository.checkpoint("bot:GroupMessage:1")
    assert checkpoint and len(contexts) > 1
    raw_tokens = sum(ConversationHistory.size(item) for item in contexts[1:])
    assert 1500 * .5 < raw_tokens <= 1500 * .65
    before = json.dumps(contexts, ensure_ascii=False)
    receive(app, "short", "补充")
    next_contexts = await app.history.contexts("bot:GroupMessage:1", 1500, retain_ratio=.65)
    assert json.dumps(next_contexts[:-1], ensure_ascii=False) == before
    assert app.history.repository.checkpoint("bot:GroupMessage:1") == checkpoint


async def test_single_oversized_message_is_kept_whole(app):
    content = "超大消息" * 5000
    receive(app, text=content)
    result = await app.history.contexts("bot:GroupMessage:1", 1000)
    assert content in result[-1]["content"]


async def test_existing_digest_checkpoint_does_not_shrink_a_long_window(app):
    first, _ = receive(app, text="合成原文：下周安排")
    rows = app.history.repository.events("bot:GroupMessage:1")
    app.history.repository.compress("bot:GroupMessage:1", rows[0]["id"], rows[0]["id"],
                                    "简短摘要", [first["message_id"]])
    contexts = await app.history.contexts("bot:GroupMessage:1", 128000)
    assert "合成原文：下周安排" in contexts[0]["content"]
    app.history.model.__wrapped__.assert_not_awaited()
