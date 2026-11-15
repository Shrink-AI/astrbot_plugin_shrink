"""Host-free upgrade rules with cheap startup and full offline verification."""
import asyncio
import json
import math
import re
import time
import uuid
from pathlib import Path

from ..config import Settings
from ..persistence.database import Database, StateRepository
from ..persistence.upgrades import (
    UpgradeStore, backup_database, digest, file_hash, integrity, inspect_upgrade,
    logical_rebuild, prepare_migration_temp, probe_data, restore_database, retained_tables,
    snapshot_records, archive_settings,
)
from ..prompts import CATALOG, placeholders
from .reply_text import PROMPT_LEAK, strip_prompt_deliberation
from .task_states import LEGACY, TRANSITIONS, TaskStates

VERSION = "continuous_history_upgrade_v1"
SETTING_NAMES = {"context_compress_tokens": "history_compress_tokens",
                 "context_retain_ratio": "history_retain_ratio"}


def settings_upgrade(stored):
    value = dict(stored or {})
    mapped = {}
    for old, new in SETTING_NAMES.items():
        if old in value:
            mapped[old] = value.pop(old)
            value.setdefault(new, mapped[old])
    unknown = {k: v for k, v in value.items() if k not in Settings.model_fields}
    clean = Settings(**{k: v for k, v in value.items() if k not in unknown}).model_dump()
    return clean, mapped, unknown


def recognizable_prompt(row):
    return (isinstance(row["id"], int) and isinstance(row["key"], str)
            and re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+", row["key"]) is not None
            and isinstance(row["created"], (float, int)) and math.isfinite(row["created"])
            and (row["text"] is None or isinstance(row["text"], str))
            and isinstance(row["note"], str))


def split_compose(value):
    """Only split the old, known six-field shell; never drop unfamiliar custom text."""
    expected = {"kind", "request", "instruction", "facts", "context", "recent"}
    lines = value.splitlines()
    boundary = next((i for i, line in enumerate(lines) if placeholders(line)), len(lines))
    rules, shell = "\n".join(lines[:boundary]).strip(), "\n".join(lines[boundary:]).strip()
    if not rules or set(placeholders(shell)) != expected:
        return None
    return rules


def migrate_prompts(repository):
    changed = []
    active = repository.active()
    old = active.get("outbound.compose")
    if old and (rules := split_compose(old["text"])):
        # User prose remains an override. Common execution clauses are represented once by
        # the current contract; only independent style/custom sentences need appending.
        contract = CATALOG["outbound.contract"]["text"]
        # A customized paragraph replaces its corresponding default paragraph; other
        # sentences are deduplicated, rather than dropping entire lines containing "事实".
        paragraphs = contract.splitlines()
        custom = []
        for line in rules.splitlines():
            if line.startswith("派工回执"):
                continue  # Current acceptance/continuation clause owns these semantics.
            if "当前人格" in line:
                index = next((i for i, p in enumerate(paragraphs) if "当前人格" in p), None)
                if index is None:
                    custom.append(line)
                else:
                    paragraphs[index] = line
                continue
            if line.startswith("默认是熟人聊天"):
                index = next((i for i, p in enumerate(paragraphs) if p.startswith("默认是熟人聊天")), None)
                if index is None:
                    custom.append(line)
                else:
                    paragraphs[index] = line
                continue
            known = {s.strip() for p in paragraphs for s in re.split(r"[。\n]", p) if s.strip()}
            custom.extend(s.strip() + "。" for s in re.split(r"[。\n]", line)
                          if s.strip() and s.strip() not in known)
        contract = "\n".join(paragraphs)
        if custom:
            contract += "\n" + "\n".join(custom)
        repository.save("outbound.contract", contract, "升级：从完整 compose 拆分表达契约")
        repository.save("outbound.compose", None, "升级：资料壳恢复默认；原全文保留在上一版本")
        changed.extend(["outbound.compose", "outbound.contract"])
    # Unknown keys stay in the existing history, exposed by PromptService.archived().
    return {"changed_keys": changed,
            "archived_keys": sorted(k for k in active if k not in CATALOG),
            "persona_preserved": "chat.persona" in active}


