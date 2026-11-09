"""New management endpoints: people, nicknames, memories, runs, review, teach, meme batch."""

from conftest import seed_known
import io
import json
import time

import httpx
import pytest
from PIL import Image

from shrink.api.app import create_app
from shrink.application import Application

PERSON_A = "qq:9000000001"


async def model(prompt, system="", images=None):
    if "管理员刚才说" in prompt:
        return json.dumps({"reply": "好", "proposals": [
            {"type": "memory", "kind": "event", "subject": "p1", "text": "成员甲会弹吉他", "importance": 3}
        ]}, ensure_ascii=False)
    return "{}"


async def nothing(*args):
    return None


def png(color):
    output = io.BytesIO()
    Image.new("RGB", (20, 20), color).save(output, "PNG")
    return output.getvalue()


@pytest.mark.asyncio
async def test_management_endpoints(tmp_path):
    app = Application(tmp_path / "data", model, nothing, nothing)
    seed_known(app)
    app.messages.ingest({
        "id": "m1", "platform": "qq", "account": "9000000001", "user_id": PERSON_A, "name": "成员甲",
        "source": "qq:group:1", "text": "在吗", "created": time.time(),
    })
    web = create_app(app, tmp_path, lambda: {"chat": [app.state.settings.model_provider],
                                             "embedding": [app.state.settings.embedding_provider]})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=web), base_url="http://127.0.0.1:6186",
        headers={"origin": "http://127.0.0.1:6186"},
    ) as client:
        for path in ("/api/v1/people", "/api/v1/review", "/api/v1/runs", "/api/v1/teach/sessions"):
            assert (await client.get(path)).status_code == 401
        password = (app.database.root / "initial-password.txt").read_text().strip()
        login = await client.post("/api/v1/login", json={"password": password})
        assert (await client.post("/api/v1/people/x/nicknames", json={"name": "a"})).status_code == 403
        client.headers["x-csrf-token"] = login.json()["csrf"]

        people = (await client.get("/api/v1/people?q=小甲")).json()
        assert people[0]["id"] == PERSON_A and {n["name"] for n in people[0]["nicknames"]} >= {"小甲"}
        assert (await client.get("/api/v1/users?q=成员甲")).status_code == 404
        bots = (await client.get("/api/v1/people?kind=bot")).json()
        assert [b["id"] for b in bots] == ["qq:9000000002"]
        added = await client.post(f"/api/v1/people/{PERSON_A}/nicknames", json={"name": "小E"})
        nickname_id = added.json()["id"]
        detail = (await client.get(f"/api/v1/people/{PERSON_A}")).json()
        assert "小E" in [n["name"] for n in detail["nicknames"]] and detail["sources"]
        assert (await client.delete(f"/api/v1/nicknames/{nickname_id}")).status_code == 200
        assert (await client.put(f"/api/v1/people/{PERSON_A}/kind", json={"kind": "robot"})).status_code == 422

        memory_id = app.memory_repository.add("event", PERSON_A, "qq:group:1", "成员甲在学rust",
                                              status="candidate", confidence=0.4)
        app.review.add("memory_candidate", memory_id, "测试")
        queue = (await client.get("/api/v1/review")).json()
        assert queue[0]["text"] == "关于 成员甲：成员甲在学rust"
        assert (await client.post(f"/api/v1/review/{queue[0]['id']}",
                                  json={"verdict": "correct"})).status_code == 200
        assert app.memory_repository.get(memory_id)["verified"] == 1
        listed = (await client.get("/api/v1/memories?status=active&kind=event")).json()
        assert [m["id"] for m in listed] == [memory_id]
        assert listed[0]["subject"] == PERSON_A and "user_id" not in listed[0]
        batch = await client.post("/api/v1/memories/batch", json={"ids": [memory_id], "action": "reject"})
        assert batch.json()["count"] == 1
        assert app.memory_repository.get(memory_id)["status"] == "rejected"

        session = (await client.post("/api/v1/teach/sessions")).json()["id"]
        reply = (await client.post(f"/api/v1/teach/sessions/{session}/messages",
                                   json={"text": "成员甲会弹吉他"})).json()
        proposal = reply["proposals"][0]
        accepted = await client.post(f"/api/v1/teach/proposals/{proposal['id']}/accept",
                                     json={"text": "成员甲会弹电吉他"})
        assert accepted.status_code == 200
        taught = app.database.rows("SELECT * FROM memories WHERE origin='admin_teach'")[0]
        assert taught["text"] == "成员甲会弹电吉他" and taught["verified"] == 1
        runs = (await client.get("/api/v1/runs")).json()
        assert runs[0]["kind"] == "teach"
        assert (await client.post(f"/api/v1/runs/{runs[0]['id']}/undo")).json()["memories"] == 1
        assert not app.database.rows("SELECT * FROM memories WHERE origin='admin_teach'")

        ids = []
        for color in ("red", "blue"):
            upload = await client.post("/api/v1/memes", files={"file": ("a.png", png(color), "image/png")})
            ids.append(upload.json()["meme"]["id"])
        assert (await client.post("/api/v1/memes/batch", json={"ids": ids, "action": "disable"})).status_code == 200
        assert len((await client.get("/api/v1/memes?enabled=0")).json()) == 2
        assert (await client.put(f"/api/v1/memes/{ids[0]}/enabled", json={"enabled": True})).status_code == 200
        assert [m["id"] for m in (await client.get("/api/v1/memes?enabled=1")).json()] == [ids[0]]
        assert (await client.post("/api/v1/memes/batch", json={"ids": ids, "action": "explode"})).status_code == 400
        assert (await client.post("/api/v1/memes/batch", json={"ids": ids, "action": "delete"})).json()["count"] == 2

        status = (await client.get("/api/v1/status")).json()
        assert {"review_open", "nicknames", "bots", "undigested"} <= set(status["counts"]) | set(status)
        assert "migration" not in status
        settings = (await client.get("/api/v1/settings")).json()["settings"]
        settings["split_probability"] = 0.3
        assert (await client.put("/api/v1/settings", json=settings)).status_code == 200
        assert app.state.settings.split_probability == 0.3
    await app.close()


