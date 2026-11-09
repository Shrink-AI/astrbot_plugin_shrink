import json
import importlib.util
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from shrink.adapters.deployment import Deployments
from shrink.services.workbench import Workbench


@pytest.mark.asyncio
async def test_core_deployment_snapshots_tested_draft_before_starting_worker(tmp_path):
    plugins = tmp_path / "plugins"
    target = plugins / "astrbot_plugin_shrink"
    target.mkdir(parents=True)
    (target / "main.py").write_text("VALUE=1\n")
    (target / "metadata.yaml").write_text("name: astrbot_plugin_shrink\n")
    work = Workbench(tmp_path / "workbench", plugins, AsyncMock(return_value={"ok": True}))
    await work.perform("checkout", target.name, "", "", None)
    await work.perform("test", target.name, "", "", None)
    operations = SimpleNamespace(workbench=work, app=SimpleNamespace(state=SimpleNamespace(root=tmp_path)))
    deployments = Deployments(operations)
    event = SimpleNamespace(unified_msg_origin="snowluma:FriendMessage:123")
    with patch("shrink.adapters.deployment.subprocess.Popen") as spawn:
        spawn.return_value.pid = 12345
        result = await deployments.submit(target.name, event)
    assert result["pending"] and not result["activated"]
    directory = deployments.root / result["deployment_id"]
    assert (directory / "worker.py").is_file()
    assert (directory / "workbench_runtime.py").is_file()
    assert (directory / "workbench/drafts/astrbot_plugin_shrink/main.py").read_text() == "VALUE=1\n"
    assert json.loads((directory / "request.json").read_text())["plugin"] == target.name
    with pytest.raises(ValueError, match="已有核心部署"):
        await deployments.submit(target.name, event)


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupted", [False, True])
async def test_worker_restores_prior_source_after_failed_reload_or_interruption(tmp_path, interrupted):
    import httpx
    plugins = tmp_path / "plugins"
    target = plugins / "astrbot_plugin_shrink"
    target.mkdir(parents=True)
    (target / "main.py").write_text("VALUE=1\n")
    (target / "metadata.yaml").write_text(f"name: {target.name}\n")
    (target / "runtime.json").write_text('{"keep":true}')
    work = Workbench(tmp_path / "workbench", plugins, AsyncMock(return_value={"ok": True}))
    await work.perform("checkout", target.name, "", "", None)
    await work.perform("write", target.name, "main.py", "VALUE=2\n", None)
    await work.perform("test", target.name, "", "", None)
    operations = SimpleNamespace(workbench=work, app=SimpleNamespace(state=SimpleNamespace(root=tmp_path)))
    deployments = Deployments(operations)
    with patch.object(deployments, "launch", return_value=123):
        submitted = await deployments.submit(target.name, SimpleNamespace(unified_msg_origin="test"))
    directory = deployments.root / submitted["deployment_id"]
    request = json.loads((directory / "request.json").read_text())
    with sqlite3.connect(request["database"]) as con:
        con.execute("CREATE TABLE operation_tasks(id TEXT,status TEXT)")
    Path(request["config"]).write_text(json.dumps({"dashboard": {"username": "test", "jwt_secret": "unused"}}))
    if interrupted:
        before = directory / "before"
        before.mkdir()
        for source, relative in work.source_files(target):
            (before / relative).write_bytes(source.read_bytes())
        (target / "main.py").write_text("VALUE=2\n")
        (directory / "result.json").write_text(json.dumps({"status": "running", "started": time.time()}))
    reload_values = []

    def handle(req):
        if req.url.path == "/api/plugin/reload":
            value = (target / "main.py").read_text()
            reload_values.append(value)
            return httpx.Response(200, json={"status": "error" if "VALUE=2" in value else "ok",
                                             "message": "injected reload failure", "data": None})
        if req.url.path == "/api/tools/list":
            return httpx.Response(200, json={"status": "ok", "data": [{"name": "shrink_develop"}]})
        return httpx.Response(200, json={"status": "ok"})

    real_client = httpx.AsyncClient
    spec = importlib.util.spec_from_file_location("deployment_under_test", directory / "worker.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"workbench_runtime": SimpleNamespace(Workbench=Workbench),
                                  "jwt": SimpleNamespace(encode=lambda *a, **k: "test")}):
        spec.loader.exec_module(module)
    with patch.object(module.httpx, "AsyncClient",
                      side_effect=lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw)), \
         patch.object(module.asyncio, "sleep", new_callable=AsyncMock):
        await module.run(directory)
    result = json.loads((directory / "result.json").read_text("utf-8"))
    assert result["status"] == "rolled_back"
    assert (target / "main.py").read_text() == "VALUE=1\n"
    assert json.loads((target / "runtime.json").read_text()) == {"keep": True}
    assert reload_values and reload_values[-1] == "VALUE=1\n"
    if not interrupted:
        assert reload_values[0] == "VALUE=2\n"
    assert json.loads((work.root / "baselines" / (target.name + ".json")).read_text()) == work.fingerprints(target)
    # Re-entering a finished request must never deploy it again.
    with patch.object(module.httpx, "AsyncClient", side_effect=AssertionError("unexpected reload")):
        await module.run(directory)


@pytest.mark.asyncio
async def test_manual_core_rollback_is_snapshotted_and_conflict_checked(tmp_path):
    target = tmp_path / "plugins" / "astrbot_plugin_shrink"
    target.mkdir(parents=True)
    (target / "main.py").write_text("VALUE=2")
    (target / "metadata.yaml").write_text(f"name: {target.name}")
    work = Workbench(tmp_path / "workbench", target.parent)
    await work.perform("checkout", target.name, "", "", None)
    backup = work.root / "backups" / target.name
    backup.mkdir(parents=True)
    (backup / "main.py").write_text("VALUE=1")
    (backup / "metadata.yaml").write_text(f"name: {target.name}")
    deployments = Deployments(SimpleNamespace(workbench=work, app=SimpleNamespace(state=SimpleNamespace(root=tmp_path))))
    event = SimpleNamespace(unified_msg_origin="test")
    with patch.object(deployments, "launch", return_value=123):
        result = await deployments.submit(target.name, event, action="rollback")
    directory = deployments.root / result["deployment_id"]
    assert json.loads((directory / "request.json").read_text())["action"] == "rollback"
    assert (directory / "workbench/backups" / target.name / "main.py").read_text() == "VALUE=1"
    (target / "main.py").write_text("VALUE=3")
    with pytest.raises(ValueError, match="线上代码"):
        await deployments.submit(target.name, event, action="rollback")
