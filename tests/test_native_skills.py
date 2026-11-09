from types import SimpleNamespace

import pytest

from shrink.application import Application
from shrink.adapters.native_skills import NativeSkills


async def embed(*args):
    return None


class FakeNative:
    def __init__(self):
        self.rows = {}

    async def call(self, resource, action, args=None, confirmed=False):
        args = args or {}
        name = args.get("name")
        if action == "list":
            return {"skills": [{"name": n, "active": r["active"]} for n, r in self.rows.items()]}
        if action == "create":
            assert name not in self.rows
            self.rows[name] = {"content": args["content"], "active": True}
        elif action == "read":
            return {"content": self.rows[name]["content"]}
        elif action == "write":
            self.rows[name]["content"] = args["content"]
        elif action == "update":
            self.rows[name]["active"] = args["active"]
        elif action == "delete":
            assert confirmed
            del self.rows[name]


@pytest.mark.asyncio
async def test_publish_edit_delete_and_reenable(tmp_path):
    app = Application(tmp_path, embed, embed, embed)
    native = FakeNative()
    bridge = NativeSkills(app, native)
    skill_id = app.skill_repository.add("查询资料", "用户询问项目资料时", "先读取原始文档再回答", "测试")
    name = f"shrink-experience-{skill_id}"
    await bridge.sync()
    assert "查询资料" in native.rows[name]["content"]
    native.rows[name]["content"] = native.rows[name]["content"].replace("先读取原始文档再回答", "先确认来源再读取文档")
    await bridge.sync()
    assert app.skill_repository.get(skill_id)["steps"] == "先确认来源再读取文档"
    assert app.skill_repository.versions(skill_id)[0]["reason"] == "原生Skill更新"
    del native.rows[name]
    await bridge.sync()
    assert app.skill_repository.get(skill_id)["status"] == "retired"
    await bridge.sync()
    assert name not in native.rows
    app.skill_repository.revise(skill_id, "恢复", status="active")
    await bridge.sync()
    assert native.rows[name]["active"]
    app.skill_repository.delete(skill_id)
    await bridge.sync()
    assert not native.rows
    await app.close()


@pytest.mark.asyncio
async def test_conflicting_edits_preserve_both(tmp_path):
    app = Application(tmp_path, embed, embed, embed)
    native = FakeNative()
    bridge = NativeSkills(app, native)
    skill_id = app.skill_repository.add("查资料", "需要资料时", "读原文", "测试")
    await bridge.sync()
    name = f"shrink-experience-{skill_id}"
    native.rows[name]["content"] += "\n原生补充"
    app.skill_repository.revise(skill_id, "本地修改", steps="核对再读")
    with pytest.raises(ValueError, match="同时修改"):
        await bridge.sync()
    assert "原生补充" in native.rows[name]["content"]
    assert app.skill_repository.get(skill_id)["steps"] == "核对再读"
    await app.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", ["local", "native"])
async def test_conflict_resolution_checks_snapshot_and_keeps_both_versions(tmp_path, choice):
    app = Application(tmp_path, embed, embed, embed)
    native = FakeNative()
    bridge = NativeSkills(app, native)
    skill_id = app.skill_repository.add("查资料", "需要资料时", "读原文", "测试")
    other = app.skill_repository.add("其他经验", "另一个条件", "另一个步骤", "测试")
    await bridge.sync()
    name = f"shrink-experience-{skill_id}"
    native.rows[name]["content"] += "\n原生补充"
    app.skill_repository.revise(skill_id, "本地修改", steps="核对再读")
    app.skill_repository.revise(other, "继续同步其他经验", steps="已经更新")
    with pytest.raises(ValueError, match="同时修改"):
        await bridge.sync()
    assert "已经更新" in native.rows[f"shrink-experience-{other}"]["content"]
    current = await bridge.inspect(skill_id)
    assert current["conflict"]
    with pytest.raises(ValueError, match="版本已变化"):
        await bridge.resolve(skill_id, choice, "stale")
    assert not app.database.rows("SELECT * FROM native_skill_resolutions")
    result = await bridge.resolve(skill_id, choice, current["snapshot"])
    assert not result["conflict"]
    expected = "核对再读" if choice == "local" else "原生补充"
    assert expected in native.rows[name]["content"]
    assert expected in app.skill_repository.get(skill_id)["steps"]
    backup = app.database.rows("SELECT * FROM native_skill_resolutions")[0]
    assert "核对再读" in backup["local_json"] and "原生补充" in backup["native_json"]
    await app.close()
