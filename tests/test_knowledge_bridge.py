import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from shrink.adapters.knowledge import KnowledgeBridge
from shrink.persistence.database import Database, StateRepository


@pytest.mark.asyncio
async def test_rerank_timeout_retains_order_and_reports_reason(tmp_path):
    db = Database(tmp_path)
    app = SimpleNamespace(database=db, state=StateRepository(db))
    provider = SimpleNamespace(rerank=AsyncMock(side_effect=TimeoutError()))
    context = SimpleNamespace(provider_manager=SimpleNamespace(
        get_provider_by_id=AsyncMock(return_value=provider)))
    bridge = KnowledgeBridge(context, app)
    rows = [(0.9, {"text": "甲"}), (0.8, {"text": "乙"})]
    assert await bridge.rerank("问题", rows) == rows
    status = app.state.get("rerank_status")
    assert status["status"] == "degraded" and "超时" in status["error"]
    provider.rerank.return_value = [SimpleNamespace(index=0, relevance_score=0.9)]
    provider.rerank.side_effect = None
    assert len(await bridge.rerank("问题", rows)) == 2
    assert app.state.get("rerank_status")["status"] == "ok"
    db.close()


@pytest.mark.asyncio
async def test_failed_upload_keeps_link_and_pending_then_retry(tmp_path):
    db = Database(tmp_path)
    app = SimpleNamespace(database=db, state=StateRepository(db))
    bridge = KnowledgeBridge(SimpleNamespace(), app)
    helper = SimpleNamespace(kb=SimpleNamespace(kb_id="kb"),
                             upload_document=AsyncMock(side_effect=RuntimeError("offline")))
    bridge.helpers["memories"] = helper
    db.connection.execute("INSERT INTO memories(id,kind,text,created,updated) VALUES(1,'topic','new',1,1)")
    db.connection.execute("INSERT INTO knowledge_links VALUES('memories',1,'kb','old','old-hash',1)")
    db.connection.commit()
    await bridge.sync()
    assert db.rows("SELECT * FROM knowledge_links")[0]["doc_id"] == "old"
    assert "offline" in bridge.status()["pending"][0]["error"]
    helper.upload_document = AsyncMock(return_value=SimpleNamespace(doc_id="new-doc"))
    helper.get_chunks_by_doc_id = AsyncMock(return_value=[{"content": "new"}])
    helper.delete_document = AsyncMock()
    bridge.context = SimpleNamespace(kb_manager=SimpleNamespace(get_kb=AsyncMock(return_value=helper)))
    await bridge.sync()
    assert not bridge.status()["pending"]
    assert db.rows("SELECT * FROM knowledge_links")[0]["digest"] == hashlib.sha256(b"new").hexdigest()
    helper.delete_document.assert_awaited_once_with("old")
    assert app.state.get("knowledge_authority") == "astrbot_kb"
    db.close()


@pytest.mark.asyncio
async def test_canonical_restore_and_pending_edits_survive(tmp_path):
    db = Database(tmp_path)
    bridge = KnowledgeBridge(SimpleNamespace(), SimpleNamespace(database=db, state=StateRepository(db)))
    with db.transaction() as con:
        con.execute("INSERT INTO memories(id,kind,text,created,updated) VALUES(1,'topic','cached',1,1)")
        con.execute("INSERT INTO knowledge_links VALUES('memories',1,'kb','doc','hash',1)")
        con.execute("DELETE FROM knowledge_outbox")
    bridge.helpers["memories"] = SimpleNamespace(get_chunks_by_doc_id=AsyncMock(
        return_value=[{"content": "canonical"}]))
    await bridge.restore_projection()
    assert db.rows("SELECT text FROM memories")[0]["text"] == "canonical"
    assert not bridge.status()["pending"]
    with db.transaction() as con:
        con.execute("UPDATE memories SET text='pending edit' WHERE id=1")
    await bridge.restore_projection()
    assert db.rows("SELECT text FROM memories")[0]["text"] == "pending edit"
    assert len(bridge.status()["pending"]) == 1
    bridge.app.state.put("knowledge_authority", "astrbot_kb")
    assert bridge.resolve([{"id": 1, "text": "pending edit"}])[0]["text"] == "canonical"
    assert bridge.resolve([{"id": 2, "text": "not uploaded"}]) == []
    restarted = KnowledgeBridge(SimpleNamespace(), bridge.app)
    assert restarted.resolve([{"id": 1, "text": "pending edit"}])[0]["text"] == "canonical"
    restarted.helpers["memories"] = SimpleNamespace(
        get_chunks_by_doc_id=AsyncMock(side_effect=RuntimeError("KB offline")))
    await restarted.restore_projection()
    assert restarted.resolve([{"id": 1, "text": "pending edit"}])[0]["text"] == "canonical"
    assert "KB offline" in restarted.status()["projection_errors"][0]
    db.close()
