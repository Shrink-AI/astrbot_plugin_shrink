"""Schema creation, data-quality gates, digest, recall, undo and directed learning."""

import json
import re
import time

import pytest

from conftest import seed_known
from shrink.application import Application
from shrink.services.memory import Query
from shrink.services.perception import Turn, address

PERSON_A, FAN, CHAO, SANQIU, FEIYU, C, YU = (
    "qq:9000000001", "qq:9000000004", "qq:9000000005", "qq:9000000003", "qq:9000000002",
    "qq:9000000006", "qq:9000000007",
)
NAMES = {PERSON_A: "成员甲", FAN: "小林", CHAO: "老陈", SANQIU: "小周", FEIYU: "小机", C: "C", YU: "成员丙"}
G, H = "qq:group:111", "qq:group:222"


async def no_vector(text):
    return None


async def no_sync(prompt):
    return None


def refs_from(prompt):
    """Map display names to p-refs from the roster block of a digest/teach prompt."""
    return {name: ref for ref, name in re.findall(r"^(p\d+) ([^（\n]+)", prompt, re.M)}


class FakeModel:
    def __init__(self):
        self.calls = []
        self.verify_drop = "小周"

    async def __call__(self, prompt, system="", images=None):
        self.calls.append(prompt)
        if "任务：整理下面这段群聊" in prompt:
            return json.dumps(self.extract(prompt), ensure_ascii=False)
        if "逐条判断" in prompt:
            items = re.findall(r"^(\d+)\. 说法：(.*)$", prompt, re.M)
            return json.dumps(
                [{"n": int(n), "supported": self.verify_drop not in claim, "joke": False}
                 for n, claim in items]
            )
        if prompt.startswith("词："):
            return '{"meaning":"形容忍不住想笑","source":1}'
        if prompt.startswith("人物："):
            return '{"summary":"在做rust项目","relation":"偶尔一起聊技术"}'
        if "管理员刚才说" in prompt:
            return json.dumps(self.teach(prompt), ensure_ascii=False)
        if "待确认" in prompt:
            n = len(re.findall(r"^\d+\. ", prompt, re.M))
            return json.dumps({"questions": [{"n": i, "text": f"问题{i}对吗"} for i in range(1, n + 1)]})
        return "嗯"

    def extract(self, prompt):
        p = refs_from(prompt)
        return {
            "summary": "小林问成员甲的rust项目，成员甲说跑通了。老陈开玩笑说石英是小甲。",
            "topics": [{
                "text": "成员甲的rust项目跑通了，但缓存占了20G", "participants": [p["小林"], p["成员甲"]],
                "importance": 3, "certainty": "stated", "evidence": ["m3", "m4"],
                "quote": "跑通了 就是缓存占了20g",
            }],
            "memories": [
                {"kind": "event", "subject": p["成员甲"], "text": "成员甲在做一个rust项目，已经跑通",
                 "importance": 3, "certainty": "stated", "evidence": ["m4"],
                 "quote": "跑通了 就是缓存占了20g", "valid_until": None},
                {"kind": "event", "subject": p["小周"], "text": "小周11月7日去上海出差，待三天",
                 "importance": 3, "certainty": "stated", "evidence": ["m7"],
                 "quote": "明天去上海 待三天", "valid_until": "2099-11-10"},
                {"kind": "impression", "subject": "p0", "text": "石英是小甲", "importance": 3,
                 "certainty": "stated", "evidence": ["m5"], "quote": "你是小甲"},
                {"kind": "impression", "subject": p["成员甲"], "text": "成员甲喜欢吃香菜",
                 "importance": 3, "certainty": "stated", "evidence": ["m8"],
                 "quote": "成员甲 喜欢吃香菜"},
                {"kind": "impression", "subject": p["C"], "text": "C想认识成员甲的妹妹",
                 "importance": 3, "certainty": "joke", "evidence": ["m1"], "quote": "我喜欢你妹妹"},
                {"kind": "impression", "subject": p["成员甲"], "text": "成员甲是程序员",
                 "importance": 3, "certainty": "stated", "evidence": ["m2"], "quote": "我是程序员"},
                {"kind": "event", "subject": p["小周"], "text": "小周明天去上海",
                 "importance": 3, "certainty": "stated", "evidence": ["m7"],
                 "quote": "明天去上海 待三天"},
                {"kind": "event", "subject": p["小林"], "text": "小林说了句水话",
                 "importance": 1, "certainty": "stated", "evidence": ["m3"],
                 "quote": "小甲你那个rust项目跑通没"},
            ],
            "nicknames": [
                {"person": p["成员甲"], "name": "小甲", "evidence": ["m3"], "quote": "小甲你那个rust项目"},
                {"person": p["小林"], "name": "老林", "evidence": ["m10"], "quote": "老林 你也来了"},
                {"person": p["成员甲"], "name": "石英", "evidence": ["m5"], "quote": "你是小甲"},
            ],
            "jargon": [{"term": "难蚌", "usage": "忍不住想笑", "evidence": ["m4"],
                        "quote": "缓存占了20g 难蚌"}],
            "lessons": [{"text": "别把思考过程发出来", "evidence": ["m9"], "quote": "又漏思考过程了"}],
        }

    def teach(self, prompt):
        p = refs_from(prompt)
        known = re.findall(r"^(k\d+) ", prompt, re.M)
        proposals = [
            {"type": "memory", "kind": "event", "subject": p.get("成员甲", "p9"),
             "text": "成员甲在2025年夏天去上海实习过", "importance": 4},
            {"type": "nickname", "person": p.get("成员甲", "p9"), "name": "小E", "scope": "all"},
            {"type": "memory", "kind": "event", "subject": "p9", "text": "不存在的人做了某事"},
            {"type": "retire", "memory": "k99"},
            {"type": "nickname", "person": "p0", "name": "英宝"},
        ]
        if known:
            proposals.append({"type": "correct", "memory": known[0], "text": "成员甲的rust项目已经上线"})
        return {"reply": "我拆成了几条建议", "proposals": proposals}


