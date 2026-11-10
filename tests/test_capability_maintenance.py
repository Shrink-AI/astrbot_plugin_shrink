import asyncio
import copy
import time
from types import SimpleNamespace

import pytest

from shrink.application import Application
from shrink.adapters.native_skills import NativeSkills
from shrink.services.maintenance import CapabilityMaintenance, RECOVERY
from test_native_skills import FakeNative


class Native(FakeNative):
    def __init__(self):
        super().__init__()
        self.agents = []

    async def call(self, resource, action, args=None, confirmed=False):
        if resource == "subagents":
            if action == "list":
                return {"agents": copy.deepcopy(self.agents)}
            self.agents = copy.deepcopy(args["agents"])
            return {}
        return await super().call(resource, action, args, confirmed)


@pytest.fixture
def setup(tmp_path):
    async def model(*args, **kwargs):
        return '{"temporary":false,"reason":"可复用"}'
    app = Application(tmp_path, model, model, model)
    native = Native()
    skills = NativeSkills(app, native)
    app.operations = SimpleNamespace(agent_config_lock=asyncio.Lock())
    maintenance = CapabilityMaintenance(app, native, skills)
    yield app, native, skills, maintenance
    app.database.close()


@pytest.mark.asyncio
async def test_retire_restore_and_no_sync_resurrection(setup):
    app, native, bridge, service = setup
    sid = app.skill_repository.add("旧方法", "查询", "旧步骤", "复盘")
    await bridge.sync()
    app.skill_repository.revise(sid, "已明确失效", status="retired")
    await bridge.sync()
    assert (await service.run())["retired"] == 1
    await bridge.sync()
    assert native.rows[f"shrink-experience-{sid}"]["active"] is False
    await service.action("skill", str(sid), "restore")
    assert native.rows[f"shrink-experience-{sid}"]["active"] is True
    row = service.repository.get("skill", str(sid))
    assert row["protected"] and row["retired"] is None


@pytest.mark.asyncio
async def test_unknown_and_valid_unused_retained_and_expired_purged(setup):
    app, native, bridge, service = setup
    unknown = app.skill_repository.add("人工", "查询", "人工方法", "管理员", origin="admin")
    useful = app.skill_repository.add("有用", "构建", "构建方法", "复盘")
    expired = app.skill_repository.add("旧", "旧", "旧", "复盘")
    app.skill_repository.revise(unknown, "暂停", status="retired")
    app.skill_repository.revise(expired, "过时", status="retired")
    await bridge.sync()
    await service.run()
    assert service.repository.get("skill", str(unknown))["state"] == "active"
    assert service.repository.get("skill", str(useful))["state"] == "active"
    await service.run()
    assert app.skill_repository.get(expired)
    service.repository.update("skill", str(expired), retired=time.time()-RECOVERY-1)
    assert (await service.run())["purged"] == 1
    await bridge.sync()
    assert not app.skill_repository.get(expired)
    assert f"shrink-experience-{expired}" not in native.rows
    assert service.repository.get("skill", str(expired))["snapshot"] == ""


@pytest.mark.asyncio
async def test_native_edit_blocks_purge(setup):
    app, native, bridge, service = setup
    sid = app.skill_repository.add("旧", "旧", "旧", "复盘")
    app.skill_repository.revise(sid, "过时", status="retired")
    await bridge.sync()
    await service.run()
    service.repository.update("skill", str(sid), retired=time.time()-RECOVERY-1)
    native.rows[f"shrink-experience-{sid}"]["content"] += "\n管理员的新步骤"
    result = await service.run()
    assert result["errors"] and not result["purged"]
    assert app.skill_repository.get(sid)


def test_inflight_dependency_protects_agents(setup, monkeypatch):
    _, _, _, service = setup
    monkeypatch.setattr(service.repository, "tasks", lambda: [
        {"id":"parent","agent":"coordinator","status":"running","dependencies":'["finished"]',"parent":"","delivery":""},
        {"id":"finished","agent":"worker","status":"completed","dependencies":"[]","parent":"","delivery":"platform_accepted"}])
    assert service.references("subagent", "worker") == ["finished"]
    assert service.references("skill", "1") == ["parent"]


