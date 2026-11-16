"""Resolve durable, scoped image references only at the host request boundary."""
import base64
import copy
import io
import json
import logging

from ..services.attachments import Attachments

logger = logging.getLogger(__name__)
PREFIX = "attachment:"


def image_ref(part):
    if part.get("type") == "image_url":
        url = part.get("image_url", {}).get("url", "")
        if url.startswith(PREFIX):
            return url
    return ""


def receipt_refs(result):
    for part in getattr(result, "content", []) or []:
        text = getattr(part, "text", "")
        if text.startswith("{"):
            try:
                value = json.loads(text)
            except ValueError:
                continue
            if isinstance(value, dict) and isinstance(value.get("image_refs"), list):
                return [r for r in value["image_refs"] if isinstance(r, str) and r.startswith(PREFIX)]
    return []


class ChatImages:
    def __init__(self, attachments, session):
        self.attachments, self.session = attachments, session
        self.encoded = {}
        self.unavailable = {}
        self.counts = {}

    def message_refs(self, message_id):
        return [PREFIX + a["id"] for a in self.attachments.message_images(message_id, self.session)]

    async def bytes(self, reference):
        identity = reference.removeprefix(PREFIX)
        row = self.attachments.authorize(identity, self.session)
        if row["status"] in {"pending", "saving"}:
            raise ValueError("图片保存中")
        if row["status"] == "failed":
            raise ValueError("图片下载失败，原记录已保留")
        _, path = self.attachments.file(identity)
        data = await self.attachments.app.scheduling.compute(path.read_bytes)
        from PIL import Image
        with Image.open(io.BytesIO(data)) as picture:
            mime = Image.MIME.get(picture.format)
        if not mime:
            raise ValueError("图片格式不受支持")
        return data, mime

    async def resolve(self, messages, *, modalities=None):
        values = [m.model_dump(exclude_none=True) if hasattr(m, "model_dump") else copy.deepcopy(m)
                  for m in messages]
        refs = [image_ref(p) for m in values if isinstance(m.get("content"), list)
                for p in m["content"] if image_ref(p)]
        authorized = []
        for ref in dict.fromkeys(refs):
            try:
                self.attachments.authorize(ref[len(PREFIX):], self.session)
                authorized.append(ref[len(PREFIX):])
            except ValueError:
                pass
        await self.attachments.wait(authorized)
        cache = {}
        counts = {"window": len(refs), "loaded": 0, "pending": 0, "failed": 0, "unsupported": 0}
        for message in values:
            if not isinstance(message.get("content"), list):
                continue
            parts = []
            for part in message["content"]:
                ref = image_ref(part)
                if not ref:
                    parts.append(part)
                    continue
                try:
                    self.attachments.authorize(ref[len(PREFIX):], self.session)
                    if modalities is not None and "image" not in modalities:
                        counts["unsupported"] += 1
                        text = f"[当前模型配置不支持图片；原图引用 {ref}]"
                        self.unavailable[text] = ref
                        parts.append({"type": "text", "text": text})
                        continue
                    if ref not in cache:
                        data, mime = await self.bytes(ref)
                        cache[ref] = f"data:{mime};base64," + base64.b64encode(data).decode()
                        self.encoded.setdefault(cache[ref], set()).add(ref)
                    parts.append({"type": "image_url", "image_url": {"url": cache[ref], "id": ref}})
                    counts["loaded"] += 1
                except (ValueError, OSError) as exc:
                    state = str(exc) if isinstance(exc, ValueError) else "图片文件缺失或不可读"
                    counts["pending" if state == "图片保存中" else "failed"] += 1
                    text = f"[{state}；原图引用 {ref}]"
                    self.unavailable[text] = ref
                    parts.append({"type": "text", "text": text})
            message["content"] = parts
        self.counts = counts
        logger.info("Shrink images window=%d loaded=%d pending=%d failed=%d unsupported=%d",
                    *counts.values())
        return values

    def compact(self, messages):
        """Replace images delivered to the runner with their durable original references."""
        values = [m.model_dump(exclude_none=True) if hasattr(m, "model_dump") else copy.deepcopy(m)
                  for m in messages]
        pending = []
        for message in values:
            if message.get("role") == "tool" and isinstance(message.get("content"), str):
                try:
                    metadata, _ = json.JSONDecoder().raw_decode(message["content"])
                    if isinstance(metadata, dict) and isinstance(metadata.get("image_refs"), list):
                        pending.extend(metadata["image_refs"])
                except ValueError:
                    pass
            if not isinstance(message.get("content"), list):
                continue
            for index, part in enumerate(message["content"]):
                if part.get("type") == "text" and part.get("text") in self.unavailable:
                    message["content"][index] = {
                        "type": "image_url", "image_url": {"url": self.unavailable[part["text"]]}}
                    continue
                if part.get("type") != "image_url":
                    continue
                url = part.get("image_url", {}).get("url", "")
                candidates = self.encoded.get(url, set())
                reference = part["image_url"].get("id")
                if reference not in candidates:
                    reference = next((ref for ref in pending if isinstance(ref, str) and ref in candidates), None)
                    if reference:
                        pending.remove(reference)
                    elif len(candidates) == 1:
                        reference = next(iter(candidates))
                if reference:
                    part["image_url"] = {"url": reference}
        return values

    async def read(self, message_id, image_index, *, modalities=None):
        from mcp.types import CallToolResult, ImageContent, TextContent
        rows = self.attachments.message_images(message_id, self.session)
        if isinstance(image_index, bool) or not isinstance(image_index, int) or not 0 <= image_index < len(rows):
            raise ValueError("图片索引不存在（从0开始）")
        ref = PREFIX + rows[image_index]["id"]
        resolved = await self.resolve([{"role": "user", "content": [Attachments.part(rows[image_index]["id"])]}],
                                      modalities=modalities)
        part = resolved[0]["content"][0]
        if part["type"] != "image_url":
            return CallToolResult(isError=True, content=[TextContent(type="text", text=part["text"])])
        data = part["image_url"]["url"]
        metadata = {"message_id": message_id, "image_index": image_index, "image_count": len(rows),
                    "next_index": image_index + 1 if image_index + 1 < len(rows) else None,
                    "image_refs": [ref]}
        logger.info("Shrink history image_read=1")
        return CallToolResult(content=[
            TextContent(type="text", text=json.dumps(metadata, ensure_ascii=False)),
            ImageContent(type="image", data=data.split(",", 1)[1], mimeType=data[5:data.index(";")])])


def selected_refs(app, event):
    """Only current input, explicit quote and images actually read in this turn."""
    from ..services.task_context import CURRENT_TASK
    if CURRENT_TASK.get():
        return []
    turn = event.get_extra("shrink_turn")
    refs = list(event.get_extra("shrink_read_images") or [])
    if not turn or not getattr(app, "attachments", None):
        return refs
    ids = [turn.id]
    if getattr(turn, "reply_to", ""):
        ids.extend(row["message_id"] for row in app.database.rows(
            "SELECT message_id FROM input_events WHERE session=? AND external_id=?",
            (event.unified_msg_origin, turn.reply_to)))
    for message_id in ids:
        try:
            refs.extend(PREFIX + row["id"] for row in app.attachments.message_images(
                message_id, event.unified_msg_origin))
        except ValueError:
            continue
    return list(dict.fromkeys(refs))