def pollution_plan(records):
    corrections = {}
    counts = {"prompt_draft": 0, "bare_json": 0, "traceback": 0, "think_tag": 0,
              "ambiguous_prompt_marker": 0}
    for row in records:
        if not row["is_bot"]:
            continue
        text = row["text"]
        clean = strip_prompt_deliberation(text)
        if clean != text and len({m.group() for m in PROMPT_LEAK.finditer(text)}) >= 2:
            corrections[row["id"]] = clean
            counts["prompt_draft"] += 1
        elif PROMPT_LEAK.search(text):
            counts["ambiguous_prompt_marker"] += 1
        if re.search(r"Traceback \(most recent call last\)", text):
            counts["traceback"] += 1
        if re.search(r"</?(?:think|thinking|analysis|reasoning)>", text, re.I):
            counts["think_tag"] += 1
        try:
            if isinstance(json.loads(text), (dict, list)):
                counts["bare_json"] += 1
        except (ValueError, TypeError):
            pass
    return corrections, counts


def initialize_repositories(db):
    from ..persistence.authorizations import Authorizations
    from ..persistence.clarifications import Clarifications
    from ..persistence.compositions import Compositions
    from ..persistence.decisions import DecisionRepository
    from ..persistence.history import HistoryRepository
    from ..persistence.inbox import InboxRepository
    from ..persistence.maintenance import MaintenanceRepository
    from ..persistence.notifications import NotificationRepository
    from ..persistence.outbox import OutboxRepository
    from ..persistence.task_inputs import TaskInputsRepository
    from ..persistence.tasks import Tasks
    from ..persistence.turn_traces import TurnTraceRepository
    for factory in (InboxRepository, Authorizations, Clarifications, Compositions, DecisionRepository,
                    MaintenanceRepository, NotificationRepository, OutboxRepository, TurnTraceRepository):
        factory(db)
    history_started = time.perf_counter()
    history = HistoryRepository(db)
    history_seconds = time.perf_counter() - history_started
    TaskInputsRepository(db)
    tasks = Tasks(db)
    states = TaskStates(tasks)
    states.migrate()
    states.recover()
    return history, tasks, history_seconds


def verify_records(before, after, corrections, unclosed_tasks=()):
    reports = {}
    for name, rows in before.items():
        actual = {r["id"]: r for r in after[name]}
        expected = []
        observed = []
        for row in rows:
            want = dict(row)
            if name == "messages" and row["id"] in corrections:
                want["text"] = corrections[row["id"]]
            if name == "operation_tasks":
                old = want["status"]
                want["status"] = LEGACY.get(old, old if old in TRANSITIONS else "needs_attention")
                if row["id"] in unclosed_tasks and old in {
                        "interrupted", "timed_out", "testing", "running", "waiting"}:
                    want["status"] = "reconciling"
                elif old in {"running", "waiting"}:
                    want["status"] = "needs_attention"
                if want["status"] != old:
                    want["finished"] = None if want["status"] not in {"completed", "failed", "cancelled"} else (
                        actual.get(row["id"], {}).get("finished"))
            got = actual.get(row["id"])
            expected.append(digest(want))
            observed.append(digest({k: got.get(k) for k in want}) if got else "missing")
        reports[name] = {"before": len(rows), "after": len(after[name]), "checked": len(rows),
                         "expected_sha256": digest(sorted(expected)),
                         "observed_sha256": digest(sorted(observed)), "consistent": expected == observed}
        if not reports[name]["consistent"]:
            raise ValueError("record identity/content mismatch: " + name)
    return reports


