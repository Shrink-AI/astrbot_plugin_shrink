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
