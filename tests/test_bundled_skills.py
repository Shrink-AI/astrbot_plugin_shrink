from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from shrink.adapters.native_skills import NativeSkills
from shrink.persistence.database import Database


@pytest.mark.asyncio
async def test_install_grilling_preserves_existing_native_skill(tmp_path):
    db = Database(tmp_path)
    app = SimpleNamespace(database=db)
    native = SimpleNamespace(call=AsyncMock(return_value={"skills": []}))
    service = NativeSkills(app, native)
    await service.install_bundled()
    create = [c for c in native.call.call_args_list if c.args[:2] == ('skills', 'create')]
    assert len(create) == 1 and create[0].args[2]['name'] == 'grilling'
    content = create[0].args[2]['content']
    assert yaml.safe_load(content.split('---')[1])['name'] == 'grilling'
    assert '不用讨论' in content and 'ask_user' in content
    native.call.reset_mock()
    native.call.return_value = {"skills": [{"name": "grilling", "active": False}]}
    await service.install_bundled()
    assert native.call.call_count == 1
    assert await service.planning_guidance('帮我写插件') == ''
    db.close()


@pytest.mark.asyncio
async def test_planning_loads_native_skill_for_work_not_progress(tmp_path):
    db = Database(tmp_path)
    content = (Path(__file__).resolve().parents[1] / 'bundled_skills/grilling/SKILL.md').read_text('utf8')
    async def call(resource, action, *args):
        return {"skills": [{"name": "grilling", "active": True}]} if action == 'list' else {"content": content}
    native = SimpleNamespace(call=AsyncMock(side_effect=call))
    service = NativeSkills(SimpleNamespace(database=db), native)
    assert await service.planning_guidance('帮我修改石英的插件') == content
    assert await service.planning_guidance('直接做游戏需求，不用讨论') == content
    native.call.reset_mock()
    assert await service.planning_guidance('干完了没') == ''
    native.call.assert_not_called()
    db.close()
