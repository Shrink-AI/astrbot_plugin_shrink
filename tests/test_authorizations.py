import asyncio
import json
import time
from unittest.mock import AsyncMock

import pytest

from shrink.persistence.authorizations import Authorizations
from shrink.services.authorizations import AuthorizationService
from tests.test_continuous_history import app  # noqa: F401


@pytest.fixture
def service(app):
    service = app.authorizations
    service.is_admin = lambda user: user in {"100", "200"}
    service.access = AsyncMock(return_value=True)
    async def command(provider, action, args, scope):
        if action == "result":
            event = next(e for e in service.repository.events() if e["operation_id"] == args["request_id"])
            return {"state": "completed", "result": {"ok": True},
                    "request": {"action": "approve", "arguments": {"allow": bool(event["allow"]),
                    "session_id": event["body"]["execution_session"], "approval_id": event["body"]["approval_id"]}}}
        return {"state": "pending"}
    service.command = AsyncMock(side_effect=command)
    return service


SCOPE = {"session": "room", "sender": "100", "admin": True}
SPEC = {"device": "pc1", "provider": "desktop", "execution_session": "exec1", "categories": ["read", "execute"]}


def user(app, text, identity="message", source="platform", scope=None):
    scope = scope or SCOPE
    return app.inbox.event(session=scope["session"], sender=scope["sender"], external_id=identity,
                           source=source, text=text, route="chat")[0]["id"]


async def grant(app, service, future=False):
    identity = user(app, "授权 pc1 exec1 read,execute" + (" 未来同类任务" if future else ""))
    return await service.grant(identity, SCOPE, {**SPEC, "future": future})


def confirmation(service, approval="approval1", **kwargs):
    return service.receive({"provider": "desktop", "device": "pc1", "execution_session": "exec1",
                            "approval_id": approval, "category": "execute", "expires": time.time() + 3600,
                            **kwargs}, SCOPE)


async def test_a01_repeated_confirmations_auto_approve_once_and_audit(app, service):
    policy = await grant(app, service)
    row = confirmation(service)
    await asyncio.gather(service.process(row["id"]), service.process(row["id"]))
    assert service.repository.event(row["id"])["state"] == "confirmed"
    await service.process(row["id"])
    assert [c.args[1] for c in service.command.await_args_list].count("approve") == 1
    saved = service.repository.event(row["id"])
    assert saved["policy"] == policy["id"] and saved["policy_version"] == 1
    assert saved["operation_id"] and saved["receipt"]
    row2 = confirmation(service, "approval2")
    assert (await service.process(row2["id"]))["state"] == "confirmed"
    assert len(service.repository.events()) == 2


async def test_a02_future_scope_is_explicit_and_never_all_devices_or_admins(app, service):
    policy = await grant(app, service)
    row = confirmation(service, execution_session="exec2")
    assert (await service.process(row["id"]))["state"] == "waiting_user"
    with pytest.raises(ValueError, match="明确授权"):
        await service.grant(user(app, "以后不要再问", "vague"), SCOPE, {**SPEC, "future": True})
    future_input = user(app, "授权 pc1 exec1 read,execute 未来同类任务", "future")
    future = await service.grant(future_input, SCOPE, {**SPEC, "future": True})
    assert future["administrator"] == "100" and future["device"] == "pc1"
    assert (await service.process(row["id"]))["state"] == "confirmed"
    other = confirmation(service, "other", device="pc2")
    assert (await service.process(other["id"]))["state"] == "held"
    assert policy["future"] == 0


@pytest.mark.parametrize("category", ["delete_data", "grant_permission"])
async def test_scope_expansion_requires_original_user_decision(app, service, category):
    await grant(app, service)
    row = confirmation(service, category=category)
    result = await service.process(row["id"])
    assert result["state"] == "waiting_user" and result["reason"].startswith("needs_authorization")
    service.command.assert_not_awaited()


@pytest.mark.parametrize("mode", ["revoked", "paused", "expired", "device_access"])
async def test_revoked_expired_and_device_access_block_pending_commands(app, service, mode):
    policy = await grant(app, service)
    row = confirmation(service)
    identity = user(app, ("暂停授权 " if mode == "paused" else "撤销授权 ") + policy["id"], "revoke")
    if mode in {"revoked", "paused"}:
        service.change(identity, SCOPE, policy["id"], mode)
    elif mode == "expired":
        with app.database.transaction() as con:
            con.execute("UPDATE authorization_policies SET expires=0 WHERE id=?", (policy["id"],))
    else:
        service.repository.access_block("desktop", "pc1", "100", identity)
    assert (await service.process(row["id"]))["state"] == "held"
    service.command.assert_not_awaited()


async def test_revocation_during_access_check_wins_before_approval(app, service):
    policy = await grant(app, service)
    row = confirmation(service)
    async def access(*args):
        service.repository.change(policy["id"], "revoked", "100", "revocation")
        return True
    service.access.side_effect = access
    assert (await service.process(row["id"]))["state"] == "held"
    service.command.assert_not_awaited()


async def test_unknown_result_reconciles_same_operation_without_resending_after_restart(app, service):
    await grant(app, service)
    row = confirmation(service)
    service.command.side_effect = TimeoutError()
    result = await service.process(row["id"])
    assert result["state"] == "unknown"
    calls = [c for c in service.command.await_args_list if c.args[1] == "approve"]
    assert len(calls) == 1
    restored = AuthorizationService(Authorizations(app.database), app.inbox.repository)
    restored.is_admin, restored.access, restored.command = service.is_admin, service.access, service.command
    await restored.recover()
    assert len([c for c in service.command.await_args_list if c.args[1] == "approve"]) == 1
    assert restored.repository.event(row["id"])["operation_id"] == result["operation_id"]
    assert restored.repository.policies()[0]["state"] == "active"


