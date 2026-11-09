"""Probe real AstrBot adapters using disposable state and an in-memory send sink."""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
sys.path.insert(0, str(ROOT))


async def main():
    from astrbot.core.provider.entities import LLMResponse, ProviderRequest
    from astrbot.core.provider.sources.openai_responses_source import ProviderOpenAIResponses
    from astrbot.core.message.message_event_result import MessageEventResult
    from shrink.config import fixed_persona
    from shrink.application import Application
    from shrink.adapters.conversation import ConversationAdapter

    cfg = json.loads(Path("/AstrBot/data/cmd_config.json").read_text(encoding="utf-8-sig"))
    model_cfg = next(p for p in cfg["provider"] if p["id"] == os.environ["SHRINK_PROBE_MODEL"])
    source = next(p for p in cfg["provider_sources"] if p["id"] == model_cfg["provider_source_id"])
    provider = ProviderOpenAIResponses({**source, **model_cfg}, cfg["provider_settings"])
    providers = {model_cfg["id"]: provider}
    embed_cfg = next(p for p in cfg["provider"] if p["id"] == os.environ["SHRINK_PROBE_EMBEDDING"])
    from astrbot.core.provider.sources.openai_embedding_source import OpenAIEmbeddingProvider

    providers[embed_cfg["id"]] = OpenAIEmbeddingProvider(embed_cfg, cfg["provider_settings"])

    with tempfile.TemporaryDirectory(prefix="shrink-probe-") as temp:

        async def chat(prompt, system="", images=None):
            response = await provider.text_chat(
                prompt=prompt, system_prompt=system, image_urls=images or [], contexts=[]
            )
            return response.completion_text

        async def embed(text):
            return await providers[embed_cfg["id"]].get_embedding(text)

        async def sync(prompt):
            assert fixed_persona() in prompt

        app = Application(Path(temp), chat, embed, sync)
        app.persona_repository.add_version("说话随和，偶尔接梗。", [], "probe")
        plugin = ConversationAdapter(app, "isolated.probe")
        app.expression.random = lambda: 1.0


        class Event:
            def __init__(self, text, key, group="A", uid="42", name="测试用户"):
                self.text, self.group, self.uid, self.name = text, group, uid, name
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
                return self.uid

            def get_self_id(self):
                return "bot"

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
                return []

            def get_result(self):
                return self.result

            async def send(self, chain):
                self.sent.append(chain)

        vector = await app.memory.embed("石英的跨群记忆测试")
        assert len(vector) == 1024
        print("EMBEDDING_OK dimensions=1024", flush=True)
        prompts = [
            "吃什么",
            "还没想好",
            "行我试试",
            "今天好累",
            "又加班了",
            "在吗",
            "刚把代码改没了",
            "不知道聊什么",
            "哈哈哈哈",
            "睡不着",
        ]
        results = []
        for i, prompt in enumerate(prompts):
            event = Event(prompt, str(i))
            await plugin.receive(event)
            assert event.get_extra("shrink_reply")
            req = ProviderRequest(prompt=prompt, system_prompt="")
            await plugin.enrich(event, req)
            for attempt in range(2):
                try:
                    response = await asyncio.wait_for(
                        provider.text_chat(
                            prompt=prompt,
                            system_prompt=req.system_prompt,
                            contexts=[
                                {"role": "user", "content": "吃什么"},
                                {
                                    "role": "assistant",
                                    "content": "今天给你三个选择：一、砂锅；二、双拼饭；"
                                    "三、盖浇饭。你先选一个。我再给你详细说说。",
                                },
                            ],
                        ),
                        90,
                    )
                    break
                except TimeoutError:
                    if attempt:
                        raise
                    print("PROVIDER_TIMEOUT retrying isolated test once", flush=True)
            await plugin.response(event, response)
            event.result = MessageEventResult().message(response.completion_text)
            await plugin.decorate(event)
            await event.send(event.result)
            await plugin.sent(event)
            length = len(response.completion_text)
            assert length <= 60
            assert len(event.sent) == 1
            results.append({"prompt": prompt, "reply": response.completion_text, "length": length})
            print("SHORT", json.dumps(results[-1], ensure_ascii=False), flush=True)
        assert sum(r["length"] <= 30 for r in results) >= 9
        duplicate = Event(prompts[0], "0")
        await plugin.receive(duplicate)
        assert duplicate.call_llm and not duplicate.get_extra("shrink_reply")
        event = Event("详细解释这段 Python 代码并给出步骤", "detail")
        await plugin.receive(event)
        response = LLMResponse(
            role="assistant", completion_text="详细说明\n```python\nprint(1)\n```\n" * 8
        )
        original = response.completion_text
        await plugin.response(event, response)
        assert response.completion_text == original
        event = Event("记住我喜欢酸辣粉", "memory")
        await plugin.receive(event)
        outcome = await plugin.remember(event, "喜欢酸辣粉")
        assert "已保存" in outcome
        event2 = Event("我爱吃什么", "recall", group="B", name="换个昵称")
        await plugin.receive(event2)
        reference = await app.memory.context(event2.text, "qq:42", "qq:group:B")
        assert "qq:group:A" in reference and "酸辣粉" in reference
        learning = await app.learning.learn(force=True)
        assert learning["status"] == "success", app.status.status()["last_error"]
        print("LEARNING_OK real model extraction and persistence", flush=True)
        from PIL import Image as PillowImage
        import io

        buffer = io.BytesIO()
        PillowImage.new("RGB", (32, 32), "green").save(buffer, "PNG")
        meme, _ = await plugin.memes.add(buffer.getvalue(), "测试图", "测试")
        event3 = Event("来张表情", "meme")
        await plugin.receive(event3)
        req = ProviderRequest(prompt=event3.text)
        await plugin.enrich(event3, req)
        response = LLMResponse(role="assistant", completion_text=f"[[meme:{meme['id']}]]")
        await plugin.response(event3, response)
        event3.result = MessageEventResult().message(response.completion_text)
        await plugin.decorate(event3)
        assert event3.result.chain == []
        assert len(event3.sent) == 1
        sticker = event3.sent[0].chain[0].toDict()
        assert sticker["type"] == "image" and sticker["data"]["sub_type"] == 1
        print("MEME_ONLY_OK one QQ sticker, no marker leakage", flush=True)

        await app.close()
        print(
            "HOOKS_OK duplicate/detail/memory/source/alias verified; external sends=0", flush=True
        )
    for p in providers.values():
        terminate = getattr(p, "terminate", None)
        if terminate:
            await terminate()


if __name__ == "__main__":
    asyncio.run(main())
