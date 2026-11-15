"""Task-scoped native subagents; the main conversation never waits for their work."""
import asyncio
import copy
import json
import re

from ..prompts import text as prompt_text
from ..config import persona_prompt
from ..persistence.clarifications import Clarifications
from ..persistence.task_inputs import TaskInputsRepository
from ..persistence.tasks import Tasks
from ..services.task_states import TaskStates, ACTIVE
from ..services.task_execution import AwaitingUserInput
from ..services.task_inputs import TaskInputs
from ..services.scheduling import shared_lock


class AgentTasks:
    """Mixin for Operations, sharing its task store and lifecycle."""

    def init_agents(self):
        self.agent_config_lock = asyncio.Lock()
        self.clarifications = Clarifications(self.database)
        settings = getattr(self.app.state, "settings", None)
        self.task_states = TaskStates(Tasks(self.database), getattr(settings, "task_retry_limit", 3))
        self.task_inputs = TaskInputs(TaskInputsRepository(self.database), self.task_states)
        self.question_signals = {}

    def owned_task(self, event, task_id):
        row = self.detail(task_id)
        if row['session'] != event.unified_msg_origin or row['sender'] != str(event.get_sender_id()):
            raise ValueError('只能操作当前会话中自己交办的任务')
        return row

    async def orchestrate(self, event, action, args):
        if not event.is_admin():
            raise ValueError('仅管理员可编排任务')
        if not isinstance(args, dict):
            raise ValueError('arguments必须是JSON对象')
        if action == 'guide':
            return {
                'create': {'name': '任务用途英文名', 'persona': '角色人格、交流方式与职责边界',
                           'instructions': '职责、范围和验收',
                           'tools': ['从tools返回列表选取'], 'provider_id': 'shrink/gpt-6.1-sol'},
                'submit': {'agent': '已创建的名称', 'request': '完整任务及来源/项目',
                           'depends_on': ['可选先前任务ID']},
                'status/cancel/resume': {'task_id': '当前会话任务ID'},
                'ask_user': {'question': '问题与建议答案', 'context': '已查明事实和受影响步骤',
                             'options': ['可选方案']},
                'resume_after_question': {'task_id': '待答复任务ID', 'answer': '原用户的答复'},
                'append_input': {'task_id': '原任务ID；本轮决策已明确时可省略', 'text': '原创建者的补充要求；消息标识由入口获取'},
                'list': {}, 'tools': {},
                'workflow': prompt_text('tasks.orchestrate.workflow'),
                'limits': prompt_text('tasks.orchestrate.limits'),
            }
        if action == 'tools':
            return await self.agent_tools()
        if action == 'create':
            return await self.create_agent(args)
        if action == 'ask_user':
            from ..services.task_context import CURRENT_TASK
            task_id = CURRENT_TASK.get()
            if not task_id or task_id not in self.question_signals:
                raise ValueError('ask_user仅供执行中的子代理使用；主会话直接询问用户')
            self.owned_task(event, task_id)
            question = args.get('question', '')
            context = args.get('context', '')
            options = args.get('options', [])
            if not isinstance(question, str) or not question.strip() or len(question) > 6000:
                raise ValueError('question需要明确问题和建议答案，最多6000字')
            if not isinstance(context, str) or len(context) > 8000 or not isinstance(options, list):
                raise ValueError('context或options格式无效')
            self.clarifications.ask(task_id, question, context, options)
            self.question_signals[task_id].set()
            return {'status': 'waiting_user', 'question_saved': True}
        if action == 'submit':
            return await self.submit_agent(event, args.get('agent', ''), args.get('request', ''),
                                           args.get('depends_on', []))
        if action == 'append_input':
            from ..services.task_context import CURRENT_TASK
            if CURRENT_TASK.get():
                raise ValueError('后台同构体不能冒充原用户追加输入')
            message = getattr(event, 'message_obj', None)
            identity = getattr(message, 'message_id', None)
            target = args.get('task_id', '')
            decisions = getattr(self.app, 'decisions', None)
            if decisions is not None:
                from ..persistence.inbox import input_id
                incoming = input_id(event.unified_msg_origin, str(event.get_sender_id()), str(identity or ''))
                if not decisions.inbox.get(incoming):
                    self.app.inbox.event(session=event.unified_msg_origin, sender=str(event.get_sender_id()),
                                         external_id=str(identity or ''), source='platform', route='task',
                                         text=event.get_message_str())
                target = decisions.append_target(incoming, target, event.unified_msg_origin, str(event.get_sender_id()))
            return self.task_inputs.append(
                target, event.unified_msg_origin, str(event.get_sender_id()),
                str(identity) if identity is not None else '', args.get('text', ''))
        if action == 'list':
            return [r for r in self.list() if r['session'] == event.unified_msg_origin
                    and r['sender'] == str(event.get_sender_id())]
        if action in {'status', 'cancel', 'resume'}:
            row = self.owned_task(event, args.get('task_id', ''))
            if action == 'status':
                return row
            if action == 'cancel':
                return await self.cancel(row['id'])
            if row['status'] in {'queued', 'running', 'waiting_external', 'retry_wait', 'cancelling'}:
                if row['status'] == 'queued':
                    self.events[row['id']] = event
                    self.execution.launch(row['id'])
                return {'task_id': row['id'], 'status': row['status'], 'already_running': True}
            active = self.running.get(row['id'])
            if active and not active.done():
                raise ValueError('执行端尚未停止，不能恢复任务')
            if row['kind'] == 'development':
                self.require_developer_slot(row['id'])
            handoff = next((h for h in getattr(self.context.subagent_orchestrator, 'handoffs', [])
                            if h.agent.name == row['agent']), None)
            if handoff is None:
                raise ValueError('原任务代理未启用，无法恢复')
            self.task_states.capacity(row['kind'], row['id'])
            answer = args.get('answer', '')
            if row['status'] == 'waiting_user':
                if not isinstance(answer, str) or not answer.strip() or len(answer) > 12000:
                    raise ValueError('请取得原用户答复后用answer续做，不能空答复重跑')
                await self.task_inputs.continue_with(row['id'], '原用户答复：' + answer)
                self.clarifications.answer(row['id'], answer)
                for child in self.task_states.repository.list():
                    if child['parent'] == row['id'] and child['status'] == 'waiting_user':
                        await self.orchestrate(event, 'resume', {'task_id': child['id'], 'answer': answer})
            await self.task_inputs.resume(row['id'], event.unified_msg_origin, str(event.get_sender_id()))
            from ..persistence.task_reports import TaskReports
            TaskReports(self.database).delivery(row['id'], '')
            self.events[row['id']] = event
            self.handoffs[row['id']] = copy.deepcopy(handoff)
            self.execution.launch(row['id'])
            return {'task_id': row['id'], 'status': self.detail(row['id'])['status'], 'resumed': True}
        raise ValueError('未知操作；先guide查看编排用法')

    async def agent_tools(self):
        tools = await self.app.native.call('subagents', 'tools')
        if hasattr(self.context, 'get_llm_tool_manager'):
            from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
            settings = self.context.get_config().get('provider_settings', {})
            runtime_tools = FunctionToolExecutor._get_runtime_computer_tools(
                settings.get('computer_use_runtime', 'none'), self.context.get_llm_tool_manager(),
                settings.get('sandbox', {}).get('booter'))
            names = {t['name'] for t in tools}
            tools = [*tools, *({'name': t.name, 'description': t.description,
                               'parameters': t.parameters, 'active': t.active}
                              for t in runtime_tools.values() if t.name not in names)]
        return tools

    async def create_agent(self, args):
        name = str(args.get('name', ''))
        if not re.fullmatch(r'[a-z][a-z0-9_]{2,63}', name):
            raise ValueError('代理名使用3至64位小写英文、数字、下划线')
        if name == 'shirnk_developer':
            raise ValueError('保留开发代理，请为任务创建独立名称')
        instructions = args.get('instructions', '')
        if not isinstance(instructions, str) or not instructions.strip() or len(instructions) > 12000:
            raise ValueError('需要明确职责及验收条件，最多12000字')
        tools = args.get('tools', [])
        if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
            raise ValueError('tools必须是工具名列表')
        available = await self.agent_tools()
        allowed = {t['name'] for t in available if t.get('active', True)}
        if tools == ['*']:
            tools = sorted(allowed)
        if set(tools) - allowed:
            raise ValueError('工具不存在或未启用：' + ','.join(sorted(set(tools) - allowed)))
        if tools and 'shrink_lookup' in allowed and 'shrink_lookup' not in tools:
            tools = [*tools, 'shrink_lookup']
        role_persona = args.get('persona', '你是石英的任务同构体，沿用石英人格，专注当前分工；'
                                '对内明确说明证据、未知和阻碍，不向用户重复发送进度。')
        if not isinstance(role_persona, str) or not role_persona.strip() or len(role_persona) > 3000:
            raise ValueError('persona需要明确角色人格，最多3000字')
        repository = getattr(self.app, 'persona_repository', None)
        personality = persona_prompt(repository.learned_persona() if repository else '')
        provider = args.get('provider_id', 'shrink/gpt-6.1-sol')
        if not any(p.get('id') == provider for p in self.context.get_config().get('provider', [])):
            raise ValueError('模型必须是已配置provider_id')
        async with shared_lock(self.agent_config_lock):
            config = await self.app.native.call('subagents', 'list')
            agents = list(config.get('agents', []))
            existing = next((a for a in agents if a.get('name') == name), None)
            definition = {'name': name, 'enabled': True, 'provider_id': provider, 'persona_id': None,
                          'tools': list(dict.fromkeys(tools)),
                          'public_description': instructions[:160],
                          'system_prompt': personality + '\n角色人格：' + role_persona + '\n任务职责：'
                                           + instructions + '\n' + prompt_text('tasks.agent_policy')}
            if existing:
                if all(existing.get(k) == v for k, v in definition.items()):
                    return {'agent': name, 'existing': True, 'provider': provider}
                raise ValueError('同名代理已存在且定义不同，请选新名称或通过原生管理明确更新')
            # Tasks run through submit; the handoff list is loaded whether or not the host
            # mounts synchronous transfer_to_* tools on the main chat, so leave that setting.
            config['agents'] = [*agents, definition]
            await self.app.native.call('subagents', 'update', config)
        self.record('agent_created', name, result=json.dumps(definition, ensure_ascii=False))
        return {'agent': name, 'provider': provider, 'tools': definition['tools'], 'created': True}

    async def submit_agent(self, event, agent, request, depends_on=None, parent=''):
        from ..services.task_context import CURRENT_TASK
        if not event.is_admin():
            raise ValueError('仅管理员可提交任务')
        if not isinstance(request, str) or not request.strip() or len(request) > 32000:
            raise ValueError('任务内容为空或超过32000字')
        dependencies = depends_on or []
        if not isinstance(dependencies, list) or not all(isinstance(x, str) for x in dependencies):
            raise ValueError('depends_on必须是已有任务ID列表')
        parent = parent or CURRENT_TASK.get()
        if parent and self.question_signals.get(parent) and self.question_signals[parent].is_set():
            raise ValueError('该任务正在等待用户决定，不能继续派工')
        ancestors, cursor = set(), parent
        while cursor:
            if cursor in ancestors:
                raise ValueError('任务祖先关系循环')
            ancestors.add(cursor)
            cursor = self.owned_task(event, cursor)['parent']
        if len(ancestors) >= 4:
            raise ValueError('分工最多4层，请复用已有任务结果')
        for dep in dependencies:
            self.owned_task(event, dep)
            if dep in ancestors:
                raise ValueError('子任务不能等待祖先任务')
        handoff = next((h for h in getattr(self.context.subagent_orchestrator, 'handoffs', [])
                        if h.agent.name == agent), None)
        if handoff is None:
            raise ValueError('代理未启用；先create创建或查看原生子代理列表')
        active = [r for r in self.task_states.repository.list() if r['status'] in ACTIVE and r['kind'] == 'agent']
        for row in active:
            if (row['session'], row['sender'], row['agent'], row['request']) == (
                    event.unified_msg_origin, str(event.get_sender_id()), agent, request):
                return {'task_id': row['id'], 'status': row['status'], 'already_running': True}
        self.task_states.capacity('agent')
        task_id = self.task_states.repository.create(
            request, event.unified_msg_origin, str(event.get_sender_id()), handoff.provider_id,
            kind='agent', agent=agent, parent=parent, dependencies=dependencies)
        # Snapshot the definition: another configuration edit cannot change an in-flight task.
        self.events[task_id] = event
        self.handoffs[task_id] = copy.deepcopy(handoff)
        self.execution.launch(task_id)
        if not CURRENT_TASK.get():
            event.set_extra('shrink_background_task', task_id)
        return {'task_id': task_id, 'status': 'queued', 'agent': agent, 'completed': False,
                'deadline_seconds': None, 'depends_on': dependencies,
                'execution': {'version': 1, 'status': 'submitted', 'task_id': task_id}}

    def checkpoint(self, task_id):
        row = self.detail(task_id)
        return json.dumps({'result': row['result'], 'events': row['events'][-30:]}, ensure_ascii=False)[-24000:]

    async def invoke_agent(self, handoff, event, request):
        from astrbot.core.astr_agent_context import AgentContextWrapper, AstrAgentContext
        from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
        from astrbot.core.agent.hooks import BaseAgentRunHooks
        from astrbot.api import logger
        from ..services.task_context import CURRENT_TASK
        owner = self
        class AuditHooks(BaseAgentRunHooks):
            async def on_tool_start(self, run_context, tool, tool_args):
                logger.info(f'Shrink tool start task={CURRENT_TASK.get()} tool={tool.name}')
                owner.record('tool_start:' + tool.name, result=json.dumps(tool_args, ensure_ascii=False, default=str))

            async def on_tool_end(self, run_context, tool, tool_args, tool_result):
                text = '\n'.join(getattr(c, 'text', '') for c in getattr(tool_result, 'content', []))
                owner.record('tool_end:' + tool.name, result=json.dumps(
                    {'arguments': tool_args, 'result': text, 'error': getattr(tool_result, 'isError', False)},
                    ensure_ascii=False, default=str))

        wrapper = AgentContextWrapper(context=AstrAgentContext(context=self.context, event=event),
                                      tool_call_timeout=180)
        tools = FunctionToolExecutor._build_handoff_toolset(wrapper, handoff.agent.tools)
        # Same native runner as handoff, with explicit hooks so partial writes survive timeouts.
        async def execute():
            from .task_runner import run_task_agent
            return await run_task_agent(self.context,
                event=event, chat_provider_id=handoff.provider_id, prompt=request,
                system_prompt=handoff.agent.instructions + '\n' + prompt_text('tasks.execution_scope')
                    + '\n' + prompt_text('tasks.development_standard')
                    + '\n' + prompt_text('tasks.agent_resources')
                    + '\n' + prompt_text('skills.authoring'), tools=tools,
                contexts=handoff.agent.begin_dialogs or [], max_steps=128,
                agent_hooks=AuditHooks(), inputs=self.task_inputs, task_id=CURRENT_TASK.get(),
                wrapper=wrapper)
        task_id = CURRENT_TASK.get()
        signal = self.question_signals.setdefault(task_id, asyncio.Event())
        execution = asyncio.create_task(execute())
        waiting = asyncio.create_task(signal.wait())
        try:
            await asyncio.wait([execution, waiting], return_when=asyncio.FIRST_COMPLETED)
            if signal.is_set():
                raise AwaitingUserInput()
            response = execution.result()
        finally:
            for pending in (execution, waiting):
                if not pending.done():
                    pending.cancel()
            await asyncio.gather(execution, waiting, return_exceptions=True)
        if getattr(response, 'role', '') == 'err':
            raise RuntimeError(response.completion_text or '子代理模型返回错误')
        return response.completion_text or ''

    async def execute_task(self, row, request, summary):
        event = self.events.get(row['id'])
        if event is None:
            event = self.task_event(row)
            self.owned_task(event, row['id'])
            self.events[row['id']] = event
        handoff = self.handoffs.get(row['id'])
        if handoff is None:
            handoff = next((h for h in getattr(self.context.subagent_orchestrator, 'handoffs', [])
                            if h.agent.name == row['agent']), None)
            if handoff is None:
                raise ValueError('原任务代理未启用，无法恢复')
            handoff = copy.deepcopy(handoff)
            self.handoffs[row['id']] = handoff
        handoff.provider_id = row['provider']
        if summary:
            handoff = copy.deepcopy(handoff)
            handoff.agent.tools = []
        question = self.clarifications.get(row['id'])
        if question and question['answer']:
            request += '\n原用户答复：' + question['answer']
        try:
            return await self.invoke_agent(handoff, event, request)
        finally:
            self.question_signals.pop(row['id'], None)

    async def deliver_completed(self, task_id):
        row = self.detail(task_id)
        event = self.events.get(task_id) or self.task_event(row)
        await self.deliver_agent_result(task_id, event)

    async def deliver_agent_result(self, task_id, event, *, retry=False):
        from .task_results import deliver_task_result
        await deliver_task_result(self, task_id, event, retry=retry)

    async def redeliver_agent_result(self, task_id, event):
        if not event.is_admin():
            raise ValueError('仅管理员可补发任务结果')
        row = self.owned_task(event, task_id)
        if row['kind'] != 'agent' or row['parent'] or row['status'] not in {'completed', 'needs_attention', 'failed', 'waiting_user'}:
            raise ValueError('只能补发已结束的顶层代理任务结果')
        if row['delivery'] in {'sending', 'unknown'}:
            raise ValueError('发送结果不明，请先核对平台记录，不能直接重发')
        active = self.running.get(task_id)
        if active and not active.done():
            raise ValueError('任务或结果发送仍在进行')
        task = asyncio.create_task(self.deliver_agent_result(task_id, event, retry=True))
        self.running[task_id] = task
        await asyncio.shield(task)
        return {'task_id': task_id, 'delivery': self.detail(task_id)['delivery']}
