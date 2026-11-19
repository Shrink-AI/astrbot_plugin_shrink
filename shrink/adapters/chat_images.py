"""Resolve durable, scoped image references only at the host request boundary."""
import asyncio
import base64
import copy
import io
import json
import logging
import os
import time
import warnings

from ..services.attachments import Attachments

logger = logging.getLogger(__name__)
PREFIX = "attachment:"
RECENT_IMAGE_SECONDS = 30 * 60
IMAGE_PAYLOAD_BYTES = 4 * 1024 * 1024
IMAGE_ENVELOPE_BYTES = 128
BUDGET_REFERENCE = "图片超过本轮加载预算"


def image_payload(path, budget):
    with path.open("rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        cost = 4 * ((size + 2) // 3) + IMAGE_ENVELOPE_BYTES
        if cost > budget:
            raise ImageBudgetExceeded
        data = stream.read(size + 1)
    if len(data) > size:
        raise ValueError("图片大小已变化，未加载")
    from PIL import Image
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as picture:
                mime = Image.MIME.get(picture.format)
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise ValueError("图片像素超过安全上限") from exc
    if not mime:
        raise ValueError("图片格式不受支持")
    return f"data:{mime};base64," + base64.b64encode(data).decode(), cost


class ImageBudgetExceeded(ValueError):
    def __str__(self):
        return BUDGET_REFERENCE


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
    def __init__(self, attachments, session, *, byte_budget=IMAGE_PAYLOAD_BYTES):
        self.attachments, self.session = attachments, session
        self.encoded = {}
        self.unavailable = {}
        self.counts = {}
        self.byte_budget = byte_budget
        self.used_bytes = 0
        self.read_lock = asyncio.Lock()

    def message_refs(self, message_id):
        return [PREFIX + a["id"] for a in self.attachments.message_images(message_id, self.session)]

    async def payload(self, reference, budget):
        identity = reference.removeprefix(PREFIX)
        row = self.attachments.authorize(identity, self.session)
        if row["status"] in {"pending", "saving"}:
            raise ValueError("图片保存中")
        if row["status"] == "failed":
            raise ValueError("图片下载失败，原记录已保留")
        _, path = self.attachments.file(identity)
        return await self.attachments.app.scheduling.compute(image_payload, path, budget)

    async def resolve(self, messages, *, modalities=None, priority_refs=(),
                      max_age=RECENT_IMAGE_SECONDS, append=False):
        values = [m.model_dump(exclude_none=True) if hasattr(m, "model_dump") else copy.deepcopy(m)
                  for m in messages]
        if not append:
            self.used_bytes = 0
        positions = [(index, part_index, ref) for index, message in enumerate(values)
                     if isinstance(message.get("content"), list)
                     for part_index, part in enumerate(message["content"])
                     if (ref := image_ref(part))]
        counts = dict(window=len(positions), loaded=0, pending=0, failed=0, unsupported=0,
                      old=0, over_budget=0)
        priorities = {ref: index for index, ref in enumerate(dict.fromkeys(priority_refs))}
        cutoff = time.time() - max_age if max_age is not None else None
        info = {}
        candidates = []
        cache = {}

        def placeholder(index, part_index, ref, reason, category):
            counts[category] += 1
            text = f"[{reason}；原图引用 {ref}]"
            self.unavailable[text] = ref
            values[index]["content"][part_index] = {"type": "text", "text": text}

        for index, part_index, ref in positions:
            if ref not in info:
                try:
                    row = self.attachments.authorize(ref[len(PREFIX):], self.session)
                except ValueError as exc:
                    info[ref] = (None, str(exc), "failed")
                else:
                    if modalities is not None and "image" not in modalities:
                        info[ref] = (None, "当前模型配置不支持图片", "unsupported")
                    elif cutoff is not None and row["message_created"] < cutoff and ref not in priorities:
                        info[ref] = (None, "较早图片未自动加载", "old")
                    else:
                        info[ref] = (row, "", "")
            row, reason, category = info[ref]
            if row is None:
                placeholder(index, part_index, ref, reason, category)
                continue
            candidates.append((priorities.get(ref, len(priorities)), -row["message_created"],
                               -index, part_index, ref))
        if candidates:
            await self.attachments.wait(ref[len(PREFIX):] for *_, ref in candidates)
        for _, _, negative_index, part_index, ref in sorted(candidates):
            index = -negative_index
            try:
                remaining = self.byte_budget - self.used_bytes
                if remaining <= IMAGE_ENVELOPE_BYTES:
                    raise ImageBudgetExceeded
                if ref not in cache:
                    cache[ref] = await self.payload(ref, remaining)
                url, cost = cache[ref]
                if cost > remaining:
                    raise ImageBudgetExceeded
                self.used_bytes += cost
                self.encoded.setdefault(url, set()).add(ref)
                values[index]["content"][part_index] = {"type": "image_url", "image_url": {"url": url, "id": ref}}
                counts["loaded"] += 1
            except ImageBudgetExceeded as exc:
                placeholder(index, part_index, ref, str(exc), "over_budget")
            except (ValueError, OSError) as exc:
                reason = str(exc) if isinstance(exc, ValueError) else "图片文件缺失或不可读"
                placeholder(index, part_index, ref, reason, "pending" if reason == "图片保存中" else "failed")
        self.counts = counts
        logger.info("Shrink images window=%d loaded=%d pending=%d failed=%d unsupported=%d "
                    "old=%d over_budget=%d payload_bytes=%d budget_bytes=%d",
                    *counts.values(), self.used_bytes, self.byte_budget)
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
        async with self.read_lock:
            resolved = await self.resolve([{"role": "user", "content": [Attachments.part(rows[image_index]["id"])]}],
                                          modalities=modalities, max_age=None, append=True)
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

