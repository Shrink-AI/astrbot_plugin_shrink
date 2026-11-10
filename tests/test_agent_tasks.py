import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from shrink.adapters.operations import Operations, CURRENT_TASK
from shrink.persistence.database import Database
from shrink.adapters.agent_tasks import AgentTasks


class Event:
    unified_msg_origin = 'qq:GroupMessage:10'
    def __init__(self): self.extra = {}
    def is_admin(self): return True
    def get_sender_id(self): return '123'
    def set_extra(self, key, value): self.extra[key] = value
    def get_extra(self, key, default=None): return self.extra.get(key, default)


@pytest.fixture
def runtime(tmp_path):
    db = Database(tmp_path / 'state')
    config = {'provider': [{'id': 'shrink/gpt-6.1-sol'}]}
    ctx = SimpleNamespace(get_config=lambda: config,
                          subagent_orchestrator=SimpleNamespace(handoffs=[]))
    app = SimpleNamespace(database=db, state=SimpleNamespace(root=tmp_path / 'state'),
                          tasks=SimpleNamespace(error=lambda *args: None))
    native_config = {'main_enable': True, 'agents': []}
    async def native(resource, action, arguments=None):
        if action == 'tools': return [{'name': 'read_source', 'active': True}, {'name': 'shrink_orchestrate', 'active': True}]
        if action == 'list': return copy.deepcopy(native_config)
        native_config.update(copy.deepcopy(arguments))
        ctx.subagent_orchestrator.handoffs = [SimpleNamespace(
            provider_id=a['provider_id'], agent=SimpleNamespace(name=a['name'], tools=a['tools'], instructions=a['system_prompt']))
            for a in arguments['agents']]
    app.native = SimpleNamespace(call=AsyncMock(side_effect=native))
    op = Operations(ctx, app, tmp_path / 'plugins' / 'core')
    op.deliver_agent_result = AsyncMock()
    yield op, Event(), native_config
    db.close()


async def create(op, name='researcher'):
    return await op.create_agent({'name': name, 'instructions': '读取并总结资料', 'tools': ['read_source']})