async def query_checks(db, history, tasks):
    from ..persistence.messages import MessageRepository
    from ..persistence.notifications import NotificationRepository
    from .history import ConversationHistory
    from .recent import RecentInteractions
    state = StateRepository(db)
    messages = MessageRepository(db, state)
    async def no_model(*args, **kwargs):
        raise RuntimeError("offline verification never invokes a model")
    service = ConversationHistory(history, messages, no_model, lambda *args: None)
    inputs = probe_data(db)
    sessions = inputs["sessions"]
    events, roles = 0, 0
    for session in sessions:
        rows = history.events(session["id"])
        events += len(rows)
        # A large verification budget reads originals and cannot call compression.
        contexts = await service.contexts(session["id"], 10**12)
        roles += len(contexts)
    people = inputs["people"]
    recent = RecentInteractions(messages)
    recent_count = sum(recent.find(row["id"], "qq:group:offline-check", "刚才",
                                  now=inputs["now"]).found for row in people)
    receipts = NotificationRepository(db)
    receipt_rows = inputs["receipts"]
    for row in receipt_rows:
        if receipts.get(row["id"]) != row:
            raise ValueError("notification query mismatch")
    task_rows = tasks.list()
    for row in task_rows:
        if tasks.task(row["id"]) != row:
            raise ValueError("task query mismatch")
    states = TaskStates(tasks)
    resume_checks = 0
    resume_blocked = 0
    class ProbeRollback(Exception):
        pass
    for row in task_rows:
        if row["status"] not in {"waiting_user", "needs_attention", "reconciling"}:
            continue
        try:
            states.capacity(row["kind"], row["id"])
        except ValueError:
            resume_blocked += 1
            continue
        try:
            with tasks.transaction():
                states.resume(row["id"])
                restored = tasks.task(row["id"])
                if restored["id"] != row["id"] or (restored["session"], restored["sender"]) != (
                        row["session"], row["sender"]):
                    raise ValueError("resume identity/scope mismatch")
                if restored["status"] not in {"queued", "running"}:
                    raise ValueError("resume state mismatch")
                raise ProbeRollback()
        except ProbeRollback:
            resume_checks += 1
        if tasks.task(row["id"]) != row:
            raise ValueError("resume probe was not rolled back")
    from ..persistence.memory import MemoryRepository
    memory = MemoryRepository(db, state)
    memory_rows = inputs["memories"]
    # Exercise the repository's current record lookup, without embedding or a provider.
    for row in memory_rows[:3]:
        if memory.get(row["id"]) is None:
            raise ValueError("memory lookup mismatch")
    fts_count = 0
    from ..text_rules import grams
    for row in memory_rows[:3]:
        fts_count += len(memory.fts(grams(row["text"])))
    from .memory import MemoryService, Query
    async def no_vector(text):
        return None
    recall = MemoryService(memory, state, no_vector, lambda *args: None)
    recalled = 0
    with db.transaction() as con:
        for row in memory_rows[:3]:
            _, chosen = await recall.recall_for(Query(row["text"], row["source"], speaker=row["subject"]))
            recalled += len(chosen)
        con.rollback()  # Probe reads must not increment live recall statistics.
    return {"sessions": len(sessions), "events": events, "rendered_roles": roles,
            "recent_people": len(people), "recent_found": recent_count,
            "tasks": len(task_rows), "task_statuses": {s: sum(r["status"] == s for r in task_rows)
                                                     for s in sorted({r["status"] for r in task_rows})},
            "receipts": len(receipt_rows), "memory_lookups": min(3, len(memory_rows)),
            "fts_matches": fts_count, "recalled": recalled, "resume_checks_rolled_back": resume_checks,
            "resume_blocked_by_capacity": resume_blocked}


