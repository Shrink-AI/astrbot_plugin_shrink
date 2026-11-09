"""Reply tools (history, lookup, stickers, sandbox) and experience (episodes → know-how)."""

import asyncio
import json
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from shrink.application import Application
from shrink.prompts.chat import turn_prompt
from shrink.services.skills import SETTLE_SECONDS, tool_step

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sandbox"))
import server as sandbox  # noqa: E402

PERSON_A, FAN, SANQIU = "qq:9000000001", "qq:9000000004", "qq:9000000003"
NAMES = {PERSON_A: "成员甲", FAN: "小林", SANQIU: "小周"}
G, H = "qq:group:111", "qq:group:222"


async def no_vector(text):
    return None


async def no_sync(prompt):
    return None


class Reflector:
    """Answers reflection prompts with a queued JSON result and keeps the prompts."""

    def __init__(self):
        self.results, self.prompts = [], []

    async def __call__(self, prompt, system="", images=None):
        self.prompts.append(prompt)
        return json.dumps(self.results.pop(0) if self.results else {"verdict": "unclear",
                                                                    "action": "none"})


@pytest.fixture
def app(tmp_path):
    model = Reflector()
    application = Application(tmp_path / "data", model, no_vector, no_sync)
    application.model = model
    yield application
    application.database.close()


def say(app, key, uid, text, source=G, ago=3600, addressee=""):
    return app.messages.ingest({
        "id": key, "platform": "qq", "account": uid.split(":")[-1], "user_id": uid,
        "name": NAMES.get(uid, uid), "source": source, "source_name": source.split(":")[-1],
        "text": text, "created": time.time() - ago, "addressee": addressee, "mentions": [],
    })


def turn(text, uid=PERSON_A, source=G, created=None, key="t1"):
    return SimpleNamespace(
        id=key, text=text, user_id=uid, source=source, created=created or time.time() - 80,
    )


# ---- chat history ---------------------------------------------------------------------------------
def test_history_finds_keyword_with_neighbours_and_stays_in_this_chat(app):
    say(app, "h1", FAN, "有人有 gamejam 报名链接吗", ago=7200)
    say(app, "h2", SANQIU, "https://spotlight.taptap.cn/jam", ago=7190)
    say(app, "h3", PERSON_A, "谢了", ago=7180)
    say(app, "h4", FAN, "gamejam 的链接在别的群", source=H, ago=7000)
    out = app.tools.history(G, keyword="gamejam")
    assert "→ [" in out and "报名链接" in out
    assert "spotlight.taptap.cn" in out  # The answer is the line after the hit.
    assert "别的群" not in out


def test_history_by_person_and_empty_results(app):
    say(app, "p1", FAN, "今晚吃火锅", ago=600)
    say(app, "p2", PERSON_A, "我也想吃", ago=500)
    out = app.tools.history(G, person="小林", days=1)
    assert "火锅" in out and "我也想吃" not in out
    assert "没找到" in app.tools.history(G, person="不存在的人")
    assert "没有" in app.tools.history(G, keyword="完全没出现过的词", days=1)


def test_history_without_filters_includes_summaries(app):
    say(app, "s1", FAN, "随便聊聊", ago=300)
    with app.database.transaction() as connection:
        connection.execute(
            "INSERT INTO digests(source,start,end,count,summary,created) VALUES(?,?,?,?,?,?)",
            (G, time.time() - 9000, time.time() - 8000, 20, "大家讨论了周末去爬山", time.time()),
        )
    out = app.tools.history(G, days=1)
    assert "周末去爬山" in out and "随便聊聊" in out


# ---- lookup -------------------------------------------------------------------------------------
async def test_lookup_returns_card_lines_and_memories(app):
    say(app, "l1", PERSON_A, "我在写 rust", ago=300)
    app.memory_repository.add("impression", PERSON_A, G, "成员甲是程序员", origin="curated")
    out = await app.tools.lookup("成员甲", source=G, speaker=FAN, private=False)
    assert "这个人 成员甲" in out and "9000000001" in out
    assert "我在写 rust" in out and "成员甲是程序员" in out
    assert "没有关于" in await app.tools.lookup("火星移民", source=G, speaker=FAN, private=False)


