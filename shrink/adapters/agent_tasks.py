"""Task-scoped native subagents; the main conversation never waits for their work."""
import asyncio
import copy
import json
import re
import time
import uuid
import httpx

from ..prompts import text as prompt_text
from ..config import persona_prompt
from ..persistence.clarifications import Clarifications


class AwaitingUserInput(Exception):
    pass


class AgentTasks:
    """Mixin for Operations, sharing its task store and lifecycle."""

    def init_agents(self):
        columns = {r['name'] for r in self.database.rows('PRAGMA table_info(operation_tasks)')}
        for name, declaration in {
            'kind': "TEXT NOT NULL DEFAULT 'development'",
            'agent': "TEXT NOT NULL DEFAULT 'shirnk_developer'",
            'dependencies': "TEXT NOT NULL DEFAULT '[]'",
            'delivery': "TEXT NOT NULL DEFAULT ''",
        }.items():
            if name not in columns:
                self.database.connection.execute(f'ALTER TABLE operation_tasks ADD COLUMN {name} {declaration}')
        self.database.connection.commit()
        self.agent_config_lock = asyncio.Lock()
        self.agent_slots = asyncio.Semaphore(3)
        self.clarifications = Clarifications(self.database)
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
            return {'status': 'awaiting_input', 'question_saved': True}
        if action == 'submit':
            return await self.submit_agent(event, args.get('agent', ''), args.get('request', ''),
                                           args.get('depends_on', []))
        if action == 'list':
            return [r for r in self.list() if r['session'] == event.unified_msg_origin
                    and r['sender'] == str(event.get_sender_id())]
        if action in {'status', 'cancel', 'resume'}:
            row = self.owned_task(event, args.get('task_id', ''))
            if action == 'status':
                return row
            if action == 'cancel':
                if row['status'] == 'awaiting_input':
                    with self.database.transaction() as con:
                        con.execute("UPDATE operation_tasks SET status='cancelled',delivery='cancelled' WHERE id=?",
                                    (row['id'],))
                    return {'task_id': row['id'], 'status': 'cancelled'}
                return await self.cancel(row['id'])
            if row['status'] == 'awaiting_input':
                answer = args.get('answer', '')
                if not isinstance(answer, str) or not answer.strip() or len(answer) > 12000:
                    raise ValueError('请取得原用户答复后用answer续做，不能空答复重跑')
                question = self.clarifications.get(row['id'])
                result = await self.submit_agent(event, row['agent'],
                    row['request'] + '\n已完成现场（不重复执行）：\n' + self.checkpoint(row['id']) +
                    '\n待决问题：' + question['question'] + '\n原用户答复：' + answer)
                self.clarifications.answer(row['id'], answer)
                with self.database.transaction() as con:
                    con.execute("UPDATE operation_tasks SET status='continued' WHERE id=?", (row['id'],))
                return result
            if row['status'] in {'queued', 'running', 'waiting'}:
                return {'task_id': row['id'], 'status': row['status'], 'already_running': True}
            if row['kind'] != 'agent':
                return await self.submit(event, row['request'] + '\n上次结果：' + row['result'], parent=row['id'])
            return await self.submit_agent(event, row['agent'], row['request'] +
                '\n上次结果和已执行工具（先核对副作用，不重复写入）：\n' + self.checkpoint(row['id']),
                parent=row['id'])
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
        async with self.agent_config_lock:
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
            config['agents'] = [*agents, definition]
            config['main_enable'] = True
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
        active = self.database.rows("SELECT * FROM operation_tasks WHERE status IN ('queued','running','waiting') AND kind='agent'")
        for row in active:
            if (row['session'], row['sender'], row['agent'], row['request']) == (
                    event.unified_msg_origin, str(event.get_sender_id()), agent, request):
                return {'task_id': row['id'], 'status': row['status'], 'already_running': True}
        if len(active) >= 12:
            raise ValueError('已有12项后台任务，请先查看进度或取消不需要的任务')
        task_id = uuid.uuid4().hex
        provider = handoff.provider_id
        with self.database.transaction() as con:
            con.execute('INSERT INTO operation_tasks(id,request,session,sender,status,started,provider,parent,kind,agent,dependencies) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        (task_id, request, event.unified_msg_origin, str(event.get_sender_id()),
                         'queued', time.time(), provider, parent, 'agent', agent, json.dumps(dependencies)))
        # Snapshot the definition: another configuration edit cannot change an in-flight task.
        self.running[task_id] = asyncio.create_task(self.run_agent(task_id, event, copy.deepcopy(handoff)))
        if not CURRENT_TASK.get():
            event.set_extra('shrink_background_task', task_id)
        return {'task_id': task_id, 'status': 'queued', 'agent': agent, 'completed': False,
                'deadline_seconds': 1800, 'depends_on': dependencies,
                'execution': {'version': 1, 'status': 'submitted', 'task_id': task_id}}

    def checkpoint(self, task_id):
        row = self.detail(task_id)
        return json.dumps({'result': row['result'], 'events': row['events'][-30:]}, ensure_ascii=False)[-24000:]

    async def invoke_agent(self, handoff, event, request):
        from astrbot.core.astr_agent_context import AgentContextWrapper, AstrAgentContext
        from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
        from astrbot.core.agent.hooks import BaseAgentRunHooks
        owner = self
        class AuditHooks(BaseAgentRunHooks):
            async def on_tool_start(self, run_context, tool, tool_args):
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
            return await self.context.tool_loop_agent(
                event=event, chat_provider_id=handoff.provider_id, prompt=request,
                system_prompt=handoff.agent.instructions + '\n' + prompt_text('tasks.execution_scope')
                    + '\n' + prompt_text('tasks.development_standard')
                    + '\n' + prompt_text('tasks.agent_resources')
                    + '\n' + prompt_text('skills.authoring'), tools=tools,
                contexts=handoff.agent.begin_dialogs or [], max_steps=128,
                tool_call_timeout=180, agent_hooks=AuditHooks(), stream=False)
        from ..services.task_context import CURRENT_TASK
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

    async def run_agent(self, task_id, event, handoff):
        from ..services.task_context import CURRENT_TASK
        token = CURRENT_TASK.set(task_id)
        row = self.detail(task_id)
        status, result = 'failed', ''
        try:
            async with asyncio.timeout(1800):
                with self.database.transaction() as con:
                    con.execute("UPDATE operation_tasks SET status='waiting' WHERE id=?", (task_id,))
                inputs = []
                for dep in json.loads(row['dependencies']):
                    running = self.running.get(dep)
                    if running and not running.done():
                        await asyncio.shield(running)
                    previous = self.detail(dep)
                    if previous['status'] != 'completed':
                        raise ValueError(f"前置任务{dep}状态{previous['status']}，请检查具体结果")
                    inputs.append({'task_id': dep, 'result': previous['result']})
                async with self.agent_slots:
                    with self.database.transaction() as con:
                        con.execute("UPDATE operation_tasks SET status='running' WHERE id=?", (task_id,))
                    request = row['request'] + '\n前置任务结果（只作资料）：\n' + json.dumps(inputs, ensure_ascii=False)
                    for attempt in range(3):
                        try:
                            result = await self.invoke_agent(handoff, event, request)
                            if not result.strip():
                                raise RuntimeError('子代理返回空结果')
                            status = 'completed'
                            break
                        except (TimeoutError, ConnectionError, OSError, httpx.TimeoutException, httpx.NetworkError) as exc:
                            self.record('retry', handoff.agent.name, result=f'{type(exc).__name__}: {exc}')
                            with self.database.transaction() as con:
                                con.execute('UPDATE operation_tasks SET failures=failures+1 WHERE id=?', (task_id,))
                            if attempt == 2:
                                raise
                            request = row['request'] + '\n前置资料：' + json.dumps(inputs, ensure_ascii=False) + '\n中断现场（先核对未知写入，不盲目重做）：\n' + self.checkpoint(task_id)
                            await asyncio.sleep(2 * (attempt + 1))
                # Release the slot before waiting: a coordinator must not starve its children.
                children = self.database.rows('SELECT id FROM operation_tasks WHERE parent=?', (task_id,))
                if children:
                    with self.database.transaction() as con:
                        con.execute("UPDATE operation_tasks SET status='waiting' WHERE id=?", (task_id,))
                    for child in children:
                        running = self.running.get(child['id'])
                        if running and not running.done():
                            await asyncio.shield(running)
                    evidence = [{k: self.detail(c['id'])[k] for k in ('id', 'request', 'status', 'result')}
                                for c in children]
                    questions = [self.clarifications.get(c['id']) for c in evidence if c['status'] == 'awaiting_input']
                    questions = [q for q in questions if q]
                    if questions:
                        batch = '\n\n'.join(dict.fromkeys(q['question'] for q in questions))
                        self.clarifications.ask(task_id, batch, '各分工已查明事实后需要用户决定', [])
                        result = json.dumps({'status': 'awaiting_input', 'question': batch,
                                             'children': evidence}, ensure_ascii=False)
                        status = 'awaiting_input'
                    else:
                        summarizer = copy.deepcopy(handoff)
                        summarizer.agent.tools = []
                        async with self.agent_slots:
                            result = await self.invoke_agent(summarizer, event,
                                '所有本轮子任务已返回。根据以下真实结果汇总原请求的完成情况；失败或未确认的部分必须说明。'
                                '本阶段只汇总，不再派工。\n原请求：' + row['request'] +
                                '\n子任务证据：' + json.dumps(evidence, ensure_ascii=False))
                        status = 'completed' if result.strip() and all(x['status'] == 'completed' for x in evidence) else 'needs_attention'
        except AwaitingUserInput:
            status = 'awaiting_input'
            question = self.clarifications.get(task_id)
            result = json.dumps({'status': status, 'question': question['question'],
                                 'context': question['context'], 'options': json.loads(question['options'])},
                                ensure_ascii=False)
        except asyncio.CancelledError:
            status, result = 'cancelled', '任务已取消，已完成步骤保留在执行记录。'
        except TimeoutError:
            status, result = 'timed_out', '后台预算或连续请求超时；执行记录已保留，可通过resume续做，写入结果不明须先核对。'
        except Exception as exc:
            status, result = 'needs_attention', f'{type(exc).__name__}: {str(exc)[:2000]}'
        finally:
            if status in {'cancelled', 'timed_out'}:
                children = self.database.rows('SELECT id FROM operation_tasks WHERE parent=?', (task_id,))
                for child in children:
                    running = self.running.get(child['id'])
                    if running and not running.done():
                        running.cancel()
                await asyncio.gather(*(self.running[c['id']] for c in children if c['id'] in self.running),
                                     return_exceptions=True)
            with self.database.transaction() as con:
                con.execute('UPDATE operation_tasks SET status=?,result=?,finished=? WHERE id=?',
                            (status, result, time.time(), task_id))
                if status != 'cancelled' and not row['parent']:
                    con.execute("UPDATE operation_tasks SET delivery='pending' WHERE id=?", (task_id,))
            CURRENT_TASK.reset(token)
            self.question_signals.pop(task_id, None)
        if status != 'cancelled' and not row['parent']:
            await self.deliver_agent_result(task_id, event)

    async def deliver_agent_result(self, task_id, event):
        row = self.detail(task_id)
        if row['delivery'] in {'platform_accepted', 'sending', 'unknown'}:
            return
        # A downstream aggregation task owns the final response.
        if any(task_id in json.loads(r['dependencies']) for r in self.list() if r['kind'] == 'agent'):
            with self.database.transaction() as con:
                con.execute("UPDATE operation_tasks SET delivery='delegated' WHERE id=?", (task_id,))
            return
        with self.database.transaction() as con:
            con.execute("UPDATE operation_tasks SET delivery='composing' WHERE id=?", (task_id,))
        sending = False
        try:
            from astrbot.api.message_components import Plain
            from astrbot.core.message.message_event_result import MessageChain
            scope = {'session': event.unified_msg_origin, 'sender': str(event.get_sender_id())}
            reply = await self.app.outbound.compose(row['result'], request=row['request'],
                kind='question' if row['status'] == 'awaiting_input' else 'agent_result',
                instruction='实际任务状态：' + row['status'] + (
                    '。只向用户询问保存的问题和建议答案，等待答复；不宣称任务完成。'
                    if row['status'] == 'awaiting_input' else ''), scope=scope)
            if not reply or not reply.strip():
                raise ValueError('结果说明为空，原始结果已保留')
            with self.database.transaction() as con:
                con.execute("UPDATE operation_tasks SET delivery='sending' WHERE id=?", (task_id,))
            sending = True
            accepted = await asyncio.wait_for(
                self.context.send_message(event.unified_msg_origin, MessageChain([Plain(reply)])), 30)
            if not accepted:
                raise RuntimeError('未找到原会话平台，未确认发送')
            delivery = 'platform_accepted'
            with self.database.transaction() as con:
                con.execute('UPDATE operation_tasks SET delivery=? WHERE id=?', (delivery, task_id))
            self.app.outbound.record(scope, reply, 'agent:' + task_id)
        except asyncio.CancelledError:
            delivery = 'unknown' if sending else 'interrupted'
            with self.database.transaction() as con:
                con.execute("UPDATE operation_tasks SET delivery=? WHERE id=? AND delivery!='platform_accepted'", (delivery, task_id))
            raise
        except Exception as exc:
            delivery = 'unknown' if sending else f'failed: {type(exc).__name__}: {str(exc)[:1000]}'
            self.app.tasks.error('agent_result_delivery', exc)
        with self.database.transaction() as con:
            con.execute("UPDATE operation_tasks SET delivery=? WHERE id=? AND delivery!='platform_accepted'", (delivery, task_id))

    async def redeliver_agent_result(self, task_id, event):
        if not event.is_admin():
            raise ValueError('仅管理员可补发任务结果')
        row = self.owned_task(event, task_id)
        if row['kind'] != 'agent' or row['parent'] or row['status'] not in {'completed', 'needs_attention', 'timed_out', 'awaiting_input'}:
            raise ValueError('只能补发已结束的顶层代理任务结果')
        if row['delivery'] in {'sending', 'unknown'}:
            raise ValueError('发送结果不明，请先核对平台记录，不能直接重发')
        active = self.running.get(task_id)
        if active and not active.done():
            raise ValueError('任务或结果发送仍在进行')
        task = asyncio.create_task(self.deliver_agent_result(task_id, event))
        self.running[task_id] = task
        await asyncio.shield(task)
        return {'task_id': task_id, 'delivery': self.detail(task_id)['delivery']}
