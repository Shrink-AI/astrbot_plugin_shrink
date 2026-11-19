"""Capture real host requests, with models and platform delivery replaced by local doubles."""
import copy
import base64
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image as PILImage

pytest.importorskip("astrbot.core.provider.manager")

from astrbot.api.message_components import At, Image, Plain, Reply  # noqa: E402
from astrbot.core.agent.hooks import BaseAgentRunHooks  # noqa: E402
from astrbot.core.agent.run_context import ContextWrapper  # noqa: E402
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner  # noqa: E402
from astrbot.core.agent.tool import ToolSet  # noqa: E402
from astrbot.core.agent.tool_executor import BaseFunctionToolExecutor  # noqa: E402
from astrbot.core.provider.entities import LLMResponse, ProviderRequest  # noqa: E402

from shrink.adapters.chat_images import (  # noqa: E402
    ChatImages, IMAGE_ENVELOPE_BYTES, RECENT_IMAGE_SECONDS, receipt_refs, selected_refs,
)
from shrink.application import Application  # noqa: E402
from shrink.persistence.execution_context import ExecutionContext  # noqa: E402
from shrink.persistence.task_inputs import TaskInputsRepository  # noqa: E402
from tests.host.model_support import host_models  # noqa: E402
from tests.host.test_real_turns import Event, World  # noqa: E402
from tests.host.test_real_runner import Model, drive, tool  # noqa: E402
from tests.test_multimodal_history import SESSION, picture, png  # noqa: E402
from tests.test_continuous_history import receive  # noqa: E402
from tests.host.test_task_inputs import RecordingModel  # noqa: E402
from shrink.adapters.task_runner import task_runner  # noqa: E402


def image_count(messages):
    return sum(p.get("type") == "image_url" for m in messages
               if isinstance(m.get("content"), list) for p in m["content"])


async def test_oversized_history_image_does_not_block_spontaneous_reply(tmp_path, monkeypatch):
    world = World(tmp_path / "data")
    try:
        event = Event(world, "今天聊点新鲜的")
        event.is_at_or_wake_command = False
        world.app.state.put("settings", {**world.app.state.settings.model_dump(),
                                         "decision_enabled": False, "spontaneous_probability": 1})
        await picture(world.app, session=event.unified_msg_origin, colors=("red",))
        monkeypatch.setattr(PILImage, "MAX_IMAGE_PIXELS", 90)

        await world.turn(event)

        assert not world.hook_errors
        assert world.calls and world.sent
        assert "图片像素超过安全上限" in str(world.requests[0]["contexts"])
        assert image_count(world.requests[0]["contexts"]) == 0
        trace = world.traces()[event.message_obj.message_id]
        assert (trace["reason"], trace["outcome"]) == ("spontaneous", "sent")
    finally:
        await world.app.close()


@pytest.mark.parametrize("protocol", [
    "openai_chat_completion", "openai_responses", "anthropic_chat_completion",
])
async def test_received_images_reach_real_protocol_after_followup_and_restart(tmp_path, monkeypatch, protocol):
    host = await host_models(tmp_path, monkeypatch)
    app = Application(tmp_path / "chat", AsyncMock(), AsyncMock(), AsyncMock())
    try:
        turn, _ = await picture(app)
        receive(app, "interruption", "其他人插话", sender="200")
        receive(app, "followup", "图里写了什么")
        root = app.database.root
        await app.close()
        app = Application(root, AsyncMock(), AsyncMock(), AsyncMock())
        entry = copy.deepcopy(host.config["provider"][0])
        entry["type"] = protocol
        await host.service.update_provider("first", entry)
        provider = host.providers.inst_map["first"]
        captured = []
        async def query(payloads, tools, **kwargs):
            captured.append(copy.deepcopy(payloads))
            return LLMResponse(role="assistant", completion_text="fixture")
        monkeypatch.setattr(provider, "_query", query)
        contexts = await app.history.contexts(SESSION, 10000)
        resolved = await ChatImages(app.attachments, SESSION).resolve(contexts)
        assert image_count(resolved) == 2
        await provider.text_chat(prompt="继续", contexts=resolved)
        serialized = json.dumps(captured[-1], ensure_ascii=False)
        expected = {"openai_chat_completion": '"image_url"', "openai_responses": '"input_image"',
                    "anthropic_chat_completion": '"image"'}[protocol]
        assert expected in serialized and "其他人插话" in serialized and "图里写了什么" in serialized
        assert "attachment:" not in serialized
        red, blue = (base64.b64encode(png(color)).decode() for color in ("red", "blue"))
        assert serialized.count(red) == serialized.count(blue) == 1
        assert serialized.index(red) < serialized.index(blue)
        assert "qq:100" in serialized
        assert all(app.attachments.get(a["id"])["status"] == "saved"
                   for a in app.attachments.for_message(turn.id))
    finally:
        await app.close()
        await host.providers.terminate()


