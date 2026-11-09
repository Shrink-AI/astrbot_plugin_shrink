import asyncio
import hashlib
import io
import time
import warnings

from PIL import Image, UnidentifiedImageError
from ..persistence.database import StateRepository
from ..persistence.media import MemeRepository
from ..ports import ChatModel, ErrorSink

from ..prompts.knowledge import MEMES
from ..prompts import text as prompt_text
from .model_output import parse_json

MAX_IMAGE_BYTES = 10 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 16_000_000


def inspect_image(data):
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("图片大小需在 10 MB 以内")
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            with Image.open(io.BytesIO(data)) as img:
                fmt = img.format
                if fmt not in {"PNG", "JPEG", "WEBP", "GIF"}:
                    raise ValueError("仅支持 PNG、JPEG、WebP 和 GIF")
                if img.width * img.height > Image.MAX_IMAGE_PIXELS:
                    raise ValueError("图片尺寸过大")
                frame = img.convert("L").resize((9, 8))
                pixels = list(frame.tobytes())
                bits = sum(
                    (pixels[y * 9 + x] > pixels[y * 9 + x + 1]) << (y * 8 + x)
                    for y in range(8)
                    for x in range(8)
                )
            with Image.open(io.BytesIO(data)) as verified:
                verified.verify()
        except (
            UnidentifiedImageError,
            OSError,
            Image.DecompressionBombWarning,
            Image.DecompressionBombError,
        ) as exc:
            raise ValueError("图片损坏或尺寸过大") from exc
    return {
        "digest": hashlib.sha256(data).hexdigest(),
        "phash": f"{bits:016x}",
        "ext": {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif"}[fmt],
    }


class MemeService:
    def __init__(
        self, repository: MemeRepository, state: StateRepository, model: ChatModel, error: ErrorSink
    ):
        self.repository, self.state, self.model, self.error = repository, state, model, error
        self.collect_lock = asyncio.Semaphore(2)
        self.pending = 0
        self.meme_sent = {}

    async def add(self, data, title="", tags="", source="upload", automatic=False):
        info = await asyncio.to_thread(inspect_image, data)
        existing = self.repository.by_digest(info["digest"])
        if existing:
            return existing, False
        # Conservative perceptual matching; manual upload permits intentionally similar art.
        if automatic and info["ext"] != "gif":
            for row in self.repository.all():
                distance = (int(info["phash"], 16) ^ int(row["phash"], 16)).bit_count()
                if distance <= 2 and row["file"].split(".")[-1] != "gif":
                    return row, False
        filename = info["digest"] + "." + info["ext"]
        (self.repository.root / filename).write_bytes(data)
        return self.repository.add(info, filename, title, tags, source), True

    async def collect(self, data, source):
        async with self.collect_lock:
            try:
                info = await asyncio.to_thread(inspect_image, data)
                if self.repository.by_digest(info["digest"]):
                    return
                import base64

                uri = "base64://" + base64.b64encode(data).decode()
                result = parse_json(
                    await asyncio.wait_for(
                        self.model(prompt_text("memes.task"), prompt_text(MEMES), [uri]),
                        45,
                    )
                )
                if result.get("collect") is True and self.state.settings.collect_enabled:
                    await self.add(
                        data,
                        str(result.get("title", "表情")),
                        ",".join(str(t) for t in result.get("tags", [])[:10]),
                        source,
                        automatic=True,
                    )
                    self.state.put("last_collection", time.time())
            except Exception as exc:
                self.error("meme_collection", exc)
            finally:
                self.pending -= 1

    def choices(self, source, explicit=False):
        cfg = self.state.settings
        if not cfg.meme_enabled:
            return []
        now = time.time()
        if not explicit and now - self.meme_sent.get(source, 0) < cfg.meme_cooldown:
            return []
        return self.repository.choices(explicit, now - cfg.meme_repeat_cooldown)

    def select(self, meme_id, source, allowed_ids, explicit=False, searched=False):
        """searched: the model looked this sticker up by keyword, so the offer list is moot."""
        if not searched:
            if meme_id not in allowed_ids:
                return None
            if meme_id not in {r["id"] for r in self.choices(source, explicit)}:
                return None
        elif not self.state.settings.meme_enabled:
            return None
        row = self.repository.get(meme_id)
        if not row or not row["enabled"]:
            return None
        path = self.repository.root / row["file"]
        return path if path.is_file() else None

    def sent(self, meme_id, source):
        now = time.time()
        self.meme_sent[source] = now
        self.repository.mark_sent(meme_id, now)

    def delete(self, meme_id):
        row = self.repository.get(meme_id)
        if not row:
            return False
        self.repository.delete(meme_id)
        (self.repository.root / row["file"]).unlink(missing_ok=True)
        return True
