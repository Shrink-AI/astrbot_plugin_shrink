"""Copied to a per-deployment directory, executed outside the plugin lifecycle."""
import asyncio
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

import httpx
import jwt

from workbench_runtime import Workbench


async def run(directory):
    request = json.loads((directory / "request.json").read_text("utf-8"))
    result_path = directory / "result.json"

    def save(result):
        temporary = directory / "result.tmp"
        temporary.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        temporary.replace(result_path)

    async def native(path, data=None):
        config = json.loads(Path(request["config"]).read_text("utf-8-sig"))["dashboard"]
        token = jwt.encode({"username": config["username"], "exp": time.time() + 120},
                           config["jwt_secret"], algorithm="HS256")
        async with httpx.AsyncClient(timeout=90, trust_env=False) as client:
            response = await client.request("POST" if data else "GET",
                f"http://127.0.0.1:{config.get('port', 6185)}/api/{path}",
                json=data, headers={"Authorization": "Bearer " + token})
            response.raise_for_status()
            value = response.json()
            if value.get("status") != "ok":
                raise RuntimeError(str(value.get("message"))[:1500])
            return value.get("data")

    class ExternalWorkbench(Workbench):
        healthy = False

        async def activate(self, manager, name):
            self.healthy = False
            await native("plugin/reload", {"name": name})
            async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
                for _ in range(30):
                    try:
                        response = await client.get("http://127.0.0.1:6186/api/v1/health")
                        if response.status_code == 200 and response.json().get("status") == "ok":
                            tools = await native("tools/list")
                            if any(t.get("name") == "shrink_develop" for t in tools):
                                self.healthy = True
                                return
                    except (httpx.HTTPError, ValueError):
                        pass
                    await asyncio.sleep(1)
            raise RuntimeError("重载后石英后台或开发工具未恢复")

    workbench = ExternalWorkbench(directory / "workbench", Path(request["plugins"]))
    target = Path(request["plugins"]) / request["plugin"]
    before = directory / "before"
    previous = json.loads(result_path.read_text("utf-8")) if result_path.exists() else {}
    if previous.get("status") in {"completed", "failed", "rolled_back"}:
        return
    baseline = Path(request["origin_workbench"]) / "baselines" / (request["plugin"] + ".json")
    changing = previous.get("status") == "running"

    async def restore():
        if not before.is_dir():
            raise RuntimeError("部署中断且缺少部署前快照，需人工核对")
        workbench.restore_source(before, target)
        await workbench.activate(None, request["plugin"])
        if workbench.fingerprints(before) != workbench.fingerprints(target):
            raise RuntimeError("恢复后的源文件与部署前快照不一致")
        baseline.write_text(json.dumps(workbench.fingerprints(target)), encoding="utf-8")

    try:
        if changing:
            await restore()
            save({"status": "rolled_back", "error": "独立执行器中断，已恢复并验证部署前版本",
                  "finished": time.time()})
            return
        # Let the native agent finish its tool result before unloading its caller.
        while True:
            with sqlite3.connect(request["database"]) as connection:
                row = connection.execute("SELECT status FROM operation_tasks WHERE id=?",
                                         (request["task_id"],)).fetchone()
            if not row or row[0] not in {"queued", "running", "testing", "deploying"}:
                break
            await asyncio.sleep(1)
        await asyncio.sleep(2)
        expected = directory / "workbench" / "baselines" / (request["plugin"] + ".json")
        if json.loads(expected.read_text("utf-8")) != workbench.fingerprints(target):
            raise RuntimeError("线上代码已改变，未执行核心部署")
        # Complete the snapshot before recording the first possible mutation.
        temporary = directory / "before.tmp"
        if temporary.exists():
            shutil.rmtree(temporary)
        temporary.mkdir()
        for source, relative in workbench.source_files(target):
            destination = temporary / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        if before.exists():
            shutil.rmtree(before)
        temporary.replace(before)
        save({"status": "running", "started": time.time()})
        changing = True
        result = json.loads(await workbench.perform(request.get("action", "deploy"), request["plugin"], "", "", None))
        baseline.write_text(json.dumps(workbench.fingerprints(target)),
                            encoding="utf-8")
        save({"status": "completed", "finished": time.time(), "validation": result})
    except Exception as exc:
        restored = False
        recovery_error = ""
        if changing:
            try:
                await restore()
                restored = True
            except Exception as recovery:
                recovery_error = f"; 恢复失败: {type(recovery).__name__}: {recovery}"
        save({"status": "rolled_back" if restored else "failed",
              "error": f"{type(exc).__name__}: {exc}{recovery_error}"[:2000], "finished": time.time()})


if __name__ == "__main__":
    # A replacement worker can be started after a core/container restart. Only
    # one process may touch this deployment, including during the reload itself.
    import fcntl
    directory = Path(sys.argv[1]).resolve()
    with (directory / "worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit(0)
        (directory / "pid").write_text(str(os.getpid()), encoding="utf-8")
        asyncio.run(run(directory))
