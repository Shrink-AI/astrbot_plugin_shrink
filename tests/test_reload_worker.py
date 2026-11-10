import json
from unittest.mock import AsyncMock

import pytest

from shrink.adapters import reload_worker


@pytest.mark.asyncio
async def test_resumed_reload_verifies_generation_without_resubmission(tmp_path, monkeypatch):
    directory = tmp_path / "request"
    directory.mkdir()
    (directory / "request.json").write_text(json.dumps({"generation": "old"}))
    (directory / "result.json").write_text(json.dumps({"status": "issued"}))
    (tmp_path / "generation").write_text("new")
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url):
            from types import SimpleNamespace
            return SimpleNamespace(status_code=200, json=lambda: {"status": "ok"})
        async def post(self, *args, **kwargs):
            raise AssertionError("must not submit a restart twice")
    monkeypatch.setattr(reload_worker.httpx, "AsyncClient", lambda **kwargs: Client())
    await reload_worker.run(directory)
    assert json.loads((directory / "result.json").read_text("utf-8"))["status"] == "completed"


@pytest.mark.asyncio
async def test_no_new_generation_is_unknown_not_success(tmp_path, monkeypatch):
    directory = tmp_path / "request"
    directory.mkdir()
    (directory / "request.json").write_text(json.dumps({"generation": "same"}))
    (directory / "result.json").write_text(json.dumps({"status": "issued"}))
    (tmp_path / "generation").write_text("same")
    monkeypatch.setattr(reload_worker.asyncio, "sleep", AsyncMock())
    await reload_worker.run(directory)
    assert json.loads((directory / "result.json").read_text("utf-8"))["status"] == "unknown"
