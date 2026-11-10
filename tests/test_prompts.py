"""Prompts are data: shipped defaults, admin overrides with history, checks, and trial runs."""
import time

import httpx
import pytest

from shrink import prompts
from shrink.api.app import create_app
from shrink.application import Application
from shrink.prompts.chat import persona_prompt, turn_prompt

GROUP = "qq:group:1"


def test_every_default_renders_with_its_own_placeholders():
    for key, entry in prompts.CATALOG.items():
        assert entry["group"] and entry["title"] and entry["text"].strip(), key
        values = {name: f"<{name}>" for name in prompts.placeholders(entry["text"])}
        rendered = prompts.render(key, **values)
        assert "{{" not in rendered, key


def test_check_guards_placeholders():
    assert prompts.check("chat.turn.current", "说话的是{{speaker}}") == "缺少 {{reason}}"
    assert "{{who}}" in prompts.check("chat.turn.end", "只输出{{who}}要说的话")
    assert prompts.check("chat.turn.end", "  \n ") == "内容不能为空"
    assert prompts.check("no.such.key", "x") == "没有这个提示词"
    assert prompts.check("chat.turn.current", "{{speaker}}刚说话。{{reason}}") == ""


def test_drafts_only_apply_inside_the_block():
    with prompts.drafting({"chat.turn.end": "草稿结尾"}):
        assert "草稿结尾" in turn_prompt(chat="x")
    assert "草稿结尾" not in turn_prompt(chat="x")


async def nothing(*args):
    return None


def test_removed_prompts_keep_custom_history_without_loading(tmp_path):
    app = Application(tmp_path, nothing, nothing, nothing)
    try:
        key = "tasks.agent_result"
        app.prompts.repository.save(key, "以前定制的回报语气", "历史版本")
        app.prompts.load()
        assert key not in {entry["key"] for entry in app.prompts.catalog()}
        assert app.prompts.archived()[0]["history"][0]["text"] == "以前定制的回报语气"
        assert app.prompts.history(key)[0]["note"] == "历史版本"
        assert not prompts.overridden(key)
    finally:
        app.database.close()


@pytest.mark.asyncio
async def test_save_reset_history_and_persona_sync(tmp_path):
    synced = []

    async def sync(prompt):
        synced.append(prompt)

    app = Application(tmp_path / "data", nothing, nothing, sync)
    try:
        assert "随口回一句" not in turn_prompt(chat="x")
        with pytest.raises(ValueError, match="缺少"):
            await app.prompts.save("chat.turn.form", "回多长都行")
        await app.prompts.save("chat.turn.end", "随口回一句就行。", "试试")
        assert "随口回一句就行。" in turn_prompt(chat="x")
        assert (await app.prompts.save("chat.turn.end", "随口回一句就行。"))["changed"] is False

        await app.prompts.save("chat.persona", "你是石英。")
        assert synced and synced[-1].startswith("你是石英。")
        assert persona_prompt("").startswith("你是石英。")

        # A fresh process picks the saved wording up from the database.
        prompts.apply({})
        Application(tmp_path / "data", nothing, nothing, sync).database.close()
        assert "随口回一句就行。" in turn_prompt(chat="x")

        await app.prompts.reset("chat.turn.end")
        assert "随口回一句就行。" not in turn_prompt(chat="x")
        history = app.prompts.history("chat.turn.end")
        assert [h["reset"] for h in history] == [True, False]
        assert history[1]["note"] == "试试"
        rows = {r["key"]: r for r in app.prompts.catalog()}
        assert rows["chat.persona"]["modified"] and not rows["chat.turn.end"]["modified"]
        assert rows["chat.turn.current"]["placeholders"] == ["reason", "speaker"]
    finally:
        await app.prompts.reset("chat.persona")
        prompts.apply({})
        app.database.close()


