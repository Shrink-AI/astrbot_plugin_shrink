from fastapi import APIRouter
from ..config import Settings


def router_for(state, status_repository, learning, providers, digest=None, consolidation=None,
               teach=None):
    router = APIRouter(prefix="/api/v1")

    @router.get("/status")
    async def status():
        if teach:
            teach.review_items(limit=0)
        return {
            **status_repository.status(),
            "learning_running": learning.learning_lock.locked(),
            "digest_running": bool(digest and digest.lock.locked()),
            "consolidation_running": bool(consolidation and consolidation.lock.locked()),
            "last_collection": state.get("last_collection"),
        }

    @router.get("/settings")
    async def settings():
        return {"settings": state.settings.model_dump(), "providers": providers()}

    @router.put("/settings")
    async def save_settings(body: Settings):
        available = providers()
        if body.model_provider not in available["chat"]:
            raise ValueError("对话模型不存在")
        if body.embedding_provider not in available["embedding"]:
            raise ValueError("向量模型不存在")
        if body.learning_batch_size < body.learning_min_messages:
            raise ValueError("每批样本数不能小于最低学习样本数")
        if body.persona_id != state.settings.persona_id:
            raise ValueError("人格 ID 固定为已接管的人格")
        state.put("settings", body.model_dump())
        return {"ok": True}

    return router
