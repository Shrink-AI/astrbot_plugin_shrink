from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import pytest

from shrink.adapters.plugin_protocol import CapabilityClient, CapabilityError, success


def provider(name='arbitrary-plugin', version=1, value='first'):
    instance = NS(plugin_capabilities=lambda: {'protocol': 1, 'capabilities': [{
        'name': 'example.read', 'version': version, 'actions': {'read': {}}}]},
        invoke_capability=AsyncMock(return_value=success(value)))
    return NS(name=name, activated=True, star_cls=instance)


@pytest.mark.asyncio
async def test_discovery_reload_unload_and_versions():
    registry = [provider()]
    bus = CapabilityClient(lambda: registry)
    assert await bus.call('example.read', 'read') == 'first'
    registry[0] = provider(value='reloaded')
    assert await bus.call('example.read', 'read') == 'reloaded'
    with pytest.raises(CapabilityError, match='不可用'):
        await bus.call('example.read', 'read', version=2)
    registry[0].activated = False
    assert bus.catalog() == []
    with pytest.raises(CapabilityError, match='不可用'):
        await bus.call('example.read', 'read')


@pytest.mark.asyncio
async def test_ambiguity_scope_copy_and_timeout_are_not_retried():
    registry = [provider(), provider('second', value='second')]
    bus = CapabilityClient(lambda: registry)
    with pytest.raises(CapabilityError, match='不唯一'):
        await bus.call('example.read', 'read')
    scope = {'admin': True, 'session': 'private-A', 'sender': '1'}
    assert await bus.call('example.read', 'read', provider='second', scope=scope) == 'second'
    call = registry[1].star_cls.invoke_capability.call_args.args[0]
    call['scope']['session'] = 'changed'
    assert scope['session'] == 'private-A'
    registry[1].star_cls.invoke_capability.reset_mock()
    registry[1].star_cls.invoke_capability.side_effect = TimeoutError()
    with pytest.raises(TimeoutError):
        await bus.call('example.read', 'read', provider='second')
    assert registry[1].star_cls.invoke_capability.await_count == 1


def test_core_has_no_external_plugin_business():
    root = Path(__file__).resolve().parents[1]
    assert not (root / 'shrink/adapters/reminders.py').exists()
    for path in [root / 'main.py', *(root / 'shrink').rglob('*.py')]:
        code = path.read_text('utf-8')
        for token in ('reminder_deliveries', 'reminder_series', 'get_integration_api',
                      'astrbot_plugin_bothouse', 'astrbot_plugin_shirnk_ext_reminder'):
            assert token not in code, (path, token)