@pytest.fixture
def world(tmp_path):
    fake = FakeModel()
    searches = []

    async def search(query, source=""):
        searches.append(query)
        return [{"title": "难蚌是什么梗", "url": "https://example.com/nanbeng",
                 "snippet": "难蚌即难绷，指忍不住"}]

    app = Application(tmp_path / "data", fake, no_vector, no_sync, search)
    seed_known(app)
    app.searches = searches
    app.fake = fake
    yield app
    app.database.close()


def say(app, key, uid, text, source=G, ago=3600, addressee="", mentions=()):
    created = time.time() - ago
    return app.messages.ingest({
        "id": key, "platform": "qq", "account": uid.split(":")[-1], "user_id": uid,
        "name": NAMES.get(uid, uid), "source": source, "source_name": source.split(":")[-1],
        "text": text, "created": created, "addressee": addressee, "mentions": list(mentions),
    })


def seed_chat(app):
    lines = [
        ("a1", C, "小甲我喜欢你妹妹可以认识一下吗", PERSON_A),
        ("a2", PERSON_A, "我妹妹不在这个群 别想了", ""),
        ("a3", FAN, "小甲你那个rust项目跑通没", PERSON_A),
        ("a4", PERSON_A, "跑通了 就是缓存占了20g 难蚌", ""),
        ("a5", CHAO, "你是小甲 阿甲是你妹妹", "self"),
        ("a6", FEIYU, "小周明天要去上海出差", ""),
        ("a7", SANQIU, "对 明天去上海 待三天", ""),
        ("a8", FEIYU, "成员甲 喜欢吃香菜", ""),
        ("a9", YU, "石英又漏思考过程了", "self"),
        ("a10", YU, "老林 你也来了", FAN),
    ]
    for index, (key, uid, text, to) in enumerate(lines):
        say(app, key, uid, text, ago=3600 - index * 10, addressee=to)


# ---- schema -------------------------------------------------------------------------------------


def test_schema_is_idempotent(tmp_path):
    root = tmp_path / "data"
    app = Application(root, FakeModel(), no_vector, no_sync)
    seed_known(app)
    db = app.database
    schema = db.rows("SELECT type,name,sql FROM sqlite_master ORDER BY type,name")
    tables = {r["name"] for r in schema if r["type"] == "table"}
    assert "users" not in tables and "migrations" not in tables
    assert {"people", "nicknames", "runs", "teach_proposals", "skills", "episodes"} <= tables
    assert app.people_repository.get(FEIYU)["kind"] == "bot"
    assert {n["name"] for n in app.people_repository.nicknames(PERSON_A)} == {"小甲", "白", "阿甲"}
    nickname = app.people_repository.nicknames(PERSON_A)[0]
    app.people_repository.delete_nickname(nickname["id"])
    app.people_repository.set_kind(FEIYU, "human")
    memory_id = app.memory_repository.add("event", PERSON_A, G, "在做rust项目", origin="curated")
    db.close()
    for _ in range(2):
        current = Application(root, FakeModel(), no_vector, no_sync)
        assert current.database.connection.execute("PRAGMA user_version").fetchone()[0] == 4
        assert current.database.rows("SELECT type,name,sql FROM sqlite_master ORDER BY type,name") == schema
        assert len(current.people_repository.nicknames(PERSON_A)) == 2
        assert current.people_repository.get(FEIYU)["kind"] == "human"
        assert current.memory_repository.get(memory_id)["origin"] == "curated"
        assert current.database.rows(
            "SELECT m.id FROM memory_fts f JOIN memories m ON m.id=f.rowid "
            "WHERE memory_fts MATCH '\"rust项\"'"
        )
        current.database.close()


