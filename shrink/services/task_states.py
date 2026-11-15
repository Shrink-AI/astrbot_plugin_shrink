"""Business transitions, recovery and durable retry scheduling."""
import time

from .task_errors import classify_error
from .task_recovery import reconciliation


TRANSITIONS = {
    "queued": {"running", "waiting_external", "cancelled", "needs_attention"},
    "running": {"waiting_external", "waiting_user", "retry_wait", "reconciling",
                "needs_attention", "completed", "failed", "cancelling"},
    "waiting_external": {"running", "queued", "waiting_user", "needs_attention", "reconciling", "cancelling",
                         "completed", "failed", "cancelled"},
    "waiting_user": {"queued", "cancelled"},
    "retry_wait": {"queued", "cancelled", "needs_attention"},
    "reconciling": {"running", "needs_attention", "cancelled", "cancelling"},
    "needs_attention": {"queued", "cancelled"},
    "completed": set(),
    "failed": {"queued", "cancelled"},
    "cancelling": {"cancelled", "reconciling"},
    "cancelled": {"queued"},
}
ACTIVE = {"queued", "running", "waiting_external", "waiting_user", "retry_wait", "reconciling", "cancelling"}
TERMINAL = {"completed", "failed", "cancelled"}
LEGACY = {
    "waiting": "waiting_external", "awaiting_input": "waiting_user",
    "interrupted": "needs_attention", "timed_out": "needs_attention",
    "blocked": "needs_attention", "testing": "needs_attention",
    "deploying": "waiting_external", "awaiting_deployment": "waiting_external",
    "continued": "cancelled",
}


