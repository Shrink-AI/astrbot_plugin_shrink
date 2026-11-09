"""Run inside the AstrBot container: sticker/text pairing and in-flight merging through the real
adapter hooks, with in-memory events. Never sends a platform message."""
import asyncio
from types import SimpleNamespace

from astrbot.api.message_components import Plain
from astrbot.core.message.message_event_result import MessageChain
from data.plugins.astrbot_plugin_shrink.shrink.adapters.conversation import ConversationAdapter
from data.plugins.astrbot_plugin_shrink.shrink.services.expression import ExpressionService


def event_for(extras, text):
    result = MessageChain([Plain(text)])
    event = SimpleNamespace(
        get_extra=lambda name, default=None: extras.get(name, default),
        set_extra=lambda name, value: extras.__setitem__(name, value),
        get_result=lambda: result,
        message_obj=SimpleNamespace(message_id=""),
    )
    return event, result


def adapter():
    settings = SimpleNamespace(split_probability=0, stale_seconds=60)
    expression = ExpressionService(SimpleNamespace(settings=settings), None, None, None, None)
    app = SimpleNamespace(
        expression=expression, state=SimpleNamespace(settings=settings),
        messages=SimpleNamespace(recent_bot=lambda *a: [], count_since=lambda *a: 0),
    )
    conversation = ConversationAdapter(app, __name__)
    conversation.record_episode = lambda *a: None
    conversation.quotes = lambda *a: False
    return conversation


async def main():
    conversation = adapter()
    turn = SimpleNamespace(text="还没有", reason="engaged", source="test", created=0, user_id="u")

    # A short reaction with a sticker: the sticker goes alone.
    extras = {"shrink_reply": True, "shrink_turn": turn, "shrink_form": "tiny", "shrink_tools": []}
    event, result = event_for(extras, "好嘞[[meme:7]]")
    response = SimpleNamespace(tools_call_name=[], role="assistant", completion_text="好嘞[[meme:7]]",
                               result_chain=result, reasoning_content="")
    await conversation.response(event, response)
    assert extras["shrink_selected_meme"] == 7 and response.completion_text == ""

    # A question keeps its words; the sticker is dropped.
    extras = {"shrink_reply": True, "shrink_turn": turn, "shrink_form": "short", "shrink_tools": []}
    event, result = event_for(extras, "哪家的[[meme:7]]")
    response = SimpleNamespace(tools_call_name=[], role="assistant", completion_text="哪家的[[meme:7]]",
                               result_chain=result, reasoning_content="")
    await conversation.response(event, response)
    assert extras["shrink_selected_meme"] is None and response.completion_text == "哪家的"

    # The same sender spoke again before the first reply went out: only the newer one is sent.
    key = ("test", "u")
    first = conversation.debouncer.begin(key, "华莱士", "engaged")
    second = conversation.debouncer.begin(key, "全国通用", "engaged")
    old = {"shrink_reply": True, "shrink_turn": turn, "shrink_final_response": True,
           "shrink_inflight": (key, first), "shrink_tools": []}
    event, result = event_for(old, "华莱士正价随便凑个十块")
    await conversation.decorate(event)
    assert result.chain == []
    new = {"shrink_reply": True, "shrink_turn": turn, "shrink_final_response": True,
           "shrink_inflight": (key, second), "shrink_tools": [], "shrink_form": "short"}
    event, result = event_for(new, "满十块送只烤鸡")
    await conversation.decorate(event)
    assert result.chain and result.chain[0].text == "满十块送只烤鸡"
    assert second.text == "华莱士\n全国通用"
    print("PASS sticker pairing and in-flight merge through real hooks; nothing sent")


if __name__ == "__main__":
    asyncio.run(main())
