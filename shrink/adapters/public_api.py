"""Core-owned persona composition, independent of consuming plugins."""
import uuid

from .plugin_protocol import validate, success
from ..services.composition import Silent


class PublicAPI:
    def __init__(self, host):
        self._host = host

    def manifest(self):
        return {'protocol': 1, 'capabilities': [{
            'name': 'message.compose', 'version': 1, 'description': '按当前人格表达工具事实，不发送消息',
            'actions': {'record_sent': {'description': '记录已确认发送的文本及唯一回执', 'readonly': False,
                'parameters': {'type': 'object', 'required': ['text', 'delivery_id'], 'properties': {
                    'text': {'type': 'string'}, 'delivery_id': {'type': 'string'}}}},
                'compose': {'description': '表达给定事实；返回status=ready与text，或status=silent（不得发送或回退原文）', 'readonly': True,
                'parameters': {'type': 'object', 'required': ['facts'], 'properties': {
                    'event_id': {'type': 'string', 'description': '原提醒或定时事件的稳定标识；重试复用'}, 'facts': {'type': 'string'}, 'instruction': {'type': 'string'}, 'request': {'type': 'string'}, 'kind': {'type': 'string'}}}}}}]}

    async def invoke(self, request):
        validate(request, 'message.compose', {'compose', 'record_sent'})
        app = self._host.application
        if app.closed:
            raise RuntimeError('石英核心正在重载')
        args = request['arguments']
        if request["action"] == "record_sent":
            if not all(isinstance(args.get(k), str) and args[k] for k in ("text", "delivery_id")):
                raise ValueError("需要文本和唯一发送回执")
            recorded = app.outbound.record(request.get("scope"), args["text"], args["delivery_id"])
            return success({"recorded": recorded})
        facts = args.get('facts')
        instruction = args.get('instruction', '说明实际结果和限制，不把提交说成完成。')
        if not isinstance(facts, str) or not isinstance(instruction, str):
            raise ValueError('facts 和 instruction 必须是字符串')
        scope = request.get("scope") or {}
        incoming = None
        if getattr(app, "inbox", None) and scope.get("session") and scope.get("sender"):
            incoming, _ = app.inbox.event(
                session=str(scope["session"]), sender=str(scope["sender"]),
                external_id=str(args.get("event_id") or uuid.uuid4().hex), source="plugin",
                route="composition", text=facts, metadata={"kind": str(args.get("kind", "result"))})
            if incoming["result"] is not None:
                return success(incoming["result"])
            app.inbox.repository.claim([incoming["id"]])
        text = await app.outbound.compose(facts, instruction=instruction,
            request=str(args.get('request', '')), kind=str(args.get('kind', 'result')),
            scope=request.get('scope'))
        result = ({'status': 'silent', 'reason': text.reason} if isinstance(text, Silent)
                  else {'status': 'ready', 'text': text})
        if incoming:
            result["event_id"] = incoming["external_id"]
            app.inbox.repository.complete([incoming["id"]], result=result)
        return success(result)