@pytest.mark.asyncio
async def test_trial_replays_answered_messages_side_by_side(tmp_path):
    async def model(prompt, system="", images=None):
        return "草稿说法" if "草稿规则" in system else "原来说法"

    app = Application(tmp_path / "data", model, nothing, nothing)
    try:
        now = time.time() - 600
        app.messages.ingest({"id": "m1", "platform": "qq", "account": "1", "user_id": "qq:1",
                             "name": "沙", "source": GROUP, "text": "华莱士", "created": now})
        app.messages.record_bot(GROUP, "哪家的", "qq:1", "engaged", created=now + 5)
        web = create_app(app, tmp_path, lambda: {"chat": [], "embedding": []})
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=web), base_url="http://127.0.0.1:6186",
            headers={"origin": "http://127.0.0.1:6186"},
        ) as client:
            password = (app.database.root / "initial-password.txt").read_text().strip()
            login = await client.post("/api/v1/login", json={"password": password})
            client.headers["x-csrf-token"] = login.json()["csrf"]
            rows = (await client.post("/api/v1/prompts/trial", json={
                "source": GROUP, "drafts": {"chat.turn.end": "草稿规则：只输出要发到群里的话。"},
            })).json()
            assert rows[0]["said"] == "华莱士" and rows[0]["original"] == ["哪家的"]
            assert rows[0]["saved"]["text"] == "原来说法"
            assert rows[0]["draft"]["text"] == "草稿说法"
            bad = await client.post("/api/v1/prompts/trial", json={
                "source": GROUP, "drafts": {"chat.turn.form": "随便"}})
            assert bad.status_code == 400
            listed = (await client.get("/api/v1/prompts")).json()
            assert any(r["key"] == "chat.persona" for r in listed)
            assert (await client.put("/api/v1/prompts/chat.turn.end", json={"text": "{{x}}"})).status_code == 400
    finally:
        prompts.apply({})
        app.database.close()


def test_tool_texts_keep_their_parameters():
    key = "tool.shrink_chat_history"
    good = "翻聊天记录。\n\n参数：\nkeyword：词\nperson：人\ndays：几天"
    assert prompts.check(key, good) == ""
    assert "缺少参数 days" in prompts.check(key, "翻聊天记录。\n\n参数：\nkeyword：词\nperson：人")
    assert "工具没有参数 limit" in prompts.check(key, good + "\nlimit：几条")
    assert "看不懂这一行" in prompts.check(key, good + "\n随便写点")
    assert "工具说明不能为空" in prompts.check(key, "参数：\nkeyword：词\nperson：人\ndays：几天")
    assert "参数说明不能为空" in prompts.check(key, "翻。\n参数：\nkeyword：\nperson：人\ndays：几天")


def test_tool_doc_and_push_follow_saved_text():
    from types import SimpleNamespace

    from shrink.adapters.tool_prompts import push

    @prompts.tool_doc("shrink_lookup", query="string")
    async def lookup():
        pass

    assert lookup.__doc__.endswith("Args:\n    query(string): 人名、外号，或者要回忆的事情")
    tool = SimpleNamespace(name="shrink_lookup", description="old",
                           handler_module_path="core.main",
                           parameters={"properties": {"query": {"type": "string", "description": "old"}}})
    other = SimpleNamespace(name="someone_else", description="keep", parameters={})
    prompts.apply({"tool.shrink_lookup": "查记忆。\n\n参数：\nquery：要查的人或事"})
    try:
        assert push([tool, other], "core.main") == 1
        assert tool.description == "查记忆。"
        assert tool.parameters["properties"]["query"]["description"] == "要查的人或事"
        assert other.description == "keep"
    finally:
        prompts.apply({})


def test_prompt_updates_do_not_touch_foreign_tools_with_same_name():
    from types import SimpleNamespace
    from shrink.adapters.tool_prompts import push
    foreign = SimpleNamespace(name='shrink_lookup', handler_module_path='other.main',
                              description='owned elsewhere', parameters={})
    assert push([foreign], 'core.main') == 0
    assert foreign.description == 'owned elsewhere'


def test_main_registers_every_tool_prompt():
    import ast
    from pathlib import Path

    tree = ast.parse(Path(__file__).resolve().parents[1].joinpath("main.py").read_text(encoding="utf-8"))
    registered = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            for d in node.decorator_list:
                if isinstance(d, ast.Call) and getattr(d.func, "id", "") == "tool_doc":
                    registered[d.args[0].value] = {k.arg: k.value.value for k in d.keywords}
    tools = {k[5:]: v for k, v in prompts.CATALOG.items() if k.startswith("tool.")}
    assert set(registered) == set(tools)
    for name, types in registered.items():
        # Call the decorator exactly as main.py does: a parameter may be called "name".
        handler = prompts.tool_doc(name, **types)(lambda: None)
        assert "Args:" in handler.__doc__, name
        assert list(types) == list(prompts.tool_parts(tools[name]["text"])[1]), name
