import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from shrink.adapters.authorizations import AuthorizationAdapter
from tests.test_authorizations import SCOPE, SPEC, app, service, user, grant, confirmation  # noqa: F401


@pytest.mark.parametrize("device,allowed", [
    ({"id": "pc1", "revoked": False}, True),
    ({"id": "pc1", "access": "owner"}, True),
    ({"id": "pc1", "access": "delegated"}, True),
    ({"id": "pc1", "access": None}, False),
    ({"id": "pc1", "access": "denied"}, False),
    ({"id": "pc1", "revoked": True}, False),
    ({"id": "other"}, False),
])
async def test_real_devices_access_optional_field(app, service, device, allowed):
    adapter = AuthorizationAdapter.__new__(AuthorizationAdapter)
    adapter.service = service
    adapter.command = AsyncMock(return_value=[device])
    assert await adapter.access({**SPEC, "origin": "room", "administrator": "100"}, "100") is allowed


async def test_real_receipt_uses_saved_request_and_null_is_unknown(app, service):
    await grant(app, service)
    row = confirmation(service)
    request = {"action": "approve", "arguments": {"approval_id": "approval1", "session_id": "exec1", "allow": True}}
    service.command.side_effect = None
    service.command.return_value = {"state": "pending", "request": request, "result": None}
    assert (await service.process(row["id"]))["state"] == "unknown"
    service.command.return_value = {"state": "completed", "request": request, "result": {"ok": True}}
    assert (await service.process(row["id"]))["state"] == "confirmed"
    assert [c.args[1] for c in service.command.await_args_list].count("approve") == 1


async def test_unknown_category_never_matches_policy(app, service):
    await grant(app, service)
    row = confirmation(service, category="unknown")
    assert (await service.process(row["id"]))["state"] == "waiting_user"
    with pytest.raises(ValueError):
        await service.grant(user(app, "授权 pc1 exec1 unknown", "bad"), SCOPE, {**SPEC, "categories": ["unknown"]})


async def test_expiry_margin_prevents_approval_and_confirmation(app, service):
    await grant(app, service)
    row = confirmation(service, expires=time.time() + 10)
    assert (await service.process(row["id"]))["state"] == "held"
    service.command.assert_not_awaited()


@pytest.mark.parametrize("text,allow", [("同意", True), ("可以", True), ("不同意", False), ("拒绝", False)])
async def test_natural_answer_single_confirmation(app, service, text, allow):
    row = confirmation(service)
    await service.process(row["id"])
    await service.process(row["id"], user(app, text))
    call = next(c for c in service.command.await_args_list if c.args[1] == "approve")
    assert call.args[2]["allow"] is allow


@pytest.mark.parametrize("text,future", [
    ("同意，以后这类不用问了", False),
    ("同意，以后同类任务也不用再问了", True),
])
async def test_natural_continuous_policy_derived_from_confirmation(app, service, text, future):
    row = confirmation(service)
    await service.process(row["id"])
    await service.process(row["id"], user(app, text))
    policy = service.repository.policies()[0]
    assert (policy["device"], policy["administrator"], policy["execution_session"], policy["task"]) == ("pc1", "100", "exec1", "")
    assert policy["categories"] == ["execute"] and bool(policy["future"]) is future
    assert (await service.process(confirmation(service, "second")["id"]))["state"] == "confirmed"
    assert (await service.process(confirmation(service, "write", category="write")["id"]))["state"] == "waiting_user"


async def test_multiple_answers_require_unique_original_target(app, service):
    a, b = confirmation(service, "a"), confirmation(service, "b")
    for row in (a, b):
        await service.process(row["id"])
    with pytest.raises(ValueError):
        await service.process(a["id"], user(app, "同意", "vague"))
    await service.process(b["id"], user(app, "同意 b", "specific"))
    assert service.repository.event(a["id"])["operation_id"] is None


@pytest.mark.parametrize("category", ["grant_permission", "delete_data", "unknown"])
async def test_sensitive_or_unknown_category_does_not_get_implicit_policy(app, service, category):
    row = confirmation(service, category=category)
    await service.process(row["id"])
    result = await service.process(row["id"], user(app, "同意，以后这类不用问了"))
    assert not service.repository.policies()
    assert "持续授权" in result["reason"]


@pytest.mark.parametrize("text", ["同意，但是只读", "同意，不要执行", "同意？", "同意 a 但不是 b",
                                 "同意，先给我看看再决定", "同意，以后不用问了吗", "同意，别执行"])
async def test_qualified_or_conflicting_answer_does_not_approve(app, service, text):
    row = confirmation(service, "a")
    with pytest.raises(ValueError):
        await service.process(row["id"], user(app, text))
    service.command.assert_not_awaited()


