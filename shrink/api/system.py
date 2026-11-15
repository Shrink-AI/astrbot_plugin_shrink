from fastapi import APIRouter
from ..config import Settings


def router_for(state, status_repository, learning, providers, digest=None, consolidation=None,
               teach=None, model_settings=None):
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
    async def settings(profile: str = "default"):
        selected = model_settings.read(profile) if model_settings else {}
        return {"settings": {**state.settings.model_dump(), **selected}, "providers": providers(),
                "model_profiles": model_settings.profiles() if model_settings else [],
                "model_profile": profile, "model_status": state.get("model_status", {})}

    @router.put("/settings")
    async def save_settings(body: Settings, profile: str = "default"):
        available = providers()
        if body.model_provider and body.model_provider not in available["chat"]:
            raise ValueError("对话模型不存在")
        if body.embedding_provider not in available["embedding"]:
            raise ValueError("向量模型不存在")
        if body.learning_batch_size < body.learning_min_messages:
            raise ValueError("每批样本数不能小于最低学习样本数")
        if body.persona_id != state.settings.persona_id:
            raise ValueError("人格 ID 固定为已接管的人格")
        for name, label in (
            ("learning_provider", "学习模型"), ("summary_provider", "总结模型"),
            ("compression_provider", "压缩模型"), ("image_caption_provider", "图片描述模型"),
        ):
            identity = getattr(body, name)
            if identity and identity not in available["chat"]:
                raise ValueError(label + "不存在或未启用")
        if len(set(body.fallback_providers)) != len(body.fallback_providers):
            raise ValueError("备用模型不能重复")
        if any(identity not in available["chat"] for identity in body.fallback_providers):
            raise ValueError("备用模型不存在或未启用")
        if model_settings:
            await model_settings.save(body, profile)
        state.put("settings", body.model_dump())
        return {"ok": True}

    return router
