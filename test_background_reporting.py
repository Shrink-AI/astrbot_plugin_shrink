"""Regression tests for background result delivery (no production I/O)."""
import unittest
from pathlib import Path


class NoLiteralFallback(unittest.IsolatedAsyncioTestCase):
    def test_conversation_uses_composed_execution_facts(self):
        root = Path(__file__).resolve().parent
        source = (root / 'shrink/adapters/conversation.py').read_text()
        self.assertNotIn('finished.text = prompt_text("reply.unverified_execution")', source)
        self.assertIn('execution_report', source)
        agent_source = (root / 'shrink/adapters/agent_tasks.py').read_text()
        self.assertIn('stream=True', agent_source)
        self.assertNotIn('stream=False', agent_source)

    async def test_failed_upstream_notifies_once_and_blocks_downstream(self):
        import tempfile
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, Mock
        from shrink.persistence.database import Database, StateRepository
        from shrink.adapters.operations import Operations

        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / 'state')
            try:
                state = StateRepository(db)
                assert '1037550568' in state.settings.group_blacklist
                assert '790980552' in state.settings.group_blacklist
                config = {'provider': [{'id': 'shrink/gpt-6.1-sol'}]}
                ctx = SimpleNamespace(
                    get_config=lambda: config,
                    subagent_orchestrator=SimpleNamespace(handoffs=[
                        SimpleNamespace(
                            provider_id='shrink/gpt-6.1-sol',
                            agent=SimpleNamespace(name='worker', tools=[], instructions='test', begin_dialogs=[]),
                        )
                    ]),
                )
                app = SimpleNamespace(
                    database=db,
                    state=SimpleNamespace(root=Path(tmp) / 'state'),
                    tasks=SimpleNamespace(error=Mock()),
                    outbound=SimpleNamespace(compose=AsyncMock(return_value='上游任务失败说明'), record=Mock()),
                )
                op = Operations(ctx, app, Path(tmp) / 'plugins' / 'core')
                event = SimpleNamespace(
                    unified_msg_origin='qq:FriendMessage:1',
                    is_admin=lambda: True,
                    get_sender_id=lambda: '1',
                    get_extra=lambda k, d=None: d,
                    set_extra=lambda k, v: None,
                    send=AsyncMock(),
                )
                op.invoke_agent = AsyncMock(side_effect=RuntimeError('model timeout'))
                first = await op.submit_agent(event, 'worker', 'step 1')
                second = await op.submit_agent(event, 'worker', 'step 2', [first['task_id']])
                await op.running[first['task_id']]
                await op.running[second['task_id']]
                self.assertEqual(op.detail(first['task_id'])['status'], 'needs_attention')
                self.assertEqual(op.detail(first['task_id'])['delivery'], 'platform_accepted')
                self.assertEqual(op.detail(second['task_id'])['status'], 'blocked')
                self.assertEqual(op.detail(second['task_id'])['delivery'], 'suppressed')
                self.assertEqual(event.send.await_count, 1)
            finally:
                db.close()