# ---- stickers -------------------------------------------------------------------------------------
def test_find_meme_prefers_title_and_skips_disabled(app):
    def add(title, tags, enabled=1, sent=0.0):
        info = {"digest": title, "phash": "0" * 16}
        row = app.meme_repository.add(info, title + ".png", title, tags, "upload")
        with app.database.transaction() as connection:
            connection.execute(
                "UPDATE memes SET enabled=?,last_sent=? WHERE id=?", (enabled, sent, row["id"])
            )
        return row["id"]

    tagged = add("猫猫", "无语,猫")
    titled = add("无语", "叹气", sent=time.time())
    add("无语到家", "无语", enabled=0)
    assert app.tools.find_meme("无语")["id"] == titled
    assert app.tools.find_meme("猫")["id"] == tagged
    assert app.tools.find_meme("完全没有") is None
    (app.meme_repository.root / "无语.png").write_bytes(b"x")
    assert app.memes.select(titled, G, [], searched=True) is not None
    assert app.memes.select(titled, G, [], searched=False) is None


# ---- sandbox ------------------------------------------------------------------------------------
def test_sandbox_runs_code_and_reports_errors():
    ok = sandbox.run("print(sum(range(101)))")
    assert ok["stdout"].strip() == "5050" and ok["exit"] == 0 and not ok["timeout"]
    bad = sandbox.run("1/0")
    assert "ZeroDivisionError" in bad["stderr"] and bad["exit"] != 0


def test_sandbox_stops_runaway_code(monkeypatch):
    monkeypatch.setattr(sandbox, "WALL_SECONDS", 1)
    result = sandbox.run("while True:\n    pass")
    assert result["timeout"]


