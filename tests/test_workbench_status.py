"""Workbench behavior tests; all mutable fixtures live in TemporaryDirectory."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from shrink.services.workbench import Workbench


class WorkbenchStatusTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='workbench-status-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.plugins = self.base / 'plugins'
        self.name = 'astrbot_plugin_shrink'
        self.target = self.plugins / self.name
        self.target.mkdir(parents=True)
        (self.target / 'main.py').write_text('VALUE = 1\n', encoding='utf-8')
        (self.target / 'metadata.yaml').write_text('name: ' + self.name + '\n', encoding='utf-8')
        self.result = {'ok': True, 'tests_run': 1}
        self.tester = AsyncMock(side_effect=self.run_isolated_fixture)
        self.manager = AsyncMock()
        self.workbench = Workbench(self.base / 'workbench', self.plugins, tester=self.tester)
        await self.call('checkout')

    async def run_isolated_fixture(self, files):
        # The real isolation runner is exercised by the outer workbench test.
        # This injected boundary returns controlled evidence to the real service.
        self.assertEqual(files['metadata.yaml'], 'name: ' + self.name + '\n')
        self.assertIn('main.py', files)
        return dict(self.result)

    async def call(self, action, path='', content=''):
        return json.loads(await self.workbench.perform(action, self.name, path, content, self.manager))

    async def test_status_draft_not_tested(self):
        status = await self.call('status')
        self.assertEqual(status['plugin'], self.name)
        self.assertCountEqual(status['draft_files'], ['main.py', 'metadata.yaml'])
        self.assertIsNone(status['test'])
        self.assertFalse(status['test_current'])
        self.assertFalse(status['rollback_available'])
        self.assertFalse(status['conflict'])
        self.tester.assert_not_awaited()
        self.manager.reload.assert_not_awaited()

    async def test_status_after_passing_test(self):
        self.assertTrue((await self.call('test'))['ok'])
        status = await self.call('status')
        self.assertEqual(status['test'], self.result)
        self.assertTrue(status['test_current'])
        self.tester.assert_awaited_once()
        self.assertFalse(status['conflict'])

    async def test_write_invalidates_passing_evidence_and_retest_refreshes_it(self):
        await self.call('test')
        await self.call('write', 'main.py', 'VALUE = 2\n')
        status = await self.call('status')
        self.assertEqual(status['test'], self.result)
        self.assertFalse(status['test_current'])
        # Deploy must reject stale evidence before any activation or copying.
        with self.assertRaisesRegex(ValueError, '尚未通过隔离功能测试'):
            await self.call('deploy')
        self.assertEqual((self.target / 'main.py').read_text('utf-8'), 'VALUE = 1\n')
        self.manager.reload.assert_not_awaited()
        await self.call('test')
        self.assertTrue((await self.call('status'))['test_current'])
        self.assertEqual(self.tester.await_count, 2)

    async def test_current_failed_test_is_not_success(self):
        self.result = {'ok': False, 'error': 'fixture test failure'}
        await self.call('test')
        status = await self.call('status')
        self.assertTrue(status['test_current'])
        self.assertFalse(status['test']['ok'])
        with self.assertRaisesRegex(ValueError, '尚未通过隔离功能测试'):
            await self.call('deploy')
        self.manager.reload.assert_not_awaited()

    async def test_conflict_and_backup_flags_are_independent_of_test_evidence(self):
        await self.call('test')
        (self.target / 'main.py').write_text('VALUE = 3\n', encoding='utf-8')
        (self.workbench.root / 'backups' / self.name).mkdir(parents=True)
        status = await self.call('status')
        self.assertTrue(status['conflict'])
        self.assertTrue(status['rollback_available'])
        self.assertTrue(status['test_current'])
        with self.assertRaisesRegex(ValueError, '线上代码已改变'):
            await self.call('deploy')
        self.manager.reload.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
