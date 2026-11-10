"""Core-owned persona composition, independent of consuming plugins."""
from .plugin_protocol import validate, success


class PublicAPI:
    def __init__(self, host):
        self._host = host

    def manifest(self):
        return {'protocol': 1, 'capabilities': [{
            'name': 'message.compose', 'version': 1, 'description': '按当前人格表达工具事实，不发送消息',
            'actions': {'record_sent': {'description': '记录已确认发送的文本及唯一回执', 'readonly': False,
                'parameters': {'type': 'object', 'required': ['text', 'delivery_id'], 'properties': {
                    'text': {'type': 'string'}, 'delivery_id': {'type': 'string'}}}},
                'compose': {'description': '表达给定事实和沟通要求', 'readonly': True,
                'parameters': {'type': 'object', 'required': ['facts'], 'properties': {
                    'facts': {'type': 'string'}, 'instruction': {'type': 'string'}, 'request': {'type': 'string'}, 'kind': {'type': 'string'}}}}}}]}

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
        text = await app.outbound.compose(facts, instruction=instruction,
            request=str(args.get('request', '')), kind=str(args.get('kind', 'result')),
            scope=request.get('scope'))
        return success({'text': text})