# ---- perception ----------------------------------------------------------------------------------


def turn(app, text, uid=C, source=G, **extra):
    return Turn(id="x", platform="qq", account=uid.split(":")[-1], user_id=uid,
                name=NAMES.get(uid, ""), source=source, source_name="", text=text,
                created=time.time(), **extra)


def test_addressing_uses_known_nicknames(world):
    say(world, "s1", PERSON_A, "在", ago=60)
    book = world.people.address_book(G)
    assert address(turn(world, "小甲我喜欢你妹妹可以认识一下吗"), book) == (PERSON_A, "小甲")
    assert address(turn(world, "阿甲 你在吗"), book)[0] == PERSON_A
    assert address(turn(world, "石英你好"), book)[0] == "self"
    assert address(turn(world, "沙发好舒服"), book) == ("", "")
    assert address(turn(world, "老王哥你来了"), book) == ("other", "老王哥")
    assert address(turn(world, "这石英贫嘴"), book)[0] == "self"
    mentioned = turn(world, "看这个", mentions=[{"id": FAN, "name": "小林"}])
    assert address(mentioned, book)[0] == FAN
    replied = turn(world, "对的", reply_sender="self")
    assert address(replied, book)[0] == "self"


def test_engaged_conversation_continues_without_mention(world):
    world.messages.record_bot(G, "跑通就好", addressee=PERSON_A)
    reason = world.participation.decide(turn(world, "主要是缓存太大", uid=PERSON_A), 0.99)
    assert reason == "engaged"
    assert world.participation.decide(turn(world, "主要是缓存太大", uid=FAN), 0.99) == "quiet"
    # Turning to someone else ends the engagement until 石英 is addressed again.
    assert world.participation.decide(turn(world, "你看", uid=PERSON_A, addressee=FAN), 0.99) == "addressed_other"
    assert world.participation.decide(turn(world, "对吧", uid=PERSON_A), 0.99) == "other_conversation_followup"


# ---- digest and data gates -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_digest_keeps_grounded_facts_and_rejects_dirty_ones(world):
    seed_chat(world)
    result = await world.digest.digest(G, force=True)
    assert result["status"] == "success"
    stats = result["stats"]
    rows = world.database.rows("SELECT * FROM memories WHERE status!='superseded'")
    texts = {r["text"]: r for r in rows}
    assert "成员甲在做一个rust项目，已经跑通" in texts
    assert texts["成员甲在做一个rust项目，已经跑通"]["status"] == "active"
    assert texts["成员甲在做一个rust项目，已经跑通"]["quote"] == "跑通了 就是缓存占了20g"
    topic = texts["成员甲的rust项目跑通了，但缓存占了20G"]
    assert set(json.loads(topic["participants"])) == {FAN, PERSON_A}
    for dirty in ("石英是小甲", "成员甲喜欢吃香菜", "C想认识成员甲的妹妹", "成员甲是程序员",
                  "小周明天去上海", "小林说了句水话", "小周11月7日去上海出差，待三天"):
        assert dirty not in texts, dirty
    rejected = stats["rejected"]
    for reason in ("memories:subject", "memories:bot_only_evidence", "memories:joke",
                   "memories:quote_not_found", "memories:relative_time",
                   "memories:low_importance", "nicknames:already_known", "nicknames:name",
                   "verify:claim"):
        assert rejected.get(reason), reason
    lesson = [r for r in rows if r["kind"] == "lesson"]
    assert lesson and lesson[0]["subject"] == "self"
    glossary = [r for r in rows if r["kind"] == "glossary"]
    assert glossary and glossary[0]["term"] == "难蚌" and glossary[0]["status"] == "active"
    assert "https://example.com/nanbeng" in glossary[0]["evidence"]
    assert world.searches and "难蚌" in world.searches[0]
    guess = world.people_repository.nickname_by_name(G, "老林")
    assert guess is None  # inferred nickname waits as a candidate
    candidates = world.database.rows("SELECT * FROM nicknames WHERE name='老林'")
    assert candidates[0]["status"] == "candidate" and candidates[0]["user_id"] == FAN
    assert any(i["kind"] == "nickname_guess" for i in world.review.open(20))
    assert world.people_repository.profile(PERSON_A)["summary"] == "在做rust项目"
    assert world.database.rows("SELECT COUNT(*) AS n FROM messages WHERE digested=0")[0]["n"] == 0
    assert world.digests.recent(G)[0]["count"] == 10
    # The extraction prompt fences the chat as data and shows bots as bots.
    prompt = world.fake.calls[0]
    assert "<群聊>" in prompt and "小机(p" in prompt and "机器人" in prompt

    # A digest run can be undone completely.
    world.undo(result["run_id"])
    assert world.database.rows("SELECT COUNT(*) AS n FROM memories")[0]["n"] == 0
    assert not world.database.rows("SELECT * FROM nicknames WHERE name='老林'")
    assert world.people_repository.profile(PERSON_A) is None
    assert not world.digests.recent(G)
    with pytest.raises(ValueError):
        world.undo(result["run_id"])