async def test_foreground_current_image_once_then_visible_next_turn(tmp_path):
    world = World(tmp_path / "chat")
    try:
        path = tmp_path / "original.png"
        path.write_bytes(png())
        event = Event(world, "看图")
        event.message_obj.message = [Plain("看图"), Image.fromFileSystem(str(path))]
        enrich = world.adapter.enrich
        async def with_host_image(current, request):
            if current is event:
                request.image_urls = [str(path)]
            await enrich(current, request)
        world.adapter.enrich = with_host_image
        await world.turn(event)
        assert world.hook_errors == []
        assert image_count(world.requests[0]["contexts"]) == 1
        path.unlink()
        await world.turn(Event(world, "刚才那张呢", sender="200"))
        assert world.hook_errors == []
        assert image_count(world.requests[-1]["contexts"]) == 1
        assert "data:image/png;base64," in json.dumps(world.requests[-1])
    finally:
        await world.app.close()


async def test_foreground_keeps_recent_images_and_reads_old_image_when_quoted(tmp_path):
    world = World(tmp_path / "chat")
    try:
        event = Event(world, "刚刚那张图呢")
        session = event.unified_msg_origin
        old, _ = await picture(world.app, "old-picture", session=session, colors=("red",))
        await picture(world.app, "recent-picture", session=session, colors=("blue",))
        with world.app.database.transaction() as con:
            con.execute("UPDATE messages SET created=? WHERE id=?",
                        (time.time() - RECENT_IMAGE_SECONDS - 60, old.id))
        await world.turn(event)
        contexts = world.requests[-1]["contexts"]
        assert image_count(contexts) == 1 and "较早图片未自动加载" in str(contexts)

        quoted = Event(world, "我问的是这张旧图")
        quoted.message_obj.message.insert(0, Reply(id="old-picture", sender_id="100"))
        quoted.message_obj.message.append(At(qq=str(quoted.get_self_id())))
        await world.turn(quoted)
        contexts = world.requests[-1]["contexts"]
        assert image_count(contexts) == 2 and "较早图片未自动加载" not in str(contexts)
        assert not world.hook_errors
    finally:
        await world.app.close()


