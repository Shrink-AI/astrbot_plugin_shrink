"""Shared test data: a known bot and a person with nicknames, written like an admin would."""

import json
import time

BOT = "qq:9000000002"
PERSON = "qq:9000000001"


def seed_known(app):
    now = time.time()
    for uid, name, kind in ((BOT, "小机", "bot"), (PERSON, "成员甲", "human")):
        platform, _, account = uid.partition(":")
        with app.database.transaction() as connection:
            connection.execute(
            "INSERT OR IGNORE INTO people(id,platform,account,name,aliases,kind,first_seen,updated) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (uid, platform, account, name, json.dumps([name], ensure_ascii=False), kind, now, now),
            )
    for name in ("小甲", "白", "阿甲"):
        app.people_repository.add_nickname(PERSON, name, origin="admin", confidence=1, verified=1)
