"""Group blacklist regression tests using only draft modules and temporary storage."""
import asyncio
import importlib
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = 'draft_group_blacklist'
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT / 'shrink')]
sys.modules[PACKAGE] = package
Settings = importlib.import_module(PACKAGE + '.config').Settings
Participation = importlib.import_module(PACKAGE + '.services.participation').Participation
Turn = importlib.import_module(PACKAGE + '.services.perception').Turn
Debouncer = importlib.import_module(PACKAGE + '.services.debounce').Debouncer
storage = importlib.import_module(PACKAGE + '.persistence.database')
system = importlib.import_module(PACKAGE + '.api.system')


def turn(**changes):
    values = dict(id='message', platform='qq', account='1', user_id='qq:1', name='member',
                  source='qq:group:123', source_name='display name', text='有人知道石英吗',
                  created=time.time())
    values.update(changes)
    return Turn(**values)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.state = SimpleNamespace(settings=Settings(group_blacklist=[' 123 ', 'other:group:99'],
                                                     spontaneous_probability=1))
        self.policy = Participation(self.state, lambda *a: time.time(), lambda t: True)

    def test_no_interest_engagement_name_or_quoted_reply_bypass(self):
        for fields in ({}, {'addressee': 'self'}, {'reply_sender': 'self', 'addressee': 'self'},
                       {'addressee': 'qq:2'}, {'speaker_bot': True, 'addressee': 'self'}):
            for _ in range(2):
                self.assertEqual(self.policy.decide(turn(**fields), 0), 'group_blacklisted')
        self.assertFalse(self.policy.spontaneous)
        self.assertFalse(self.policy.bot_replies)

    def test_at_and_host_wake_command_private_exceptions(self):
        for fields in ({'mentions': [{'id': 'self'}], 'addressee': 'self'},
                       {'wake': True}, {'wake': True, 'addressee': 'self'}, {'private': True}):
            self.assertEqual(self.policy.decide(turn(**fields), 0), 'direct')
        self.assertEqual(self.policy.decide(turn(wake=True, addressee='qq:2'), 0),
                         'group_blacklisted')

    def test_exact_matching_platform_and_sender_isolation(self):
        self.state.settings.engaged_seconds = 0
        for source in ('qq:group:123', 'other:group:123', 'other:group:99'):
            for user in ('qq:1', 'qq:2'):
                self.assertEqual(self.policy.decide(turn(source=source, user_id=user), 0),
                                 'group_blacklisted')
        for source in ('qq:group:1234', 'qq:group:99', 'qq:group:124'):
            self.assertEqual(self.policy.decide(turn(source=source), 0), 'spontaneous')

    def test_explicit_call_does_not_open_followup_window(self):
        self.assertEqual(self.policy.decide(turn(wake=True), 0), 'direct')
        self.assertEqual(self.policy.decide(turn(), 0), 'group_blacklisted')

    def test_other_bot_keeps_rate_limit_for_explicit_calls(self):
        self.assertEqual(self.policy.decide(turn(speaker_bot=True, wake=True), 0), 'direct')
        self.assertEqual(self.policy.decide(turn(speaker_bot=True, wake=True), 0), 'bot_quiet')

    def test_configuration_removal_is_live(self):
        self.assertEqual(self.policy.decide(turn(), 0), 'group_blacklisted')
        self.state.settings = Settings(spontaneous_probability=1, engaged_seconds=0)
        self.assertEqual(self.policy.decide(turn(), 0), 'spontaneous')


class PersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_settings_endpoint_repeat_save_and_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db = storage.Database(root)
            try:
                state = storage.StateRepository(db)
                providers = lambda: {'chat': [''], 'embedding': ['']}
                router = system.router_for(state, None, None, providers)
                get = next(r.endpoint for r in router.routes if r.path.endswith('/settings') and 'GET' in r.methods)
                put = next(r.endpoint for r in router.routes if r.path.endswith('/settings') and 'PUT' in r.methods)
                body = Settings(**(await get())['settings'])
                body.group_blacklist = ['123', 'other:group:99']
                for _ in range(2):
                    self.assertEqual(await put(body), {'ok': True})
                self.assertEqual((await get())['settings']['group_blacklist'], body.group_blacklist)
                invalid = body.model_copy(update={'model_provider': 'missing'})
                with self.assertRaises(ValueError):
                    await put(invalid)
                self.assertEqual(state.settings.group_blacklist, body.group_blacklist)
                with self.assertRaises(ValueError):
                    Settings(group_blacklist='123')
            finally:
                db.close()
            reopened = storage.Database(root)
            try:
                state = storage.StateRepository(reopened)
                self.assertEqual(state.settings.group_blacklist, ['123', 'other:group:99'])
                policy = Participation(state, lambda *a: 0, lambda t: False)
                self.assertEqual(policy.decide(turn(), 0), 'group_blacklisted')
            finally:
                reopened.close()

    async def test_old_settings_migrate_without_losing_values(self):
        with tempfile.TemporaryDirectory() as folder:
            db = storage.Database(Path(folder))
            try:
                state = storage.StateRepository(db)
                old = Settings(spontaneous_probability=0.23).model_dump()
                del old['group_blacklist']
                state.put('settings', old)
                migrated = storage.StateRepository(db)
                self.assertEqual(migrated.settings.group_blacklist, [])
                self.assertEqual(migrated.settings.spontaneous_probability, 0.23)
                self.assertIn('group_blacklist', migrated.get('settings'))
            finally:
                db.close()

    async def test_failed_write_rolls_back_previous_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            db = storage.Database(Path(folder))
            try:
                state = storage.StateRepository(db)
                state.put('settings', Settings(group_blacklist=['123']).model_dump())
                db.connection.execute("CREATE TRIGGER reject_settings BEFORE INSERT ON kv WHEN NEW.key='settings' BEGIN SELECT RAISE(ABORT, 'blocked'); END")
                with self.assertRaises(Exception):
                    state.put('settings', Settings(group_blacklist=[]).model_dump())
                self.assertEqual(state.settings.group_blacklist, ['123'])
            finally:
                db.close()


class BurstTests(unittest.IsolatedAsyncioTestCase):
    async def test_blacklisted_message_does_not_merge_with_pending_call(self):
        service = Debouncer()
        key = ('session-a', 'sender')
        pending = asyncio.create_task(service.collect(key, '@石英', [], 'direct', quiet=0.01))
        await asyncio.sleep(0)
        for _ in range(2):
            muted = await service.collect(key, '闲聊', [], 'group_blacklisted')
            self.assertEqual(muted.reason, 'group_blacklisted')
        result = await pending
        self.assertEqual(result.texts, ['@石英'])
        self.assertEqual(result.reason, 'direct')

    async def test_cancel_pending_call_and_keep_other_session(self):
        service = Debouncer()
        first = asyncio.create_task(service.collect(('a', 'u'), 'call', [], 'direct', quiet=1))
        second = asyncio.create_task(service.collect(('b', 'u'), 'call-b', [], 'direct', quiet=0.01))
        await asyncio.sleep(0)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        self.assertNotIn(('a', 'u'), service.pending)
        self.assertEqual((await second).texts, ['call-b'])
        self.assertFalse(service.pending)

    async def test_unknown_transport_receipt_cannot_change_policy(self):
        # Participation must never send, or derive permission from a delivery receipt.
        state = SimpleNamespace(settings=Settings(group_blacklist=['123']),
                                send=AsyncMock(return_value=None))
        policy = Participation(state, lambda *a: time.time(), lambda t: True)
        self.assertEqual(policy.decide(turn(), 0), 'group_blacklisted')
        state.send.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
