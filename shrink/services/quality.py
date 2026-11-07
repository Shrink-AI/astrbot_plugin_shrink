"""Write gates for extracted knowledge. Everything a model proposes passes through here first."""

import datetime
import json
import re
from dataclasses import dataclass, field

from ..config import BOT_NAME
from ..text_rules import GENERIC_ADDRESS, INSULT, RELATIVE_DAY, SELF_IDENTITY, normalize

CERTAINTY_BASE = {"stated": 0.75, "inferred": 0.5, "hearsay": 0.45}
ACTIVE_AT = 0.6
MAX_PER_DIGEST = 12
MAX_PER_SUBJECT = 3
MAX_PER_DAY = 8
PRIVATE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)|\d{17}[\dXx]|[\w.+-]+@[\w-]+\.\w+|密码|身份证")
URLISH = re.compile(r"https?://|www\.|\d{5,}")


@dataclass
class Batch:
    """A digest batch: short ids the model sees mapped to real rows and identities."""

    lines: dict = field(default_factory=dict)  # m1 -> message row
    refs: dict = field(default_factory=dict)  # p1 -> uid ('self' for the bot)
    bots: set = field(default_factory=set)
    names: dict = field(default_factory=dict)  # uid -> set of names, aliases, nicknames
    now: float = 0.0

    def uid(self, ref):
        return self.refs.get(str(ref or "").strip())


def _rows(item, batch):
    ids = item.get("evidence") if isinstance(item, dict) else None
    if not isinstance(ids, list) or not ids:
        return None
    rows = [batch.lines.get(str(i)) for i in ids]
    return rows if all(rows) else None


def _quote_ok(quote, rows):
    if not isinstance(quote, str):
        return False
    q = normalize(quote)
    for row in rows:
        text = normalize(row["text"])
        if q and q in text and (len(q) >= 4 or q == text):
            return True
    return False


def _human(row, batch):
    return not row.get("is_bot") and row["user_id"] not in batch.bots and row["user_id"] != "self"


def _touches(uid, row, batch):
    """The person is the speaker, or is @-mentioned, replied to, addressed or named in the row."""
    if row["user_id"] == uid or row.get("addressee") == uid:
        return True
    mentions = row.get("mentions") or "[]"
    mentions = json.loads(mentions) if isinstance(mentions, str) else mentions
    if any(m.get("id") == uid for m in mentions if isinstance(m, dict)):
        return True
    quote = row.get("quote") or "{}"
    quote = json.loads(quote) if isinstance(quote, str) else quote
    if isinstance(quote, dict) and quote.get("user_id") and uid.endswith(":" + str(quote["user_id"])):
        return True
    return any(name and len(name) >= 2 and name in row["text"] for name in batch.names.get(uid, ()))


def confidence_for(certainty, rows, batch):
    base = CERTAINTY_BASE.get(certainty, 0.4)
    humans = {r["user_id"] for r in rows if _human(r, batch)}
    if len(rows) >= 2:
        base += 0.1
    if len(humans) >= 2:
        base += 0.1
    return round(min(base, 0.95), 2)


def _valid_until(value):
    if not value or not isinstance(value, str):
        return None
    try:
        day = datetime.datetime.strptime(value.strip()[:10], "%Y-%m-%d")
    except ValueError:
        return None
    return day.timestamp() + 86399


def _text_problem(text, low=4, high=300):
    if not isinstance(text, str) or not low <= len(text.strip()) <= high:
        return "length"
    if SELF_IDENTITY.search(text):
        return "self_identity"
    if RELATIVE_DAY.search(text):
        return "relative_time"
    if PRIVATE.search(text):
        return "private"
    if INSULT.search(text):
        return "insult"
    return None


def _common(item, batch):
    rows = _rows(item, batch)
    if rows is None:
        return None, "evidence"
    if not _quote_ok(item.get("quote"), rows):
        return None, "quote_not_found"
    if not any(_human(r, batch) for r in rows):
        return None, "bot_only_evidence"
    certainty = item.get("certainty", "stated")
    if certainty == "joke":
        return None, "joke"
    if certainty not in CERTAINTY_BASE:
        return None, "certainty"
    return rows, None


def check_memory(item, batch):
    if not isinstance(item, dict) or item.get("kind") not in ("impression", "event"):
        return None, "kind"
    rows, problem = _common(item, batch)
    if problem:
        return None, problem
    subject = batch.uid(item.get("subject"))
    if not subject or subject == "self":
        return None, "subject"
    if not any(_touches(subject, r, batch) for r in rows):
        return None, "attribution"
    if subject in batch.bots and any(
        r["user_id"] == subject and not _human(r, batch) for r in rows
    ) and not any(_human(r, batch) and r["user_id"] != subject for r in rows):
        return None, "bot_self_report"
    problem = _text_problem(item.get("text"))
    if problem:
        return None, problem
    importance = item.get("importance")
    if not isinstance(importance, int) or not 1 <= importance <= 5:
        return None, "importance"
    if importance <= 2:
        return None, "low_importance"
    certainty = item.get("certainty", "stated")
    confidence = confidence_for(certainty, rows, batch)
    return {
        "kind": item["kind"],
        "subject": subject,
        "text": item["text"].strip(),
        "quote": item["quote"].strip(),
        "evidence": [r["id"] for r in rows],
        "participants": [],
        "importance": importance,
        "certainty": certainty,
        "confidence": confidence,
        "status": "active" if confidence >= ACTIVE_AT else "candidate",
        "valid_until": _valid_until(item.get("valid_until")),
        "same_as": item.get("same_as"),
        "replaces": item.get("replaces"),
        "source": rows[0]["source"],
    }, None


