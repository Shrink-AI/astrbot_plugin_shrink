import time

from fastapi import APIRouter, HTTPException

from ..services.turn_trace import distribution


def router_for(app):
    router = APIRouter(prefix="/api/v1")

    @router.get("/runs")
    async def runs(kind: str = "", offset: int = 0):
        return app.runs.list(kind, offset)

    @router.get("/runs/{run_id}")
    async def run(run_id: int):
        row = app.runs.get(run_id)
        if not row:
            raise HTTPException(404, "记录不存在")
        return row

    @router.post("/runs/{run_id}/undo")
    async def undo(run_id: int):
        row = app.runs.get(run_id)
        if not row:
            raise HTTPException(404, "记录不存在")
        if row["status"] == "running":
            raise HTTPException(409, "任务还在进行中")
        touched = app.undo(run_id)
        return {"ok": True, "memories": len(touched)}

    @router.get("/turns/latency")
    async def turn_latency(hours: float = 24):
        """Where foreground turns spent their time, per outcome, over the last hours."""
        rows = app.turn_traces.since(time.time() - max(0.0, min(hours, 336)) * 3600)
        outcomes = {}
        for row in rows:
            outcomes.setdefault(row["outcome"] or "ended", []).append(row)
        return {"hours": hours, "all": distribution(rows),
                "outcomes": {name: distribution(group) for name, group in outcomes.items()},
                "slowest": sorted(rows, key=lambda r: r["total"], reverse=True)[:20]}

    @router.get("/digests")
    async def digests(source: str = "", offset: int = 0):
        return app.digests.recent(source, 30, offset)

    @router.post("/digest")
    async def digest_now():
        if app.digest.lock.locked():
            return {"status": "running"}

        async def work():
            sources = app.digest.due() or [
                r["source"] for r in app.messages.digest_candidates(10**12) if r["n"] >= 3
            ]
            for source in sources:
                await app.digest.digest(source, force=True)

        app.tasks.spawn(work())
        return {"status": "queued"}

    @router.post("/consolidate")
    async def consolidate_now():
        if app.consolidation.lock.locked():
            return {"status": "running"}
        app.tasks.spawn(app.consolidation.nightly())
        return {"status": "queued"}

    @router.get("/review")
    async def review(offset: int = 0):
        return app.teach.review_items(offset=offset)

    return router
