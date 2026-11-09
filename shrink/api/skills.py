from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .schemas import SkillEdit


class ResolveSkill(BaseModel):
    choice: str
    snapshot: str


def router_for(app):
    router = APIRouter(prefix="/api/v1")
    repository = app.skill_repository

    @router.get("/skills")
    async def skills(status: str = "", offset: int = 0):
        rows = repository.list(status, offset)
        for row in rows:
            row.pop("vector", None)
        return rows

    @router.get("/skills/{skill_id}")
    async def skill(skill_id: int):
        detail = app.skills.detail(skill_id)
        if not detail:
            raise HTTPException(404, "经验不存在")
        return detail

    @router.patch("/skills/{skill_id}")
    async def edit_skill(skill_id: int, body: SkillEdit):
        if not repository.get(skill_id):
            raise HTTPException(404, "经验不存在")
        fields = body.model_dump(exclude_none=True)
        if not fields:
            raise HTTPException(400, "没有要改的内容")
        await app.skills.edit(skill_id, **fields)
        return {"ok": True}

    @router.get("/skills/{skill_id}/native")
    async def native_skill(skill_id: int):
        return await app.native_skills.inspect(skill_id)

    @router.post("/skills/{skill_id}/native")
    async def resolve_skill(skill_id: int, body: ResolveSkill):
        return await app.native_skills.resolve(skill_id, body.choice, body.snapshot)

    @router.delete("/skills/{skill_id}")
    async def delete_skill(skill_id: int):
        if not repository.delete(skill_id):
            raise HTTPException(404, "经验不存在")
        return {"ok": True}

    @router.get("/episodes")
    async def episodes(offset: int = 0):
        return [app.skills.describe(e) for e in repository.episodes(offset=offset)]

    return router
