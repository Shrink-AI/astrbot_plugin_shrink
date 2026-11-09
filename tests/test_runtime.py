import asyncio
import socket
import sqlite3
import time

import httpx
import pytest
from fastapi import FastAPI

from shrink.application import Application
from shrink.runtime import DashboardServer


async def unavailable(*args):
    raise AssertionError("This check must not call an external provider")


@pytest.mark.asyncio
async def test_application_shutdown_cancels_work_before_database_closes(tmp_path):
    app = Application(tmp_path, unavailable, unavailable, unavailable)
    app.state.put("last_learning_check", time.time())
    app.start()
    scheduler = next(iter(app.tasks.jobs))
    app.start()
    assert app.tasks.jobs == {scheduler}
    entered = asyncio.Event()

    async def running():
        entered.set()
        try:
            await asyncio.Future()
        finally:
            app.state.put("cancelled_cleanly", True)

    task = app.tasks.spawn(running())
    await entered.wait()
    await app.close()

    await app.close()
    assert task.cancelled() and scheduler.cancelled()
    with sqlite3.connect(tmp_path / "shrink.db") as connection:
        assert (
            connection.execute("SELECT value FROM kv WHERE key='cancelled_cleanly'").fetchone()[0]
            == "true"
        )
    with pytest.raises(RuntimeError, match="closed"):
        app.start()
    with pytest.raises(RuntimeError, match="stopped"):
        app.tasks.spawn(running())






@pytest.mark.asyncio
async def test_dashboard_releases_port_and_failed_start_does_not_steal_listener():
    web = FastAPI()

    @web.get("/ready")
    async def ready():
        return {"ok": True}

    server = DashboardServer(web, host="127.0.0.1", port=0)
    await server.start()
    port = server.socket.getsockname()[1]
    try:
        async with httpx.AsyncClient() as client:
            assert (await client.get(f"http://127.0.0.1:{port}/ready")).json() == {"ok": True}
        second = DashboardServer(web, host="127.0.0.1", port=port)
        with pytest.raises(OSError):
            await second.start()
        await second.close()
        assert server.server.started
    finally:
        await server.close()
    await server.close()
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))


@pytest.mark.asyncio
async def test_background_failure_is_recorded_without_private_details(tmp_path):
    app = Application(tmp_path, unavailable, unavailable, unavailable)

    async def failed():
        raise RuntimeError("credential-bearing upstream detail")

    task = app.tasks.spawn(failed())
    await asyncio.gather(task, return_exceptions=True)
    assert app.state.get("last_error")["type"] == "RuntimeError"
    assert "credential" not in str(app.state.get("last_error"))
    await app.close()
