"""Authenticated dashboard mutations enter the same durable inbox as platform inputs."""
import json
import uuid

from fastapi.responses import JSONResponse, Response


async def dispatch(request, call_next, inbox):
    external_id = request.headers.get("idempotency-key") or uuid.uuid4().hex
    if len(external_id) > 200:
        return JSONResponse({"detail": "输入标识过长"}, status_code=400)
    content_type = request.headers.get("content-type", "")
    raw = await request.body()
    text = raw.decode("utf-8", errors="replace") if "json" in content_type else (
        f"{request.method} {request.url.path}；附件通过原上传接口保存")
    metadata = {"method": request.method, "path": request.url.path, "query": request.url.query}
    row, fresh = inbox.event(session="webui", sender="dashboard:administrator",
                             external_id=external_id, source="webui", text=text,
                             route="webui", metadata=metadata)
    headers = {"X-Input-ID": external_id}
    if not fresh:
        if row["text"] != text or row["metadata"] != metadata:
            return JSONResponse({"detail": "同一输入标识不能用于不同操作"}, status_code=409, headers=headers)
        if row["result"] is not None:
            return JSONResponse(row["result"]["body"], status_code=row["result"]["status"], headers=headers)
        if row["claimed"] is not None:
            return JSONResponse({"detail": "操作已领取，结果尚未确认；请核对实际状态", "input_id": row["id"]},
                                status_code=409, headers=headers)
    inbox.repository.claim([row["id"]])
    response = await call_next(request)
    content = b"".join([chunk async for chunk in response.body_iterator])
    try:
        body = json.loads(content)
    except (ValueError, UnicodeError):
        body = {"status": response.status_code}
    inbox.repository.complete([row["id"]], result={"body": body, "status": response.status_code})
    return Response(content, status_code=response.status_code,
                    headers={**dict(response.headers), **headers}, media_type=response.media_type)
