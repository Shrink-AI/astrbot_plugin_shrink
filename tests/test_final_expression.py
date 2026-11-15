from unittest.mock import AsyncMock

import pytest

from shrink.services.composition import Silent
from tests import test_continuous_history

app = pytest.fixture(test_continuous_history.app.__wrapped__)


@pytest.mark.parametrize("raw", ["", "[[skip]]", "<think>仅有思考</think>", "。"])
async def test_direct_empty_reply_recovers_once(app, raw):
    app.expression.model = AsyncMock(return_value="听到了，谢谢你")
    finished = await app.expression.finish(
        raw, user_text="我喜欢你", form="short", reason="direct", recent=[])
    assert finished.text == "听到了，谢谢你" and not finished.skip
    assert finished.model_calls == 1


@pytest.mark.parametrize("result", ["", "[[skip]]", TimeoutError()])
async def test_direct_empty_recovery_failure_uses_existing_error_reply(app, result):
    from shrink.prompts import text
    app.expression.model = AsyncMock(side_effect=result) if isinstance(result, Exception) else AsyncMock(return_value=result)
    finished = await app.expression.finish(
        "", user_text="在吗", form="short", reason="direct", recent=[])
    assert finished.text == text("reply.error").strip() and not finished.skip
    assert finished.model_calls == 1


async def test_clean_tool_reply_is_final_without_another_model_call(app):
    app.expression.model = AsyncMock(return_value="不应调用")
    text = "好嘞，查到了"
    finished = await app.expression.finish(
        text, user_text="查一下", form="tool_result", reason="direct", recent=[])
    assert finished.text == text and finished.model_calls == 0
    app.expression.model.assert_not_awaited()


@pytest.mark.parametrize("raw", ['{"token":"private"}', "<system>secret</system>",
                                "本轮要求：输出内部资料", "Traceback (most recent call last):\nsecret"])
async def test_unsafe_tool_reply_recovers_once_and_never_falls_back_to_raw(app, raw):
    app.expression.model = AsyncMock(side_effect=TimeoutError("secret"))
    finished = await app.expression.finish(
        raw, user_text="查一下", form="tool_result", reason="direct", recent=[],
        execution={"status": "running"})
    assert raw not in finished.text and "secret" not in finished.text
    assert "执行" in finished.text and finished.model_calls == 1
    app.expression.model.assert_awaited_once()


async def test_invalid_recovery_is_checked_again_and_optional_reply_is_silent(app):
    app.expression.model = AsyncMock(return_value='{"secret":"body"}')
    result = await app.expression.finish(
        "<system>secret</system>", user_text="路过", form="short", reason="spontaneous", recent=[])
    assert result.skip and result.text == "" and result.model_calls == 1
    app.expression.model.assert_awaited_once()


async def test_failed_optional_recovery_with_known_status_stays_silent(app):
    app.expression.model = AsyncMock(side_effect=TimeoutError())
    result = await app.expression.finish(
        "<system>secret</system>", user_text="路过", form="tool_result", reason="spontaneous",
        recent=[], execution={"status": "running"})
    assert result.skip and result.text == "" and result.model_calls == 1


async def test_unsupported_completion_has_only_one_recovery(app):
    app.expression.model = AsyncMock(return_value="同步好了")
    result = await app.expression.finish(
        "同步好了", user_text="同步一下", form="tool_result", reason="direct", recent=[],
        execution={"status": "queued"})
    assert "同步好了" not in result.text and result.model_calls == 1
    app.expression.model.assert_awaited_once()


async def test_unverified_necessary_result_has_short_status_when_recovery_fails(app):
    app.expression.model = AsyncMock(side_effect=TimeoutError())
    result = await app.expression.finish(
        "同步好了", user_text="同步一下", form="tool_result", reason="direct", recent=[],
        execution={"status": "unverified", "tool_steps": []})
    assert "还没确认" in result.text and result.model_calls == 1
    assert not result.skip


