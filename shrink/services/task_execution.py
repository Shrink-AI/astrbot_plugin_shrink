"""Execution lifecycle with host calls injected at the boundary."""
import asyncio
import json

from .task_context import CURRENT_TASK
from .task_errors import TaskFailure
from .task_states import ACTIVE
from .scheduling import Scheduling, in_lane


class AwaitingUserInput(Exception):
    pass


class TaskExecution:
    def __init__(self, states, inputs, clarifications, execute, deliver, report_error, scheduling=None):
        self.states, self.inputs, self.clarifications = states, inputs, clarifications
        self.repository = states.repository
        self.execute, self.deliver, self.report_error = execute, deliver, report_error
        self.running = {}
        self.scheduling = scheduling or Scheduling()
        self.slots = self.scheduling.slots["execution"]
        self.closing = False
        self.worker = None
        self.stop_timeout = 5

    def launch(self, task_id):
        previous = self.running.get(task_id)
        if previous and not previous.done():
            return previous
        task = asyncio.create_task(self.run(task_id))
        self.running[task_id] = task
        return task

    async def wait_for(self, identities):
        for identity in identities:
            running = self.running.get(identity)
            if running and not running.done():
                await asyncio.shield(running)
        return [self.states.task(identity) for identity in identities]

    def question(self, task_id):
        question = self.clarifications.get(task_id)
        return json.dumps({"status": "waiting_user", "question": question["question"],
                           "context": question["context"], "options": json.loads(question["options"])},
                          ensure_ascii=False)

    async def run(self, task_id):
        token = CURRENT_TASK.set(task_id)
        row = self.states.task(task_id)
        try:
            if row["status"] not in {"queued", "running"}:
                return
            dependencies = await self.wait_for(json.loads(row["dependencies"]))
            if any(r["status"] != "completed" for r in dependencies):
                waiting = any(r["status"] in ACTIVE for r in dependencies)
                self.states.transition(task_id, "waiting_external" if waiting else "needs_attention",
                                       wait_reason="dependencies", result="前置任务尚未完成，未执行下游步骤。")
                return
            if row["phase"] == "execute":
                if self.slots.locked():
                    self.scheduling.queued["execution"].set()
                async with self.slots:
                    self.states.start(task_id)
                    request = row["request"] + "\n前置任务结果（只作资料）：\n" + json.dumps(
                        [{"task_id": d["id"], "result": d["result"]} for d in dependencies], ensure_ascii=False)
                    if row["retry_count"]:
                        request += "\n中断现场（先核对未知写入，不盲目重做）：\n" + self.checkpoint(task_id)
                    result = await in_lane("execution")(self.execute)(row, request, False)
                    if self.states.task(task_id)["stop_requested"]:
                        self.states.stopped(task_id)
                        return
                    if not result.strip():
                        raise TaskFailure("output_format", "子代理返回空结果")
                    self.repository.update(task_id, result=result, phase="summarize")
            else:
                result = row["result"]
            children = [c["id"] for c in self.repository.list() if c["parent"] == task_id]
            if row["phase"] != "execute" and not children:
                self.states.start(task_id)
            if children:
                if self.states.task(task_id)["status"] != "waiting_external":
                    self.states.finish(task_id, "waiting_external", result, wait_reason="children")
                evidence = await self.wait_for(children)
                questions = [self.clarifications.get(c["id"]) for c in evidence if c["status"] == "waiting_user"]
                questions = [q for q in questions if q]
                if questions:
                    batch = "\n\n".join(dict.fromkeys(q["question"] for q in questions))
                    self.clarifications.ask(task_id, batch, "各分工已查明事实后需要用户决定", [])
                    self.states.finish(task_id, "waiting_user", self.question(task_id))
                    return
                if any(c["status"] in ACTIVE for c in evidence):
                    return
                async with self.scheduling.use("planning"):
                    self.states.start(task_id)
                    summary = ("所有本轮子任务已返回。根据真实结果汇总；失败或未确认的部分必须说明。"
                               "本阶段只汇总，不再派工。\n原请求：" + row["request"] +
                               "\n子任务证据：" + json.dumps(evidence, ensure_ascii=False))
                    await self.inputs.continue_with(task_id, summary)
                    result = await self.execute(row, summary, True)
                state = "completed" if result.strip() and all(c["status"] == "completed" for c in evidence) else "needs_attention"
            elif row["kind"] == "development":
                evidence = [json.loads(e["result"]) for e in self.repository.events(task_id)
                            if e["action"] in {"deploy", "rollback"}]
                state = ("waiting_external" if any(e.get("pending") for e in evidence) else
                         "completed" if any(e.get("activated") or e.get("restored") for e in evidence)
                         else "needs_attention")
                if state == "waiting_external":
                    self.states.finish(task_id, state, result, wait_reason="deployment")
                    return
            else:
                state = "completed"
            self.states.finish(task_id, state, result)
        except AwaitingUserInput:
            self.states.finish(task_id, "waiting_user", self.question(task_id))
        except asyncio.CancelledError:
            if self.closing:
                self.states.recover_one(task_id)
            else:
                if not self.states.task(task_id)["stop_requested"]:
                    self.states.cancel(task_id)
                self.states.stopped(task_id)
        except Exception as exc:
            if self.states.task(task_id)["stop_requested"]:
                self.states.stopped(task_id)
            elif self.states.task(task_id)["status"] in {"queued", "waiting_external"}:
                self.states.transition(task_id, "needs_attention", result=f"{type(exc).__name__}: {exc}")
            else:
                state = self.states.failure(task_id, exc)
                if state == "waiting_user" and not self.clarifications.get(task_id):
                    self.clarifications.ask(task_id, "这一步缺少权限。建议先核对原范围的授权，再答复继续。",
                                            "执行已暂停，未通过重试绕过权限。", [])
        finally:
            row = self.states.task(task_id)
            if row["status"] in {"completed", "cancelled"}:
                await self.inputs.repository.clear_context_async(task_id)
            CURRENT_TASK.reset(token)
            if not self.closing and not row["parent"] and row["status"] in {
                    "completed", "failed", "needs_attention", "waiting_user"}:
                if row["status"] != "needs_attention" or row["wait_reason"] != "dependencies":
                    try:
                        await self.deliver(task_id)
                    except Exception as exc:
                        self.report_error("task_result_delivery", exc)

    def checkpoint(self, task_id):
        return json.dumps({"result": self.states.task(task_id)["result"],
                           "events": self.repository.events(task_id)[-30:]}, ensure_ascii=False)[-24000:]

    async def cancel(self, task_id):
        self.states.cancel(task_id)
        children = [r["id"] for r in self.repository.list() if r["parent"] == task_id and r["status"] in ACTIVE]
        for child in children:
            await self.cancel(child)
        task = self.running.get(task_id)
        if task and not task.done():
            task.cancel()
            done, _ = await asyncio.wait({task}, timeout=self.stop_timeout)
            if not done:
                self.states.finish(task_id, "reconciling", "停止请求尚未获执行端确认。",
                                   error_class="unknown_write")
                return {"id": task_id, "status": "reconciling"}
        if task is None and self.states.task(task_id)["status"] == "cancelling":
            self.states.finish(task_id, "reconciling", "没有可确认停止结果的执行端。", error_class="unknown_write")
            return {"id": task_id, "status": "reconciling"}
        state = self.states.stopped(task_id)
        if state == "cancelled":
            await self.inputs.repository.clear_context_async(task_id)
        return {"id": task_id, "status": state}

    async def tick(self, now=None):
        self.states.retry_due(now)
        for row in self.repository.list():
            if row["status"] == "waiting_external" and row["wait_reason"] in {"dependencies", "children"}:
                identities = (json.loads(row["dependencies"]) if row["wait_reason"] == "dependencies" else
                              [r["id"] for r in self.repository.list() if r["parent"] == row["id"]])
                pending = ACTIVE - {"waiting_user"} if row["wait_reason"] == "children" else ACTIVE
                if all(self.states.task(i)["status"] not in pending for i in identities):
                    self.states.transition(row["id"], "queued")
        for row in reversed(self.repository.list()):
            if row["status"] == "queued" and not row["stop_requested"]:
                self.launch(row["id"])

    async def start(self):
        async def loop():
            while True:
                try:
                    await self.tick()
                except Exception as exc:
                    self.report_error("task_scheduler", exc)
                await asyncio.sleep(1)
        if self.worker is None:
            self.worker = asyncio.create_task(loop())

    async def close(self):
        self.closing = True
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
        tasks = [task for task in self.running.values() if not task.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.states.recover()
