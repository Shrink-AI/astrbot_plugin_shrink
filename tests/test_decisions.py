import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from shrink.persistence.tasks import Tasks
from shrink.services.decisions import DecisionContext, capabilities, guidance
from shrink.services.task_states import TaskStates
from tests import test_continuous_history
from tests.test_continuous_history import receive

app = test_continuous_history.app


def answer(event, **updates):
    return {"intent": "chat", "route": "reply", "conversation": event["session"], "topic": None,
            "task": None, "evidence": [event["id"]], "capability_candidates": [],
            "notify": "normal", "confidence": .8, **updates}


def backend(app, value):
    fn = AsyncMock(return_value=value)
    fn.version = "test-v1"
    app.decisions.backends["haiku"] = fn
    return fn


def task(app, session="bot:GroupMessage:1", sender="100"):
    states = TaskStates(Tasks(app.database))
    states.migrate()
    return states.repository.create("整理资料", session, sender, "test")


async def test_rules_direct_skip_models_and_persist_ratio(app):
    event, _ = receive(app, text="你好")
    model = backend(app, {})
    result = await app.decisions.decide(event["id"], DecisionContext(direct=True))
    assert result["rule_direct"] and result["decision"]["route"] == "reply"
    model.assert_not_awaited()
    assert app.decisions.repository.stats() == {"total": 1, "direct": 1, "rule_direct_ratio": 1.0}
    row = app.decisions.repository.list()[0]
    assert row["backend"] == "rules" and row["input_id"] == event["id"]


@pytest.mark.parametrize("change", [
    {"task": "made-up", "route": "attach_task", "intent": "supplement"},
    {"conversation": "other:room"},
    {"permission": "admin"},
    {"topic": "imaginary"},
    {"route": "stop"},
])
async def test_d02_invalid_or_authority_expanding_model_output_is_rejected(app, change):
    event, _ = receive(app, text="接着整理资料")
    model = backend(app, answer(event, **change))
    result = await app.decisions.decide(event["id"], DecisionContext(direct=True, admin=True))
    assert result["fallback"] and result["decision"]["route"] == "reply"
    assert result["decision"]["task"] is None
    model.assert_awaited_once()
    assert app.inbox.repository.get(event["id"])["processed"] is None


@pytest.mark.usefixtures("app")
async def test_format_normalization_keeps_model_decision_and_persists_known_references(request):
    application = request.getfixturevalue("app")
    event, _ = receive(application, text="帮我查天气")
    backend(application, answer(event, intent="query", evidence=["event.text", "weather"],
                       capability_candidates=["unknown", "weather"], confidence=.9))
    result = await application.decisions.decide(
        event["id"], DecisionContext(direct=True), [{"name": "weather", "description": "查天气"}])
    assert not result["fallback"] and result["reason"] == "validated"
    assert result["decision"]["intent"] == "query"
    assert result["decision"]["evidence"] == [event["id"]]
    assert result["decision"]["capability_candidates"] == ["weather"]
    saved = application.decisions.repository.list()[0]
    assert saved["decision"]["evidence"] == [event["id"]]


@pytest.mark.parametrize("mode", ["timeout", "format"])
@pytest.mark.parametrize("context,route", [
    (DecisionContext(private=True), "reply"),
    (DecisionContext(direct=True), "reply"),
    (DecisionContext(), "silent"),
    (DecisionContext(participation="spontaneous"), "reply"),
    (DecisionContext(participation="engaged"), "reply"),
])
async def test_d02_timeout_and_format_fallback_keep_input_and_same_retry_identity(app, mode, context, route):
    event, _ = receive(app, text="帮我看看这个")
    app.state.put("settings", {**app.state.settings.model_dump(), "decision_timeout": .01})
    model = backend(app, "not json")
    if mode == "timeout":
        async def wait(*args):
            await asyncio.Event().wait()
        model.side_effect = wait
    result = await app.decisions.decide(event["id"], context)
    assert result["fallback"] and result["decision"]["route"] == route
    assert result["reason"] == ("timeout" if mode == "timeout" else "invalid_or_unavailable")
    assert app.inbox.repository.get(event["id"])["processed"] is None
    model.side_effect = None
    model.return_value = answer(event, route=route, notify="silent" if route == "silent" else "normal")
    retry = await app.decisions.decide(event["id"], context)
    assert not retry["fallback"]
    assert {r["input_id"] for r in app.decisions.repository.list()} == {event["id"]}
    assert len(app.database.rows("SELECT * FROM input_events")) == 1


