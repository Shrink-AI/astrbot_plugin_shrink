import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from shrink.persistence.history import HistoryRepository
from shrink.services.tool_projection import expression_facts
from tests.test_continuous_history import app, receive  # noqa: F401
from tests.test_agent_tasks import runtime  # noqa: F401


async def test_tool_history_projects_large_payload_but_preserves_originals(app):
    receive(app, text="整理项目")
    session = "bot:GroupMessage:1"
    argument = {"body": "UNRELATED_DETAIL" * 10000}
    raw = json.dumps({"arguments": argument, "result": "COMPLETE_RECEIPT" * 10000})
    app.history.tool_start(session, "call", "write", argument)
    app.history.tool_end(session, "call", raw)
    receive(app, "m2", "你在吗", sender="200")
    rendered = await app.history.contexts(session, 10000)
    assert len(json.dumps(rendered)) < 4000
    calls = next(m["tool_calls"] for m in rendered if "tool_calls" in m)
    output = next(m for m in rendered if m["role"] == "tool")
    assert calls[0]["id"] == output["tool_call_id"]
    assert "record_ref" in output["content"] and "qq:200" in rendered[-1]["content"]
    assert app.messages.get_many(["tool:call"])[0]["text"] == raw
    original = next(r for r in app.history.repository.events(session) if r["tool_calls"])
    assert json.loads(json.loads(original["tool_calls"])[0]["function"]["arguments"]) == argument


async def test_legacy_tool_summary_invalidates_once_without_deleting_chat(app):
    receive(app)
    session = "bot:GroupMessage:1"
    app.history.tool_start(session, "old", "query", {"q": "x"})
    app.history.tool_end(session, "old", "raw")
    app.history.repository.compress(session, 1, 3, "OLD_TOOL_DUMP", ["tool:old"])
    with app.database.transaction() as con:
        con.execute("DELETE FROM kv WHERE key='history_tool_projection_v1'")
    repo = HistoryRepository(app.database)
    assert repo.checkpoint(session) is None
    assert len(repo.events(session)) == 3
    repo.compress(session, 1, 3, "NEW_COMPACT", ["tool:old"])
    assert HistoryRepository(app.database).checkpoint(session)["summary"] == "NEW_COMPACT"


@pytest.mark.parametrize("action", ["tools", "guide", "create", "submit", "ask_user"])
async def test_foreground_cannot_bypass_dispatch_boundary(runtime, action):
    op, event, _ = runtime
    event.set_extra("shrink_reply", True)
    with pytest.raises(ValueError, match="同构体"):
        await op.orchestrate(event, action, {})
    assert op.list() == []


async def test_dispatch_keeps_original_request_and_only_quoted_context(runtime):
    op, event, _ = runtime
    event.set_extra("shrink_reply", True)
    event.set_extra("shrink_turn", SimpleNamespace(
        text="原始授权要求", source="qq:group:10", quote={"text": "指定项目资料"}))
    op.invoke_agent = AsyncMock(return_value="done")
    result = await op.orchestrate(event, "dispatch", {"request": "整理指定项目，验收卡片无重复"})
    await op.running[result["task_id"]]
    request = json.loads(op.detail(result["task_id"])["request"])
    assert request["original_request"] == "原始授权要求"
    assert request["quoted_context"]["text"] == "指定项目资料"


def test_large_expression_input_has_bounded_evidence_and_reference():
    facts = {"status": "completed", "task_id": "id", "result_ref": "task:id",
             "result_summary_unverified": "answer" * 10000,
             "tool_records": [{"action": "tool_end:test", "result": "body" * 30000}] * 20}
    projected = expression_facts(facts)
    assert len(json.dumps(projected, ensure_ascii=False)) <= 6000
    assert projected["result_ref"] == "task:id" and projected["status"] == "completed"
    assert len(facts["tool_records"]) == 20
