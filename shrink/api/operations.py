from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field


class Submit(BaseModel):
    request: str = Field(min_length=1, max_length=16000)
    session: str = ""


class NativeCall(BaseModel):
    resource: str
    action: str
    arguments: dict = Field(default_factory=dict)
    confirmed: bool = False


class WorkbenchCall(BaseModel):
    action: str
    name: str = ""
    path: str = ""
    content: str = ""
    session: str


def router_for(app):
    router = APIRouter(prefix="/api/v1/operations")

    def event_for(session=""):
        return app.operations.event_for(session)

    @router.get("/tasks")
    async def tasks():
        return app.operations.list()

    @router.get("/deployments")
    async def deployments():
        return app.operations.deployments.listing()

    @router.post("/workbench")
    async def workbench(body: WorkbenchCall):
        import json
        result = await app.operations.work(
            event_for(body.session), body.action, body.name, body.path, body.content)
        try:
            return json.loads(result)
        except (ValueError, TypeError):
            return {"text": result}

    @router.get("/tasks/{task_id}")
    async def detail(task_id: str):
        return app.operations.detail(task_id)

    @router.post("/tasks")
    async def submit(body: Submit):
        return await app.operations.submit(event_for(body.session), body.request)

    @router.post("/tasks/{task_id}/cancel")
    async def cancel(task_id: str):
        return await app.operations.cancel(task_id)

    @router.post("/tasks/{task_id}/resume")
    async def resume(task_id: str):
        previous = app.operations.detail(task_id)
        if previous.get('kind') == 'agent':
            return await app.operations.orchestrate(event_for(previous['session']), 'resume', {'task_id': task_id})
        return await app.operations.submit(
            event_for(previous["session"]),
            previous["request"] + "\n继续已有草稿，先查看差异与上次结果：\n" + previous["result"],
            parent=task_id)

    @router.post("/tasks/{task_id}/redeliver")
    async def redeliver(task_id: str):
        previous = app.operations.detail(task_id)
        return await app.operations.redeliver_agent_result(task_id, event_for(previous['session']))

    @router.get("/capabilities")
    async def capabilities():
        return {"plugins": await app.native.call("plugins", "list"),
                "tools": await app.native.call("tools", "list"),
                "skills_sync_error": app.state.get("native_skills_error", ""),
                "catalog": await app.native.call("catalog", "list")}

    @router.post("/native")
    async def native(body: NativeCall):
        return await app.native.call(body.resource, body.action, body.arguments, body.confirmed)

    @router.get("/knowledge")
    async def knowledge():
        return app.knowledge.status()

    @router.post("/knowledge/sync")
    async def sync():
        await app.knowledge.sync()
        return app.knowledge.status()

    @router.get("/plugin-capabilities")
    async def plugin_capabilities():
        return app.plugin_bus.catalog()

    @router.post("/plugin-capabilities")
    async def plugin_call(body: dict):
        event = event_for(body.get('session', ''))
        return await app.plugin_bus.call(body['capability'], body['action'], body.get('arguments', {}),
                                        provider=body.get('provider'), version=body.get('version', 1),
                                        scope={'session': event.unified_msg_origin,
                                               'sender': str(event.get_sender_id()), 'admin': True})

    @router.get("/materials")
    async def materials():
        return app.materials.list()

    @router.get("/materials/{material_id}")
    async def material(material_id: str):
        return app.materials.get(material_id)

    @router.get("/chats")
    async def chats(source: str = "", keyword: str = "", offset: int = 0):
        return app.database.rows(
            "SELECT m.* FROM messages m WHERE (?='' OR m.source=?) AND "
            "(m.text LIKE ? OR EXISTS(SELECT 1 FROM chat_attachments a "
            "WHERE a.message_id=m.id AND a.name LIKE ?)) ORDER BY m.created DESC LIMIT 100 OFFSET ?",
            (source, source, f"%{keyword[:200]}%", f"%{keyword[:200]}%", max(0, offset)))

    @router.get("/chat-sources")
    async def chat_sources():
        return app.database.rows(
            "SELECT source,max(source_name) name,count(*) count,max(created) latest "
            "FROM messages GROUP BY source ORDER BY latest DESC")

    @router.get("/attachments")
    async def attachments(source: str, keyword: str = ""):
        return app.attachments.list(source, keyword)

    @router.get("/attachments/{attachment_id}/file")
    async def attachment(attachment_id: str):
        row, path = app.attachments.file(attachment_id)
        return FileResponse(path, filename=row["name"], media_type="application/octet-stream")

    return router