def upgrade_database(path, *, checkpoint=lambda stage: None, verify=True):
    """Caller owns an offline database. Failure restores this invocation's backup."""
    path = Path(path).resolve()
    if not path.exists():
        return {"status": "new"}
    started = time.perf_counter()
    source = inspect_upgrade(path, VERSION, full=verify)
    if source is None:
        return {"status": "new"}
    stored = source["settings"]
    settings, mapped, unknown = settings_upgrade(stored)
    previous = source["previous"]
    prompt_rows = source["prompts"]
    accepted = [r for r in prompt_rows if recognizable_prompt(r)]
    rejected = [r for r in prompt_rows if not recognizable_prompt(r)]
    health = source["integrity"]
    compose = max((r for r in accepted if r["key"] == "outbound.compose"), key=lambda r: r["id"], default=None)
    structure_current = (source["current_history"] and not source["old_delivery"] and not rejected
                         and health == ["ok"]
                         and not (compose and compose["text"] and split_compose(compose["text"])))
    if previous and structure_current:
        if mapped or unknown:
            archive_settings(path, stored, {k: (SETTING_NAMES[k], v) for k, v in mapped.items()}, unknown)
        if not verify:
            return {"status": "settings_updated" if mapped or unknown else "unchanged", "version": VERSION}
        report = {**previous, "status": "unchanged", "integrity": source["integrity"],
                  "database_sha256": file_hash(path)}
        if verify:
            from ..persistence.history import HistoryRepository
            from ..persistence.tasks import Tasks
            db = Database(path.parent, filename=path.name, initialize=False)
            try:
                prepare_migration_temp(db)
                report["queries"] = asyncio.run(query_checks(db, HistoryRepository(db), Tasks(db)))
            finally:
                db.close()
            if file_hash(path) != report["database_sha256"]:
                raise ValueError("repeat preflight changed database")
        return report
    if not verify:
        source = inspect_upgrade(path, VERSION)
        health = source["integrity"]
    before = source["records"]
    backup = path.with_name(path.stem + ".before-upgrade-" + uuid.uuid4().hex[:12] + ".db")
    backup_hash = backup_database(path, backup)
    report = {"status": "ok", "version": VERSION, "backup": str(backup), "backup_sha256": backup_hash,
              "prompt_repair": {"before": len(prompt_rows), "kept": len(accepted),
                                "rejected_ids": [r["id"] for r in rejected],
                                "rejected_sha256": digest(rejected), "logical_rebuild": False},
              "settings": {"mapped": sorted(mapped), "archived_unknown": sorted(unknown)}}
    db = None
    try:
        checkpoint("backed_up")
        if health != ["ok"] or rejected:
            logical_rebuild(path, accepted)
            report["prompt_repair"]["logical_rebuild"] = True
        checkpoint("repaired")
        db = Database(path.parent, filename=path.name)
        prepare_migration_temp(db)
        archive = UpgradeStore(db)
        for category, values in (("settings_mapped", mapped), ("settings_unknown", unknown)):
            for key, value in values.items():
                archive.archive(category, key, value)
        archive.replace_settings(settings)
        StateRepository(db)
        report["archived_context_prefixes"] = archive.remove_prefixes()
        # Preserve old migration logs and plugin-owned tables until their owner has
        # independently confirmed a complete copy. Keeping these does not activate them.
        report["retained_tables"] = retained_tables(db)
        corrections, pollution = pollution_plan(before["messages"]) if not previous else ({}, {})
        for identity, text in corrections.items():
            archive.correct_message(identity, text)
        report["pollution"] = previous["pollution"] if previous and "pollution" in previous else {
                               "counts": pollution, "corrected": len(corrections),
                               "identities_sha256": digest(sorted(corrections)),
                               "uncertain_dependencies": archive.dependent_records(corrections)}
        from ..persistence.prompts import PromptRepository
        report["prompts"] = migrate_prompts(PromptRepository(db))
        history, tasks, history_seconds = initialize_repositories(db)
        report["initial_history_seconds"] = round(history_seconds, 6)
        checkpoint("migrated")
        after = snapshot_records(db.connection)
        # delivery left the business table but remains in the same repository's joined query.
        after["operation_tasks"] = tasks.list()
        from .task_recovery import reconciliation
        unclosed = {row["id"] for row in before["operation_tasks"] if reconciliation(
            [event for event in source["task_events"] if event["task_id"] == row["id"]])}
        report["records"] = verify_records(before, after, corrections, unclosed)
        if verify:
            report["queries"] = asyncio.run(query_checks(db, history, tasks))
        report["integrity"] = integrity(db.connection)
        if report["integrity"] != ["ok"]:
            raise ValueError("post-upgrade integrity check failed")
        checkpoint("verified")
        report["seconds"] = round(time.perf_counter() - started, 6)
        archive.complete(VERSION, report)
        db.close()
        db = None
        report["database_sha256"] = file_hash(path)
        return report
    except BaseException as exc:
        if db is not None:
            db.close()
        try:
            restore_database(path, backup)
        except BaseException as restore_error:
            restore_error.upgrade_report = {"status": "failed_restore", "backup": str(backup),
                                            "backup_sha256": backup_hash}
            raise
        exc.upgrade_report = {"status": "failed_restored", "backup": str(backup),
                              "backup_sha256": backup_hash}
        raise


def startup_upgrade(path):
    # Application can be constructed inside an event loop; startup does not run
    # async query probes, enqueue notifications, or start any executor.
    return upgrade_database(path, verify=False)


def record_new_database(db):
    UpgradeStore(db).complete(VERSION, {"version": VERSION, "status": "new", "prompts": {
        "changed_keys": [], "archived_keys": [], "persona_preserved": False}})
