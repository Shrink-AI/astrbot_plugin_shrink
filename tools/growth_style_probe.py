"""Compare a real preview candidate with its baseline using disposable chat state."""

import asyncio
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path("/AstrBot/data/plugins/astrbot_plugin_shrink")
sys.path[:0] = [str(ROOT.parent), str(ROOT)]


class Event:
    def __init__(self, text, key):
        self.text = text
        self.message_obj = SimpleNamespace(message_id=key, message_str=text)
        self.extras = {}
        self.is_at_or_wake_command = True
        self.call_llm = False
        self.result = None
        self.sent = []

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def set_extra(self, key, value):
        self.extras[key] = value

    def get_message_str(self):
        return self.text

    def get_sender_id(self):
        return "42"

    def get_self_id(self):
        return "bot"

    def get_platform_name(self):
        return "aiocqhttp"

    def get_platform_id(self):
        return "isolated"

    def get_group_id(self):
        return "synthetic"

    def get_sender_name(self):
        return "测试用户"

    def is_private_chat(self):
        return False

    def get_messages(self):
        return []

    def get_result(self):
        return self.result

    async def send(self, chain):
        self.sent.append(chain)


async def no_embedding(text):
    return None


async def no_sync(prompt):
    pass


async def main():
    from astrbot.core.message.message_event_result import MessageEventResult
    from astrbot.core.provider.entities import ProviderRequest
    from astrbot.core.provider.sources.openai_responses_source import ProviderOpenAIResponses
    from shrink.application import Application
    from shrink.adapters.conversation import ConversationAdapter

    with sqlite3.connect(
        "file:/AstrBot/data/plugin_data/astrbot_plugin_shrink/shrink.db?mode=ro", uri=True
    ) as db:
        rows = db.execute(
            "SELECT id,diagnostics FROM learning_runs WHERE diagnostics IS NOT NULL ORDER BY id DESC"
        ).fetchall()
        run_id, diagnostic = next(
            (key, json.loads(raw)) for key, raw in rows if json.loads(raw).get("mode") == "preview"
        )
    assert diagnostic["decision"] == "would_apply", "Latest preview has no eligible candidate"
    cfg = json.loads(Path("/AstrBot/data/cmd_config.json").read_text(encoding="utf-8-sig"))
    model_cfg = next(p for p in cfg["provider"] if p["id"] == diagnostic["provider"])
    source = next(p for p in cfg["provider_sources"] if p["id"] == model_cfg["provider_source_id"])
    provider = ProviderOpenAIResponses({**source, **model_cfg}, cfg["provider_settings"])
    cases = [
        ("吃什么", [{"role": "user", "content": "今天懒得做饭了"}]),
        (
            "还没想好",
            [
                {"role": "user", "content": "晚上吃啥"},
                {
                    "role": "assistant",
                    "content": "今天给你三个选择：一、砂锅；二、双拼饭；三、盖浇饭。你先选一个。我再给你详细说说。",
                },
            ],
        ),
        ("行我试试", [{"role": "assistant", "content": "先把插件重载一下试试。"}]),
        (
            "终于修好了，原来是我端口填错了",
            [
                {"role": "user", "content": "折腾半天还是连不上"},
                {"role": "assistant", "content": "地址和端口都对一下。"},
            ],
        ),
    ]
    results = []
    try:
        for label, style in (
            ("before", diagnostic["original_style"]),
            ("candidate", diagnostic["candidate"]),
        ):
            with tempfile.TemporaryDirectory(prefix="shrink-growth-probe-") as temp:

                async def chat(prompt, system="", images=None):
                    response = await provider.text_chat(
                        prompt=prompt, system_prompt=system, image_urls=images or [], contexts=[]
                    )
                    return response.completion_text

                app = Application(Path(temp), chat, no_embedding, no_sync)
                app.persona_repository.add_version(style, [], "isolated probe")
                plugin = ConversationAdapter(app, "isolated.probe")
                app.expression.random = lambda: 1.0

                for index, (text, history) in enumerate(cases):
                    event = Event(text, str(index))
                    await plugin.receive(event)
                    assert event.get_extra("shrink_reply")
                    request = ProviderRequest(prompt=text, system_prompt="")
                    await plugin.enrich(event, request)
                    response = await asyncio.wait_for(
                        provider.text_chat(
                            prompt=text, system_prompt=request.system_prompt, contexts=history
                        ),
                        90,
                    )
                    assert response.role != "err"
                    await plugin.response(event, response)
                    event.result = MessageEventResult().message(response.completion_text)
                    await plugin.decorate(event)
                    await event.send(event.result)
                    await plugin.sent(event)
                    assert len(event.sent) == 1
                    assert len(response.completion_text) <= 60
                    result = {
                        "mode": label,
                        "prompt": text,
                        "reply": response.completion_text,
                        "length": len(response.completion_text),
                    }
                    results.append(result)
                    print("STYLE " + json.dumps(result, ensure_ascii=False), flush=True)
                    duplicate = Event(text, str(index))
                    await plugin.receive(duplicate)
                    assert not duplicate.get_extra("shrink_reply")
                assert app.database.rows("SELECT COUNT(*) AS n FROM messages")[0]["n"] == len(cases)
                await app.close()
        print(
            "STYLE "
            + json.dumps(
                {
                    "run_id": run_id,
                    "cases": len(results),
                    "within_30": sum(r["length"] <= 30 for r in results),
                    "duplicate_suppressed": True,
                    "storage_verified": True,
                    "external_sends": 0,
                }
            ),
            flush=True,
        )
    finally:
        await provider.terminate()


if __name__ == "__main__":
    asyncio.run(main())