def check_topic(item, batch):
    if not isinstance(item, dict):
        return None, "format"
    rows, problem = _common(item, batch)
    if problem:
        return None, problem
    problem = _text_problem(item.get("text"))
    if problem:
        return None, problem
    importance = item.get("importance")
    if not isinstance(importance, int) or not 1 <= importance <= 5:
        return None, "importance"
    if importance <= 2:
        return None, "low_importance"
    refs = item.get("participants") if isinstance(item.get("participants"), list) else []
    participants = []
    for ref in refs:
        uid = batch.uid(ref)
        if uid and (uid == "self" and any(r.get("is_bot") for r in rows) or any(
            _touches(uid, r, batch) for r in rows
        )):
            participants.append(uid)
    participants = list(dict.fromkeys(participants))
    if not [p for p in participants if p != "self"]:
        return None, "participants"
    certainty = item.get("certainty", "stated")
    confidence = confidence_for(certainty, rows, batch)
    return {
        "kind": "topic",
        "subject": "",
        "text": item["text"].strip(),
        "quote": item["quote"].strip(),
        "evidence": [r["id"] for r in rows],
        "participants": participants,
        "importance": importance,
        "certainty": certainty,
        "confidence": confidence,
        "status": "active" if confidence >= ACTIVE_AT else "candidate",
        "valid_until": None,
        "same_as": item.get("same_as"),
        "replaces": None,
        "source": rows[0]["source"],
    }, None


def check_nickname(item, batch):
    if not isinstance(item, dict):
        return None, "format"
    rows = _rows(item, batch)
    if rows is None:
        return None, "evidence"
    if not _quote_ok(item.get("quote"), rows) or not any(_human(r, batch) for r in rows):
        return None, "quote_not_found"
    uid = batch.uid(item.get("person"))
    name = str(item.get("name") or "").strip()
    if not uid or uid == "self":
        return None, "person"
    if not 1 <= len(name) <= 12 or name.lower() in GENERIC_ADDRESS or BOT_NAME in name:
        return None, "name"
    if SELF_IDENTITY.search(name) or URLISH.search(name):
        return None, "name"
    if name not in item.get("quote", "") or not any(name in r["text"] for r in rows):
        return None, "name_not_in_quote"
    if name in batch.names.get(uid, set()):
        return None, "already_known"
    return {
        "user_id": uid,
        "name": name,
        "evidence": [r["id"] for r in rows],
        "confidence": confidence_for("inferred", rows, batch),
        "source": rows[0]["source"],
    }, None


def check_jargon(item, batch):
    if not isinstance(item, dict):
        return None, "format"
    rows = _rows(item, batch)
    if rows is None:
        return None, "evidence"
    term = str(item.get("term") or "").strip()
    if not 2 <= len(term) <= 20 or URLISH.search(term) or BOT_NAME in term:
        return None, "term"
    if not _quote_ok(item.get("quote"), rows) or term not in item.get("quote", ""):
        return None, "quote_not_found"
    if not any(_human(r, batch) for r in rows):
        return None, "bot_only_evidence"
    if any(term in names for names in batch.names.values()):
        return None, "person_name"
    usage = str(item.get("usage") or "").strip()[:80]
    return {
        "term": term,
        "usage": usage,
        "lines": [r["text"][:120] for r in rows],
        "evidence": [r["id"] for r in rows],
        "source": rows[0]["source"],
    }, None


def check_lesson(item, batch):
    if not isinstance(item, dict):
        return None, "format"
    rows = _rows(item, batch)
    if rows is None:
        return None, "evidence"
    if not _quote_ok(item.get("quote"), rows):
        return None, "quote_not_found"
    aimed = [
        r for r in rows
        if _human(r, batch) and (r.get("addressee") == "self" or BOT_NAME in r["text"])
    ]
    if not aimed:
        return None, "not_to_bot"
    text = str(item.get("text") or "").strip()
    if not 4 <= len(text) <= 80 or INSULT.search(text):
        return None, "length"
    return {
        "kind": "lesson",
        "subject": "self",
        "text": text,
        "quote": item["quote"].strip(),
        "evidence": [r["id"] for r in aimed],
        "participants": [],
        "importance": 3,
        "certainty": "stated",
        "confidence": 0.7,
        "status": "active",
        "valid_until": batch.now + 30 * 86400 if batch.now else None,
        "same_as": None,
        "replaces": None,
        "source": aimed[0]["source"],
    }, None


def apply_limits(items, created_today):
    """Cap a digest so one chatty person cannot flood the memory."""
    accepted, rejected, per_subject = [], [], {}
    for item in sorted(items, key=lambda i: (-i["importance"], -i["confidence"])):
        key = item["subject"] or tuple(item["participants"])
        if len(accepted) >= MAX_PER_DIGEST:
            rejected.append((item, "digest_limit"))
        elif per_subject.get(key, 0) >= MAX_PER_SUBJECT:
            rejected.append((item, "subject_limit"))
        elif item["subject"] and created_today(item["subject"]) + per_subject.get(key, 0) >= MAX_PER_DAY:
            rejected.append((item, "daily_limit"))
        else:
            per_subject[key] = per_subject.get(key, 0) + 1
            accepted.append(item)
    return accepted, rejected
