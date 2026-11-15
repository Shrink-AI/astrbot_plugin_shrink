"""Offline upgrade uses synthetic records and the original release DDL."""
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest


@pytest.fixture
def legacy(tmp_path):
    path = tmp_path / "shrink.db"
    with closing(sqlite3.connect(path)) as con, con:
        con.executescript(Path(__file__).with_name("fixtures").joinpath("legacy_3f9112e.sql").read_text("utf-8"))
        con.execute("INSERT INTO kv VALUES('settings',?)", (json.dumps({
            "context_compress_tokens": 128000, "context_retain_ratio": .65,
            "context_budget": 4000, "removed_synthetic_setting": {"keep": 1}}),))
        con.execute("INSERT INTO kv VALUES('context_prefix:qq:group:1','[0, \"\"]')")
        con.execute("INSERT INTO people VALUES('qq:100','qq','100','合成人物','[]','human',1,1)")
        con.execute("INSERT INTO messages(id,user_id,source,text,created) "
                    "VALUES('qq:bot:qq:group:1:m1','qq:100','qq:group:1','合成消息',1)")
        con.execute("INSERT INTO memories(id,kind,text,created,updated) VALUES(1,'fact','合成事实',1,1)")
        for i, status in enumerate(("completed", "interrupted", "needs_attention", "waiting", "awaiting_input")):
            con.execute("INSERT INTO operation_tasks(id,request,session,sender,status,started,provider,delivery)"
                        " VALUES(?,?,?,?,?,1,'synthetic','platform_accepted')",
                        (str(i), "合成任务", "bot:GroupMessage:1", "100", status))
        con.execute("CREATE TABLE notification_receipts(id TEXT PRIMARY KEY,state TEXT NOT NULL,"
                    "text TEXT NOT NULL DEFAULT '',created REAL NOT NULL,error TEXT NOT NULL DEFAULT '')")
        con.execute("INSERT INTO notification_receipts VALUES('receipt','platform_accepted','合成回执',1,'')")
        con.execute("INSERT INTO prompt_versions VALUES(1,'chat.persona','合成人格','',1)")
        con.execute("INSERT INTO prompt_versions VALUES(2,'outbound.compose',?,'',2)",
                    ("合成规则：保留必要事实。\n" +
                     "\n".join("{{" + key + "}}" for key in
                               ("kind", "request", "instruction", "facts", "context", "recent")),))
        con.execute("INSERT INTO prompt_versions VALUES(3,'task-style-id','损坏等价内容','','wrong type')")
        con.execute("INSERT INTO prompt_versions VALUES(4,'chat.acceptance_policy','合成旧政策','',4)")
    return path


