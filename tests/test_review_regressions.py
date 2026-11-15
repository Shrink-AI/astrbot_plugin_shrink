"""Review regressions; local data and event barriers only."""
import asyncio
from pathlib import Path
import tempfile
import time
import json
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from shrink import prompts
from shrink.adapters.knowledge import KnowledgeBridge
from shrink.adapters.public_api import PublicAPI
from shrink.api.app import create_app
from shrink.persistence.database import StateRepository
from tests import test_continuous_history

app = pytest.fixture(test_continuous_history.app.__wrapped__)


def test_temporary_files_and_pytest_capture_stay_outside_repository_root(pytestconfig):
    root = pytestconfig.rootpath.resolve()
    with tempfile.NamedTemporaryFile() as file:
        assert Path(file.name).resolve().parent != root
    capture = pytestconfig.pluginmanager.getplugin("capturemanager")._global_capturing
    for stream in (capture.out, capture.err):
        file = getattr(stream, "tmpfile", None)
        name = getattr(getattr(file, "buffer", None), "name", None)
        if isinstance(name, str):
            assert Path(name).resolve().parent != root


def test_pytest_startup_does_not_create_capture_files_in_current_directory(tmp_path, pytestconfig):
    log = tmp_path / "capture-paths.json"
    script = """
import json, os, sys, tempfile
tempfile.tempdir = os.getcwd()
paths = []
def audit(event, args):
    if event == 'tempfile.mkstemp':
        paths.append(args[0])
sys.addaudithook(audit)
import pytest
result = pytest.main(['--collect-only', 'tests/test_architecture.py', '-q', '-p', 'no:cacheprovider'])
with open(sys.argv[1], 'w', encoding='utf-8') as file:
    json.dump(paths, file)
raise SystemExit(result)
"""
    result = subprocess.run([sys.executable, "-c", script, str(log)], cwd=pytestconfig.rootpath,
                            capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    paths = json.loads(log.read_text("utf-8"))
    assert all(Path(path).resolve().parent != pytestconfig.rootpath.resolve() for path in paths)


def test_sqlite_keeps_default_writer_wait(app):
    assert app.database.rows("PRAGMA busy_timeout")[0]["timeout"] == 5000


def test_saved_settings_fill_foreground_budgets_without_resetting_user_values(app):
    stored = app.state.settings.model_dump()
    for name in ("foreground_retrieval_timeout", "foreground_rerank_timeout", "foreground_grilling_timeout"):
        stored.pop(name, None)
    stored["recall_count"] = 9
    app.state.put("settings", stored)
    state = StateRepository(app.database)
    assert state.settings.foreground_retrieval_timeout == 10
    assert state.settings.foreground_rerank_timeout == 20
    assert state.settings.foreground_grilling_timeout == 0
    assert state.get("settings")["foreground_retrieval_timeout"] == 10
    assert state.settings.recall_count == 9


@pytest.mark.parametrize("retrieval,rerank", [(10, 20), (17, 27)])
async def test_idle_foreground_knowledge_uses_configured_original_budgets(app, monkeypatch, retrieval, rerank):
    from shrink.adapters import knowledge
    values = {**app.state.settings.model_dump(),
              "foreground_retrieval_timeout": retrieval, "foreground_rerank_timeout": rerank}
    app.state.put("settings", values)
    app.state.put("knowledge_authority", "astrbot_kb")
    provider = SimpleNamespace(rerank=AsyncMock(return_value=[SimpleNamespace(index=0, relevance_score=1)]))
    context = SimpleNamespace(kb_manager=SimpleNamespace(retrieve=AsyncMock(return_value={})),
                              provider_manager=SimpleNamespace(get_provider_by_id=AsyncMock(return_value=provider)))
    bridge = KnowledgeBridge(context, app)
    waits, timeouts = [], []
    original_wait, original_timeout = asyncio.wait_for, asyncio.timeout
    async def wait(coro, timeout):
        waits.append(timeout)
        return await original_wait(coro, timeout)
    def timeout(value):
        timeouts.append(value)
        return original_timeout(value)
    monkeypatch.setattr(knowledge.asyncio, "wait_for", wait)
    monkeypatch.setattr(knowledge.asyncio, "timeout", timeout)
    async with app.scheduling.use("chat"):
        await bridge.candidates("问题")
        await bridge.rerank("问题", [(1, {"text": "已筛选的记忆"})])
    assert waits == [retrieval] and timeouts == [rerank]


def test_contract_is_scoped_and_contains_only_original_composition_rules():
    contract = prompts.CATALOG["outbound.contract"]["text"]
    assert contract.startswith("只在本轮用过工具、或在说任务与执行结果时适用；闲聊按人格与本轮要求来。")
    assert "像普通群友说话，少点评、少吐槽" not in contract
    assert "好嘞" not in contract and "那再等等" not in contract
    assert "没有记录不能推断未执行" not in contract


async def test_dashboard_sync_models_do_not_queue_behind_nightly_model(app, tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    lanes = []
    from shrink.services.scheduling import LANE
    async def model(prompt, *args):
        lanes.append(LANE.get())
        if prompt == "nightly":
            entered.set()
            await release.wait()
        return '{"reply":"好","proposals":[]}'
    app.teach.model = app.scheduling.wrap(model, "model")
    async def nightly():
        async with app.scheduling.use("maintenance"):
            await app.teach.model("nightly")
    web = create_app(app, tmp_path, lambda: {})
    worker = asyncio.create_task(nightly())
    try:
        await entered.wait()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web),
                                    base_url="http://127.0.0.1:6186",
                                    headers={"origin": "http://127.0.0.1:6186"}) as client:
            password = (app.state.root / "initial-password.txt").read_text().strip()
            login = await client.post("/api/v1/login", json={"password": password})
            client.headers["x-csrf-token"] = login.json()["csrf"]
            session = (await client.post("/api/v1/teach/sessions")).json()["id"]
            await asyncio.wait_for(client.post(f"/api/v1/teach/sessions/{session}/messages",
                                               json={"text": "记住这件事"}), 2)
            assert lanes == ["maintenance", "interactive"] and not release.is_set()
    finally:
        release.set()
        await worker


