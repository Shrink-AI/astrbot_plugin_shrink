"""Who is who: names, nicknames used by others, person and group cards."""

import json
import time

from ..config import BOT_NAME

RECENT_SPEAKERS = 7 * 86400


class PeopleService:
    def __init__(self, repository, messages):
        self.repository, self.messages = repository, messages
        self._names_cache = {}

    # ---- names -------------------------------------------------------------------------------------
    def names_of(self, uid, scope=None):
        """Display name first, then aliases and active nicknames (deduplicated)."""
        if uid == "self":
            return [BOT_NAME]
        person = self.repository.get(uid)
        names = []
        if person:
            names.append(person["name"])
            names.extend(json.loads(person["aliases"] or "[]"))
        names.extend(n["name"] for n in self.repository.nicknames(uid, scope))
        return [n for n in dict.fromkeys(names) if n]

    def nicknames_of(self, uid, scope=None):
        return [n["name"] for n in self.repository.nicknames(uid, scope)]

    def address_book(self, source):
        """Names (2+ chars) people use in this conversation, mapped to uids. Cached for a minute."""
        cached = self._names_cache.get(source)
        if cached and cached[0] > time.time():
            return cached[1]
        book = {}
        since = time.time() - RECENT_SPEAKERS
        uids = [r["user_id"] for r in self.messages.speakers(source, since)]
        for uid, person in self.repository.many(uids).items():
            for name in [person["name"], *json.loads(person["aliases"] or "[]")]:
                if name and len(name) >= 2 and name != BOT_NAME:
                    book.setdefault(name, uid)
        for nickname in self.repository.nicknames(scope=source):
            if len(nickname["name"]) >= 2:
                book[nickname["name"]] = nickname["user_id"]
        self._names_cache[source] = (time.time() + 60, book)
        return book

    def invalidate(self):
        self._names_cache.clear()

    def mentioned(self, text, source):
        """People referred to by name or nickname anywhere in the text."""
        found = []
        for name, uid in self.address_book(source).items():
            if name in (text or "") and uid not in found:
                found.append(uid)
        return found

    def display(self, uid):
        if uid == "self":
            return BOT_NAME
        person = self.repository.get(uid)
        return person["name"] if person else uid.split(":")[-1]

    # ---- cards -------------------------------------------------------------------------------------
    def card(self, uid, role, scope=None):
        if uid == "self":
            return ""
        person = self.repository.get(uid)
        if not person:
            return ""
        nick = self.nicknames_of(uid, scope)
        aliases = [a for a in json.loads(person["aliases"] or "[]") if a != person["name"]][-3:]
        label = person["name"]
        extra = []
        if person["kind"] == "bot":
            extra.append("机器人")
        if nick:
            extra.append("群里也叫：" + "、".join(nick[:5]))
        if aliases:
            extra.append("以前的昵称：" + "、".join(aliases))
        line = f"{role} {label}" + (f"（{'；'.join(extra)}）" if extra else "")
        profile = self.repository.profile(uid)
        if profile:
            line += f"：{profile['summary']}"
            if profile["relation"]:
                line += f" 和你：{profile['relation']}"
        return line

    def group_card(self, source):
        profile = self.repository.profile(source)
        return f"这个群：{profile['summary']}" if profile else ""

    def roster(self, uids, scope=None):
        """Short references for prompts: p0 is always 石英."""
        refs = {"p0": "self"}
        lines = [f"p0 {BOT_NAME}（就是你自己，机器人）"]
        for index, uid in enumerate([u for u in dict.fromkeys(uids) if u and u != "self"], 1):
            ref = f"p{index}"
            refs[ref] = uid
            person = self.repository.get(uid)
            name = person["name"] if person else uid
            bits = []
            if person and person["kind"] == "bot":
                bits.append("机器人")
            nick = self.nicknames_of(uid, scope)
            if nick:
                bits.append("称呼：" + "、".join(nick[:6]))
            lines.append(f"{ref} {name}" + (f"（{'，'.join(bits)}）" if bits else ""))
        return refs, "\n".join(lines)

    def all_names(self, uids, scope=None):
        return {uid: set(self.names_of(uid, scope)) for uid in uids if uid != "self"}
