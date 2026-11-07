from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from ..services.memes import MAX_IMAGE_BYTES
from .schemas import BatchAction, MemeEdit


def router_for(memes):
    router = APIRouter(prefix="/api/v1")
    repository = memes.repository

    @router.get("/memes")
    async def meme_list(q: str = "", offset: int = 0, enabled: int | None = None):
        return repository.search(q, offset, enabled)

    @router.post("/memes/batch")
    async def meme_batch(body: BatchAction):
        if body.action in ("enable", "disable"):
            repository.set_enabled(body.ids, body.action == "enable")
        elif body.action == "delete":
            for meme_id in body.ids:
                memes.delete(meme_id)
        else:
            raise HTTPException(400, "无效的操作")
        return {"ok": True, "count": len(body.ids)}

    @router.put("/memes/{meme_id}/enabled")
    async def meme_enabled(meme_id: int, body: dict):
        if not repository.get(meme_id):
            raise HTTPException(404, "表情不存在")
        repository.set_enabled([meme_id], bool(body.get("enabled")))
        return {"ok": True}

    @router.post("/memes")
    async def upload(file: UploadFile = File(...), title: str = Form(""), tags: str = Form("")):
        data = await file.read(MAX_IMAGE_BYTES + 1)
        await file.close()
        row, created = await memes.add(data, title or "未命名表情", tags)
        return {"meme": row, "created": created}

    @router.get("/memes/{meme_id}/image")
    async def image(meme_id: int):
        row = repository.get(meme_id)
        if not row:
            raise HTTPException(404, "表情不存在")
        return FileResponse(repository.root / row["file"])

    @router.put("/memes/{meme_id}")
    async def edit_meme(meme_id: int, body: MemeEdit):
        if not repository.edit(meme_id, body.title, body.tags, body.enabled):
            raise HTTPException(404, "表情不存在")
        return {"ok": True}

    @router.delete("/memes/{meme_id}")
    async def delete_meme(meme_id: int):
        if not memes.delete(meme_id):
            raise HTTPException(404, "表情不存在")
        return {"ok": True}

    return router