@pytest.mark.asyncio
async def test_review_pagination_verdicts_and_deleted_targets(tmp_path):
    app = Application(tmp_path / "data", model, nothing, nothing)
    web = create_app(app, tmp_path, lambda: {"chat": [], "embedding": []})
    try:
        # Deleted targets on both sides of a page boundary must not leave holes or badges.
        deleted_ids = []
        for index in range(65):
            memory_id = app.memory_repository.add(
                "event", PERSON_A, "", f"待确认内容 {index}", status="candidate", origin="digest"
            )
            app.review.add("memory_candidate", memory_id, f"需要确认 {index}")
            if index in (0, 40):
                deleted_ids.append(memory_id)
        for memory_id in deleted_ids:
            app.memory_repository.delete_memory(memory_id)
        app.review.add("suspected_bot", "qq:missing", "目标已删除")
        all_items = app.review.open(100)
        missing = [item for item in all_items if app.teach.describe(item) is None]
        expected = [item for item in all_items if app.teach.describe(item) is not None]
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=web), base_url="http://127.0.0.1:6186",
            headers={"origin": "http://127.0.0.1:6186"},
        ) as client:
            password = (app.database.root / "initial-password.txt").read_text().strip()
            login = await client.post("/api/v1/login", json={"password": password})
            client.headers["x-csrf-token"] = login.json()["csrf"]
            pages = [(await client.get(f"/api/v1/review?offset={offset}")).json()
                     for offset in (0, 30, 60)]
            assert [len(page) for page in pages] == [30, 30, 3]
            assert [item["id"] for page in pages for item in page] == [i["id"] for i in expected]
            assert all({"id", "kind", "target", "reason", "text", "created"} <= item.keys()
                       and item["text"] for page in pages for item in page)
            assert all(app.review.get(item["id"])["status"] == "skipped" for item in missing)
            status = (await client.get("/api/v1/status")).json()
            assert status["counts"]["review_open"] == 63
            assert (await client.get("/api/v1/review?offset=90")).json() == []

            session = (await client.post("/api/v1/teach/sessions")).json()["id"]
            asked = (await client.post(f"/api/v1/teach/sessions/{session}/review")).json()
            assert asked["proposals"]
            by_queue = {p["payload"]["queue_id"]: p for p in asked["proposals"]}
            for item, verdict in zip(expected[:3], ("correct", "wrong", "skip")):
                result = await client.post(f"/api/v1/review/{item['id']}", json={"verdict": verdict})
                assert result.status_code == 200
                row = app.memory_repository.get(int(item["target"]))
                assert row["status"] == {"correct": "active", "wrong": "rejected",
                                         "skip": "candidate"}[verdict]
                assert row["verified"] == {"correct": 1, "wrong": -1, "skip": 0}[verdict]
                assert app.teach_repository.proposal(by_queue[item["id"]]["id"])["status"] != "pending"
            assert (await client.get("/api/v1/status")).json()["counts"]["review_open"] == 60
            assert len((await client.get("/api/v1/review")).json()) == 30
            for item in expected[3:]:
                response = await client.post(f"/api/v1/review/{item['id']}", json={"verdict": "skip"})
                assert response.status_code == 200
            assert (await client.get("/api/v1/review")).json() == []
            assert (await client.get("/api/v1/status")).json()["counts"]["review_open"] == 0

            memory_id = app.memory_repository.add("event", PERSON_A, "", "已删除的目标")
            app.review.add("memory_unverified", memory_id, "需要确认")
            app.memory_repository.delete_memory(memory_id)
            # Reading status first must also exclude invalid targets from the navigation count.
            assert (await client.get("/api/v1/status")).json()["counts"]["review_open"] == 0
    finally:
        await app.close()