def test_upgrade_preserves_records_maps_settings_and_tasks_and_is_idempotent(legacy):
    from shrink.services.upgrades import upgrade_database
    first = upgrade_database(legacy)
    assert first["status"] == "ok" and first["integrity"] == ["ok"]
    assert first["prompt_repair"]["rejected_ids"] == [3]
    assert all(value["consistent"] for value in first["records"].values())
    with closing(sqlite3.connect(legacy)) as con, con:
        settings = json.loads(con.execute("SELECT value FROM kv WHERE key='settings'").fetchone()[0])
        assert settings["history_compress_tokens"] == 128000 and settings["history_retain_ratio"] == .65
        assert "removed_synthetic_setting" not in settings and "context_compress_tokens" not in settings
        states = dict(con.execute("SELECT id,status FROM operation_tasks"))
        assert states == {"0": "completed", "1": "needs_attention", "2": "needs_attention",
                          "3": "needs_attention", "4": "waiting_user"}
        assert con.execute("SELECT count(*) FROM task_delivery").fetchone()[0] == 5
        assert con.execute("SELECT text FROM prompt_versions WHERE key='chat.persona'").fetchone()[0] == "合成人格"
        assert con.execute("SELECT text FROM prompt_versions WHERE key='outbound.contract' "
                           "ORDER BY id DESC LIMIT 1").fetchone()[0].startswith("只在")
        assert con.execute("SELECT count(*) FROM prompt_versions WHERE key='outbound.compose'").fetchone()[0] == 2
    before = hashlib.sha256(legacy.read_bytes()).hexdigest()
    second = upgrade_database(legacy)
    assert second["status"] == "unchanged"
    assert hashlib.sha256(legacy.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("failure_stage", ["backed_up", "repaired", "migrated", "verified"])
def test_failure_restores_exact_backup_and_can_retry(legacy, failure_stage):
    from shrink.services.upgrades import upgrade_database
    with closing(sqlite3.connect(legacy)) as con:
        original = con.execute("SELECT * FROM operation_tasks ORDER BY id").fetchall()
    def fail(stage):
        if stage == failure_stage:
            raise RuntimeError("synthetic injected failure")
    with pytest.raises(RuntimeError):
        upgrade_database(legacy, checkpoint=fail)
    backup = next(legacy.parent.glob("shrink.before-upgrade-*.db"))
    assert legacy.read_bytes() == backup.read_bytes()
    with closing(sqlite3.connect(legacy)) as con:
        assert con.execute("SELECT * FROM operation_tasks ORDER BY id").fetchall() == original
    assert upgrade_database(legacy)["status"] == "ok"


def test_unknown_future_setting_is_archived_on_startup(legacy):
    from shrink.application import Application
    from shrink.services.upgrades import upgrade_database
    upgrade_database(legacy)
    with closing(sqlite3.connect(legacy)) as con, con:
        settings = json.loads(con.execute("SELECT value FROM kv WHERE key='settings'").fetchone()[0])
        settings["deleted_in_future"] = "preserve in archive"
        con.execute("UPDATE kv SET value=? WHERE key='settings'", (json.dumps(settings),))
    async def forbidden(*args, **kwargs):
        pytest.fail("offline upgrade called a model")
    app = Application(legacy.parent, forbidden, forbidden, forbidden)
    try:
        assert "deleted_in_future" not in app.state.get("settings")
        assert app.database.rows("SELECT * FROM upgrade_archive WHERE category='settings_unknown'")
    finally:
        app.scheduling.close()
        app.database.close()


def test_prompt_split_keeps_archived_original_and_unknown_key_history(legacy):
    from shrink.services.upgrades import upgrade_database
    upgrade_database(legacy)
    from shrink.persistence.database import Database
    from shrink.persistence.prompts import PromptRepository
    from shrink.services.prompts import PromptService
    db = Database(legacy.parent)
    try:
        service = PromptService(PromptRepository(db))
        history = service.history("outbound.compose")
        assert history[0]["reset"] and "合成规则" in history[1]["text"]
        assert service.archived()[0]["key"] == "chat.acceptance_policy"
    finally:
        db.close()


def test_identity_and_content_verification_detects_same_count_tampering():
    from shrink.services.upgrades import verify_records
    with pytest.raises(ValueError, match="messages"):
        verify_records({"messages": [{"id": "1", "text": "original"}]},
                       {"messages": [{"id": "1", "text": "changed"}]}, {})


def test_pollution_only_cleans_unambiguous_bot_draft_tail(legacy):
    from shrink.services.upgrades import upgrade_database
    original = "有效结果\n本轮要求：合成草稿\n这次回：合成限制"
    with closing(sqlite3.connect(legacy)) as con, con:
        for identity, bot in (("bot-draft", 1), ("user-quote", 0)):
            con.execute("INSERT INTO messages(id,user_id,source,text,is_bot,created)"
                        " VALUES(?,'qq:100','qq:group:1',?,?,2)", (identity, original, bot))
        con.execute("INSERT INTO digests(source,start,end,count,summary,created)"
                    " VALUES('qq:group:1',1,3,3,'不能仅凭覆盖时间判定污染的摘要',3)")
    report = upgrade_database(legacy)
    assert report["pollution"]["corrected"] == 1
    with closing(sqlite3.connect(legacy)) as con:
        assert con.execute("SELECT text FROM messages WHERE id='bot-draft'").fetchone()[0] == "有效结果"
        assert con.execute("SELECT text FROM messages WHERE id='user-quote'").fetchone()[0] == original
        assert con.execute("SELECT count(*) FROM digests").fetchone()[0] == 1


def test_prompt_cleanup_acceptance_and_obsolete_instructions():
    from shrink.prompts import CATALOG
    contract = CATALOG["outbound.contract"]["text"]
    assert "受理" in contract and "参数" in contract
    assert "重复受理保持安静" in contract
    assert "说明谁去查、查什么" not in contract
    assert "一次请求只通知一次" not in CATALOG["tasks.workbench_guide"]["text"]
    assert "只做语法检查" not in CATALOG["tasks.workbench_guide"]["text"]
    assert "标了“当前”" not in CATALOG["chat.turn.current"]["text"]
    assert "只接受原会话、原创建者" not in CATALOG["tasks.orchestrate.workflow"]["text"]


def test_migrated_contract_keeps_custom_paragraphs_and_new_acceptance(legacy):
    from shrink.prompts import CATALOG
    from shrink.services.upgrades import upgrade_database
    custom = ["用石英当前人格说话，保留合成人格定制。", "默认是熟人聊天，保留合成沟通定制。"]
    with closing(sqlite3.connect(legacy)) as con, con:
        old = con.execute("SELECT text FROM prompt_versions WHERE id=2").fetchone()[0]
        con.execute("UPDATE prompt_versions SET text=? WHERE id=2",
                    ("\n".join(custom + old.splitlines()[1:]),))
    upgrade_database(legacy)
    with closing(sqlite3.connect(legacy)) as con:
        contract = con.execute("SELECT text FROM prompt_versions WHERE key='outbound.contract' "
                               "ORDER BY id DESC LIMIT 1").fetchone()[0]
        assert all(line in contract.splitlines() for line in custom)
        assert contract.splitlines()[-2:] == CATALOG["outbound.contract"]["text"].splitlines()[-2:]
        assert "可以说有结果再回" not in contract
        assert "不介绍同构体或派工过程" in contract
        assert con.execute("SELECT text FROM prompt_versions WHERE key='chat.persona'").fetchone()[0] == "合成人格"


def test_damaged_prompt_index_is_logically_rebuilt(legacy):
    from shrink.services.upgrades import upgrade_database
    with closing(sqlite3.connect(legacy)) as con:
        page_size = con.execute("PRAGMA page_size").fetchone()[0]
        root = con.execute("SELECT rootpage FROM sqlite_master WHERE name='prompt_versions_key'").fetchone()[0]
    data = bytearray(legacy.read_bytes())
    offset = (root - 1) * page_size
    assert data[offset] == 10  # A leaf index page, not a table page.
    data[offset + 3:offset + 5] = b"\0\0"
    legacy.write_bytes(data)
    with closing(sqlite3.connect(legacy)) as con:
        assert con.execute("PRAGMA integrity_check").fetchall() != [("ok",)]
    result = upgrade_database(legacy)
    assert result["prompt_repair"]["logical_rebuild"]
    assert result["integrity"] == ["ok"]


def test_completed_startup_only_adds_missing_default_without_scan_or_backup(legacy, monkeypatch):
    from shrink.application import Application
    from shrink.persistence import upgrades as persistence
    from shrink.services import upgrades
    upgrades.upgrade_database(legacy)
    with closing(sqlite3.connect(legacy)) as con, con:
        settings = json.loads(con.execute("SELECT value FROM kv WHERE key='settings'").fetchone()[0])
        default = settings.pop("history_retain_ratio")
        con.execute("UPDATE kv SET value=? WHERE key='settings'", (json.dumps(settings),))
        text = "正文\n本轮要求：升级后用户保留的内容\n这次回：不能在将来启动时剪掉"
        con.execute("UPDATE messages SET text=?,is_bot=1", (text,))
        messages = con.execute("SELECT * FROM messages").fetchall()
    backups = set(legacy.parent.glob("*.db"))
    def forbidden(*args, **kwargs):
        pytest.fail("completed startup performed a full scan, repair, or backup")
    for module, name in ((persistence, "snapshot_records"), (persistence, "integrity"),
                         (upgrades, "pollution_plan"), (upgrades, "backup_database")):
        monkeypatch.setattr(module, name, forbidden)
    app = Application(legacy.parent, forbidden, forbidden, forbidden)
    try:
        restored = app.state.get("settings")
        assert restored == {**settings, "history_retain_ratio": default}
        assert [tuple(r) for r in app.database.connection.execute("SELECT * FROM messages")] == messages
        assert set(legacy.parent.glob("*.db")) == backups
    finally:
        app.scheduling.close()
        app.database.close()


def test_unknown_key_after_completed_upgrade_does_not_repeat_data_migration(legacy, monkeypatch):
    from shrink.services import upgrades
    upgrades.upgrade_database(legacy)
    with closing(sqlite3.connect(legacy)) as con, con:
        settings = json.loads(con.execute("SELECT value FROM kv WHERE key='settings'").fetchone()[0])
        settings["removed_after_downgrade"] = {"synthetic": True}
        con.execute("UPDATE kv SET value=? WHERE key='settings'", (json.dumps(settings),))
    backups = set(legacy.parent.glob("*.db"))
    def forbidden(*args, **kwargs):
        pytest.fail("settings cleanup repeated data migration")
    monkeypatch.setattr(upgrades, "pollution_plan", forbidden)
    monkeypatch.setattr(upgrades, "backup_database", forbidden)
    upgrades.startup_upgrade(legacy)
    assert set(legacy.parent.glob("*.db")) == backups
    with closing(sqlite3.connect(legacy)) as con:
        assert con.execute("SELECT value FROM upgrade_archive WHERE identity='removed_after_downgrade'").fetchone()


def test_completed_upgrade_repairs_restored_old_compose_without_cleaning_messages(legacy, monkeypatch):
    from shrink.services import upgrades
    upgrades.upgrade_database(legacy)
    with closing(sqlite3.connect(legacy)) as con, con:
        shell = "\n".join("{{" + key + "}}" for key in
                          ("kind", "request", "instruction", "facts", "context", "recent"))
        con.execute("INSERT INTO prompt_versions(key,text,note,created) VALUES('outbound.compose',?,'',10)",
                    ("合成规则：保留恢复的定制。\n" + shell,))
        text = "合成正文\n本轮要求：完成升级后的草稿\n这次回：保留原文"
        con.execute("UPDATE messages SET text=?,is_bot=1", (text,))
    def forbidden(*args, **kwargs):
        pytest.fail("structural repair repeated pollution correction")
    monkeypatch.setattr(upgrades, "pollution_plan", forbidden)
    upgrades.upgrade_database(legacy)
    with closing(sqlite3.connect(legacy)) as con:
        assert con.execute("SELECT text FROM prompt_versions WHERE key='outbound.compose' "
                           "ORDER BY id DESC LIMIT 1").fetchone()[0] is None
        assert all(row[0] == text for row in con.execute("SELECT text FROM messages"))


@pytest.mark.parametrize("custom", ["用石英当前人格说话，保留合成定制。", "默认是熟人聊天，保留合成定制。"])
def test_prompt_migration_appends_custom_when_default_paragraph_was_reworded(legacy, monkeypatch, custom):
    from shrink.persistence.database import Database
    from shrink.persistence.prompts import PromptRepository
    from shrink.services.upgrades import CATALOG, migrate_prompts
    with closing(sqlite3.connect(legacy)) as con, con:
        shell = "\n".join("{{" + key + "}}" for key in
                          ("kind", "request", "instruction", "facts", "context", "recent"))
        con.execute("UPDATE prompt_versions SET text=? WHERE id=2", (custom + "\n" + shell,))
    monkeypatch.setitem(CATALOG, "outbound.contract", {**CATALOG["outbound.contract"], "text": "改写后的默认契约。"})
    db = Database(legacy.parent)
    try:
        repository = PromptRepository(db)
        migrate_prompts(repository)
        assert custom in repository.active()["outbound.contract"]["text"]
    finally:
        db.close()
