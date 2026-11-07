from fastapi import APIRouter, HTTPException

from .schemas import KindEdit, NicknameCreate


def router_for(app):
    router = APIRouter(prefix="/api/v1")
    repository = app.people_repository

    def listing(q="", kind="", source="", offset=0):
        rows = repository.search(q, kind, source, offset)
        for row in rows:
            row["nicknames"] = repository.nicknames(row["id"], status="")
        return rows

    @router.get("/people")
    async def people(q: str = "", kind: str = "", source: str = "", offset: int = 0):
        return listing(q, kind, source, offset)

    @router.get("/people/{uid}")
    async def person(uid: str):
        row = repository.get(uid)
        if not row:
            raise HTTPException(404, "没有这个人")
        return {
            "person": row,
            "nicknames": repository.nicknames(uid, status=""),
            "profile": repository.profile(uid),
            "sources": repository.sources(uid),
            "interactions": app.messages.interactions(uid),
            "memories": app.memory_repository.search(user_id=uid, limit=100),
        }

    @router.put("/people/{uid}/kind")
    async def set_kind(uid: str, body: KindEdit):
        if not repository.set_kind(uid, body.kind):
            raise HTTPException(404, "没有这个人")
        if body.kind == "bot":
            app.messages.mark_unlearnable(uid)
        app.people.invalidate()
        return {"ok": True}

    @router.post("/people/{uid}/nicknames")
    async def add_nickname(uid: str, body: NicknameCreate):
        if not repository.get(uid):
            raise HTTPException(404, "没有这个人")
        scope = body.scope.strip()
        if scope and scope.isdigit():
            scope = f"qq:group:{scope}"
        nid, outcome = repository.add_nickname(
            uid, body.name.strip(), scope=scope, origin="admin", confidence=1.0,
            evidence=["管理员录入"], verified=1,
        )
        if outcome in ("conflict", "rejected"):
            repository.set_nickname(nid, user_id=uid, origin="admin", verified=1, confidence=1.0,
                                    status="active")
        app.review.close_target("nickname%", nid)
        app.people.invalidate()
        return {"id": nid}

    @router.delete("/nicknames/{nickname_id}")
    async def delete_nickname(nickname_id: int):
        if not repository.delete_nickname(nickname_id):
            raise HTTPException(404, "称呼不存在")
        app.people.invalidate()
        return {"ok": True}

    @router.post("/nicknames/{nickname_id}/verify")
    async def verify_nickname(nickname_id: int, body: dict):
        right = bool(body.get("correct", True))
        if not repository.set_nickname(
            nickname_id, status="active" if right else "rejected", verified=1 if right else -1,
            confidence=1.0 if right else 0.0,
        ):
            raise HTTPException(404, "称呼不存在")
        app.review.close_target("nickname%", nickname_id)
        app.people.invalidate()
        return {"ok": True}

    @router.get("/groups")
    async def groups():
        cards = {p["subject"]: p for p in repository.profiles("group")}
        return [
            {**row, "profile": cards.get(row["source"])}
            for row in repository.known_sources()
            if ":group:" in row["source"]
        ]

    return router