@pytest.mark.asyncio
async def test_review_items_carry_their_chat_and_merge_duplicates(tmp_path):
    app = Application(tmp_path / "data", model, nothing, nothing)
    seed_known(app)
    try:
        start = time.time() - 3600
        lines = [("qq:1", "甲", "今天好安静"), (PERSON_A, "睡觉机", "某平台又要开杀了"),
                 ("qq:2", "乙", "啥意思"), (PERSON_A, "睡觉机", "大规模封号"), ("qq:2", "乙", "离谱")]
        for n, (uid, name, text) in enumerate(lines):
            app.messages.ingest({"id": f"m{n}", "user_id": uid, "name": name, "account": uid[3:],
                                 "source": "qq:group:9", "source_name": "测试群", "text": text,
                                 "created": start + n})
        app.messages.ingest({"id": "far", "user_id": "qq:1", "name": "甲", "account": "1",
                             "source": "qq:group:9", "source_name": "测试群",
                             "text": "上次开杀封了一堆号", "created": start - 86400})
        term = app.memory_repository.add("glossary", "", "", "平台大规模封号", term="开杀",
                                         evidence=["m1", "m3"], status="candidate", confidence=0.45)
        # The low-confidence write and the glossary guess are one question about one row.
        app.review.add("memory_candidate", term, "置信度不足")
        app.review.add("glossary_guess", term, "没有搜到依据")
        assert [(i["kind"], i["reason"]) for i in app.review.open(10)] == [("glossary_guess", "没有搜到依据")]
        # Duplicates already in the queue collapse into the newest one when listed.
        old = app.memory_repository.add("event", PERSON_A, "", "成员甲在做中转站", evidence=["m3"],
                                        status="candidate", confidence=0.4)
        with app.database.transaction() as connection:
            for kind in ("memory_candidate", "memory_unverified"):
                connection.execute(
                    "INSERT INTO review_queue(kind,target,reason,priority,status,created) "
                    "VALUES(?,?,?,3,'open',?)", (kind, str(old), kind, time.time()))
        app.memory_repository.add("impression", PERSON_A, "", "成员甲不吃辣", origin="curated")
        app.people.repository.save_profile(PERSON_A, "person", "运营中转站，不吃辣")
        app.review.add("profile_new", PERSON_A, "新的人物小结")

        items = {i["kind"]: i for i in app.teach.review_items()}
        assert set(items) == {"glossary_guess", "memory_unverified", "profile_new"}
        assert app.review.count_open() == 3
        glossary = items["glossary_guess"]
        assert [(c["id"], c["cited"]) for c in glossary["chat"]] == [
            ("m0", False), ("m1", True), ("m2", False), ("m3", True), ("m4", False)]
        assert glossary["chat"][1]["speaker"] == "睡觉机" and glossary["chat"][1]["group"] == "测试群"
        assert [u["id"] for u in glossary["uses"]] == ["far"]
        memory = items["memory_unverified"]
        assert memory["person"]["account"] == "9000000001" and "小甲" in memory["person"]["names"]
        assert [c["id"] for c in memory["chat"] if c["cited"]] == ["m3"]
        assert items["profile_new"]["memories"][0]["text"] in {"成员甲在做中转站", "成员甲不吃辣"}
        assert len(items["profile_new"]["memories"]) == 2

        await app.teach.verdict(glossary["id"], "correct")
        assert app.memory_repository.get(term)["status"] == "active"
        assert app.review.count_open() == 2
    finally:
        await app.close()
