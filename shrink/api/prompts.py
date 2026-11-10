from fastapi import APIRouter
from pydantic import BaseModel, Field


class PromptEdit(BaseModel):
    text: str = Field(max_length=20000)
    note: str = Field("", max_length=200)


class PromptTrial(BaseModel):
    source: str
    drafts: dict[str, str] = {}
    limit: int = Field(4, ge=1, le=6)


def router_for(app):
    router = APIRouter(prefix="/api/v1")
    service = app.prompts

    @router.get("/prompts")
    async def catalog():
        return service.catalog()

    @router.get("/prompts-archive")
    async def archive():
        return service.archived()

    @router.put("/prompts/{key}")
    async def save(key: str, body: PromptEdit):
        return await service.save(key, body.text, body.note)

    @router.delete("/prompts/{key}")
    async def reset(key: str):
        return await service.reset(key)

    @router.get("/prompts/{key}/history")
    async def history(key: str):
        return service.history(key)

    @router.get("/prompts/sources")
    async def sources():
        """Conversations 石英 has answered in: the only ones a trial can replay."""
        return app.database.rows(
            "SELECT m.source, (SELECT max(source_name) FROM messages n WHERE n.source=m.source) AS name, "
            "count(*) AS count, max(m.created) AS latest FROM messages m "
            "WHERE m.is_bot=1 AND m.text!='[表情]' GROUP BY m.source ORDER BY latest DESC")

    @router.post("/prompts/trial")
    async def trial(body: PromptTrial):
        return await service.trial(app, body.source, body.drafts, body.limit)

    return router
