"""Check group audience and tone with isolated state; no platform sender is used."""

import asyncio
import importlib
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools"))
probe = importlib.import_module("growth_style_probe")

sys.path[:0] = [str(ROOT.parent), str(ROOT)]


class GroupEvent(probe.Event):
    def __init__(self, text, key, components=(), wake=False):
        super().__init__(text, key)
        self.components = list(components)
        self.is_at_or_wake_command = wake

    def get_self_id(self):
        return "90001"

    def get_messages(self):
        return self.components


async def main():
    from astrbot.api.message_components import At, Reply
    from astrbot.core.provider.entities import ProviderRequest
    from astrbot.core.provider.sources.openai_responses_source import ProviderOpenAIResponses
    from shrink.application import Application
    from shrink.adapters.conversation import ConversationAdapter

    with sqlite3.connect(
        "file:/AstrBot/data/plugin_data/astrbot_plugin_shrink/shrink.db?mode=ro", uri=True
    ) as db:
        settings = json.loads(db.execute("SELECT value FROM kv WHERE key='settings'").fetchone()[0])
        active = json.loads(
            db.execute("SELECT value FROM kv WHERE key='active_persona'").fetchone()[0]
        )
        style = db.execute("SELECT text FROM persona_versions WHERE id=?", (active,)).fetchone()[0]
    cfg = json.loads(Path("/AstrBot/data/cmd_config.json").read_text(encoding="utf-8-sig"))
    model_cfg = next(p for p in cfg["provider"] if p["id"] == settings["model_provider"])
    source = next(p for p in cfg["provider_sources"] if p["id"] == model_cfg["provider_source_id"])
    provider = ProviderOpenAIResponses({**source, **model_cfg}, cfg["provider_settings"])
    try:
        with tempfile.TemporaryDirectory(prefix="shrink-tone-probe-") as temp:

            async def chat(prompt, system="", images=None):
                response = await provider.text_chat(
                    prompt=prompt, system_prompt=system, image_urls=images or [], contexts=[]
                )
                return response.completion_text

            app = Application(Path(temp), chat, probe.no_embedding, probe.no_sync)
            app.persona_repository.add_version(style, [], "isolated probe")
            app.state.put("settings", settings)
            plugin = ConversationAdapter(app, "isolated.probe")
            app.expression.random = lambda: 1.0

            with patch.object(
                importlib.import_module("shrink.adapters.conversation").random,
                "random",
                return_value=0,
            ):
                other = GroupEvent("说个曼波曼波", "other", [At(qq="12345")], wake=True)
                await plugin.receive(other)
                assert other.get_extra("shrink_reply_reason") == "addressed_other"
                followup = GroupEvent("发语音", "followup")
                await plugin.receive(followup)
                assert followup.get_extra("shrink_reply_reason") == "other_conversation_followup"
                called = GroupEvent("那你不如小机了", "called", [At(qq="90001")])
                await plugin.receive(called)
                assert called.get_extra("shrink_reply_reason") == "direct"
                quoted = GroupEvent(
                    "继续说",
                    "quoted",
                    [
                        Reply(id="10", sender_id="12345", message_str="hello"),
                    ],
                )
                await plugin.receive(quoted)
                assert quoted.get_extra("shrink_reply_reason") == "addressed_other"
                named = GroupEvent("石英，吃什么", "named")
                await plugin.receive(named)
                assert named.get_extra("shrink_reply_reason") == "direct"
                casual = GroupEvent("今天终于下班了", "casual")
                await plugin.receive(casual)
                assert casual.get_extra("shrink_reply_reason") == "spontaneous"
                req = ProviderRequest(prompt=casual.text, system_prompt="")
                await plugin.enrich(casual, req)
                assert "没人叫你" in req.system_prompt
            print("TONE audience_checks_passed", flush=True)
            histories = [
                (
                    "那你不如小机了",
                    [
                        {"role": "user", "content": "发语音"},
                        {"role": "assistant", "content": "发不了语音，我这破系统又没装发声器官。"},
                    ],
                ),
                (
                    "666",
                    [
                        {"role": "user", "content": "那你不如小机了"},
                        {
                            "role": "assistant",
                            "content": "得了吧，小机刚才不也是发纯文字么，装啥呢。",
                        },
                    ],
                ),
                (
                    "别老怼人",
                    [
                        {"role": "user", "content": "666"},
                        {
                            "role": "assistant",
                            "content": "被我当场抓包了吧，两个纯文本bot比什么发音啊。",
                        },
                    ],
                ),
            ]
            for index, (text, history) in enumerate(histories):
                event = GroupEvent(text, "tone" + str(index), [At(qq="90001")])
                await plugin.receive(event)
                req = ProviderRequest(prompt=text, system_prompt="")
                await plugin.enrich(event, req)
                response = await asyncio.wait_for(
                    provider.text_chat(
                        prompt=text,
                        system_prompt=req.system_prompt,
                        contexts=history,
                    ),
                    90,
                )
                assert response.role != "err"
                await plugin.response(event, response)
                reply = response.completion_text
                assert len(reply) <= 60
                assert not any(
                    word in reply for word in ["装啥", "抓包", "得了吧", "你急了", "破防", "闭嘴"]
                )
                print(
                    "TONE " + json.dumps({"prompt": text, "reply": reply}, ensure_ascii=False),
                    flush=True,
                )
            assert not any(event.sent for event in [other, followup, called, quoted, named, casual])
            await app.close()
            print("TONE external_sends=0 production_data_unchanged", flush=True)
    finally:
        await provider.terminate()


if __name__ == "__main__":
    asyncio.run(main())