async def test_a03_answer_binds_confirmation_task_and_cannot_be_reused(app, service):
    a, b = confirmation(service, "a"), confirmation(service, "b")
    wrong = user(app, "同意", "vague")
    with pytest.raises(ValueError, match="明确"):
        service.answer(wrong, SCOPE, a["id"], "")
    identity = user(app, "同意 a", "answer")
    with pytest.raises(ValueError, match="任务"):
        service.answer(identity, SCOPE, a["id"], "other-task")
    with pytest.raises(ValueError, match="明确"):
        service.answer(identity, SCOPE, b["id"], "")
    assert (await service.process(a["id"], identity))["state"] == "confirmed"
    assert service.repository.event(b["id"])["operation_id"] is None
    assert service.repository.event(a["id"])["body"]["origin"] == "room"


@pytest.mark.parametrize("source", ["plugin", "task", "notification", "confirmation"])
async def test_model_tool_memory_content_cannot_grant(app, service, source):
    identity = user(app, "授权 pc1 exec1 read,execute", source=source)
    with pytest.raises(ValueError, match="授权来源"):
        await service.grant(identity, SCOPE, SPEC)


async def test_policy_persists_across_database_reopen(app, service):
    from shrink.persistence.database import Database
    policy = await grant(app, service)
    second = Database(app.database.root)
    try:
        assert Authorizations(second).policy(policy["id"])["input_id"] == policy["input_id"]
    finally:
        second.close()


async def test_webui_exact_form_and_administrator_scope(app, service):
    data = {"action": "grant", "session": "room", "arguments": {**SPEC, "future": True}}
    row, _ = app.inbox.event(session="webui", sender="dashboard:administrator", external_id="form",
                             source="webui", route="webui", text=json.dumps(data),
                             metadata={"path": "/api/v1/authorizations/action"})
    with pytest.raises(ValueError, match="未来"):
        await service.grant(row["id"], SCOPE, data["arguments"])
    with pytest.raises(ValueError, match="会话"):
        await service.grant(row["id"], {**SCOPE, "session": "other"}, data["arguments"])


async def test_device_execution_rechecks_queued_operation_after_revocation(app, service):
    policy = await grant(app, service)
    row = confirmation(service)
    operation = service.repository.reserve(row["id"], policy)
    assert service.execution_allowed(row["id"], operation)
    service.repository.access_block("desktop", "pc1", "100", "revoke-access")
    assert not service.execution_allowed(row["id"], operation)
    assert not service.execution_allowed(row["id"], "different")


async def test_a03_same_local_approval_id_across_sessions_requires_exact_form(app, service):
    first = confirmation(service)
    confirmation(service, execution_session="exec2")
    with pytest.raises(ValueError, match="不唯一"):
        service.answer(user(app, "同意 approval1"), SCOPE, first["id"], "")


async def test_background_agent_cannot_manage_authorization(app, service):
    from shrink.adapters.authorizations import AuthorizationAdapter
    from shrink.services.task_context import CURRENT_TASK
    adapter = AuthorizationAdapter.__new__(AuthorizationAdapter)
    adapter.service = service
    token = CURRENT_TASK.set("background")
    try:
        with pytest.raises(ValueError, match="后台"):
            await adapter.manage("grant", SPEC, SCOPE, user(app, "授权 pc1 exec1 read,execute"))
    finally:
        CURRENT_TASK.reset(token)


async def test_duplicate_confirmation_submission_is_immutable_and_approved_once(app, service):
    await grant(app, service)
    row = confirmation(service)
    again = service.receive(row["body"], SCOPE)
    assert again["id"] == row["id"]
    await asyncio.gather(service.process(row["id"]), service.process(again["id"]))
    assert [c.args[1] for c in service.command.await_args_list].count("approve") == 1
    with pytest.raises(ValueError, match="不同操作"):
        service.receive({**row["body"], "category": "delete_data"}, SCOPE)


async def test_device_access_and_other_administrator_are_independent_of_policy(app, service):
    await grant(app, service, future=True)
    row = confirmation(service)
    service.access.return_value = False
    assert (await service.process(row["id"]))["state"] == "held"
    service.access.return_value = True
    other = service.receive({**row["body"], "approval_id": "other"}, {**SCOPE, "sender": "200"})
    assert (await service.process(other["id"]))["state"] == "waiting_user"
    service.command.assert_not_awaited()


async def test_a03_two_real_tasks_cannot_exchange_confirmation_answers(app, service):
    from shrink.persistence.tasks import Tasks
    from shrink.services.task_states import TaskStates
    states = TaskStates(Tasks(app.database))
    states.migrate()
    ids = [states.repository.create("work", "room", "100", "host") for _ in range(2)]
    for identity in ids:
        states.start(identity)
    first, second = [confirmation(service, "a" + str(i), task=identity) for i, identity in enumerate(ids)]
    answer = user(app, "同意 a0", "answer")
    with pytest.raises(ValueError, match="任务"):
        service.answer(answer, SCOPE, first["id"], ids[1])
    assert (await service.process(first["id"], answer))["state"] == "confirmed"
    assert service.repository.event(second["id"])["operation_id"] is None


async def test_recovery_requeues_unsent_authorization_question(app, service):
    row = confirmation(service)
    assert (await service.process(row["id"]))["state"] == "waiting_user"
    service.notify = AsyncMock()
    await service.recover()
    service.notify.assert_awaited_once()
    assert service.notify.await_args.args[0]["id"] == row["id"]
    service.command.assert_not_awaited()