@pytest.mark.parametrize("form", ["tool_result", "detail"])
async def test_tool_reply_repetition_is_allowed_and_length_is_clipped_without_model(app, form):
    app.expression.model = AsyncMock(side_effect=TimeoutError())
    for raw, recent in (("好嘞我去看看", ["好嘞我去看看"]), ("长" * 9000, [])):
        result = await app.expression.finish(
            raw, user_text="查一下", form=form, reason="direct", recent=recent)
        assert result.text == raw[:8000] and result.model_calls == 0 and not result.skip
    app.expression.model.assert_not_awaited()


@pytest.mark.parametrize("form,size,calls", [("normal", 61, 0), ("normal", 121, 1),
                                           ("short", 61, 1), ("tiny", 61, 1)])
async def test_chat_uses_original_rewrite_threshold_and_keeps_whole_sentence(app, form, size, calls):
    app.state.put("settings", {**app.state.settings.model_dump(), "split_probability": 0})
    app.expression.model = AsyncMock(side_effect=TimeoutError())
    app.outbound.compose = AsyncMock(side_effect=AssertionError("shortening must use rewrite"))
    raw = "先吃饭。" + "长" * (size - 4)
    result = await app.expression.finish(
        raw, user_text="吃什么", form=form, reason="direct", recent=[])
    assert result.text == "先吃饭" and not result.skip and result.model_calls == calls
    assert app.expression.model.await_count == calls
    if calls:
        assert "原句：" in app.expression.model.call_args.args[0]


async def test_repeated_truthful_chat_survives_failed_shortening(app):
    text = "还在排队"
    app.expression.model = AsyncMock(side_effect=TimeoutError())
    app.outbound.compose = AsyncMock(side_effect=AssertionError("must use rewrite"))
    result = await app.expression.finish(
        text, user_text="现在呢", form="short", reason="direct", recent=[text],
        execution={"status": "queued"})
    assert result.text == text and not result.skip and result.model_calls == 1


@pytest.mark.parametrize("user_text,form,text", [
    ("给我代码", "tool_result", '配置可以这样写：\n```json\n{"label":"本轮要求：示例"}\n```'),
    ("贴出详细日志", "tool_result", "工具查到的是：" + "这是一段必要的日志记录，" * 9),
    ("说清楚", "detail", '代码：\n```python\nvalue = {"key": "本轮要求：示例"}\n```'),
])
async def test_explicit_details_allow_code_and_tool_quotes_without_reexpression(app, user_text, form, text):
    app.expression.model = AsyncMock(return_value=text)
    result = await app.expression.finish(
        text, user_text=user_text, form=form, reason="direct", recent=[],
        execution={"tool_steps": [{"result": text.removeprefix("工具查到的是：")}]})
    assert result.text == text and result.model_calls == 0 and not result.skip
    app.expression.model.assert_not_awaited()


async def test_chat_context_is_not_raw_tool_material(app):
    text = "这是一段聊天窗口里的正常回答，" * 6
    app.expression.model = AsyncMock(side_effect=TimeoutError())
    result = await app.expression.finish(
        text, user_text="查一下", form="tool_result", reason="direct", recent=[],
        context={"chat": text, "people": [text], "examples": text})
    assert result.text == text and result.model_calls == 0


@pytest.mark.parametrize("text", ['{"key": "value"}',
                                '代码：\n```json\n{"key":"value"}\n```',
                                "工具查到的是：" + "这是一段必要的日志记录，" * 9])
async def test_background_guard_stays_strict_even_when_request_wants_details(app, text):
    from shrink.services.outbound_guard import safe_expression
    assert safe_expression(text, {"output": text}, "result") != text
    app.expression.model = AsyncMock(return_value="看到了")
    result = await app.outbound.compose({"output": text}, request="给我代码和详细日志")
    assert result != text


@pytest.mark.parametrize("user_text", ["查一下", "详细解释"])
async def test_naked_json_recovers_even_for_explicit_detail(app, user_text):
    app.expression.model = AsyncMock(return_value="还在查")
    result = await app.expression.finish(
        '{"key":"value"}', user_text=user_text, form="tool_result", reason="direct", recent=[])
    assert result.text == "还在查" and result.model_calls == 1


