"""Keep the dashboard controls and the public Settings schema in sync."""
import re
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from shrink.api.system import router_for
from shrink.adapters.agent_tasks import AgentTasks
from shrink.config import Settings
from shrink.persistence.database import Database, StateRepository

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "web/src/pages/SettingsPage.tsx"
EXCLUDED_CONTROLS = {
    "persona_id": "已接管的宿主人格 ID；PUT /settings 禁止修改，人格页只管理习惯版本。",
}


def interface_fields():
    source = (ROOT / "web/src/api/types.ts").read_text("utf-8")
    body = re.search(r"export interface Settings \{(.*?)\n\}", source, re.S)[1]
    return dict(re.findall(r"^\s*(\w+):\s*([^;]+);", body, re.M))


@pytest.mark.parametrize("name", Settings.model_fields)
def test_every_setting_has_a_type_and_control(name):
    types = interface_fields()
    source = PAGE.read_text("utf-8")
    errors = []
    if name not in types:
        errors.append("Settings 接口缺少字段")
    else:
        schema = Settings.model_json_schema()["properties"][name]
        expected = {"integer": "number", "number": "number", "boolean": "boolean",
                    "array": "string[]", "string": "string"}[schema["type"]]
        if "enum" in schema:
            expected = " | ".join(repr(value) for value in schema["enum"])
        if types[name] != expected:
            errors.append(f"类型应为 {expected}")
    assert set(EXCLUDED_CONTROLS) <= Settings.model_fields.keys()
    assert all(EXCLUDED_CONTROLS.values())
    if name not in EXCLUDED_CONTROLS:
        helper = re.search(rf"\b(?:num|flag|select)\('{name}'\s*,", source)
        fields = re.findall(r"<Field(?:\s[^>]*)?>.*?</Field>", source, re.S)
        control = any(re.search(r"<(?:Input|Textarea|Select|Switch)\b", field)
                      and re.search(rf"\bset\('{name}'\s*,", field) for field in fields)
        if not helper and not control:
            errors.append("后台缺少对应控件")
    assert not errors, f"{name}: {'；'.join(errors)}"


def test_types_have_no_stale_settings():
    assert interface_fields().keys() == Settings.model_fields.keys()


def test_protocol_selector_declares_only_the_supported_host_adapter_types():
    source = (PAGE.parent / "CapabilitiesPage.tsx").read_text("utf-8")
    block = re.search(r"const chatProtocols[^=]*=\s*(\{.*?\});", source, re.S)[1]
    assert set(re.findall(r"^\s*(\w+):", block, re.M)) == {
        "openai_chat_completion", "openai_responses", "anthropic_chat_completion",
    }
    assert 'htmlFor="model-protocol"' in source
    assert "modelConfig?.provider_source_id" in source and "delete next.type" in source


@pytest.mark.parametrize("page,endpoint", [
    ("PersonaPage.tsx", "/learning"),
    ("PeoplePage.tsx", "/nicknames/${n.id}/verify"),
    ("OperationsPage.tsx", "/operations/tasks/${selected.id}/redeliver"),
])
def test_missing_management_actions_have_buttons(page, endpoint):
    source = (PAGE.parent / page).read_text("utf-8")
    assert re.search(rf"api\(['\"`]{re.escape(endpoint)}['\"`], 'POST'", source)


@pytest.mark.parametrize("name", ["top_k_dense", "top_k_sparse", "top_m_final"])
def test_existing_knowledge_search_limits_have_controls(name):
    source = (PAGE.parent / "CapabilitiesPage.tsx").read_text("utf-8")
    assert re.search(rf"\bknowledgeNumber\('{name}',", source)


def test_redelivery_requires_confirmation_before_action():
    source = (PAGE.parent / "OperationsPage.tsx").read_text("utf-8")
    button = re.search(r'<Button[^>]*onClick=\{async \(\) => \{(.*?)\}\}>补发结果</Button>', source, re.S)[1]
    confirmation = re.search(r"if \(!await ask\(\{.*?desc: '[^']*原会话[^']*'.*?\}\)\) return;", button, re.S)
    assert confirmation
    assert confirmation.end() < button.index("void action(") < button.index("/redeliver")


def test_nickname_verification_actions_only_appear_for_candidates():
    source = (PAGE.parent / "PeoplePage.tsx").read_text("utf-8")
    chip = source.split("function NicknameChip", 1)[1].split("function PersonPanel", 1)[0]
    candidate = re.search(r"\{n.status === 'candidate' && <>(.*?)</>\}", chip, re.S)[1]
    assert "correct: true" in candidate and "correct: false" in candidate
    assert chip.count("/verify") == candidate.count("/verify") == 2