@pytest.mark.asyncio
async def test_verify_failure_keeps_items_only_as_candidates(world):
    seed_chat(world)
    original = world.fake.__call__

    async def broken_verify(prompt, system="", images=None):
        if "逐条判断" in prompt:
            raise TimeoutError()
        return await original(prompt, system, images)

    world.digest.model = broken_verify
    world.glossary.model = broken_verify
    result = await world.digest.digest(G, force=True)
    assert result["stats"]["verify_failed"]
    claims = world.database.rows("SELECT * FROM memories WHERE kind IN ('event','topic')")
    assert claims and all(r["status"] == "candidate" for r in claims)


@pytest.mark.asyncio
async def test_failed_digest_backs_off_and_keeps_messages(world):
    seed_chat(world)

    async def broken(*args):
        return "not json"

    world.digest.model = broken
    result = await world.digest.digest(G, force=True)
    assert result["status"] == "failed"
    assert world.database.rows("SELECT COUNT(*) AS n FROM messages WHERE digested=0")[0]["n"] == 10
    assert G not in world.digest.due(time.time() + 10**6 - 10**6)


@pytest.mark.asyncio
async def test_unloaded_provider_retries_soon(world):
    from shrink.ports import ProviderUnavailable

    seed_chat(world)

    async def missing(*args, **kwargs):
        raise ProviderUnavailable("not loaded yet")

    world.digest.model = missing
    assert (await world.digest.digest(G, force=True))["status"] == "failed"
    assert world.state.get("digest_backoff")[G] - time.time() < 200


# ---- recall ----------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recall_is_selective_person_centred_and_cross_group(world):
    seed_chat(world)
    await world.digest.digest(G, force=True)
    say(world, "h1", FAN, "随便聊聊", source=H, ago=30)
    # Unrelated chatter recalls nothing, even though memories about the speaker exist.
    block, rows = await world.memory.recall_for(Query("今天天气不错", H, speaker=SANQIU))
    assert block == "" and rows == []
    # Mentioning 小甲 in another group brings 成员甲's memories, rendered without raw ids.
    mentioned = world.people.mentioned("小甲最近在干嘛", H)
    assert mentioned == [PERSON_A]
    block, rows = await world.memory.recall_for(
        Query("小甲最近在干嘛", H, speaker=FAN, mentioned=mentioned)
    )
    assert any("rust" in r["text"] for r in rows)
    assert "关于 成员甲" in block and "qq:" not in block and "{" not in block
    # The speaker's own memories follow them to another group when relevant.
    block, rows = await world.memory.recall_for(Query("我的rust项目缓存又变大了", H, speaker=PERSON_A))
    assert any(r["subject"] == PERSON_A for r in rows)
    # Glossary terms are recalled by exact term.
    block, rows = await world.memory.recall_for(Query("这也太难蚌了", H, speaker=SANQIU))
    assert any(r["kind"] == "glossary" for r in rows) and "难蚌" in block
    recalls = world.database.rows("SELECT MAX(recalls) AS n FROM memories")[0]["n"]
    assert recalls >= 1


