import asyncio
import socket
import time
from contextlib import contextmanager

import uvicorn


class Tasks:
    def __init__(self, state):
        self.state = state
        self.jobs = set()
        self.closed = False

    def error(self, stage, error):
        self.state.put(
            "last_error", {"stage": stage, "type": type(error).__name__, "at": time.time()}
        )

    def spawn(self, coroutine):
        if self.closed:
            coroutine.close()
            raise RuntimeError("Background tasks have stopped")
        task = asyncio.create_task(coroutine)
        self.jobs.add(task)
        task.add_done_callback(self.finished)
        return task

    def finished(self, task):
        self.jobs.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            self.error("background", error)

    async def close(self):
        self.closed = True
        jobs = list(self.jobs)
        for task in jobs:
            task.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)


class Scheduler:
    def __init__(self, state, learning, memory, error, digest=None, consolidation=None,
                 skills=None, startup_delay=90):
        self.state, self.learning, self.memory, self.error = state, learning, memory, error
        self.digest, self.consolidation, self.skills = digest, consolidation, skills
        # AstrBot loads model providers after plugins initialise; let it finish first.
        self.startup_delay = startup_delay

    async def tick(self):
        last = self.state.get("last_learning_check", 0)
        if time.time() - last >= self.state.settings.learning_hours * 3600:
            await self.learning.learn()
            self.state.put("last_learning_check", time.time())
        for item in self.memory.repository.unindexed():
            await self.memory.index_memory(item["id"])
        if self.digest:
            await self.digest.run_due()
        if self.consolidation:
            if self.consolidation.due():
                await self.consolidation.nightly()
        if self.skills:
            await self.skills.reflect_due()

    async def run(self):
        await asyncio.sleep(self.startup_delay)
        while True:
            try:
                await self.tick()
            except Exception as exc:
                self.error("scheduler", exc)
            await asyncio.sleep(60)


class EmbeddedServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self):
        # AstrBot owns process signals.
        yield


class DashboardServer:
    def __init__(self, app, host="0.0.0.0", port=6186):
        self.app, self.host, self.port = app, host, port
        self.server = self.task = self.socket = None

    async def start(self):
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            option = getattr(socket, "SO_EXCLUSIVEADDRUSE", socket.SO_REUSEADDR)
            self.socket.setsockopt(socket.SOL_SOCKET, option, 1)
            self.socket.bind((self.host, self.port))
            self.socket.listen(128)
            self.server = EmbeddedServer(
                uvicorn.Config(
                    self.app,
                    host=self.host,
                    port=self.port,
                    log_level="warning",
                    access_log=False,
                    proxy_headers=False,
                    lifespan="off",
                )
            )
            self.task = asyncio.create_task(self.server.serve(sockets=[self.socket]))
            for _ in range(50):
                if self.server.started:
                    return
                if self.task.done():
                    await self.task
                    raise RuntimeError("Dashboard exited before startup")
                await asyncio.sleep(0.1)
            raise RuntimeError("Dashboard startup timed out")
        except BaseException:
            await self.close()
            raise

    async def close(self):
        if self.server:
            self.server.should_exit = True
        try:
            if self.task:
                try:
                    await asyncio.wait_for(self.task, 10)
                except asyncio.TimeoutError:
                    self.task.cancel()
                    await asyncio.gather(self.task, return_exceptions=True)
        finally:
            if self.socket:
                self.socket.close()
            self.server = self.task = self.socket = None
