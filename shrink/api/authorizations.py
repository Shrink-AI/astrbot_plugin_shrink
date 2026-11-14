from fastapi import APIRouter, Request


def router_for(app):
    router = APIRouter(prefix="/api/v1/authorizations")

    def scope(session):
        event = app.operations.event_for(session)
        return {"session": event.unified_msg_origin, "sender": str(event.get_sender_id()), "admin": event.is_admin()}

    @router.get("")
    async def listing(session: str):
        return app.authorizations.listing(scope(session))

    @router.post("/action")
    async def action(body: dict, request: Request):
        return await app.authorization_adapter.manage(body["action"], body.get("arguments", {}),
                                                     scope(body["session"]), request.state.input_id)

    return router
