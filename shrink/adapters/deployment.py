"""Independent process boundary for reloading the core that owns the active task."""
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path


class Deployments:
    def __init__(self, operations):
        self.operations = operations
        self.app = operations.app
        self.root = self.app.state.root / "deployments"
        self.root.mkdir(exist_ok=True)
        self.worker = None

    def listing(self):
        results = []
        for path in sorted(self.root.glob("*/request.json"), reverse=True):
            request = json.loads(path.read_text("utf-8"))
            result = path.with_name("result.json")
            results.append({k: v for k, v in request.items()
                            if k in {"id", "task_id", "plugin", "session", "created", "action"}} |
                           (json.loads(result.read_text("utf-8")) if result.exists()
                            else {"status": "queued"}))
        return sorted(results, key=lambda r: r["created"], reverse=True)[:100]

    def launch(self, directory):
        with (directory / "worker.log").open("ab") as log:
            process = subprocess.Popen([sys.executable, str(directory / "worker.py"), str(directory)],
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                       start_new_session=True)
        return process.pid

    def alive(self, directory):
        try:
            pid = int((directory / "pid").read_text())
            os.kill(pid, 0)
            # The deployed worker runs on Linux. PID reuse must not disguise a
            # dead deployment process as a running one.
            command = Path(f"/proc/{pid}/cmdline").read_bytes()
            return str(directory / "worker.py").encode() in command.split(b"\0")
        except (OSError, ValueError):
            return False

    def rollback_source(self, name):
        for row in self.listing():
            if row["status"] == "completed" and row["plugin"] == name:
                directory = self.root / row["id"]
                previous = directory / "before"
                if not previous.is_dir():
                    previous = directory / "workbench" / "backups" / name
                if previous.is_dir():
                    return previous
        previous = self.operations.workbench.root / "backups" / name
        if previous.is_dir():
            return previous
        raise ValueError("没有核心上一版备份")

    async def submit(self, name, event, task_id="", action="deploy"):
        workbench = self.operations.workbench
        full_name = workbench.name(name)
        draft = workbench.root / "drafts" / full_name
        test_path = workbench.root / "tests" / (full_name + ".json")
        evidence = json.loads(test_path.read_text("utf-8")) if test_path.exists() else {}
        if action == "deploy":
            workbench.check(draft, full_name)
            if evidence.get("files") != workbench.fingerprints(draft) or not evidence.get("result", {}).get("ok"):
                raise ValueError("核心草稿必须先通过当前版本的隔离测试")
        elif action == "rollback":
            draft = self.rollback_source(full_name)
            workbench.check(draft, full_name)
        else:
            raise ValueError("不支持的核心部署操作")
        baseline = workbench.root / "baselines" / (full_name + ".json")
        if not baseline.is_file() or json.loads(baseline.read_text("utf-8")) != workbench.fingerprints(workbench.plugins / full_name):
            raise ValueError("线上代码已改变，不能覆盖；请核对差异")
        if any(r["status"] in {"queued", "running"} for r in self.listing()):
            raise ValueError("已有核心部署等待完成")
        job_id = uuid.uuid4().hex
        directory = self.root / job_id
        directory.mkdir()
        snapshot = directory / "workbench"
        for folder in ("drafts", "tests", "baselines", "backups"):
            (snapshot / folder).mkdir(parents=True)
        for source, relative in workbench.source_files(draft):
            target = snapshot / ("drafts" if action == "deploy" else "backups") / full_name / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        if action == "deploy":
            shutil.copy2(test_path, snapshot / "tests" / test_path.name)
        shutil.copy2(baseline, snapshot / "baselines" / baseline.name)
        # Copy executable code out of the plugin directory before it is replaced.
        shutil.copy2(Path(__file__).parents[1] / "services" / "workbench.py", directory / "workbench_runtime.py")
        shutil.copy2(Path(__file__).with_name("deployment_worker.py"), directory / "worker.py")
        request = {"id": job_id, "task_id": task_id, "plugin": full_name,
                   "action": action,
                   "plugins": str(workbench.plugins), "database": str(self.app.state.root / "shrink.db"),
                   "origin_workbench": str(workbench.root),
                   "session": event.unified_msg_origin, "created": time.time(),
                   "config": str(workbench.plugins.parent / "cmd_config.json")}
        (directory / "request.json").write_text(json.dumps(request), encoding="utf-8")
        pid = self.launch(directory)
        return {"ok": True, "activated": False, "pending": True, "deployment_id": job_id,
                "pid": pid, "message": "独立执行器已接手；健康验证完成后回传结果"}

    async def start(self):
        self.worker = asyncio.create_task(self.loop())

    async def loop(self):
        while True:
            for row in self.listing():
                directory = self.root / row["id"]
                if row["status"] in {"queued", "running"} and not self.alive(directory):
                    try:
                        self.launch(directory)
                    except OSError as exc:
                        self.app.tasks.error("deployment_restart", exc)
                    continue
                if row["status"] not in {"completed", "rolled_back", "failed"} or (directory / "reported").exists():
                    continue
                try:
                    result = json.dumps(row, ensure_ascii=False)
                    if row.get("task_id"):
                        with self.app.database.transaction() as con:
                            con.execute("UPDATE operation_tasks SET status=?,result=?,finished=? WHERE id=?",
                                        ("completed" if row["status"] == "completed" else "needs_attention",
                                         result, time.time(), row["task_id"]))
                    from astrbot.api.message_components import Plain
                    from astrbot.core.message.message_event_result import MessageChain
                    from ..prompts import render, text as prompt_text
                    text = await asyncio.wait_for(self.app.expression.model(
                        render("deployment.report", result=result),
                        prompt_text("deployment.report.system")), 45)
                    accepted = await self.operations.context.send_message(row["session"], MessageChain([Plain(text)]))
                    if not accepted:
                        raise RuntimeError("结果发送未找到匹配平台，将重试")
                    (directory / "reported").write_text(str(time.time()))
                except Exception as exc:
                    self.app.tasks.error("deployment_report", exc)
            await asyncio.sleep(5)

    async def close(self):
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
