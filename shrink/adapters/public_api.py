"""Core-owned persona composition, independent of consuming plugins."""
import uuid
import json

from .plugin_protocol import validate, success
from ..services.composition import Silent


class PublicAPI:
    def __init__(self, host):
        self._host = host

    def manifest(self):
        confirmation_properties = {key: {'type': 'string'} for key in (
            'provider', 'device', 'execution_session', 'approval_id', 'category', 'task', 'summary',
            'confirmation_id', 'request_id')}
        confirmation_properties['expires'] = {'type': 'number', 'description': '确认对象有效期，Unix 秒'}
        notification_properties = {key: {'type': 'string'} for key in (
            'event_id', 'message_type', 'kind', 'task_id')}
        notification_properties.update(facts={'oneOf': [{'type': 'object'}, {'type': 'string'}]},
                                       fact_refs={'type': 'array', 'items': {'type': 'string'}})
        return {'protocol': 1, 'capabilities': [{
            'name': 'message.compose', 'version': 1, 'description': '按当前人格表达工具事实，不发送消息',
            'actions': {'record_sent': {'description': '记录已确认发送的文本及唯一回执', 'readonly': False,
                'parameters': {'type': 'object', 'required': ['text', 'delivery_id'], 'properties': {
                    'text': {'type': 'string'}, 'delivery_id': {'type': 'string'},
                    'event_id': {'type': 'string'}, 'message_type': {'type': 'string'}}}},
                'compose': {'description': '表达给定事实；返回status=ready与text，或status=silent（不得发送或回退原文）', 'readonly': True,
                'parameters': {'type': 'object', 'required': ['facts'], 'properties': {
                    'message_type': {'type': 'string', 'description': '兼容事件类型，与提交能力保持一致'},
                    'event_id': {'type': 'string', 'description': '原提醒或定时事件的稳定标识；重试复用'}, 'facts': {'type': 'string'}, 'instruction': {'type': 'string'}, 'request': {'type': 'string'}, 'kind': {'type': 'string'}}}}}}, {
            'name': 'authorization.confirmation', 'version': 1, 'description': '提交具体确认对象并查询审批回执',
            'actions': {action: {'readonly': action == 'validate', 'parameters': {
                'type': 'object', 'properties': confirmation_properties,
                'required': ['provider', 'device', 'execution_session', 'approval_id', 'category', 'expires']
                if action == 'submit' else ['confirmation_id', 'request_id'] if action == 'validate' else ['confirmation_id']},
                'description': '可信电脑插件确认事件；不接收授权策略'} for action in ('submit', 'check', 'validate')}
        }, {
            'name': 'message.notify', 'version': 1, 'description': '提交事实到核心发件箱；调用者不得另行发送',
            'actions': {action: {'readonly': action == 'status', 'parameters': {
                'type': 'object', 'properties': notification_properties,
                'required': ['event_id', 'message_type', 'facts'] if action == 'submit' else ['event_id', 'message_type']},
                'description': '稳定事件ID、消息类型、事实及原会话'} for action in ('submit', 'status')}
        }]}

    async def invoke(self, request):
        if request.get('capability') == 'message.notify':
            validate(request, 'message.notify', {'submit', 'status'})
            app = self._host.application
            if app.closed:
                raise RuntimeError('石英核心正在重载')
            args, scope = request['arguments'], request.get('scope') or {}
            if not all(isinstance(args.get(k), str) and args[k] for k in ('event_id', 'message_type')):
                raise ValueError('需要稳定事件ID和消息类型')
            if request['action'] == 'submit':
                if 'facts' not in args:
                    raise ValueError('需要通知事实')
                row = app.notification_outbox.submit(scope, args['event_id'], args['facts'],
                    kind=args.get('kind', 'result'), message_type=args['message_type'],
                    task_id=args.get('task_id', ''), fact_refs=args.get('fact_refs', ()))
            else:
                if args.get('task_id'):
                    task = app.notification_outbox.repository.task(args['task_id'])
                    if task['sender'] != str(scope.get('sender', '')):
                        raise ValueError('通知任务不属于当前创建者')
                    scope = {**scope, 'session': task['session']}
                identity = json.dumps([scope['session'], str(scope['sender']), args['event_id']], ensure_ascii=False)
                row = app.notification_outbox.repository.get(identity)
                if not row or row['message_type'] != args['message_type']:
                    raise ValueError('通知不存在')
            return success({k: row[k] for k in ('id', 'event_id', 'state', 'reason', 'session')})
        if request.get("capability") == "authorization.confirmation":
            validate(request, "authorization.confirmation", {"submit", "check", "validate"})
            scope = request.get("scope") or {}
            if request["action"] == "submit":
                return success(await self._host.application.authorization_adapter.receive(request["arguments"], scope))
            service = self._host.application.authorizations
            event = service.repository.event(request["arguments"]["confirmation_id"])
            if not scope.get("admin") or not service.is_admin(str(scope.get("sender", ""))) or (
                event["body"]["origin"], event["body"]["administrator"]) != (scope.get("session"), str(scope.get("sender", ""))):
                raise ValueError("确认对象范围不匹配")
            if request["action"] == "validate":
                return success({"allowed": service.execution_allowed(event["id"], request["arguments"]["request_id"])})
            return success(await service.process(event["id"]))
        validate(request, 'message.compose', {'compose', 'record_sent'})
        app = self._host.application
        if app.closed:
            raise RuntimeError('石英核心正在重载')
        args = request['arguments']
        scope = request.get('scope') or {}
        if getattr(app, 'notification_outbox', None) and scope.get('session') and scope.get('sender'):
            if not app.notification_outbox.repository.ownership(
                scope['session'], str(scope['sender']), args.get('message_type', args.get('kind', 'result')), 'compatibility'):
                if request['action'] == 'compose':
                    return success({'status': 'silent', 'reason': 'core_send_owner'})
                raise ValueError('该类事件由核心发送；兼容入口不得再次发送或记录')
            if args.get('event_id'):
                identity = json.dumps([scope['session'], str(scope['sender']), args['event_id']], ensure_ascii=False)
                if not app.notification_outbox.repository.event_owner(identity, 'compatibility'):
                    if request['action'] == 'compose':
                        return success({'status': 'silent', 'reason': 'core_event_owner'})
                    raise ValueError('该事件已由核心接管')
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
