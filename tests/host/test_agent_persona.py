import copy
from types import SimpleNamespace

import pytest

pytest.importorskip("astrbot.core.agent.runners.tool_loop_agent_runner")
pytest.importorskip("astrbot.api")

from astrbot.core.db.sqlite import SQLiteDatabase  # noqa: E402
from astrbot.core.persona_mgr import PersonaManager  # noqa: E402
from astrbot.core.subagent_orchestrator import SubAgentOrchestrator  # noqa: E402
from tests.test_agent_tasks import runtime  # noqa: E402, F401


async def test_native_persona_persists_and_loads_the_handoff_without_main_persona(runtime, tmp_path):
    op, _, config = runtime
    db = SQLiteDatabase(str(tmp_path / "astrbot-personas.db"))
    try:
        await db.initialize()
        manager = PersonaManager(db, SimpleNamespace(default_conf={}))
        await manager.initialize()
        await manager.update_persona("default", system_prompt="MAIN_PERSONA_SENTINEL")
        op.context.persona_manager = manager
        orchestrator = SubAgentOrchestrator(SimpleNamespace(), manager)
        op.context.subagent_orchestrator = orchestrator

        async def native(resource, action, arguments=None):
            if action == "tools":
                return [{"name": "read_source", "active": True}]
            if action == "list":
                return copy.deepcopy(config)
            config.update(copy.deepcopy(arguments))
            await orchestrator.reload_from_config(config)

        op.app.native.call.side_effect = native
        result = await op.create_agent({"name": "tech_reader", "instructions": "阅读并核对资料",
                                        "persona": "EVIDENCE_READER：逐条对照来源", "tools": ["read_source"]})
        assert config["agents"][0]["persona_id"] == result["persona_id"] != "default"
        restarted = PersonaManager(db, SimpleNamespace(default_conf={}))
        await restarted.initialize()
        reloaded = SubAgentOrchestrator(SimpleNamespace(), restarted)
        await reloaded.reload_from_config(config)
        agent = reloaded.handoffs[0].agent
        assert "EVIDENCE_READER" in agent.instructions and "MAIN_PERSONA_SENTINEL" not in agent.instructions
        assert agent.tools == ["read_source"] and agent.begin_dialogs == []
        assert (await restarted.get_persona("default")).system_prompt == "MAIN_PERSONA_SENTINEL"
    finally:
        await db.engine.dispose()