async def test_external_confirmation_does_not_inherit_maintenance_lane(app):
    from shrink.services.scheduling import LANE
    lanes = []
    async def receive(*args):
        lanes.append(LANE.get())
        return {}
    app.authorization_adapter = SimpleNamespace(receive=receive)
    api = PublicAPI(SimpleNamespace(application=app))
    result = await api.invoke({"protocol": 1, "version": 1, "capability": "authorization.confirmation",
                               "action": "submit", "arguments": {}, "scope": {}})
    assert result["ok"] and lanes == ["interactive"]


async def test_prompt_trial_uses_interactive_model_quota(app):
    from shrink.services.scheduling import LANE
    lanes = []
    async def model(*args):
        lanes.append(LANE.get())
        return "好嘞"
    app.expression.model = model
    created = time.time() - 600
    app.messages.ingest({"id": "trial", "platform": "qq", "account": "1", "user_id": "qq:1",
                         "name": "甲", "source": "qq:group:1", "text": "在吗", "created": created})
    app.messages.record_bot("qq:group:1", "等会", "qq:1", "direct", created=created + 1)
    rows = await app.prompts.trial(app, "qq:group:1", {})
    assert rows[0]["saved"]["text"] == "好嘞" and lanes == ["interactive", "interactive"]


async def test_compatibility_composition_uses_expression_lane_while_maintenance_is_full(app):
    from shrink.services.scheduling import LANE
    lanes = []
    async def model(*args):
        lanes.append(LANE.get())
        return "查到了"
    app.expression.model = model
    async with app.scheduling.use("maintenance"):
        result = await PublicAPI(SimpleNamespace(application=app)).invoke({
            "protocol": 1, "version": 1, "capability": "message.compose", "action": "compose",
            "arguments": {"facts": "查到的事实"}})
        assert result["data"]["text"] == "查到了" and lanes == ["expression"]
