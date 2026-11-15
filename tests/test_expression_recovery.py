from unittest.mock import AsyncMock

import pytest

from shrink.services.expression import ExpressionService, choose_form, wants_detail
from tests import test_continuous_history

app = pytest.fixture(test_continuous_history.app.__wrapped__)


@pytest.mark.parametrize("boundary", ["，", "、", "；", "：", " "])
def test_trim_prefers_last_clause_inside_limit(boundary):
    prefix = "甲" * 20 + boundary + "乙" * 20
    assert ExpressionService.trim(prefix + boundary + "丙" * 80) == prefix


def test_trim_without_boundary_still_enforces_limit():
    assert ExpressionService.trim("甲" * 90) == "甲" * 60


@pytest.mark.parametrize("text", ["你看这个方案怎么样", "看一下代码", "详情"])
def test_exposure_permission_does_not_choose_long_chat_form(text):
    assert not wants_detail(text)
    assert choose_form(text, 0) == "tiny"


async def test_explicit_tool_quote_permission_is_separate_from_chat_form(app):
    text = '可以这样写：\n```json\n{"value":"本轮要求：示例"}\n```'
    app.expression.model = AsyncMock(side_effect=AssertionError("already safe"))
    result = await app.expression.finish(text, user_text="看一下代码", form="tool_result",
                                         reason="direct", recent=[])
    assert result.text == text and result.model_calls == 0


async def test_unverified_completion_restores_specific_recovery_instruction(app):
    app.outbound.compose = AsyncMock(return_value="执行情况还没确认。")
    await app.expression.finish("同步好了", user_text="同步一下", form="tool_result",
                                reason="direct", recent=[], execution={"status": "unverified"})
    args = app.outbound.compose.call_args.kwargs
    assert args["kind"] == "execution_status"
    for phrase in ("只说明可确认的状态", "缺少的回执", "自然、简短、温和", "不责备用户",
                   "没有记录不能推断未执行", "不能声称完成", "不承诺自动重试"):
        assert phrase in args["instruction"]
