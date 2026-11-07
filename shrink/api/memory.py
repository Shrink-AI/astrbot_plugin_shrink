import time

from fastapi import APIRouter, HTTPException

from .schemas import BatchAction, TextEdit, Verdict


def router_for(app):
    router = APIRouter(prefix="/api/v1")
    repository = app.memory_repository

    @router.get("/memories")
    async def memories(
        q: str = "", user_id: str = "", source: str = "", kind: str = "", status: str = "",
        min_confidence: float = 0.0, run: str = "", offset: int = 0,
    ):
        return repository.search(q, user_id, source, offset, kind, status, min_confidence, run)

    @router.get("/memories/{memory_id}")
    async def memory(memory_id: int):
        row = repository.get(memory_id)
        if not row:
            raise HTTPException(404, "记忆不存在")
        row.pop("vector", None)
        return row

    @router.put("/memories/{memory_id}")
    async def edit_memory(memory_id: int, body: TextEdit):
        if not repository.update(memory_id, text=body.text, verified=1, verified_at=time.time(),
                                 confidence=1.0, status="active"):
            raise HTTPException(404, "记忆不存在")
        app.tasks.spawn(app.memory.index_memory(memory_id))
        app.review.close_target("%", memory_id)
        return {"ok": True}

    @router.post("/memories/{memory_id}/verify")
    async def verify_memory(memory_id: int, body: Verdict):
        if body.verdict == "skip":
            return {"ok": True}
        right = body.verdict == "correct"
        if not repository.update(
            memory_id, status="active" if right else "rejected", verified=1 if right else -1,
            confidence=1.0 if right else 0.0, verified_at=time.time(),
        ):
            raise HTTPException(404, "记忆不存在")
        app.review.close_target("%", memory_id)
        return {"ok": True}

    @router.delete("/memories/{memory_id}")
    async def delete_memory(memory_id: int):
        if not repository.delete_memory(memory_id):
            raise HTTPException(404, "记忆不存在")
        return {"ok": True}

    @router.post("/memories/batch")
    async def batch(body: BatchAction):
        if body.action not in ("confirm", "reject", "delete"):
            raise HTTPException(400, "无效的操作")
        done = 0
        for memory_id in body.ids:
            if body.action == "delete":
                done += repository.delete_memory(memory_id)
            else:
                right = body.action == "confirm"
                done += repository.update(
                    memory_id, status="active" if right else "rejected",
                    verified=1 if right else -1, confidence=1.0 if right else 0.0,
                    verified_at=time.time(),
                )
            app.review.close_target("%", memory_id)
        return {"ok": True, "count": done}

    return router
