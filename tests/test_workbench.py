import json
from unittest.mock import AsyncMock, patch

import pytest

from shrink.services.workbench import Workbench


@pytest.mark.asyncio
async def test_deploy_requires_current_tests_and_preserves_runtime(tmp_path):
    tester = AsyncMock(return_value={"ok": True, "exit": 0})
    work = Workbench(tmp_path / "work", tmp_path / "plugins", tester)
    full = "astrbot_plugin_shirnk_ext_example"
    code = "class Example: pass\n"
    await work.perform("write", "example", "main.py", code, None)
    await work.perform("write", "example", "metadata.yaml", f"name: {full}\n", None)
    with pytest.raises(ValueError, match="隔离"):
        await work.perform("deploy", "example", "", "", None)
    await work.perform("test", "example", "", "", None)
    await work.perform("write", "example", "main.py", code + "VERSION=1\n", None)
    with pytest.raises(ValueError, match="隔离"):
        await work.perform("deploy", "example", "", "", None)
    await work.perform("test", "example", "", "", None)
    with patch.object(work, "activate", new_callable=AsyncMock):
        assert json.loads(await work.perform("deploy", "example", "", "", None))["activated"]
        runtime = tmp_path / "plugins" / full / "state.json"
        runtime.write_text('{"important":true}')
        await work.perform("write", "example", "main.py", code + "VERSION=2\n", None)
        await work.perform("test", "example", "", "", None)
        await work.perform("deploy", "example", "", "", None)
        await work.perform("rollback", "example", "", "", None)
        assert "VERSION=1" in runtime.with_name("main.py").read_text()
        assert json.loads(runtime.read_text()) == {"important": True}


@pytest.mark.asyncio
async def test_conflicts_and_escape(tmp_path):
    work = Workbench(tmp_path / "work", tmp_path / "plugins",
                     AsyncMock(return_value={"ok": True}))
    target = tmp_path / "plugins" / "astrbot_plugin_existing"
    target.mkdir(parents=True)
    (target / "main.py").write_text("OLD=1")
    (target / "metadata.yaml").write_text("name: astrbot_plugin_existing")
    await work.perform("checkout", target.name, "", "", None)
    await work.perform("test", target.name, "", "", None)
    (target / "main.py").write_text("SOMEONE_ELSE=1")
    with pytest.raises(ValueError, match="线上代码"):
        await work.perform("deploy", target.name, "", "", None)
    with pytest.raises(ValueError):
        await work.perform("write", "example", "../../escape.py", "x=1", None)


@pytest.mark.asyncio
async def test_refresh_archives_old_edits_and_requires_new_tests(tmp_path):
    work = Workbench(tmp_path / "work", tmp_path / "plugins", AsyncMock(return_value={"ok": True}))
    target = tmp_path / "plugins" / "astrbot_plugin_existing"
    target.mkdir(parents=True)
    (target / "main.py").write_text("VERSION=1")
    (target / "metadata.yaml").write_text("name: astrbot_plugin_existing")
    (target / "defaults.toml").write_text('prompt="old"')
    await work.perform("checkout", target.name, "", "", None)
    await work.perform("write", target.name, "main.py", "VERSION=1\nMY_EDIT=True", None)
    await work.perform("test", target.name, "", "", None)
    assert work.tester.call_args.args[0]["defaults.toml"] == 'prompt="old"'
    (target / "main.py").write_text("VERSION=2\nNEW_FEATURE=True")
    (target / "defaults.toml").write_text('prompt="new"')
    result = json.loads(await work.perform("refresh", target.name, "", "", None))
    assert result["activated"] is False
    assert "NEW_FEATURE" in await work.perform("read", target.name, "main.py", "", None)
    assert 'prompt="new"' == await work.perform("read", target.name, "defaults.toml", "", None)
    archived = await work.perform("read_archive", target.name, result["archive_id"] + "/main.py", "", None)
    assert "MY_EDIT" in archived
    assert len(json.loads(await work.perform("archives", target.name, "", "", None))) == 1
    status = json.loads(await work.perform("status", target.name, "", "", None))
    assert not status["conflict"] and status["test"] is None
    with pytest.raises(ValueError, match="隔离"):
        await work.perform("deploy", target.name, "", "", None)
    with pytest.raises(ValueError):
        await work.perform("read_archive", target.name, result["archive_id"] + "/../../main.py", "", None)
    assert (target / "main.py").read_text() == "VERSION=2\nNEW_FEATURE=True"
