"""Exercise the actual tool body without requiring the AstrBot SDK locally."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.fixture
def entrypoint():
    tree = ast.parse((Path(__file__).resolve().parents[1] / 'main.py').read_text('utf-8'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ShrinkPlugin')
    method = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'orchestrate')
    method.decorator_list = []
    namespace = {'AstrMessageEvent': object}
    exec(compile(ast.Module(body=[method], type_ignores=[]), 'main.py', 'exec'), namespace)
    operations = SimpleNamespace(orchestrate=AsyncMock(return_value={'workflow': 'guide'}), record=Mock())
    instance = SimpleNamespace(host=SimpleNamespace(operations=operations))
    return namespace['orchestrate'], instance, operations


@pytest.mark.asyncio
async def test_empty_tool_call_returns_readonly_guide(entrypoint):
    call, instance, operations = entrypoint
    event = object()
    assert json.loads(await call(instance, event)) == {'workflow': 'guide'}
    operations.orchestrate.assert_awaited_once_with(event, 'guide', {})


@pytest.mark.asyncio
@pytest.mark.parametrize('kwargs', [
    {'action': None}, {'action': ''}, {'action': 1},
    {'action': 'submit', 'arguments': '{"agent":"a"}{"request":"b"}'},
])
async def test_invalid_arguments_do_not_dispatch(entrypoint, kwargs):
    call, instance, operations = entrypoint
    assert json.loads(await call(instance, object(), **kwargs))['ok'] is False
    operations.orchestrate.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_submission_keeps_payload(entrypoint):
    call, instance, operations = entrypoint
    event = object()
    arguments = {'agent': 'researcher', 'request': 'Read the source'}
    await call(instance, event, action='submit', arguments=json.dumps(arguments))
    operations.orchestrate.assert_awaited_once_with(event, 'submit', arguments)


@pytest.mark.asyncio
async def test_permission_error_is_reported_without_success(entrypoint):
    call, instance, operations = entrypoint
    operations.orchestrate.side_effect = ValueError('仅管理员可编排任务')
    assert json.loads(await call(instance, object()))['ok'] is False
    operations.record.assert_not_called()
