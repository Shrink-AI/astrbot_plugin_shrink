"""Standalone reload request; an uncertain restart is never submitted twice."""
import asyncio
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import httpx


async def run(directory):
    request = json.loads((directory / "request.json").read_text("utf-8"))
    result_path = directory / "result.json"

    def save(value):
        tmp = result_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        tmp.replace(result_path)

    result = json.loads(result_path.read_text()) if result_path.exists() else {}
    if result.get("status") in {"completed", "failed", "unknown"}:
        return
    try:
        if result.get("status") != "issued":
            while request["task_id"]:
                with sqlite3.connect(request["database"]) as db:
                    row = db.execute("SELECT status FROM operation_tasks WHERE id=?",
                                     (request["task_id"],)).fetchone()
                if not row or row[0] not in {"queued", "running", "waiting", "testing", "deploying"}:
                    break
                await asyncio.sleep(1)
            # Allow the submitting tool result and foreground response to leave.
            await asyncio.sleep(8)
            config = json.loads(Path(request["config"]).read_text("utf-8-sig"))["dashboard"]
            import jwt
            token = jwt.encode({"username": config["username"], "exp": time.time() + 120},
                               config["jwt_secret"], algorithm="HS256")
            path = "plugin/reload" if request["action"] == "hot" else "stat/restart-core"
            body = {"name": request["plugin"]} if request["action"] == "hot" else {}
            save({"status": "issued", "started": time.time()})
            try:
                async with httpx.AsyncClient(timeout=90, trust_env=False) as client:
                    response = await client.post(
                        f"http://127.0.0.1:{config.get('port', 6185)}/api/{path}",
                        json=body, headers={"Authorization": "Bearer " + token})
                    response.raise_for_status()
                    if response.json().get("status") != "ok":
                        raise RuntimeError("原生重载接口拒绝请求")
            except httpx.TransportError:
                # Restart may close the connection before acknowledging it.
                pass
        marker = directory.parent / "generation"
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            for _ in range(90):
                if marker.exists() and marker.read_text() != request["generation"]:
                    try:
                        response = await client.get("http://127.0.0.1:6186/api/v1/health")
                        if response.status_code == 200 and response.json().get("status") == "ok":
                            save({"status": "completed", "finished": time.time(),
                                  "validation": {"generation_changed": True, "healthy": True}})
                            return
                    except (httpx.HTTPError, ValueError):
                        pass
                await asyncio.sleep(1)
        save({"status": "unknown", "finished": time.time(),
              "error": "重载已提交但未确认新实例健康；保留记录，不自动重复重启"})
    except Exception as exc:
        save({"status": "failed", "finished": time.time(),
              "error": f"{type(exc).__name__}: {exc}"[:1500]})


if __name__ == "__main__":
    import fcntl
    directory = Path(sys.argv[1]).resolve()
    with (directory / "worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit(0)
        (directory / "pid").write_text(str(os.getpid()))
        asyncio.run(run(directory))