@pytest.mark.asyncio
async def test_agent_failure_does_not_block_skill_maintenance(setup, monkeypatch):
    app, native, bridge, service = setup
    first = app.skill_repository.add("一", "相同条件", "相同步骤", "复盘")
    second = app.skill_repository.add("二", "相同条件", "相同步骤", "复盘")
    await bridge.sync()
    original = native.call
    async def fail_agent(resource, action, *args):
        if resource == "subagents":
            raise ConnectionError("agent inventory unavailable")
        return await original(resource, action, *args)
    monkeypatch.setattr(native, "call", fail_agent)
    result = await service.run()
    assert result["errors"] and result["retired"] == 1
    assert app.skill_repository.get(first)["status"] == "active"
    assert app.skill_repository.get(second)["status"] == "retired"
    assert app.runs.list("subagent_maintenance")[0]["status"] == "failed"
    assert app.runs.list("skill_maintenance")[0]["status"] == "success"


@pytest.mark.asyncio
async def test_restore_refuses_edited_content_and_protected_duplicates_stay(setup):
    app, _, bridge, service = setup
    app.skill_repository.add("一", "条件", "步骤", "复盘")
    second = app.skill_repository.add("二", "条件", "步骤", "复盘")
    await bridge.sync()
    await service.action("skill", str(second), "protect")
    assert (await service.run())["retired"] == 0
    await service.action("skill", str(second), "unprotect")
    assert (await service.run())["retired"] == 1
    app.skill_repository.revise(second, "并发编辑", steps="修改后的步骤")
    with pytest.raises(ValueError, match="修改"):
        await service.action("skill", str(second), "restore")
    assert app.skill_repository.get(second)["steps"] == "修改后的步骤"


@pytest.mark.asyncio
async def test_retirement_interruption_retries_sync_before_marking_complete(setup, monkeypatch):
    app, native, bridge, service = setup
    sid = app.skill_repository.add("旧", "条件", "步骤", "复盘")
    await bridge.sync()
    await service.inventory()
    row = service.repository.get("skill", str(sid))
    value = (await service.inventory())[("skill", str(sid))]
    original = bridge.sync
    async def interrupted():
        raise ConnectionError("sync failed")
    monkeypatch.setattr(bridge, "sync", interrupted)
    with pytest.raises(ConnectionError):
        await service.retire(row, value, "已失效")
    assert service.repository.get("skill", str(sid))["state"] == "retiring"
    monkeypatch.setattr(bridge, "sync", original)
    await service.run()
    assert service.repository.get("skill", str(sid))["state"] == "retired"
    assert not native.rows[f"shrink-experience-{sid}"]["active"]


@pytest.mark.asyncio
async def test_native_adoption_duplicate_and_recovery_window(setup):
    import base64
    app, native, bridge, _ = setup
    snapshots = {}
    for name in ("a", "b"):
        content = f"---\nname: {name}\ndescription: 条件\n---\n相同步骤"
        native.rows[name] = {"content": content, "active": True}
        snapshots[name] = {"active": True, "files": {"SKILL.md": base64.b64encode(content.encode()).decode()}}
    class Archive:
        async def snapshot(self, name):
            return {**snapshots[name], "active": native.rows[name]["active"]}
        async def retire(self, name, expected):
            assert await self.snapshot(name) == expected
            native.rows[name]["active"] = False
        async def restore(self, name, expected):
            native.rows[name]["active"] = expected["active"]
        async def purge(self, name, expected):
            assert not native.rows[name]["active"]
            del native.rows[name]
    service = CapabilityMaintenance(app, native, bridge, Archive())
    assert (await service.run())["retired"] == 0
    await service.action("native_skill", "b", "adopt")
    assert (await service.run())["retired"] == 1
    assert not native.rows["b"]["active"]
    await service.action("native_skill", "b", "restore")
    assert native.rows["b"]["active"]
    assert service.repository.get("native_skill", "b")["retired"] is None