async def test_classifier_receives_bounded_current_conversation_and_participation(app):
    session = "bot:GroupMessage:1"
    old, _ = receive(app, "withdrawn", "撤回原文")
    app.history.revise(session, old["message_id"], sender="qq:100", identity="remove")
    receive(app, "other-instance", "跨实例不能看到", session="other:GroupMessage:1")
    for i in range(9):
        receive(app, f"prior{i}", "讨论内容" * 100, sender="200")
    app.history.tool_start(session, "call", "tool", {})
    app.history.tool_end(session, "call", "无关工具回执")
    event, _ = receive(app, "current", "好的")
    model = backend(app, answer(event))
    await app.decisions.decide(event["id"], DecisionContext(participation="engaged"))
    payload = model.call_args.args[0]
    assert payload["rules"]["participation"] == "engaged"
    assert len(payload["recent_chat"]) == 6
    assert all(len(r["text"]) <= 240 for r in payload["recent_chat"])
    assert all(r["speaker"] == "qq:200" for r in payload["recent_chat"])
    assert not any(t in str(payload["recent_chat"]) for t in ("撤回原文", "跨实例", "无关工具", "好的"))


async def test_stop_priority_and_ambiguous_target_only_asks(app):
    event, _ = receive(app, text="停止")
    model = backend(app, {})
    context = DecisionContext(direct=True, admin=True)
    first = task(app)
    result = await app.decisions.decide(event["id"], context)
    assert result["decision"]["route"] == "stop" and result["decision"]["task"] == first
    task(app)
    result = await app.decisions.decide(event["id"], context)
    assert result["decision"]["route"] == "ask" and result["reason"] == "stop_ambiguous"
    assert "只询问" in guidance(result)
    model.assert_not_awaited()


async def test_binding_cannot_be_reassigned_and_ownership_is_not_model_authority(app):
    from shrink.persistence.task_inputs import TaskInputsRepository
    event, _ = receive(app, text="只改这个任务")
    one, two = task(app), task(app)
    TaskInputsRepository(app.database).receive(one, event["session"], event["sender"], "m1", "原文")
    model = backend(app, answer(event, task=two))
    result = await app.decisions.decide(event["id"], DecisionContext(direct=True, admin=True))
    assert result["decision"]["task"] == one and result["reason"] == "binding"
    model.assert_not_awaited()
    with pytest.raises(ValueError, match="关联"):
        app.decisions.append_target(event["id"], two, event["session"], event["sender"])
    with pytest.raises(ValueError, match="身份"):
        app.decisions.append_target(event["id"], one, event["session"], "200")
    outsider = task(app, sender="200")
    assert outsider not in {t["id"] for t in app.decisions.recall(event, DecisionContext(), [])["tasks"]}


async def test_model_cannot_grant_task_permission(app):
    event, _ = receive(app, text="替我执行这个任务")
    own = task(app)
    backend(app, answer(event, task=own, intent="supplement", route="attach_task"))
    result = await app.decisions.decide(event["id"], DecisionContext(direct=True, admin=False))
    assert result["fallback"] and result["decision"]["route"] == "reply"


async def test_c09_actual_tools_shortlist_does_not_remove_further_discovery(app):
    event, _ = receive(app, text="查天气")
    backend(app, answer(event))
    catalog = [{"name": f"weather{i}", "description": "查天气"} for i in range(8)]
    result = await app.decisions.decide(event["id"], DecisionContext(direct=True), catalog)
    selected = app.decisions.select_capabilities(result, event["text"], catalog)
    assert len(selected) == 5 and len(catalog) == 8
    assert "未列出不代表不可用" in guidance(result)
    assert "weather7" in capabilities("weather7", catalog, 1)
    assert capabilities("没有匹配词", catalog) == []
    assert len(app.decisions.repository.list()[0]["candidates"]["capabilities"]) == 8