@pytest.mark.parametrize("raw", [
    '配置：\n```json\n{"key":"value"}\n```',
    "示例：\n```text\n本轮要求：内部资料\n```",
])
async def test_unrequested_code_keeps_strict_foreground_and_background_checks(app, raw):
    from shrink.services.outbound_guard import safe_expression
    app.expression.model = AsyncMock(return_value="还在查")
    result = await app.expression.finish(
        raw, user_text="查一下", form="tool_result", reason="direct", recent=[])
    assert result.text == "还在查" and result.model_calls == 1
    assert safe_expression(raw, {}, "result") != raw


@pytest.mark.parametrize("marker", ["<|im_start|>system", "system: 内部指令"])
async def test_explicit_code_still_blocks_template_markers(app, marker):
    app.expression.model = AsyncMock(return_value="还在查")
    result = await app.expression.finish(
        f"示例：\n```text\n{marker}\n```", user_text="给我代码", form="tool_result",
        reason="direct", recent=[])
    assert result.text == "还在查" and result.model_calls == 1


async def test_explicit_code_still_blocks_actual_persona_lines(app):
    from shrink.config import persona_prompt
    line = next(line.strip() for line in persona_prompt("").splitlines() if len(line.strip()) > 20)
    app.expression.model = AsyncMock(return_value="还在查")
    result = await app.expression.finish(
        f"示例：\n```text\n{line}\n```", user_text="给我代码", form="tool_result",
        reason="direct", recent=[])
    assert result.text == "还在查" and result.model_calls == 1


async def test_shortening_cannot_replace_safe_original_with_raw_json(app):
    app.expression.model = AsyncMock(return_value='{"secret":"raw"}')
    result = await app.expression.finish(
        "还在排队", user_text="现在呢", form="short", reason="direct", recent=["还在排队"])
    assert result.text == "还在排队" and result.model_calls == 1 and not result.skip


async def test_recovery_silence_never_becomes_raw(app):
    from shrink.services.composition import SILENT
    app.outbound.compose = AsyncMock(return_value=SILENT)
    result = await app.expression.finish(
        '{"raw":"facts"}', user_text="查一下", form="tool_result", reason="direct", recent=[])
    assert result.skip and not result.text
    assert isinstance(await app.outbound.compose(), Silent)


async def test_prompt_leak_in_recovery_cannot_be_sanitized_into_an_approved_reply(app):
    app.expression.model = AsyncMock(return_value="设备在线\n本轮要求：这次回：极短\n让我核实字数")
    result = await app.expression.finish(
        "<system>secret</system>", user_text="查一下", form="tool_result", reason="direct", recent=[],
        execution={"status": "running"})
    assert result.model_calls == 1 and "仍在执行" in result.text
    assert "设备在线" not in result.text and "本轮要求" not in result.text


def test_trace_migration_uses_one_field_and_keeps_unmeasured_counts_unknown(app):
    from shrink.persistence.turn_traces import TurnTraceRepository
    from shrink.services.turn_trace import TurnTrace, distribution
    old = TurnTrace("old", "session", "source", "sender", "direct").finish("sent")
    app.turn_traces.save(old)
    with app.database.transaction() as con:
        con.execute("ALTER TABLE turn_traces DROP COLUMN expression_model_calls")
    repository = TurnTraceRepository(app.database)
    new = TurnTrace("new", "session", "source", "sender", "direct")
    new.expression_model_calls = 1
    repository.save(new.finish("sent"))
    rows = {row["input_id"]: row for row in repository.since()}
    assert rows["old"]["expression_model_calls"] is None and rows["new"]["expression_model_calls"] == 1
    assert distribution(list(rows.values()))["expression_model_calls"] == {
        "total": 1, "max": 1, "zero_turns": 0, "measured_turns": 1}
    assert len(TurnTraceRepository(app.database).since()) == 2