@pytest.mark.asyncio
@pytest.mark.parametrize('phase,expected', [('compose', 'interrupted'), ('send', 'unknown')])
async def test_delivery_cancellation_is_persisted(runtime, monkeypatch, phase, expected):
    import sys
    op, event, _ = runtime
    await create(op)
    op.invoke_agent = AsyncMock(return_value='completed facts')
    submitted = await op.submit_agent(event, 'researcher', 'test')
    await op.running[submitted['task_id']]
    monkeypatch.setitem(sys.modules, 'astrbot.api.message_components', SimpleNamespace(Plain=lambda x: x))
    monkeypatch.setitem(sys.modules, 'astrbot.core.message.message_event_result', SimpleNamespace(MessageChain=lambda x: x))
    entered = asyncio.Event()
    async def wait(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
    op.app.outbound = SimpleNamespace(compose=AsyncMock(side_effect=wait) if phase == 'compose' else AsyncMock(return_value='result'), record=Mock())
    op.context.send_message = AsyncMock(side_effect=wait)
    task = asyncio.create_task(AgentTasks.deliver_agent_result(op, submitted['task_id'], event))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert op.detail(submitted['task_id'])['delivery'] == expected
    if phase == 'compose':
        op.context.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_accepted_result_is_not_sent_twice(runtime, monkeypatch):
    import sys
    op, event, _ = runtime
    await create(op)
    op.invoke_agent = AsyncMock(return_value='completed facts')
    submitted = await op.submit_agent(event, 'researcher', 'test')
    await op.running[submitted['task_id']]
    monkeypatch.setitem(sys.modules, 'astrbot.api.message_components', SimpleNamespace(Plain=lambda x: x))
    monkeypatch.setitem(sys.modules, 'astrbot.core.message.message_event_result', SimpleNamespace(MessageChain=lambda x: x))
    op.app.outbound = SimpleNamespace(compose=AsyncMock(return_value='result'), record=Mock())
    op.context.send_message = AsyncMock(return_value=True)
    await AgentTasks.deliver_agent_result(op, submitted['task_id'], event)
    await AgentTasks.deliver_agent_result(op, submitted['task_id'], event)
    assert op.detail(submitted['task_id'])['delivery'] == 'platform_accepted'
    op.context.send_message.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('delivery', ['sending', 'unknown'])
async def test_redelivery_rejects_uncertain_platform_result(runtime, delivery):
    op, event, _ = runtime
    await create(op)
    op.invoke_agent = AsyncMock(return_value='completed facts')
    submitted = await op.submit_agent(event, 'researcher', 'test')
    task_id = submitted['task_id']
    await op.running[task_id]
    with op.database.transaction() as con:
        con.execute('UPDATE operation_tasks SET delivery=? WHERE id=?', (delivery, task_id))
    op.deliver_agent_result.reset_mock()
    with pytest.raises(ValueError, match='发送结果不明'):
        await op.redeliver_agent_result(task_id, event)
    op.deliver_agent_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_redelivery_uses_existing_result_and_enforces_session(runtime):
    op, event, _ = runtime
    await create(op)
    op.invoke_agent = AsyncMock(return_value='completed facts')
    submitted = await op.submit_agent(event, 'researcher', 'test')
    task_id = submitted['task_id']
    await op.running[task_id]
    op.deliver_agent_result.reset_mock()
    other = Event()
    other.unified_msg_origin = 'qq:GroupMessage:other'
    with pytest.raises(ValueError, match='当前会话'):
        await op.redeliver_agent_result(task_id, other)
    await op.redeliver_agent_result(task_id, event)
    op.deliver_agent_result.assert_awaited_once_with(task_id, event)
    op.invoke_agent.assert_awaited_once()
    assert len(op.list()) == 1
    assert op.detail(task_id)['result'] == 'completed facts'


@pytest.mark.asyncio
async def test_native_dynamic_create_preserves_agents_and_rejects_missing_tools(runtime):
    op, event, config = runtime
    await create(op, 'first_agent')
    await create(op, 'second_agent')
    assert len(config['agents']) == 2
    assert (await create(op, 'first_agent'))['existing']
    with pytest.raises(ValueError, match='工具不存在'):
        await op.create_agent({'name': 'bad_agent', 'instructions': 'test', 'tools': ['invented']})
    with pytest.raises(ValueError, match='同名'):
        await op.create_agent({'name': 'first_agent', 'instructions': 'changed', 'tools': []})


@pytest.mark.asyncio
async def test_background_submit_returns_before_work_and_deduplicates(runtime):
    op, event, _ = runtime
    await create(op)
    gate = asyncio.Event()
    async def invoke(*args):
        await gate.wait()
        return 'read source complete'
    op.invoke_agent = invoke
    first = await op.submit_agent(event, 'researcher', 'read')
    second = await op.submit_agent(event, 'researcher', 'read')
    assert first['task_id'] == second['task_id'] and second['already_running']
    assert not op.running[first['task_id']].done()
    gate.set()
    await op.running[first['task_id']]
    assert op.detail(first['task_id'])['status'] == 'completed'
    op.deliver_agent_result.assert_awaited_once()


@pytest.mark.asyncio
async def test_coordinator_waits_for_children_without_deadlock(runtime):
    op, event, _ = runtime
    await create(op, 'coordinator')
    await create(op, 'researcher')
    op.agent_slots = asyncio.Semaphore(1)
    calls = []
    async def invoke(handoff, ev, request):
        calls.append((handoff.agent.name, request))
        if handoff.agent.name == 'coordinator' and handoff.agent.tools:
            await op.submit_agent(ev, 'researcher', 'read source')
            return 'submitted'
        return 'verified source result'
    op.invoke_agent = invoke
    submitted = await op.submit_agent(event, 'coordinator', 'collect')
    await asyncio.wait_for(op.running[submitted['task_id']], 2)
    assert [x[0] for x in calls] == ['coordinator', 'researcher', 'coordinator']
    assert 'verified source result' in calls[-1][1]
    assert op.detail(submitted['task_id'])['result'] == 'verified source result'
    op.deliver_agent_result.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancelled_foreground_does_not_cancel_submitted_agent(runtime):
    op, event, _ = runtime
    await create(op)
    gate, submitted_event = asyncio.Event(), asyncio.Event()
    async def invoke(*args):
        await gate.wait()
        return 'background completed'
    op.invoke_agent = invoke
    async def foreground():
        await op.submit_agent(event, 'researcher', 'long work')
        submitted_event.set()
        await asyncio.Event().wait()
    foreground_task = asyncio.create_task(foreground())
    await submitted_event.wait()
    foreground_task.cancel()
    await asyncio.gather(foreground_task, return_exceptions=True)
    task_id = event.get_extra('shrink_background_task')
    assert not op.running[task_id].done()
    gate.set()
    await op.running[task_id]
    assert op.detail(task_id)['result'] == 'background completed'


@pytest.mark.asyncio
async def test_dependencies_receive_results_and_cannot_cross_sessions(runtime):
    op, event, _ = runtime
    await create(op)
    op.invoke_agent = AsyncMock(return_value='source evidence')
    first = await op.submit_agent(event, 'researcher', 'first')
    second = await op.submit_agent(event, 'researcher', 'summary', [first['task_id']])
    await op.running[second['task_id']]
    assert 'source evidence' in op.invoke_agent.call_args.args[2]
    other = Event(); other.unified_msg_origin = 'qq:GroupMessage:other'
    with pytest.raises(ValueError, match='当前会话'):
        await op.submit_agent(other, 'researcher', 'cross', [first['task_id']])


@pytest.mark.asyncio
async def test_timeout_retries_with_checkpoint_not_empty_restart(runtime):
    op, event, _ = runtime
    await create(op)
    async def invoke(h, e, request):
        if '中断现场' not in request:
            op.record('tool:create_card', result='card=42')
            raise TimeoutError('network')
        assert 'card=42' in request
        return 'confirmed card=42, not duplicated'
    op.invoke_agent = invoke
    submitted = await op.submit_agent(event, 'researcher', 'collect')
    await op.running[submitted['task_id']]
    row = op.detail(submitted['task_id'])
    assert row['failures'] == 1 and row['status'] == 'completed'


@pytest.mark.asyncio
async def test_cannot_wait_on_ancestor_and_cancel_cascades(runtime):
    op, event, _ = runtime
    await create(op)
    entered = asyncio.Event()
    async def invoke(h, e, request):
        if request.startswith('parent'):
            with pytest.raises(ValueError, match='祖先'):
                await op.submit_agent(e, 'researcher', 'bad', [CURRENT_TASK.get()])
            await op.submit_agent(e, 'researcher', 'child')
            entered.set()
        await asyncio.Event().wait()
    op.invoke_agent = invoke
    first = await op.submit_agent(event, 'researcher', 'parent')
    await entered.wait()
    await op.cancel(first['task_id'])
    assert all(r['status'] == 'cancelled' for r in op.list())
