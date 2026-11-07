from fastapi import APIRouter, HTTPException
from ..config import FIXED_PERSONA
from .schemas import EnabledEdit, TextEdit


def router_for(persona_service, learning, tasks, state, expressions):
    router = APIRouter(prefix="/api/v1")
    personas = persona_service.repository
    runs = learning.repository

    @router.get("/persona")
    async def persona():
        return {
            "fixed": FIXED_PERSONA,
            "learned": personas.learned_persona(),
            "active": state.get("active_persona"),
            "versions": personas.versions(),
        }

    @router.put("/persona")
    async def edit_persona(body: TextEdit):
        return {"id": await persona_service.set_persona(body.text, [], "管理员编辑")}

    @router.post("/persona/{version_id}/rollback")
    async def rollback(version_id: int):
        return {"id": await persona_service.rollback(version_id)}

    @router.post("/learning")
    async def learn():
        if learning.learning_lock.locked():
            return {"status": "running"}
        tasks.spawn(learning.learn(force=True))
        return {"status": "queued"}

    @router.post("/learning/preview")
    async def preview_learning():
        if learning.learning_lock.locked():
            return {"status": "running"}
        tasks.spawn(learning.learn(preview=True))
        return {"status": "queued"}

    @router.get("/learning/runs")
    async def learning_runs(offset: int = 0):
        return runs.list(offset)

    @router.get("/learning/runs/{run_id}")
    async def learning_run(run_id: int):
        row = runs.get(run_id)
        if not row:
            raise HTTPException(404, "学习记录不存在")
        return row

    @router.get("/expressions")
    async def expression_list(q: str = "", offset: int = 0):
        return expressions.list(q, offset)

    @router.put("/expressions/{expression_id}")
    async def expression_enabled(expression_id: int, body: EnabledEdit):
        if not expressions.set_enabled(expression_id, body.enabled):
            raise HTTPException(404, "表达不存在")
        return {"ok": True}

    @router.delete("/expressions/{expression_id}")
    async def expression_delete(expression_id: int):
        if not expressions.delete(expression_id):
            raise HTTPException(404, "表达不存在")
        return {"ok": True}

    return router
