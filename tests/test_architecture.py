"""Import boundaries are enforced without installing AstrBot or optional plugins."""
import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).resolve().parents[1]


def imported_modules(path):
    for node in ast.walk(ast.parse(path.read_text('utf-8'))):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            yield node.module or ''


def test_domain_and_http_do_not_import_host_adapters():
    for folder in ('services', 'persistence', 'api'):
        for path in (ROOT / 'shrink' / folder).rglob('*.py'):
            for module in imported_modules(path):
                assert not module.startswith(('astrbot', 'data.plugins')), (path, module)
                assert 'adapters' not in module.split('.'), (path, module)


def test_core_never_imports_another_plugin():
    for path in (ROOT / 'shrink').rglob('*.py'):
        for module in imported_modules(path):
            assert 'data.plugins' not in module, (path, module)
            assert not any(x.startswith('astrbot_plugin_') for x in module.split('.')), (path, module)


def test_task_context_has_no_operations_cycle():
    for path in [ROOT / 'main.py', ROOT / 'shrink/adapters/conversation.py', ROOT / 'shrink/adapters/agent_tasks.py']:
        tree = ast.parse(path.read_text('utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(a.name == 'CURRENT_TASK' for a in node.names):
                assert node.module.endswith('services.task_context'), path


def test_copied_workbench_loads_without_plugin_package(tmp_path):
    path = tmp_path / 'standalone_workbench.py'
    path.write_bytes((ROOT / 'shrink/services/workbench.py').read_bytes())
    spec = importlib.util.spec_from_file_location('standalone_workbench', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.Workbench(tmp_path / 'work', tmp_path / 'plugins').fingerprints(tmp_path / 'missing') == {}


@pytest.mark.asyncio
async def test_workbench_uses_injected_runtime(tmp_path):
    from shrink.services.workbench import Workbench
    runtime = SimpleNamespace(activate=AsyncMock(), read_source=lambda action, path: action + ':' + path)
    work = Workbench(tmp_path, tmp_path / 'plugins', runtime=runtime, guide=lambda: 'guide')
    await work.activate('host-manager', 'plugin')
    runtime.activate.assert_awaited_once_with('host-manager', 'plugin')
    assert await work.perform('docs', '', 'core/example.py', '', None) == 'docs:core/example.py'
    assert await work.perform('guide', '', '', '', None) == 'guide'
    with pytest.raises(RuntimeError, match='运行时'):
        await Workbench(tmp_path, tmp_path).activate(None, 'missing')
