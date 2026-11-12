"""Run the dashboard against disposable local data. ``--seed`` fills it with demo content."""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shrink.application import Application  # noqa: E402
from shrink.api.app import create_app  # noqa: E402

GROUP = "qq:group:9100000001"
PEOPLE = {
    "qq:9000000001": "白宝",
    "qq:9000000004": "小林",
    "qq:9000000003": "小周",
    "qq:9000000002": "小机",
    "qq:9000000007": "成员丙",
}


async def model(prompt, system="", images=None):
    if "管理员刚才说" in prompt:
        return json.dumps({"reply": "我拆成了一条记忆，确认后写入。", "proposals": [
            {"type": "memory", "kind": "event", "subject": "p1", "text": "预览：这个人在学吉他", "importance": 3}
        ]}, ensure_ascii=False)
    if "待确认" in prompt:
        return '{"questions":[{"n":1,"text":"这条对吗？"}]}'
    return '{"style":"说话随和。","evidence_ids":[],"expressions":[]}'


async def embed(text):
    return None


async def sync(prompt):
    pass


def seed(app):
    if app.state.get("preview_seeded"):
        return
    now = time.time()
    lines = [
        ("qq:9000000004", "小甲你那个rust项目跑通没", "qq:9000000001"),
        ("qq:9000000001", "跑通了 就是缓存占了20g 难蚌", ""),
        ("qq:9000000003", "对 下周去上海 待三天", ""),
        ("qq:9000000002", "小周要去上海出差", ""),
        ("qq:9000000007", "石英又漏思考过程了", "self"),
        ("qq:9000000001", "听说那边又要开杀了", ""),
        ("qq:9000000004", "开杀是啥", ""),
        ("qq:9000000001", "就是一大批号一起封", ""),
    ]
    for index, (uid, text, to) in enumerate(lines):
        app.messages.ingest({
            "id": f"preview-{index}", "platform": "qq", "account": uid.split(":")[1], "user_id": uid,
            "name": PEOPLE[uid], "source": GROUP, "source_name": "9100000001", "text": text,
            "created": now - 4000 + index * 30, "addressee": to,
        })
    run = app.runs.start("digest", GROUP)
    repo = app.memory_repository
    repo.add("event", "qq:9000000001", GROUP, "阿白在做一个rust项目，已经跑通", quote="跑通了 就是缓存占了20g",
             evidence=["preview-1"], importance=3, confidence=0.75, origin="digest", run_id=run)
    repo.add("topic", "", GROUP, "阿白的rust项目缓存占了20G", participants=["qq:9000000004", "qq:9000000001"],
             quote="缓存占了20g", evidence=["preview-0", "preview-1"], origin="digest", run_id=run)
    candidate = repo.add("impression", "qq:9000000003", GROUP, "小周经常出差", certainty="inferred",
                         confidence=0.5, status="candidate", evidence=["preview-2"], origin="digest", run_id=run)
    repo.add("glossary", "", "", "形容忍不住想笑，同“难绷”", term="难蚌", evidence=["preview-1"],
             confidence=0.7, origin="glossary", run_id=run)
    repo.add("lesson", "self", GROUP, "别把思考过程发出来", quote="又漏思考过程了", evidence=["preview-4"],
             origin="digest", run_id=run)
    nid, _ = app.people_repository.add_nickname("qq:9000000004", "老林", scope=GROUP, confidence=0.5, run_id=run)
    app.people_repository.set_nickname(nid, status="candidate")
    app.people_repository.add_nickname("qq:9000000001", "阿白", scope=GROUP, confidence=0.9, run_id=run)
    slang = repo.add("glossary", "", "", "指平台开始大规模封号", term="开杀", certainty="inferred",
                     evidence=["preview-5", "preview-7"], confidence=0.45, status="candidate",
                     origin="glossary", run_id=run)
    app.review.add("memory_candidate", slang, "置信度不足，等待印证或确认")
    app.review.add("glossary_guess", slang, "没有搜到依据，按群里用法推测的释义")
    app.review.add("memory_candidate", candidate, "置信度不足，等待印证或确认")
    app.review.add("nickname_guess", nid, "推断的称呼，等待确认")
    app.digests.add(GROUP, now - 4000, now - 3880, 5, "小林问阿白的rust项目，阿白说跑通了但缓存很大；小周下周去上海出差。", run)
    app.runs.finish(run, "success", {"messages": 5, "accepted": {"event": 1, "topic": 1},
                                     "rejected": {"memories:joke": 1, "memories:bot_only_evidence": 1},
                                     "outcomes": {"created": 4}}, "演示数据")
    app.people_repository.save_profile("qq:9000000001", "person", "在做rust项目，常聊技术。", "偶尔一起聊技术")
    app.review.add("profile_new", "qq:9000000001", "新的人物小结")
    app.expressions.add(GROUP, "别人分享好消息时", "好耶", ["preview-1"])
    from shrink.services.skills import tool_step

    asker = SimpleNamespace(id="preview-8", text="帮我查查taptap今年都举办了些什么gamejam",
                            user_id="qq:9000000001", source=GROUP, created=now - 3000)
    app.messages.ingest({
        "id": "preview-9", "platform": "qq", "account": "9000000001", "user_id": "qq:9000000001",
        "name": "成员甲", "source": GROUP, "source_name": "9100000001",
        "text": "那都哪几场，链接给一下", "created": now - 2880, "addressee": "self",
    })
    episode = app.skills.record(asker, [
        tool_step("web_search_deepseek", {"query": "TapTap 聚光灯 GameJam 2026"}, "【参赛必读】社区运营指南…"),
        tool_step("deepseek_fetch_web_page", {"url": "https://spotlight.taptap.cn/"}, "聚光灯计划 赛事列表…"),
        tool_step("web_search_deepseek", {"query": "\"2026\" 48小时 聚光灯"}, "【已开奖】2026 48H TapTap 制造周末丨广州场"),
    ], "主要就三场聚光灯", "ok", [])
    app.skill_repository.update_episode(episode, reviewed=1, verdict="bad",
                                        lesson="搜了好几轮才找到官网，应该直接看官网赛事列表")
    skill = app.skill_repository.add(
        "查 TapTap 聚光灯 GameJam", "有人问 TapTap 办过哪些 gamejam 或聚光灯活动",
        "1. 直接抓 spotlight.taptap.cn 看赛事列表\n2. 回答时附上每场的链接", "这次绕了远路",
        episodes=[episode],
    )
    app.skill_repository.revise(skill, "补上只列今年的场次", episodes=[episode],
                                steps="1. 直接抓 spotlight.taptap.cn 看赛事列表\n2. 只列今年的场次\n3. 每场附上链接")
    app.state.put("preview_seeded", True)


async def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=6186)
    parser.add_argument("--data", type=Path, default=root / "runtime" / "preview")
    parser.add_argument("--seed", action="store_true")
    args = parser.parse_args()
    application = Application(args.data, model, embed, sync)
    if args.seed:
        seed(application)
    app = create_app(
        application,
        root / "static",
        lambda: {
            "chat": [application.state.settings.model_provider],
            "embedding": [application.state.settings.embedding_provider],
        },
        allowed_hosts=frozenset({f"127.0.0.1:{args.port}", f"localhost:{args.port}"}),
    )
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning")
    )
    try:
        await server.serve()
    finally:
        await application.close()


if __name__ == "__main__":
    asyncio.run(main())