async def test_run_python_tool_talks_to_the_sandbox(app):
    server = ThreadingHTTPServer(("127.0.0.1", 0), sandbox.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        settings = app.state.settings.model_dump()
        settings["sandbox_url"] = f"http://127.0.0.1:{server.server_address[1]}"
        app.state.put("settings", settings)
        assert (await app.tools.run_python("print(2 ** 10)")).strip() == "1024"
        assert "print" in await app.tools.run_python("x = 1")
        settings["sandbox_url"] = "http://127.0.0.1:9"
        app.state.put("settings", settings)
        assert "连不上" in await app.tools.run_python("print(1)")
    finally:
        server.shutdown()


# ---- experience -----------------------------------------------------------------------------------
SEARCH = [
    tool_step("web_search_deepseek", {"query": "TapTap 聚光灯 GameJam 2026"}, "很多结果"),
    tool_step("web_search_deepseek", {"query": "聚光灯 48小时 2026"}, "又是很多结果"),
    tool_step("deepseek_fetch_web_page", {"url": "https://spotlight.taptap.cn/"}, "赛事列表"),
]


def test_only_meaningful_episodes_are_kept(app):
    t = turn("帮我查查 taptap 今年办了哪些 gamejam")
    assert app.skills.record(t, [], "嗯", "ok", []) is None
    assert app.skills.record(t, [tool_step("shrink_remember", {}, "ok")], "好", "ok", []) is None
    assert app.skills.record(t, SEARCH, "三场", "ok", [])
    assert app.skills.record(t, [], "", "timeout", [])


def _episode(app, text="帮我查查 taptap 今年办了哪些 gamejam", tools=SEARCH, outcome="ok",
             skills=()):
    episode = app.skills.record(turn(text), tools, "主要三场聚光灯", outcome, list(skills))
    with app.database.transaction() as connection:
        connection.execute(
            "UPDATE episodes SET created=? WHERE id=?", (time.time() - SETTLE_SECONDS - 5, episode)
        )
    return episode


async def test_reflection_creates_then_revises_know_how(app):
    first = _episode(app)
    say(app, "f1", PERSON_A, "那都哪几场，链接给一下", ago=SETTLE_SECONDS - 30, addressee="self")
    app.model.results.append({
        "verdict": "bad", "lesson": "搜了好几轮才找到官网", "action": "create",
        "title": "查 TapTap 聚光灯 GameJam", "when": "有人问 TapTap 办过哪些 gamejam 或聚光灯活动",
        "steps": "1. 直接抓 spotlight.taptap.cn 看赛事列表\n2. 回答时附上每场的链接",
        "reason": "这次绕了远路",
    })
    assert await app.skills.reflect_due() == 1
    prompt = app.model.prompts[-1]
    assert "web_search_deepseek" in prompt and "那都哪几场" in prompt and "提问的人" in prompt
    episode = app.skill_repository.episode(first)
    assert episode["reviewed"] == 1 and episode["verdict"] == "bad"
    skill = app.skill_repository.list()[0]
    assert skill["title"] == "查 TapTap 聚光灯 GameJam" and skill["version"] == 1

    found = await app.skills.relevant("taptap 最近有什么 gamejam")
    assert [s["id"] for s in found] == [skill["id"]]
    assert "spotlight.taptap.cn" in app.skills.render(found)
    assert await app.skills.relevant("今天天气怎么样") == []

    second = _episode(app, skills=[skill["id"]])
    app.model.results.append({
        "verdict": "good", "action": "update", "target": "e1",
        "title": "查 TapTap 聚光灯 GameJam", "when": "有人问 TapTap 办过哪些 gamejam 或聚光灯活动",
        "steps": "1. 抓 spotlight.taptap.cn\n2. 只列今年的场次和链接", "reason": "补上只列今年",
    })
    await app.skills.reflect_due()
    assert "这次用上了" in app.model.prompts[-1]
    detail = app.skills.detail(skill["id"])
    assert detail["version"] == 2 and detail["uses"] == 1 and detail["wins"] == 1
    assert [v["reason"] for v in detail["versions"]] == ["补上只列今年", "这次绕了远路"]
    assert {e["id"] for e in detail["episodes"]} == {first, second}
    assert detail["episodes"][0]["user_name"] == "成员甲"


async def test_reflection_rejects_private_or_malformed_know_how(app):
    _episode(app)
    app.model.results.append({
        "verdict": "good", "action": "create", "title": "找 成员甲",
        "when": "有人找 成员甲", "steps": "1. 直接 at qq 9000000001", "reason": "-",
    })
    _episode(app)
    app.model.results.append({"verdict": "good", "action": "create", "title": "", "when": "x",
                              "steps": "y"})
    _episode(app)
    app.model.results.append({"verdict": "good", "action": "update", "target": "e7",
                              "title": "a", "when": "b", "steps": "c"})
    assert await app.skills.reflect_due(limit=5) == 3
    assert app.skill_repository.list() == []
    assert app.runs.list("skill") == []


async def test_failing_know_how_is_retired_and_runs_can_be_undone(app):
    skill_id = app.skill_repository.add("查天气", "有人问天气", "1. 搜城市+天气", "人工")
    for _ in range(3):
        _episode(app, text="明天上海天气", skills=[skill_id])
        app.model.results.append({"verdict": "bad", "action": "none"})
    await app.skills.reflect_due(limit=5)
    skill = app.skill_repository.get(skill_id)
    assert skill["losses"] == 3 and skill["status"] == "retired"
    assert app.skill_repository.versions(skill_id)[0]["origin"] == "rule"

    _episode(app)
    app.model.results.append({
        "verdict": "good", "action": "create", "title": "查游戏比赛",
        "when": "问游戏比赛", "steps": "1. 去官网", "reason": "新经验",
    })
    await app.skills.reflect_due()
    run = app.runs.list("skill")[0]
    created = int(run["detail"].split("#")[-1])
    assert app.skill_repository.get(created)
    app.undo(run["id"])
    assert app.skill_repository.get(created) is None


async def test_admin_edit_is_a_new_version(app):
    skill_id = app.skill_repository.add("查天气", "有人问天气", "1. 搜城市+天气", "人工")
    assert await app.skills.edit(skill_id, steps="1. 搜“城市 天气 今天”")
    assert not await app.skills.edit(skill_id, steps="1. 搜“城市 天气 今天”")
    versions = app.skill_repository.versions(skill_id)
    assert [v["origin"] for v in versions] == ["admin", "reflection"]
    assert app.skill_repository.get(skill_id)["version"] == 2


def test_skills_disabled_skips_recall_and_reflection(app):
    settings = app.state.settings.model_dump()
    settings["skills_enabled"] = False
    app.state.put("settings", settings)
    app.skill_repository.add("查天气", "有人问天气", "1. 搜城市+天气", "人工")
    assert asyncio.run(app.skills.relevant("天气怎么样")) == []


def test_turn_prompt_offers_tools_and_know_how():
    with_skill = turn_prompt(chat="x", skills="- 查天气（有人问天气）：\n  1. 搜")
    assert "shrink_chat_history" in with_skill and "<你攒的经验>" in with_skill
    assert "<你攒的经验>" not in turn_prompt(chat="x")
