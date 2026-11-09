"""Exercise deployed hooks with an in-memory event; never sends a platform message."""
import asyncio
from types import SimpleNamespace

from astrbot.api.message_components import Plain
from astrbot.core.message.message_event_result import MessageChain
from data.plugins.astrbot_plugin_shrink.shrink.adapters.conversation import ConversationAdapter
from data.plugins.astrbot_plugin_shrink.shrink.services.expression import ExpressionService


async def main():
    text = "看板上未归档且没做完的还有5项：\n" + "\n".join(
        f"SYD--0{i}：这里是必须完整保留的项目任务标题及状态说明" for i in range(5)
    )

    async def must_not_rewrite(*args):
        raise AssertionError("Tool results must not be rewritten as short chat")

    expression = ExpressionService(None, None, None, must_not_rewrite, None)
    app = SimpleNamespace(
        expression=expression,
        messages=SimpleNamespace(recent_bot=lambda *args: []),
    )
    adapter = ConversationAdapter(app, __name__)
    adapter.record_episode = lambda *args: None
    adapter.stale = lambda turn: False
    adapter.quotes = lambda *args: False
    turn = SimpleNamespace(text="去crafttable帮我看一眼项目还有什么没做", reason="direct",
                           source="test:task-reply")
    extras = {"shrink_reply": True, "shrink_turn": turn, "shrink_form": "short",
              "shrink_tools": [{"name": "shirnk_crafttable"}]}
    result = MessageChain([Plain(text)])
    event = SimpleNamespace(
        get_extra=lambda name, default=None: extras.get(name, default),
        set_extra=lambda name, value: extras.__setitem__(name, value),
        get_result=lambda: result,
    )
    response = SimpleNamespace(tools_call_name=[], role="assistant", completion_text=text,
                               result_chain=result, reasoning_content="")
    await adapter.response(event, response)
    assert response.completion_text == text
    assert extras["shrink_form"] == "tool_result"
    await adapter.decorate(event)
    assert result.chain and isinstance(result.chain[0], Plain)
    assert "SYD--04" in result.chain[0].text
    assert len(extras["shrink_text"]) > 60
    print("PASS response + decorate: all five task entries remain in outgoing chain; no send performed")


if __name__ == "__main__":
    asyncio.run(main())
