"""Evaluate the prompt package against real-log cases with the real model (inside the container).

Usage: python tools/prompt_eval.py <plugin dir>
Each case runs in its own disposable database; nothing is sent anywhere. Prints EVAL lines and a
summary; exits non-zero when a threshold is missed (chat properties >= 90%, must_reject 100%).
"""

import asyncio
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path[:0] = [str(ROOT.parent), str(ROOT), str(ROOT / "tools")]
CASES = json.loads((ROOT / "tests/prompt_cases/cases.json").read_text(encoding="utf-8"))
ONLY = set(sys.argv[2].split(",")) if len(sys.argv) > 2 else {"chat", "digest", "teach"}
CASES = {k: (v if k in ONLY else []) for k, v in CASES.items()}
ROAST = re.compile(r"装啥|抓包|得了吧|你急了|破防|闭嘴|菜就|就这|拱火")
GROUP = "qq:group:9100000001"


def emit(kind, **data):
    print("EVAL " + json.dumps({"kind": kind, **data}, ensure_ascii=False), flush=True)


async def main():
    from astrbot.core.message.message_event_result import MessageEventResult
    from astrbot.core.provider.entities import ProviderRequest
    from astrbot.core.provider.sources.openai_responses_source import ProviderOpenAIResponses
    from human_probe import Event
    from shrink.adapters.conversation import ConversationAdapter
    from shrink.application import Application
    from shrink.prompts.chat import TURN, PERSONA_VERSION
    from shrink.prompts.digest import EXTRACT, VERIFY
    from shrink.prompts.teach import PROPOSE
    from shrink.services.reply_text import REASON_WORDS, looks_like_meta

    cfg = json.loads(Path("/AstrBot/data/cmd_config.json").read_text(encoding="utf-8-sig"))
    model_id = os.environ["SHRINK_PROBE_MODEL"]
    model_cfg = next(p for p in cfg["provider"] if p["id"] == model_id)
    source = next(p for p in cfg["provider_sources"] if p["id"] == model_cfg["provider_source_id"])
    provider = ProviderOpenAIResponses({**source, **model_cfg}, cfg["provider_settings"])

    async def chat(prompt, system="", images=None):
        response = await provider.text_chat(prompt=prompt, system_prompt=system, contexts=[])
        return response.completion_text

    async def nothing(*args, **kwargs):
        return None

    async def no_search(query, source=""):
        return []

    checks, failures, errors = {}, [], []

    def check(name, ok, case):
        checks.setdefault(name, []).append(bool(ok))
        if not ok:
            failures.append(f"{name}:{case}")

    try:
        for case in CASES["chat"]:
            try:
                with tempfile.TemporaryDirectory(prefix="shrink-eval-") as temp:
                    app = Application(Path(temp), chat, nothing, nothing, no_search)
                    app.expression.random = lambda: 1.0
                    plugin = ConversationAdapter(app, "eval")
                    now = time.time()
                    for index, (uid, name, text) in enumerate(case.get("history", [])):
                        if uid == "self":
                            app.messages.record_bot(GROUP, text, created=now - 60 + index)
                        else:
                            app.messages.ingest({"id": f"h{index}", "platform": "qq", "account": uid,
                                                 "user_id": f"qq:{uid}", "name": name, "source": GROUP,
                                                 "text": text, "created": now - 60 + index})
                    for index, text in enumerate(case.get("recent", [])):
                        app.messages.record_bot(GROUP, text, created=now - 120 + index)
                    event = Event(case["text"], case["id"], uid=case.get("uid", "4242"),
                                  name=case.get("name", "群友"))
                    if case.get("image"):
                        from astrbot.api.message_components import Image
                        event.components = [Image(file="https://example.invalid/a.png")]
                        event.text = ""
                    event.is_at_or_wake_command = case["reason"] == "direct"
                    await plugin.receive(event)
                    turn = event.get_extra("shrink_turn")
                    turn.reason = case["reason"]
                    event.set_extra("shrink_reply", True)
                    req = ProviderRequest(prompt=case["text"], system_prompt="")
                    await plugin.enrich(event, req)
                    response = await asyncio.wait_for(
                        provider.text_chat(prompt=case["text"], system_prompt=req.system_prompt, contexts=[]),
                        120,
                    )
                    raw = response.completion_text or ""
                    await plugin.response(event, response)
                    event.result = MessageEventResult().message(response.completion_text)
                    await plugin.decorate(event)
                    finished = event.get_extra("shrink_finished")
                    reply = event.get_extra("shrink_text", "") if event.result.chain else ""
                    expect = case["expect"]
                    check("no_meta", not (looks_like_meta(reply) or REASON_WORDS.search(reply)), case["id"])
                    if not expect.get("detail"):
                        check("no_period", not reply.endswith("。"), case["id"])
                    if "max_len" in expect:
                        check("length", len(reply) <= expect["max_len"], case["id"])
                    if expect.get("no_roast"):
                        check("no_roast", not ROAST.search(reply), case["id"])
                    for word in expect.get("avoid", []):
                        check("avoid_repeat", word not in reply, case["id"])
                    if expect.get("skip_or_not_self"):
                        check("not_self", finished.skip or not re.search(r"叫谁白宝|我不是白宝|不叫白宝|我哪来的妹妹", reply), case["id"])
                    emit("chat", id=case["id"], form=event.get_extra("shrink_form"), skip=finished.skip,
                         raw=raw[:160], reply=reply)
                    await app.close()
            except Exception as exc:  # a slow relay must not abort the whole evaluation
                errors.append(f"chat:{case['id']}:{type(exc).__name__}")
                emit("error", id=case["id"], section='chat', error=type(exc).__name__, detail=str(exc)[:200])

        for case in CASES["digest"]:
            try:
                with tempfile.TemporaryDirectory(prefix="shrink-eval-") as temp:
                    app = Application(Path(temp), chat, nothing, nothing, no_search)
                    now = time.time() - 3600
                    for index, (uid, name, text, to) in enumerate(case["lines"]):
                        app.messages.ingest({
                            "id": f"d{index}", "platform": "qq", "account": uid, "user_id": f"qq:{uid}",
                            "name": name, "source": GROUP, "text": text, "created": now + index * 20,
                            "addressee": "self" if to == "self" else (f"qq:{to}" if to else ""),
                        })
                    result = await app.digest.digest(GROUP, force=True)
                    rows = app.database.rows("SELECT kind,text FROM memories WHERE status IN ('active','candidate')")
                    texts = " | ".join(r["text"] for r in rows).lower()
                    for word in case["must_reject"]:
                        check("must_reject", word.lower() not in texts, f"{case['id']}:{word}")
                    for word in case["must_keep"]:
                        check("must_keep", word.lower() in texts, f"{case['id']}:{word}")
                    emit("digest", id=case["id"], status=result["status"], written=[r["text"] for r in rows],
                         rejected=(result.get("stats") or {}).get("rejected"))
                    await app.close()
            except Exception as exc:  # a slow relay must not abort the whole evaluation
                errors.append(f"digest:{case['id']}:{type(exc).__name__}")
                emit("error", id=case["id"], section='digest', error=type(exc).__name__, detail=str(exc)[:200])

        for case in CASES["teach"]:
            try:
                with tempfile.TemporaryDirectory(prefix="shrink-eval-") as temp:
                    app = Application(Path(temp), chat, nothing, nothing, no_search)
                    app.messages.ingest({"id": "e", "platform": "qq", "account": "9000000001",
                                         "user_id": "qq:9000000001", "name": "阿白", "source": GROUP,
                                         "text": "在", "created": time.time()})
                    session = app.teach_repository.create_session()
                    result = await app.teach.chat(session, case["text"])
                    count = len(result["proposals"])
                    if "min_proposals" in case:
                        check("teach_split", count >= case["min_proposals"], case["id"])
                    if "max_proposals" in case:
                        check("teach_ask", count <= case["max_proposals"], case["id"])
                    emit("teach", id=case["id"], reply=result["reply"],
                         proposals=[(p["kind"], p["payload"].get("text") or p["payload"].get("name")) for p in result["proposals"]])
                    await app.close()
            except Exception as exc:  # a slow relay must not abort the whole evaluation
                errors.append(f"teach:{case['id']}:{type(exc).__name__}")
                emit("error", id=case["id"], section='teach', error=type(exc).__name__, detail=str(exc)[:200])
    finally:
        await provider.terminate()

    summary = {name: f"{sum(v)}/{len(v)}" for name, v in checks.items()}
    rates = {name: sum(v) / len(v) for name, v in checks.items()}
    passed = all(rate >= 0.9 for name, rate in rates.items() if name not in ("must_reject",)) and rates.get(
        "must_reject", 1.0) == 1.0
    emit("summary", prompts={"persona": PERSONA_VERSION, "turn": TURN.tag, "extract": EXTRACT.tag,
                             "verify": VERIFY.tag, "teach": PROPOSE.tag},
         checks=summary, failures=failures, errors=errors, passed=passed)
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    asyncio.run(main())
