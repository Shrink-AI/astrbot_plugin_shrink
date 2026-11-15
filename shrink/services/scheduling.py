"""Independent local budgets. Remote providers keep their own service limits."""
import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from functools import wraps
from concurrent.futures import ThreadPoolExecutor

LANE = ContextVar("shrink_scheduling_lane", default="maintenance")
EXPRESSION_CALLS = ContextVar("shrink_expression_calls", default=None)
CAPACITY = {"chat": 8, "interactive": 4, "planning": 1, "execution": 3,
            "expression": 1, "urgent": 1, "maintenance": 1}


def in_lane(name):
    def decorate(function):
        @wraps(function)
        async def run(*args, **kwargs):
            token = LANE.set(name)
            try:
                return await function(*args, **kwargs)
            finally:
                LANE.reset(token)
        return run
    return decorate


def measure_expression(function):
    @wraps(function)
    async def run(*args, **kwargs):
        counter = [0]
        token = EXPRESSION_CALLS.set(counter)
        try:
            result = await function(*args, **kwargs)
            result.model_calls = counter[0]
            return result
        finally:
            EXPRESSION_CALLS.reset(token)
    return run


@asynccontextmanager
async def shared_lock(lock):
    # A foreground write cannot bypass serialization or act on a stale cached definition.
    if LANE.get() == "chat" and lock.locked():
        raise ValueError("资料正在同步，这一步尚未执行，可以稍后再试")
    async with lock:
        yield


class Scheduling:
    def __init__(self):
        self.slots = {name: asyncio.Semaphore(count) for name, count in CAPACITY.items()}
        self.queued = {name: asyncio.Event() for name in CAPACITY}
        self.resources = {}
        self.attachments = asyncio.Semaphore(2)
        self.executors = {}

    async def compute(self, function, *args):
        lane = "chat" if LANE.get() == "chat" else "background"
        executor = self.executors.get(lane)
        if executor is None:
            executor = self.executors[lane] = ThreadPoolExecutor(
                max_workers=2, thread_name_prefix="shrink-" + lane)
        return await asyncio.get_running_loop().run_in_executor(executor, function, *args)

    def close(self):
        for executor in self.executors.values():
            executor.shutdown(wait=False, cancel_futures=True)

    @asynccontextmanager
    async def use(self, name):
        slot = self.slots[name]
        if slot.locked():
            self.queued[name].set()
        async with slot:
            token = LANE.set(name)
            try:
                yield
            finally:
                LANE.reset(token)

    def wrap(self, function, resource):
        @wraps(function)
        async def call(*args, **kwargs):
            lane = LANE.get()
            slot = self.resources.setdefault((resource, lane), asyncio.Semaphore(CAPACITY[lane]))
            async with slot:
                return await function(*args, **kwargs)
        return call
