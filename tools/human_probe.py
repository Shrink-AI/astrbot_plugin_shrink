"""End-to-end check inside the AstrBot container with the real model, real embeddings and
real web search, on a disposable copy of the production database. Nothing is sent to QQ.

Usage (inside the container): python tools/human_probe.py <plugin dir> [--snapshot path]
Prints one JSON line per section, prefixed with HUMAN.
"""

import argparse
import asyncio
import json
import re
import sqlite3
import statistics
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

parser = argparse.ArgumentParser()
parser.add_argument("root")
parser.add_argument("--snapshot", default="/AstrBot/data/plugin_data/astrbot_plugin_shrink/shrink.db")
parser.add_argument("--group", default="qq:group:9100000001")
parser.add_argument("--only", default="", help="comma-separated sections to run")
args, _ = parser.parse_known_args()
ONLY = {x for x in args.only.split(",") if x}


def wanted(section):
    return not ONLY or section in ONLY
ROOT = Path(args.root).resolve()
sys.path[:0] = [str(ROOT.parent), str(ROOT)]

EICY = "qq:9000000001"
CASUAL = [
    "今天好累", "又加班了", "吃什么", "刚把代码改没了", "哈哈哈哈", "睡不着", "下雨了",
    "这游戏也太肝了", "周末有人打游戏吗", "新显卡到了", "终于修好了", "好饿",
]


def emit(section, **data):
    print("HUMAN " + json.dumps({"section": section, **data}, ensure_ascii=False), flush=True)


class Event:
    """Minimal AstrMessageEvent stand-in that records sends instead of delivering them."""

    def __init__(self, text, key, uid="42", name="测试群友", group="9100000001", components=()):
        self.text, self.uid, self.name, self.group = text, uid, name, group
        self.message_obj = SimpleNamespace(message_id=key, message_str=text)
        self.extras, self.components = {}, list(components)
        self.is_at_or_wake_command = False
        self.call_llm = False
        self.result = None
        self.sent = []
        self.unified_msg_origin = f"probe:GroupMessage:{group}"

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def set_extra(self, key, value):
        self.extras[key] = value

    def get_message_str(self):
        return self.text

    def get_sender_id(self):
        return self.uid

    def get_self_id(self):
        return "9000000000"

    def get_platform_name(self):
        return "aiocqhttp"

    def get_platform_id(self):
        return "probe"

    def get_group_id(self):
        return self.group

    def get_sender_name(self):
        return self.name

    def is_private_chat(self):
        return not self.group

    def get_messages(self):
        return self.components

    def get_result(self):
        return self.result

    async def send(self, chain):
        self.sent.append(chain)