async def test_receipt_wrong_request_and_expiry_edge_remain_unknown(app, service, monkeypatch):
    await grant(app, service)
    row = confirmation(service)
    operation = service.repository.reserve(row["id"], service.repository.policies()[0])
    service.command.side_effect = None
    service.command.return_value = {"state": "completed", "request": {"action": "approve", "arguments": {
        "session_id": "other", "approval_id": "approval1", "allow": True}}, "result": {"ok": True}}
    assert (await service.process(row["id"]))["state"] == "unknown"
    assert not service.execution_allowed(row["id"], operation + "other")
    service.command.return_value["request"]["arguments"]["session_id"] = "exec1"
    monkeypatch.setattr(time, "time", lambda: row["body"]["expires"] - 10)
    assert (await service.process(row["id"]))["state"] == "unknown"
    assert not service.execution_allowed(row["id"], operation)


async def test_reply_to_one_sent_question_resolves_multiple_objects(app, service):
    first, second = confirmation(service, "a"), confirmation(service, "b")
    for row in (first, second):
        await service.process(row["id"])
    box = app.notification_outbox
    notice = box.submit(SCOPE, "asked-a", {"confirmations": [{"confirmation_id": first["id"]}]}, kind="question")
    box.receipts.reserve(notice["id"])
    box.receipts.state(notice["id"], "platform_accepted", "是否允许设备 pc1 的操作 a？")
    incoming, _ = app.inbox.event(session="room", sender="100", external_id="reply", source="platform",
        text="同意", route="chat", metadata={"reply_to": "platform-message-42", "reply_text": "是否允许设备 pc1 的操作 a？"})
    assert (await service.process(first["id"], incoming["id"]))["state"] == "confirmed"
    assert service.repository.event(second["id"])["operation_id"] is None


async def test_natural_grant_never_expands_device_actor_or_future_without_words(app, service):
    first = confirmation(service)
    await service.process(first["id"], user(app, "同意，以后这类不用问了"))
    for args in ({"device": "pc2"}, {"execution_session": "exec2"}):
        row = confirmation(service, "other" + str(args), **args)
        assert (await service.process(row["id"]))["state"] in {"waiting_user", "held"}
    row = service.receive({**first["body"], "approval_id": "admin2"}, {**SCOPE, "sender": "200"})
    assert (await service.process(row["id"]))["state"] == "waiting_user"


async def test_validate_rechecks_actual_device_access_and_request_binding(app, service):
    from shrink.adapters.public_api import PublicAPI
    await grant(app, service)
    row = confirmation(service)
    operation = service.repository.reserve(row["id"], service.repository.policies()[0])
    api = PublicAPI(SimpleNamespace(application=app))
    request = {"protocol": 1, "version": 1, "capability": "authorization.confirmation",
               "action": "validate", "scope": SCOPE, "arguments": {
                   "confirmation_id": row["id"], "request_id": operation, "device": "pc1",
                   "execution_session": "exec1", "approval_id": "approval1", "allow": True}}
    assert (await api.invoke(request))["data"]["allowed"]
    request["arguments"]["allow"] = False
    assert not (await api.invoke(request))["data"]["allowed"]
    request["arguments"]["allow"] = True
    service.access.return_value = False
    assert not (await api.invoke(request))["data"]["allowed"]


@pytest.mark.parametrize("source", ["plugin", "task", "notification", "confirmation"])
async def test_natural_answer_cannot_use_model_or_tool_text(app, service, source):
    row = confirmation(service)
    with pytest.raises(ValueError, match="授权来源"):
        await service.process(row["id"], user(app, "同意，以后这类不用问了", source=source))
    assert not service.repository.policies()
    service.command.assert_not_awaited()


async def test_multiple_pending_objects_return_choices_without_guessing(app, service):
    first, second = confirmation(service, "a"), confirmation(service, "b")
    for row in (first, second):
        await service.process(row["id"])
    adapter = AuthorizationAdapter.__new__(AuthorizationAdapter)
    adapter.service, adapter.ask = service, AsyncMock()
    result = await adapter.manage("answer", {}, SCOPE, user(app, "同意"))
    assert result["state"] == "waiting_user" and set(result["confirmations"]) == {first["id"], second["id"]}
    service.command.assert_not_awaited()


@pytest.mark.parametrize("category,words", [("delete_data", "删除业务数据"), ("grant_permission", "新增权限")])
async def test_sensitive_continuous_scope_requires_exact_original_category(app, service, category, words):
    row = confirmation(service, category=category)
    await service.process(row["id"], user(app, f"同意，以后{words}不用问了"))
    assert service.repository.policies()[0]["categories"] == [category]