@pytest.mark.parametrize("name,schema", [
    (name, schema) for name, schema in Settings.model_json_schema()["properties"].items()
    if schema["type"] in {"integer", "number"}
])
def test_numeric_controls_match_backend_constraints(name, schema):
    source = PAGE.read_text("utf-8")
    match = re.search(
        rf"\bnum\('{name}', '[^']+', ([\d.]+), ([\d.]+)([^}}\n]*)\)", source)
    assert match, f"{name}: 缺少数值控件"
    minimum, maximum, options = match.groups()
    if "exclusiveMinimum" in schema:
        assert Decimal(minimum) == Decimal(str(schema["exclusiveMinimum"]))
        assert options.endswith(", true")
    else:
        assert Decimal(minimum) == Decimal(str(schema["minimum"]))
        assert not options.endswith(", true")
    assert Decimal(maximum) == Decimal(str(schema["maximum"]))
    fractional = re.search(r"const fractionalSettings = new Set<NumberKey>\(\[(.*?)\]\)", source, re.S)[1]
    assert (f"'{name}'" in fractional) == (schema["type"] == "number")


@pytest.mark.parametrize("name,step", [
    ("spontaneous_probability", "0.01"), ("engaged_seconds", "10"), ("stale_seconds", "5"),
    ("split_probability", "0.01"), ("history_compress_tokens", "1000"),
    ("history_retain_ratio", "0.05"), ("context_budget", "100"),
    ("debounce_seconds", "0.1"), ("debounce_max_seconds", "0.5"),
    ("decision_timeout", "0.1"), ("memory_budget", "50"),
    ("foreground_retrieval_timeout", "0.1"), ("foreground_rerank_timeout", "0.1"),
    ("foreground_grilling_timeout", "0.1"), ("notification_max_delay", "1"),
    ("learning_hours", "0.25"),
])
def test_numeric_arrow_steps_are_preserved(name, step):
    source = PAGE.read_text("utf-8")
    assert re.search(rf"\bnum\('{name}', '[^']+', [\d.]+, [\d.]+, {re.escape(step)}[,)]", source)


@pytest.mark.asyncio
async def test_settings_roundtrip_preserves_every_field(tmp_path):
    database = Database(tmp_path)
    try:
        state = StateRepository(database)
        original = state.settings.model_dump()
        original.update(model_provider="chat", embedding_provider="embedding",
                        persona_id="managed-persona", group_blacklist=["10001", "10002"],
                        task_retry_limit=0, decision_timeout=0.01,
                        foreground_retrieval_timeout=0.001, history_compress_tokens=1001)
        state.put("settings", original)
        app = FastAPI()
        app.include_router(router_for(state, None, None, lambda: {
            "chat": ["chat"], "embedding": ["embedding"]}))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            read = (await client.get("/api/v1/settings")).json()["settings"]
            assert read == original
            read["spontaneous_probability"] = 0.065
            read["foreground_retrieval_timeout"] = 0.05
            response = await client.put("/api/v1/settings", json=read)
            assert response.status_code == 200, response.text
            saved = (await client.get("/api/v1/settings")).json()["settings"]
            assert saved == {**original, "spontaneous_probability": 0.065,
                             "foreground_retrieval_timeout": 0.05}
            assert state.get("settings") == saved
            assert set(saved) == set(Settings.model_fields)
            invalid = await client.put("/api/v1/settings", json={**saved, "unknown": True})
            assert invalid.status_code == 422
            assert state.get("settings") == saved
    finally:
        database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("initial,updated,expected", [(3, 0, "needs_attention"), (0, 2, "retry_wait")])
async def test_saving_retry_limit_changes_next_retry_decision(tmp_path, initial, updated, expected):
    database = Database(tmp_path)
    try:
        state = StateRepository(database)
        state.put("settings", {**state.settings.model_dump(), "task_retry_limit": initial})
        operations = AgentTasks()
        operations.database = database
        operations.app = SimpleNamespace(state=state)
        operations.init_agents()
        states = operations.task_states
        identity = states.repository.create("work", "session", "creator", "provider")
        states.start(identity)
        app = FastAPI()
        app.include_router(router_for(state, None, None, lambda: {"chat": [""], "embedding": [""]}))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            settings = (await client.get("/api/v1/settings")).json()["settings"]
            response = await client.put("/api/v1/settings", json={**settings, "task_retry_limit": updated})
            assert response.status_code == 200, response.text
        assert states.failure(identity, TimeoutError(), now=100) == expected
        assert states.task(identity)["retry_count"] == (1 if expected == "retry_wait" else 0)
    finally:
        database.close()
