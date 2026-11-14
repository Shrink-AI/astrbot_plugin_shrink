from fastapi import APIRouter


def router_for(service):
    router = APIRouter(prefix="/api/v1")

    @router.get("/decisions")
    async def decisions(limit: int = 100, offset: int = 0):
        return {"records": service.repository.list(limit, offset), "stats": service.repository.stats()}

    return router