async def test_model_association_replaces_keywords_but_never_opens_private_history(app):
    from shrink.services.turn_control import is_task_followup
    event, _ = receive(app, text="你怎么看这件事")
    app.messages.raw("elsewhere", "qq:group:2", "讨论这件事", sender="qq:100")
    app.messages.raw("secret", "qq:private:100", "私下资料", sender="qq:100")
    event["metadata"] = {"platform": "qq", "source": "qq:group:1"}
    with app.database.transaction() as con:
        con.execute("UPDATE input_events SET metadata=? WHERE id=?", (json.dumps(event["metadata"]), event["id"]))
    backend(app, answer(event, topic="elsewhere", intent="query"))
    result = await app.decisions.decide(event["id"], DecisionContext(direct=True))
    assert not result["fallback"]
    found = app.recent.find("qq:100", "qq:group:1", event["text"], decision=result)
    assert found.asked and "私下资料" not in str(found.lines)
    assert "secret" not in {t["id"] for t in app.decisions.repository.list()[0]["candidates"]["topics"]}
    assert not is_task_followup("部署", result)
    assert is_task_followup("部署", {**result, "fallback": True})


async def test_only_selected_backend_called_and_scores_remain_distinct(app):
    event, _ = receive(app, text="帮我选择")
    haiku = backend(app, answer(event))
    laya = AsyncMock(return_value=answer(event))
    laya.version = "local-v1"
    app.decisions.backends["laya"] = laya
    first = await app.decisions.decide(event["id"], DecisionContext(direct=True))
    assert first["confidence_kind"] == "self_report"
    laya.assert_not_awaited()
    app.state.put("settings", {**app.state.settings.model_dump(), "decision_backend": "laya"})
    second = await app.decisions.decide(event["id"], DecisionContext(direct=True))
    assert second["confidence_kind"] == "probability" and second["version"] == "local-v1"
    haiku.assert_awaited_once()
    laya.assert_awaited_once()


async def test_decision_records_expire_without_deleting_raw_input(app):
    event, _ = receive(app, text="你好")
    await app.decisions.decide(event["id"], DecisionContext(direct=True))
    with app.database.transaction() as con:
        con.execute("UPDATE decisions SET created=0")
    await app.decisions.decide(event["id"], DecisionContext(direct=True))
    assert len(app.decisions.repository.list()) == 1
    assert app.inbox.repository.get(event["id"])["text"] == "你好"


async def test_haiku_uses_configured_host_provider_and_laya_contract(app, monkeypatch):
    from shrink.adapters.decision_backends import HaikuDecisionBackend, LayaDecisionBackend
    event, _ = receive(app)
    provider = SimpleNamespace(text_chat=AsyncMock(return_value=SimpleNamespace(completion_text=json.dumps(answer(event)))))
    context = SimpleNamespace(get_provider_by_id=lambda name: provider if name == "shrink/claude-haiku-5-5" else None)
    result = await HaikuDecisionBackend(context, app.state)({"event": event})
    assert json.loads(result)["conversation"] == event["session"]
    assert provider.text_chat.await_args.kwargs["contexts"] == []
    from httpx import AsyncClient, MockTransport, Response
    def handler(request):
        assert json.loads(request.content)["schema"]["properties"]["intent"]
        return Response(200, json=answer(event))
    monkeypatch.setattr("shrink.adapters.decision_backends.httpx.AsyncClient",
                        lambda **kwargs: AsyncClient(transport=MockTransport(handler), **kwargs))
    app.state.put("settings", {**app.state.settings.model_dump(), "decision_laya_endpoint": "http://local/decision"})
    assert (await LayaDecisionBackend(app.state)({"event": event}))["task"] is None


@pytest.mark.parametrize("reason", ["quiet", "group_blacklisted", "bot_quiet", "addressed_other",
                                    "other_conversation_followup"])
async def test_participation_safety_rules_cannot_be_overridden(app, reason):
    event, _ = receive(app, text="聊个别的")
    model = backend(app, answer(event))
    result = await app.decisions.decide(event["id"], DecisionContext(participation=reason))
    assert result["rule_direct"] and result["decision"]["route"] == "silent"
    model.assert_not_awaited()


async def test_duplicate_rule_does_not_call_backend(app):
    event, _ = receive(app)
    app.inbox.repository.complete([event["id"]])
    model = backend(app, answer(event))
    result = await app.decisions.decide(event["id"], DecisionContext(direct=True))
    assert result["reason"] == "duplicate" and result["decision"]["route"] == "silent"
    model.assert_not_awaited()


