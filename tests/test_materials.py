import json

import pytest

from shrink.application import Application


async def unavailable(*args):
    return None


@pytest.mark.asyncio
async def test_materials_preserve_source_and_reject_invented_quotes(tmp_path):
    async def model(*args, **kwargs):
        return json.dumps({"summary": "项目以回合制战斗为主。",
                           "items": [{"text": "该项目使用回合制战斗", "quote": "使用回合制战斗",
                                      "importance": 4, "certainty": "stated"},
                                     {"text": "该项目有一百种角色", "quote": "一百种角色",
                                      "importance": 4, "certainty": "stated"}]}, ensure_ascii=False)
    app = Application(tmp_path, model, unavailable, unavailable)
    result = await app.materials.ingest(
        "项目设计", "crafttable:document:one", "此项目使用回合制战斗。", "qq:private:123", "qq:123")
    assert len(result["memories"]) == 1
    assert result["rejected"] == ["quote_not_found"]
    row = app.memory_repository.get(result["memories"][0]["memory_id"])
    assert row["source"] == "qq:private:123"
    assert json.loads(row["evidence"]) == ["material:" + result["material_id"]]
    assert app.materials.get(result["material_id"])["content"] == "此项目使用回合制战斗。"
    app.undo(app.materials.get(result["material_id"])["run_id"])
    assert not app.memory_repository.get(row["id"])
    await app.close()
