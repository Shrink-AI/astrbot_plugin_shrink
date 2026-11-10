from unittest.mock import AsyncMock

import pytest
from shrink.application import Application
from shrink import prompts


@pytest.mark.asyncio
async def test_persona_scope_fallback_and_receipt(tmp_path):
    model = AsyncMock(return_value="配置写好了，运行中是否生效还没确认")
    app = Application(tmp_path, model, model, model)
    try:
        app.outbound.resolve_scope = lambda scope: {"source":"qq:private:1","user_id":"qq:1"}
        app.people.card = lambda *args: "接收者"
        text = await app.outbound.compose("配置已写入，运行中未核验", request="写吧",
                                          scope={"session":"platform:FriendMessage:1","sender":"1"})
        assert "没确认" in text
        call = model.call_args.args
        assert "配置已写入" in call[0] and "石英" in call[1]
        assert "{{facts}}" not in call[0]
        app.outbound.record({"sender":"1"}, text, "one")
        app.outbound.record({"sender":"1"}, text, "one")
        assert len(app.messages.recent_bot("qq:private:1", 5)) == 1
        model.side_effect = RuntimeError("offline")
        assert await app.outbound.compose("task facts") == prompts.render("outbound.fallback")
    finally:
        app.database.close()


@pytest.mark.asyncio
async def test_tool_reply_uses_shared_expression_without_truncation(tmp_path):
    model = AsyncMock(return_value="结果和重要限制。" * 20)
    app = Application(tmp_path, model, model, model)
    try:
        result = await app.expression.finish("raw", user_text="详细解释", form="tool_result",
                                             reason="direct", recent=[])
        assert len(result.text) > 60
        model.assert_awaited_once()
    finally:
        app.database.close()


@pytest.mark.asyncio
async def test_context_failure_and_tool_rewrite_failure_keep_result(tmp_path):
    model = AsyncMock(side_effect=RuntimeError("offline"))
    app = Application(tmp_path, model, model, model)
    try:
        def broken_scope(scope):
            raise ValueError("stale platform")
        app.outbound.resolve_scope = broken_scope
        assert await app.outbound.compose("facts") == prompts.render("outbound.fallback")
        result = await app.outbound.compose("写入成功，运行状态未验证", kind="tool_result")
        assert result == "写入成功，运行状态未验证"
    finally:
        app.database.close()