@pytest.mark.asyncio
async def test_recall_skips_candidates_rejected_and_expired(world):
    repo = world.memory_repository
    live = repo.add("event", PERSON_A, G, "成员甲在学rust", importance=4, confidence=0.9)
    repo.add("event", PERSON_A, G, "成员甲在学rust和go", status="candidate", confidence=0.4)
    repo.add("event", PERSON_A, G, "成员甲在学rust吗", status="rejected", verified=-1)
    repo.add("event", PERSON_A, G, "成员甲这周在学rust", valid_until=time.time() - 10)
    _, rows = await world.memory.recall_for(Query("rust学得怎样", G, speaker=PERSON_A))
    assert [r["id"] for r in rows] == [live]


# ---- context window ----------------------------------------------------------------------------------


def test_context_window_is_budgeted_and_marks_speakers(world):
    for i in range(40):
        say(world, f"w{i}", PERSON_A if i % 2 else FAN, f"第{i}条消息" + "啊" * 90, ago=4000 - i * 5)
    say(world, "far", SANQIU, "很久以前", ago=90000)
    world.messages.record_bot(G, "嗯嗯", addressee=PERSON_A, created=time.time() - 3500)
    current = turn(world, "第39条", uid=PERSON_A)
    current.id = "w39"
    current.created = time.time() - 4000 + 39 * 5
    window = world.context.window(current)
    lines = [line for line in window.text.split("\n") if line.startswith("[")]
    assert 6 <= len(lines) < 40
    assert sum(len(line) for line in lines) <= world.state.settings.context_budget
    assert "（当前）" in lines[-1]
    assert "很久以前" not in window.text
    say(world, "other1", PERSON_A, "我在别的群问了个问题", source=H, ago=600)
    window = world.context.window(current)
    assert "刚才在别处" in window.text and "我在别的群问了个问题" in window.text


# ---- directed learning ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_teach_splits_validates_and_applies(world):
    seed_chat(world)
    say(world, "e", PERSON_A, "我在", ago=10)
    session = world.teach_repository.create_session()
    result = await world.teach.chat(session, "成员甲以前在上海实习过，群里也叫小E")
    kinds = sorted(p["kind"] for p in result["proposals"])
    assert kinds == ["memory", "nickname"]
    invalid = [p for p in world.teach_repository.proposals(session) if p["status"] == "invalid"]
    assert len(invalid) == 3  # unknown person, unknown memory, nickname for 石英
    for proposal in result["proposals"]:
        await world.teach.accept(proposal["id"])
    memory = world.database.rows("SELECT * FROM memories WHERE origin='admin_teach'")[0]
    assert memory["verified"] == 1 and memory["subject"] == PERSON_A and memory["confidence"] == 1.0
    nickname = world.people_repository.nickname_by_name(G, "小E")
    assert nickname["origin"] == "admin" and nickname["verified"] == 1
    with pytest.raises(ValueError):
        await world.teach.accept(result["proposals"][0]["id"])

    # Corrections supersede the old memory; the review queue can be answered directly.
    await world.digest.digest(G, force=True)
    result = await world.teach.chat(session, "成员甲的rust项目已经上线了")
    correct = [p for p in result["proposals"] if p["kind"] == "correct"]
    assert correct
    await world.teach.accept(correct[0]["id"])
    old = world.memory_repository.get(correct[0]["payload"]["memory_id"])
    assert old["status"] == "superseded" and old["superseded_by"]
    asked = await world.teach.ask(session)
    questions = [p for p in asked["proposals"] if p["kind"] == "question"]
    assert questions and questions[0]["payload"]["text"].startswith("问题")
    guess = next(q for q in questions if q["payload"]["kind"] == "nickname_guess")
    await world.teach.verdict(guess["payload"]["queue_id"], "wrong", guess["id"])
    assert world.database.rows("SELECT status FROM nicknames WHERE name='老林'")[0]["status"] == "rejected"
    # A rejected nickname is not re-learned by later digests.
    nid, outcome = world.people_repository.add_nickname(FAN, "老林", scope=G)
    assert outcome == "rejected"


@pytest.mark.asyncio
async def test_teach_asks_when_person_is_unknown(world):
    session = world.teach_repository.create_session()

    async def ambiguous(prompt, system="", images=None):
        return '{"reply":"你说的阿明是哪一位？","proposals":[]}'

    world.teach.model = ambiguous
    result = await world.teach.chat(session, "阿明以前当过兵")
    assert result["proposals"] == [] and "哪一位" in result["reply"]
