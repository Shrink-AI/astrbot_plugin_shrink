from fastapi import APIRouter


def router_for(service):
    router = APIRouter(prefix="/api/v1")

    @router.get("/notifications")
    async def listing(limit: int = 100):
        rows = service.repository.list(limit)
        return {"records": [{**row, "receipt": service.receipts.get(row["id"])} for row in rows]}

    return router