async def main():
    from astrbot.core.message.message_event_result import MessageEventResult
    from astrbot.core.provider.entities import ProviderRequest
    from astrbot.core.provider.sources.openai_embedding_source import OpenAIEmbeddingProvider
    from astrbot.core.provider.sources.openai_responses_source import ProviderOpenAIResponses
    from shrink.adapters.conversation import ConversationAdapter
    from shrink.application import Application
    from shrink.services.memory import Query
    from shrink.services.reply_text import REASON_WORDS, looks_like_meta

    cfg = json.loads(Path("/AstrBot/data/cmd_config.json").read_text(encoding="utf-8-sig"))
    with sqlite3.connect(f"file:{args.snapshot}?mode=ro", uri=True) as db:
        settings = json.loads(db.execute("SELECT value FROM kv WHERE key='settings'").fetchone()[0])
    model_cfg = next(p for p in cfg["provider"] if p["id"] == settings["model_provider"])
    source_cfg = next(p for p in cfg["provider_sources"] if p["id"] == model_cfg["provider_source_id"])
    provider = ProviderOpenAIResponses({**source_cfg, **model_cfg}, cfg["provider_settings"])
    embed_cfg = next(p for p in cfg["provider"] if p["id"] == settings["embedding_provider"])
    embedder = OpenAIEmbeddingProvider(embed_cfg, cfg["provider_settings"])

    async def chat(prompt, system="", images=None):
        response = await provider.text_chat(prompt=prompt, system_prompt=system,
                                            image_urls=images or [], contexts=[])
        return response.completion_text

    async def embed(text):
        return await embedder.get_embedding(text)

    async def search(query, source=""):
        from astrbot.core.tools import web_search_tools
        from shrink.adapters.providers import SEARCH_TOOLS
        from shrink.services.glossary import GlossaryService

        settings_ = cfg["provider_settings"]
        tool_class = getattr(web_search_tools, SEARCH_TOOLS[settings_.get("websearch_provider", "tavily")])
        tool = tool_class()
        context = SimpleNamespace(get_config=lambda umo=None: cfg)
        wrapper = SimpleNamespace(context=SimpleNamespace(
            event=SimpleNamespace(unified_msg_origin=""), context=context))
        kwargs = {"query": query[:128]}
        if "count" in (tool.parameters or {}).get("properties", {}):
            kwargs["count"] = 5
        raw = await tool.call(wrapper, **kwargs)
        return GlossaryService.parse_results(raw if isinstance(raw, str) else str(raw))

    async def no_sync(prompt):
        return None

    try:
        with tempfile.TemporaryDirectory(prefix="shrink-human-probe-") as temp:
            data = Path(temp)
            src = sqlite3.connect(f"file:{args.snapshot}?mode=ro", uri=True)
            dst = sqlite3.connect(data / "shrink.db")
            src.backup(dst)
            src.close()
            dst.close()
            app = Application(data, chat, embed, no_sync, search)
            db = app.database

            plugin = ConversationAdapter(app, "probe.module")
            app.expression.random = lambda: 1.0

            if wanted('digest'):
                try:
                    recent = db.rows("SELECT id FROM messages WHERE source=? ORDER BY created DESC LIMIT 80",
                                     (args.group,))
                    db.connection.execute("UPDATE messages SET digested=1")
                    db.connection.executemany("UPDATE messages SET digested=0 WHERE id=?",
                                              [(r["id"],) for r in recent])
                    db.connection.commit()
                    started = time.time()
                    result = await app.digest.digest(args.group, force=True)
                    run = app.runs.get(result.get("run_id")) if result.get("run_id") else None
                    written = db.rows("SELECT kind,subject,text,quote,confidence,status FROM memories "
                                      "WHERE run=? ORDER BY id", (f"run:{result.get('run_id')}",))
                    emit("digest", status=result["status"], seconds=round(time.time() - started, 1),
                         stats=run["stats"] if run else None, summary=(app.digests.recent(args.group, 1) or [{}])[0].get("summary"),
                         written=[f"[{r['kind']}/{r['status']}/{r['confidence']}] {r['text']} ⟵ {r['quote']}" for r in written],
                         nicknames=[dict(r) for r in db.rows("SELECT user_id,name,status FROM nicknames WHERE run=?",
                                                             (f"run:{result.get('run_id')}",))])
                except Exception as exc:  # keep going; report which section failed
                    emit('digest', error=type(exc).__name__)

            if wanted('recall'):
                try:
                    recall = {}
                    for label, query in {
                        "unrelated": Query("今天天气不错", args.group, speaker="qq:1"),
                        "mention_shabao": Query("白宝最近在忙啥", args.group, speaker="qq:9000000004",
                                                mentioned=app.people.mentioned("白宝最近在忙啥", args.group)),
                        "speaker_cross_group": Query("我那个项目又出问题了", "qq:group:9000000009", speaker=EICY),
                    }.items():
                        block, rows = await app.memory.recall_for(query)
                        recall[label] = [r["text"][:60] for r in rows]
                    emit("recall", **recall)
                except Exception as exc:  # keep going; report which section failed
                    emit('recall', error=type(exc).__name__)

            if wanted('replies'):
                try:
                    replies = []
                    for index, text in enumerate(CASUAL):
                        event = Event(text, f"casual-{index}", uid=str(900 + index))
                        event.is_at_or_wake_command = True
                        await plugin.receive(event)
                        req = ProviderRequest(prompt=text, system_prompt="")
                        req.contexts = [{"role": "assistant", "content": "思考过程：用户发了消息。回复：嗯"}]
                        await plugin.enrich(event, req)
                        assert req.contexts == []
                        try:
                            response = await asyncio.wait_for(
                                provider.text_chat(prompt=text, system_prompt=req.system_prompt, contexts=[]), 150)
                        except asyncio.TimeoutError:
                            replies.append({"prompt": text, "error": "model_timeout"})
                            continue
                        raw = response.completion_text
                        await plugin.response(event, response)
                        event.result = MessageEventResult().message(response.completion_text)
                        await plugin.decorate(event)
                        reply = event.get_extra("shrink_text", "")
                        replies.append({"prompt": text, "form": event.get_extra("shrink_form"), "raw": raw[:120],
                                        "reply": reply})
                        if event.result.chain:
                            await event.send(event.result)
                            await plugin.sent(event)
                    lengths = [len(r["reply"]) for r in replies if r.get("reply")]
                    emit("replies", replies=replies,
                         median_length=statistics.median(lengths) if lengths else 0,
                         tiny=sum(1 for n in lengths if n <= 8), ending_period=sum(r.get("reply", "").endswith("。") for r in replies),
                         meta=sum(bool(looks_like_meta(r.get("reply", "")) or REASON_WORDS.search(r.get("reply", ""))) for r in replies),
                         empty=sum("reply" in r and not r["reply"] for r in replies),
                         timeouts=sum("error" in r for r in replies))
                except Exception as exc:  # keep going; report which section failed
                    emit('replies', error=type(exc).__name__)

            if wanted('addressing'):
                try:
                    shabao = Event("白宝我喜欢你妹妹可以认识一下吗", "shabao", uid="9000000006", name="C -少年C-")
                    await plugin.receive(shabao)
                    forced = Event("白宝你的rust跑通没", "shabao-forced", uid="9000000004", name="小林")
                    await plugin.receive(forced)
                    forced.set_extra("shrink_reply", True)
                    forced.get_extra("shrink_turn").reason = "spontaneous"
                    req = ProviderRequest(prompt=forced.text, system_prompt="")
                    await plugin.enrich(forced, req)
                    response = await provider.text_chat(prompt=forced.text, system_prompt=req.system_prompt, contexts=[])
                    raw = response.completion_text
                    await plugin.response(forced, response)
                    finished = forced.get_extra("shrink_finished")
                    emit("addressing", shabao_reason=shabao.get_extra("shrink_reply_reason"),
                         shabao_addressee=shabao.get_extra("shrink_turn").addressee,
                         forced_raw=raw[:100], forced_skip=finished.skip, forced_reply=finished.text,
                         says_not_shabao=bool(re.search(r"不叫白宝|叫谁白宝|我不是白宝", finished.text)))
                except Exception as exc:  # keep going; report which section failed
                    emit('addressing', error=type(exc).__name__)

            if wanted('leak'):
                try:
                    leak = Event("看这个", "leak")
                    leak.is_at_or_wake_command = True
                    await plugin.receive(leak)
                    leak.set_extra("shrink_form", "short")
                    fake = SimpleNamespace(role="assistant", tools_call_name=None, reasoning_content="x",
                                           completion_text="思考过程：用户Yu发了张图。\n回复：哈哈这也太可爱了",
                                           result_chain=None)
                    await plugin.response(leak, fake)
                    emit("leak", cleaned=fake.completion_text, reasoning_cleared=fake.reasoning_content == "")
                except Exception as exc:  # keep going; report which section failed
                    emit('leak', error=type(exc).__name__)

            if wanted('teach'):
                try:
                    session = app.teach_repository.create_session()
                    taught = await app.teach.chat(session, "阿白 群里也叫白宝，以前在做一个 rust 项目，最近缓存占用太大")
                    asked = await app.teach.ask(session)
                    emit("teach", reply=taught["reply"],
                         proposals=[(p["kind"], p["payload"]) for p in taught["proposals"]],
                         questions=[p["payload"].get("text") for p in asked["proposals"]])
                except Exception as exc:  # keep going; report which section failed
                    emit('teach', error=type(exc).__name__)

            await app.close()
            emit("done", external_sends=0, production_data_unchanged=True)
    finally:
        await provider.terminate()


if __name__ == "__main__":
    asyncio.run(main())
