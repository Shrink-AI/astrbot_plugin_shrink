"""Task input authorization and durable consumption positions."""


class TaskCancelled(Exception):
    pass


class TaskInputs:
    ACTIVE = {"queued", "running", "waiting_external", "retry_wait"}

    def __init__(self, repository, tasks=None):
        from ..persistence.tasks import Tasks
        from .task_states import TaskStates
        self.repository = repository
        self.tasks = tasks or TaskStates(Tasks(repository.db))

    def owned(self, task_id, session, sender):
        row = self.tasks.task(task_id)
        if (row["session"], row["sender"]) != (session, sender):
            raise ValueError("只能操作当前会话中自己交办的任务")
        return row

    def append(self, task_id, session, sender, message_id, text):
        row = self.owned(task_id, session, sender)
        if row["status"] not in self.ACTIVE:
            raise ValueError("任务不接受补充，当前状态：" + row["status"])
        if not isinstance(message_id, str) or not message_id.strip():
            raise ValueError("补充要求需要原消息标识")
        if not isinstance(text, str) or not text.strip() or len(text) > 12000:
            raise ValueError("补充文本不能为空或超过12000字")
        saved = self.repository.receive(task_id, session, sender, message_id, text)
        return {"task_id": task_id, "input_id": saved["id"], "message_id": saved["message_id"],
                "status": "processed" if saved["processed"] is not None else
                          "entered" if saved["entered"] is not None else "received"}

    def pending(self, task_id):
        if self.tasks.task(task_id)["status"] not in self.ACTIVE:
            return []
        return self.repository.pending(task_id)

    async def deliver(self, task_id, append, snapshot):
        row = self.tasks.task(task_id)
        if row["stop_requested"] or row["status"] in {"cancelled", "cancelling"}:
            raise TaskCancelled()
        pending = self.pending(task_id)
        new = [row for row in pending if row["entered"] is None]
        for row in new:
            append(f"原创建者补充要求（输入 {row['id']}）：\n{row['text']}")
        # The persisted context is the acknowledgement; neither can advance alone.
        if new or not self.repository.has_context(task_id):
            await self.repository.checkpoint_async(task_id, snapshot(), entered=[row["id"] for row in new])
        return [row["id"] for row in pending]

    async def processed(self, task_id, messages, identities):
        await self.repository.checkpoint_async(task_id, messages, processed=identities)
        self.tasks.step_succeeded(task_id)

    async def continue_with(self, task_id, text):
        messages = self.repository.context(task_id)
        if messages is not None:
            await self.repository.checkpoint_async(task_id, [*messages, {"role": "user", "content": text}])

    async def resume(self, task_id, session, sender):
        row = self.owned(task_id, session, sender)
        self.tasks.capacity(row["kind"], task_id)
        if row["status"] not in {"reconciling", "needs_attention", "waiting_user", "failed", "cancelled"}:
            raise ValueError("任务不能续做，当前状态：" + row["status"])
        from .task_recovery import reconciliation
        events = self.tasks.events(task_id)
        notice = reconciliation(events)
        messages = self.repository.context(task_id)
        if messages is None:
            import json
            messages = [{"role": "user", "content": row["request"]}]
            notice = ("继续原任务：先核对副作用，不重复执行已完成步骤。已有执行记录：\n" +
                      json.dumps(events, ensure_ascii=False)[-24000:] + "\n" + notice)
        if notice:
            await self.repository.checkpoint_async(task_id, [*messages, {"role": "user", "content": notice}])
        self.tasks.resume(task_id)