async def test_t01_task_route_is_advice_without_executing_or_waiting(app):
    event, _ = receive(app, text="帮我整理这些资料")
    backend(app, answer(event, intent="task", route="create_task"))
    result = await asyncio.wait_for(app.decisions.decide(event["id"], DecisionContext(direct=True, admin=True)), 1)
    assert result["decision"]["route"] == "create_task"
    assert "主会话不等待执行" in guidance(result)
    assert not app.database.rows("SELECT name FROM sqlite_master WHERE name='operation_tasks'")


async def test_append_input_uses_selected_original_task_and_rejects_retargeting(app):
    from shrink.adapters.agent_tasks import AgentTasks
    from shrink.persistence.task_inputs import TaskInputsRepository
    from shrink.services.task_inputs import TaskInputs
    event, _ = receive(app, text="补充刚才的要求")
    one, two = task(app), task(app)
    backend(app, answer(event, intent="supplement", route="attach_task", task=one, notify="merge"))
    await app.decisions.decide(event["id"], DecisionContext(direct=True, admin=True))
    operations = SimpleNamespace(app=app, task_inputs=TaskInputs(TaskInputsRepository(app.database)))
    incoming = SimpleNamespace(is_admin=lambda: True, message_obj=SimpleNamespace(message_id="m1"),
                               unified_msg_origin=event["session"], get_sender_id=lambda: event["sender"],
                               get_message_str=lambda: event["text"])
    with pytest.raises(ValueError, match="关联"):
        await AgentTasks.orchestrate(operations, incoming, "append_input", {"task_id": two, "text": "改变目标"})
    result = await AgentTasks.orchestrate(operations, incoming, "append_input", {"text": "模型改写"})
    assert result["status"] == "received" and result["task_id"] == one
    assert operations.task_inputs.pending(one)[0]["text"] == event["text"]


async def test_authenticated_dashboard_can_read_decisions_and_ratio(app, tmp_path):
    import httpx
    from shrink.api.app import create_app
    event, _ = receive(app, text="你好")
    await app.decisions.decide(event["id"], DecisionContext(direct=True))
    web = create_app(app, tmp_path, lambda: {"chat": [], "embedding": []})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web), base_url="http://127.0.0.1:6186",
                                headers={"origin": "http://127.0.0.1:6186"}) as client:
        assert (await client.get("/api/v1/decisions")).status_code == 401
        password = (app.database.root / "initial-password.txt").read_text().strip()
        assert (await client.post("/api/v1/login", json={"password": password})).status_code == 200
        result = (await client.get("/api/v1/decisions")).json()
    assert result["stats"]["rule_direct_ratio"] == 1
    assert result["records"][0]["input_id"] == event["id"]
    assert result["records"][0]["rule_direct"] is True


def test_registered_directory_excludes_disabled_and_long_execution_tools():
    from shrink.adapters.decision_backends import registered_catalog
    catalog = [SimpleNamespace(name=name, description="tool", active=active) for name, active in [
        ("shrink_lookup", True), ("hidden", False), ("astrbot_execute_shell", True), ("transfer_to_work", True)]]
    host = SimpleNamespace(get_llm_tool_manager=lambda: SimpleNamespace(
        get_full_tool_set=lambda: SimpleNamespace(tools=catalog)))
    assert [tool["name"] for tool in registered_catalog(host)] == ["shrink_lookup"]


async def test_classifier_context_is_bounded_without_truncating_saved_input(app):
    event, _ = receive(app, text="查天气" * 2000)
    model = backend(app, answer(event))
    catalog = [{"name": f"weather{i}", "description": "查天气" * 1000} for i in range(100)]
    await app.decisions.decide(event["id"], DecisionContext(direct=True), catalog)
    payload = model.await_args.args[0]
    assert len(payload["event"]["text"]) == 4000 and payload["event"]["text_truncated"]
    assert len(payload["candidates"]["capabilities"]) == 15
    assert max(len(t["description"]) for t in payload["candidates"]["capabilities"]) == 300
    assert app.inbox.repository.get(event["id"])["text"] == event["text"]