class TaskStates:
    def __init__(self, repository, retry_limit=3, *, retry_limit_provider=None):
        self.repository = repository
        self.retry_limit = max(0, min(3, retry_limit))
        self.retry_limit_provider = retry_limit_provider

    def task(self, task_id):
        return self.repository.task(task_id)

    def events(self, task_id):
        return self.repository.events(task_id)

    def transition(self, task_id, target, *, migration=False, recovery=False, **fields):
        row = self.task(task_id)
        source = row["status"]
        if target not in TRANSITIONS:
            raise ValueError("未知任务状态：" + target)
        if set(fields) - {"result", "next_run", "retry_count", "wait_reason", "stop_requested"}:
            raise ValueError("无效转换字段")
        if migration:
            if not self.repository.needs_migration:
                raise ValueError("状态迁移已经完成")
        elif recovery:
            if target not in {"queued", "reconciling", "needs_attention", "cancelled"}:
                raise ValueError("无效恢复状态")
        elif target not in TRANSITIONS.get(source, set()):
            raise ValueError(f"非法任务状态转换：{source} -> {target}")
        fields["finished"] = time.time() if target in TERMINAL else None
        if target != "retry_wait":
            fields["next_run"] = None
        with self.repository.transaction():
            self.repository._transition(task_id, source, target, fields)
            self.repository.record(task_id, "state_transition", result=f"{source} -> {target}")
        return target

    def migrate(self):
        if not self.repository.needs_migration:
            return
        with self.repository.transaction():
            for row in self.repository.list():
                old = row["status"]
                target = LEGACY.get(old, old if old in TRANSITIONS else "needs_attention")
                if old in {"interrupted", "timed_out", "testing"} and self.unknown_tools(row["id"]):
                    target = "reconciling"
                if target != old:
                    reason = "deployment" if old in {"deploying", "awaiting_deployment"} else "external"
                    self.transition(row["id"], target, migration=True,
                                    wait_reason=reason if target == "waiting_external" else "")
            self.repository.migration_done()

    def unknown_tools(self, task_id):
        return bool(reconciliation(self.events(task_id)))

    def capacity(self, kind, excluding=""):
        live = [r for r in self.repository.list() if r["id"] != excluding and r["status"] in ACTIVE]
        if kind == "agent" and sum(r["kind"] == "agent" for r in live) >= 12:
            raise ValueError("已有12项后台任务，请先查看进度或取消不需要的任务")
        if kind == "development" and any(r["kind"] == "development" for r in live):
            raise ValueError("已有开发任务运行，请等待完成或先取消")

    def start(self, task_id):
        row = self.task(task_id)
        with self.repository.transaction():
            if row["status"] != "running":
                self.transition(task_id, "running")
            return self.repository.begin_attempt(row, "astrbot", row["session"])

    def finish(self, task_id, state, result, *, error_class="", wait_reason=""):
        row = self.task(task_id)
        if row["stop_requested"] and state not in {"cancelled", "reconciling"}:
            self.stopped(task_id)
            self.repository.update(task_id, result=result)
            return
        attempt_status = ("succeeded" if state == "completed" else "cancelled" if state == "cancelled"
                          else "unknown" if state == "reconciling" else "failed" if error_class else "paused")
        with self.repository.transaction():
            self.repository.end_attempt(task_id, attempt_status, state, error_class)
            if row["status"] != state:
                self.transition(task_id, state, result=result, wait_reason=wait_reason)
            else:
                self.repository.update(task_id, result=result)

    def failure(self, task_id, error, *, now=None):
        row = self.task(task_id)
        limit = self.retry_limit_provider() if self.retry_limit_provider is not None else self.retry_limit
        fault = classify_error(error, unclosed_tools=self.unknown_tools(task_id),
                               exhausted=row["retry_count"] >= max(0, min(3, limit)))
        result = f"{type(error).__name__}: {str(error)[:2000]}"
        with self.repository.transaction():
            self.repository.end_attempt(task_id, "unknown" if fault.state == "reconciling" else "failed",
                                        result, fault.category)
            fields = {"result": result}
            if fault.retryable:
                fields.update(retry_count=row["retry_count"] + 1,
                              next_run=(time.time() if now is None else now) + 2 ** (row["retry_count"] + 1))
            if fault.state == "waiting_external":
                fields["wait_reason"] = "external"
            self.transition(task_id, fault.state, **fields)
        return fault.state

    def step_succeeded(self, task_id):
        row = self.task(task_id)
        self.repository.update(task_id, retry_count=0, step=row["step"] + 1)

    def retry_due(self, now=None):
        now = time.time() if now is None else now
        due = [r for r in self.repository.list()
               if r["status"] == "retry_wait" and r["next_run"] is not None and r["next_run"] <= now]
        for row in due:
            self.transition(row["id"], "queued")
        return [r["id"] for r in due]

    def resume(self, task_id):
        row = self.task(task_id)
        self.capacity(row["kind"], task_id)
        if row["status"] not in {"waiting_user", "needs_attention", "failed", "cancelled", "reconciling"}:
            raise ValueError("任务不能续做，当前状态：" + row["status"])
        # A stopped task needs a new, explicitly authorized attempt.
        self.transition(task_id, "running" if row["status"] == "reconciling" else "queued",
                        stop_requested=0, retry_count=0)

    def cancel(self, task_id):
        row = self.task(task_id)
        if row["status"] in {"completed", "cancelled", "failed"}:
            return row["status"]
        if row["status"] == "cancelling":
            return "cancelling"
        pending = (row["status"] in {"running", "reconciling"} or
                   row["status"] == "waiting_external" and row["wait_reason"] in {"deployment", "external"})
        return self.transition(task_id, "cancelling" if pending else "cancelled",
                               stop_requested=1)

    def deployment_result(self, task_id, status, result):
        row = self.task(task_id)
        if row["status"] in TERMINAL or row["stop_requested"]:
            return
        target = "completed" if status == "completed" else "needs_attention"
        if row["status"] == target:
            return
        if row["status"] == "needs_attention" and target == "completed":
            self.transition(task_id, "queued")
            self.transition(task_id, "waiting_external", wait_reason="deployment")
        self.finish(task_id, target, result)

    def stopped(self, task_id):
        row = self.task(task_id)
        if row["status"] == "cancelling" or row["status"] == "reconciling" and row["stop_requested"]:
            state = ("reconciling" if self.unknown_tools(task_id) or row["wait_reason"] in {"deployment", "external"}
                     else "cancelled")
            self.finish(task_id, state, "用户已停止；执行记录已保留。",
                        error_class="unknown_write" if state == "reconciling" else "",
                        wait_reason=row["wait_reason"])
        return self.task(task_id)["status"]

    def recover(self):
        for row in self.repository.list():
            self.recover_one(row["id"])

    def recover_one(self, task_id):
        row = self.task(task_id)
        state = row["status"]
        if state in TERMINAL or state in {"waiting_user", "needs_attention", "reconciling", "retry_wait"}:
            return
        attempts = self.repository.attempts(task_id)
        if state == "waiting_external" and not self.unknown_tools(task_id):
            if row["wait_reason"] != "external" or attempts:
                return
        if row["stop_requested"]:
            target = ("reconciling" if self.unknown_tools(task_id) or row["wait_reason"] in {"deployment", "external"}
                      else "cancelled")
        elif self.unknown_tools(task_id):
            target = "reconciling"
        elif (state == "queued" and not any(a["started"] is not None and a["finished"] is None for a in attempts)
              or attempts and attempts[-1]["started"] is None):
            target = "queued"
        else:
            target = "needs_attention"
        with self.repository.transaction():
            self.repository.end_attempt(task_id, "interrupted", target,
                                        "unknown_write" if target == "reconciling" else "unavailable")
            if state != target:
                self.transition(task_id, target, recovery=True)
