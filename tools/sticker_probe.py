"""Exercise the installed QQ serializer with a simulated sender, never real QQ."""

import asyncio
import base64
import io
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path[:0] = [str(ROOT.parent), str(ROOT)]


class Sender:
    def __init__(self):
        self.messages = []
        self.fail_sticker = False

    async def send_group_msg(self, **payload):
        await self.send("group", payload)

    async def send_private_msg(self, **payload):
        await self.send("private", payload)

    async def send(self, kind, payload):
        if self.fail_sticker and payload["message"][0]["type"] == "image":
            raise RuntimeError("simulated send failure")
        self.messages.append((kind, payload))


class Event:
    def __init__(self, source, sender, meme_id, caption, explicit=False):
        self.source, self.bot = source, sender
        self.result = None
        from shrink.services.perception import Turn

        self.extras = {
            "shrink_reply": True,
            "shrink_turn": Turn(
                id="probe", platform="qq", account="42", user_id="qq:42", name="测试",
                source=source, source_name="", text="来张表情" if explicit else caption,
                created=time.time(), reason="direct",
            ),
            "shrink_form": "short",
            "shrink_meme_choices": [meme_id],
            "shrink_meme_explicit": explicit,
        }

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def set_extra(self, key, value):
        self.extras[key] = value

    def get_result(self):
        return self.result

    def get_platform_name(self):
        return "aiocqhttp"

    async def send(self, chain):
        from aiocqhttp import Event as RawEvent
        from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
            AiocqhttpMessageEvent,
        )

        await AiocqhttpMessageEvent.send_message(
            self.bot,
            chain,
            event=RawEvent({"self_id": 12345}),
            is_group="group" in self.source,
            session_id="42",
        )


async def unused(*args):
    raise AssertionError("No model, vector service or persona sync needed")


async def main():
    from PIL import Image
    from astrbot.core.message.message_event_result import MessageChain, MessageEventResult
    from astrbot.core.provider.entities import LLMResponse
    from shrink.application import Application
    from shrink.adapters.conversation import ConversationAdapter

    cases = [
        ("caption_group", "qq:group:42", "笑死。", False, False, False),
        ("only_sticker_private", "qq:private:42", "", False, False, False),
        ("disabled", "qq:group:42", "笑死。", True, False, False),
        ("cooldown", "qq:group:42", "笑死。", False, True, False),
        ("explicit_bypasses_cooldown", "qq:private:42", "", False, True, True),
        ("send_failure", "qq:group:42", "笑死。", False, False, False),
        ("existing_chain", "qq:group:42", "笑死。", False, False, False),
    ]
    for name, source, caption, disabled, cooldown, explicit in cases:
        with tempfile.TemporaryDirectory(prefix="shrink-sticker-probe-") as temp:
            app = Application(Path(temp), unused, unused, unused)
            plugin = ConversationAdapter(app, "isolated.probe")
            app.expression.random = lambda: 1.0

            buffer = io.BytesIO()
            Image.new("RGB", (32, 32), "red").save(
                buffer,
                "GIF",
                save_all=True,
                append_images=[Image.new("RGB", (32, 32), "blue")],
                duration=100,
                loop=0,
            )
            original = buffer.getvalue()
            meme, _ = await app.memes.add(original, "测试表情", "测试")
            if disabled:
                app.database.connection.execute("UPDATE memes SET enabled=0")
                app.database.connection.commit()
            if cooldown:
                app.memes.sent(meme["id"], source)
            before_sent = app.database.rows("SELECT last_sent FROM memes")[0]["last_sent"]
            sender = Sender()
            sender.fail_sticker = name == "send_failure"
            event = Event(source, sender, meme["id"], caption, explicit)
            raw_text = caption + f"[[meme:{meme['id']}]]"
            response = LLMResponse(role="assistant", completion_text=raw_text)
            if name == "existing_chain":
                response.result_chain = MessageChain().message(raw_text)
            await plugin.response(event, response)
            assert "[[meme:" not in response.completion_text
            event.result = (
                MessageEventResult(chain=response.result_chain.chain)
                if response.result_chain
                else MessageEventResult().message(response.completion_text)
            )
            await plugin.decorate(event)
            if event.result.chain:
                assert not sender.messages
                await event.send(event.result)
                await plugin.sent(event)
            expected_sticker = (
                not disabled and (not cooldown or explicit) and not sender.fail_sticker
            )
            assert len(sender.messages) == int(bool(caption)) + int(expected_sticker)
            for index, (kind, payload) in enumerate(sender.messages):
                assert kind == ("group" if "group" in source else "private")
                assert payload["self_id"] == 12345
                (segment,) = payload["message"]
                if caption and index == 0:
                    assert segment == {"type": "text", "data": {"text": caption.rstrip("。")}}
                else:
                    assert segment["type"] == "image"
                    assert segment["data"]["sub_type"] == 1
                    assert segment["data"]["summary"] == "[表情]"
                    assert (
                        base64.b64decode(segment["data"]["file"].removeprefix("base64://"))
                        == original
                    )
            after_sent = app.database.rows("SELECT last_sent FROM memes")[0]["last_sent"]
            assert (after_sent > before_sent) if expected_sticker else (after_sent == before_sent)
            count = len(sender.messages)
            await plugin.sent(event)
            await plugin.decorate(event)
            assert len(sender.messages) == count
            assert not event.result.chain
            if sender.fail_sticker:
                assert app.state.get("last_error")["stage"] == "meme_send"
            print(
                "STICKER " + json.dumps({"case": name, "messages": count, "ok": True}), flush=True
            )
            await app.close()
    print("STICKER all_passed external_sends=0", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
