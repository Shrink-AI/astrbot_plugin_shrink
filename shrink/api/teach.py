from fastapi import APIRouter, HTTPException

from .schemas import ProposalAccept, TeachMessage, Verdict


def router_for(app):
    router = APIRouter(prefix="/api/v1")
    repository = app.teach_repository

    @router.get("/teach/sessions")
    async def sessions():
        return repository.sessions()

    @router.post("/teach/sessions")
    async def create_session():
        return {"id": repository.create_session()}

    @router.get("/teach/sessions/{session_id}")
    async def session(session_id: int):
        row = repository.session(session_id)
        if not row:
            raise HTTPException(404, "会话不存在")
        return {
            "session": row,
            "messages": repository.messages(session_id),
            "proposals": repository.proposals(session_id),
        }

    @router.post("/teach/sessions/{session_id}/messages")
    async def message(session_id: int, body: TeachMessage):
        return await app.teach.chat(session_id, body.text.strip())

    @router.post("/teach/sessions/{session_id}/review")
    async def ask(session_id: int):
        if not repository.session(session_id):
            raise HTTPException(404, "会话不存在")
        return await app.teach.ask(session_id)

    @router.post("/teach/proposals/{proposal_id}/accept")
    async def accept(proposal_id: int, body: ProposalAccept | None = None):
        edits = body.model_dump(exclude_none=True) if body else None
        return {"result": await app.teach.accept(proposal_id, edits or None)}

    @router.post("/teach/proposals/{proposal_id}/reject")
    async def reject(proposal_id: int):
        app.teach.reject(proposal_id)
        return {"ok": True}

    @router.post("/review/{queue_id}")
    async def verdict(queue_id: int, body: Verdict, proposal: int | None = None):
        await app.teach.verdict(queue_id, body.verdict, proposal)
        return {"ok": True}

    return router
