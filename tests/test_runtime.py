import asyncio
import socket
import sqlite3
import time
import zipfile

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


def test_release_failure_restores_code_and_preserves_new_runtime_data(tmp_path, monkeypatch):
    from tools import update as release

    root = tmp_path / "installation"
    plugin = root / "astrbot-data/plugins/astrbot_plugin_shrink"
    plugin.mkdir(parents=True)
    originals = {"main.py": b"old", "metadata.yaml": b"version: 0.2.2", "requirements.txt": b"same"}
    for name, data in originals.items():
        (plugin / name).write_bytes(data)
    runtime = root / "astrbot-data/plugin_data/astrbot_plugin_shrink"
    runtime.mkdir(parents=True)
    for target in [runtime / "shrink.db", root / "astrbot-data/data_v4.db"]:
        with sqlite3.connect(target) as connection:
            connection.execute("CREATE TABLE retained(value TEXT)")
            connection.execute("INSERT INTO retained VALUES('before')")
    (root / "astrbot-data/cmd_config.json").write_text("{}")
    baseline, candidate = tmp_path / "baseline.zip", tmp_path / "candidate.zip"
    with zipfile.ZipFile(baseline, "w") as archive:
        for name, data in originals.items():
            archive.writestr(name, data)
    with zipfile.ZipFile(candidate, "w") as archive:
        for name, data in {
            **originals,
            "main.py": b"new",
            "shrink/application.py": b"new",
            "static/index.html": b"new",
        }.items():
            archive.writestr(name, data)
    calls = []
    monkeypatch.setattr(release, "container", calls.append)

    def unhealthy():
        if (plugin / "main.py").read_bytes() == b"new":
            with sqlite3.connect(runtime / "shrink.db") as connection:
                connection.execute("INSERT INTO retained VALUES('after')")
            return False
        return True

    monkeypatch.setattr(release, "health", unhealthy)
    with pytest.raises(RuntimeError, match="healthy"):
        release.update(root, candidate, baseline)
    assert (plugin / "main.py").read_bytes() == b"old"
    # The failed release may have migrated the schema; the old code gets its snapshot back.
    with sqlite3.connect(runtime / "shrink.db") as connection:
        assert connection.execute("SELECT value FROM retained").fetchall() == [("before",)]
    assert calls == ["stop", "start", "stop", "start"]


def test_rollback_can_restore_pre_upgrade_database(tmp_path, monkeypatch):
    from tools import update as release

    root = tmp_path / "installation"
    plugin = root / "astrbot-data/plugins/astrbot_plugin_shrink"
    plugin.mkdir(parents=True)
    (plugin / "main.py").write_bytes(b"new")
    runtime = root / "astrbot-data/plugin_data/astrbot_plugin_shrink"
    runtime.mkdir(parents=True)
    backup = root / "backups/shrink-release-1"
    (backup / "plugin").mkdir(parents=True)
    (backup / "plugin/main.py").write_bytes(b"old")
    for target, value in [(backup / "shrink.db", "before"), (runtime / "shrink.db", "after")]:
        with sqlite3.connect(target) as connection:
            connection.execute("CREATE TABLE retained(value TEXT)")
            connection.execute("INSERT INTO retained VALUES(?)", (value,))
    monkeypatch.setattr(release, "container", lambda action: None)
    monkeypatch.setattr(release, "health", lambda: True)
    release.rollback(root, backup, restore_data=True)
    assert (plugin / "main.py").read_bytes() == b"old"
    with sqlite3.connect(runtime / "shrink.db") as connection:
        assert connection.execute("SELECT value FROM retained").fetchall() == [("before",)]
    assert list(backup.glob("data-before-rollback-*.db"))


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