async def test_explicit_old_image_reads_share_the_existing_byte_budget(tmp_path):
    app = Application(tmp_path / "chat", AsyncMock(), AsyncMock(), AsyncMock())
    try:
        turn, _ = await picture(app, colors=("red",))
        with app.database.transaction() as con:
            con.execute("UPDATE messages SET created=1 WHERE id=?", (turn.id,))
        cost = 4 * ((len(png()) + 2) // 3) + IMAGE_ENVELOPE_BYTES
        images = ChatImages(app.attachments, SESSION, byte_budget=cost)
        first, second = await images.read(turn.id, 0), await images.read(turn.id, 0)
        assert not first.isError and receipt_refs(first)
        assert second.isError and "超过本轮加载预算" in second.content[0].text
    finally:
        await app.close()


async def test_history_tool_image_reaches_next_real_runner_call_and_durable_receipt(tmp_path):
    app = Application(tmp_path / "chat", AsyncMock(), AsyncMock(), AsyncMock())
    try:
        turn, _ = await picture(app)
        images = ChatImages(app.attachments, SESSION)
        model = Model(("history",), "done")
        class Tools(BaseFunctionToolExecutor):
            @classmethod
            async def execute(cls, tool, run_context, **arguments):
                result = await images.read(turn.id, 1)
                app.history.tool_end(SESSION, "call", result.content[0].text, image_refs=receipt_refs(result))
                yield result
        app.history.tool_start(SESSION, "call", "history", {"message_id": turn.id, "image_index": 1})
        runner = ToolLoopAgentRunner()
        await runner.reset(provider=model, request=ProviderRequest(prompt="读取旧图", func_tool=ToolSet([tool("history")])),
                           run_context=ContextWrapper(context=SimpleNamespace()),
                           tool_executor=Tools, agent_hooks=BaseAgentRunHooks(), streaming=False)
        await drive(runner)
        contexts = [m.model_dump(exclude_none=True) if hasattr(m, "model_dump") else m
                    for m in model.seen[-1]["contexts"]]
        assert image_count(contexts) == 1
        assert "data:image/png;base64," in json.dumps(contexts)
        saved = images.compact(contexts)
        TaskInputsRepository(app.database)
        execution = ExecutionContext(app.database)
        execution.save("task", saved)
        stored = execution.read("task")
        assert "data:image/" not in str(stored) and "attachment:" in str(stored)
        restarted_images = ChatImages(app.attachments, SESSION)
        restored = await restarted_images.resolve(stored)
        assert image_count(restored) == 1 and "data:image/" in str(restored)
        replay = await app.history.contexts(SESSION, 10000)
        assert "attachment:" in str(replay) and "data:image/" not in str(replay)
        with pytest.raises(ValueError, match="当前会话"):
            await ChatImages(app.attachments, "other:GroupMessage:1").read(turn.id, 0)
        with pytest.raises(ValueError, match="索引"):
            await images.read(turn.id, 2)
    finally:
        await app.close()


async def test_dispatch_refs_only_current_quote_and_read_images(tmp_path):
    app = Application(tmp_path / "chat", AsyncMock(), AsyncMock(), AsyncMock())
    try:
        old, _ = await picture(app, "unrelated")
        current, _ = await picture(app, "current")
        quoted, _ = await picture(app, "quoted")
        current.reply_to = "quoted"
        event = SimpleNamespace(unified_msg_origin=SESSION, get_extra=lambda key: {
            "shrink_turn": current, "shrink_read_images": []}.get(key))
        refs = selected_refs(app, event)
        assert len(refs) == 4
        assert not set(refs) & {"attachment:" + a["id"] for a in app.attachments.for_message(old.id)}
    finally:
        await app.close()


async def test_task_runner_checkpoints_and_rehydrates_original_images(tmp_path):
    app = Application(tmp_path / "chat", AsyncMock(), AsyncMock(), AsyncMock())
    try:
        original, _ = await picture(app, colors=("red",))
        with app.database.transaction() as con:
            con.execute("UPDATE messages SET created=1 WHERE id=?", (original.id,))
        TaskInputsRepository(app.database)
        execution = ExecutionContext(app.database)
        async def save(task_id, messages, pending):
            execution.save(task_id, messages)
        inputs = SimpleNamespace(deliver=AsyncMock(return_value=[]), processed=save,
                                 pending=lambda task_id: [], tasks=SimpleNamespace(events=lambda task_id: []))
        images = ChatImages(app.attachments, SESSION)
        contexts = await app.history.contexts(SESSION, 10000)
        for restored in (False, True):
            runner = task_runner(inputs, "task")
            runner.images = images if not restored else ChatImages(app.attachments, SESSION)
            contexts = execution.read("task") if restored else contexts
            model = RecordingModel("done")
            await runner.reset(provider=model, request=ProviderRequest(prompt="继续", contexts=contexts),
                               run_context=ContextWrapper(context=SimpleNamespace()),
                               tool_executor=SimpleNamespace(), agent_hooks=BaseAgentRunHooks(), streaming=False)
            await drive(runner)
            assert image_count(model.seen[-1]["contexts"]) == 1
            assert "data:image/" in str(model.seen[-1])
            saved = execution.read("task")
            assert "data:image/" not in str(saved) and "attachment:" in str(saved)
        assert app.attachments.for_message(original.id)[0]["status"] == "saved"
    finally:
        await app.close()
