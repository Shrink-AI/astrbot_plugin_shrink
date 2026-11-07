"""Turn a platform message into a Turn and work out whom it is addressed to."""

import re
from dataclasses import dataclass, field

from ..config import BOT_NAME

SELF = "self"
OTHER = "other"
# Pet-name style vocatives such as 白宝 / 老王哥 / 小李姐; only treated as addressing someone.
PET_NAME = re.compile(
    r"^\s*@?([一-鿿A-Za-z]{1,3}(?:宝宝|宝|哥哥|哥|姐姐|姐|佬|酱|仔|老师|同学))"
    r"(?=[\s，,：:！!？?~～]|你|我|在|快|帮|有|也|还|要|今|明)"
)
AFTER_NAME = re.compile(r"^(?:[\s，,：:！!？?~～]|你|我|在|快|帮|有|也|还|要|呢|吗|啊|呀|今|明|$)")


@dataclass
class Turn:
    id: str
    platform: str
    account: str
    user_id: str
    name: str
    source: str
    source_name: str
    text: str
    created: float
    private: bool = False
    mentions: list = field(default_factory=list)  # [{"id": uid or "self", "name": str}]
    reply_to: str = ""
    reply_sender: str = ""  # uid or "self"
    quote: dict = field(default_factory=dict)
    has_image: bool = False
    wake: bool = False
    umo: str = ""
    speaker_bot: bool = False
    addressee: str = ""  # uid, "self", "other" (unknown person) or ""
    addressee_name: str = ""
    reason: str = ""

    def record(self):
        return {
            "id": self.id,
            "platform": self.platform,
            "account": self.account,
            "user_id": self.user_id,
            "name": self.name,
            "source": self.source,
            "source_name": self.source_name,
            "text": self.text,
            "quote": self.quote,
            "mentions": self.mentions,
            "has_image": self.has_image,
            "reply_to": self.reply_to,
            "addressee": self.addressee if self.addressee != OTHER else "",
            "created": self.created,
        }


def leading_name(text, candidates):
    """The longest known name the message opens with, as a vocative."""
    stripped = text.lstrip().lstrip("@")
    best = ""
    for name in candidates:
        if len(name) >= 2 and len(name) > len(best) and stripped.startswith(name):
            if AFTER_NAME.match(stripped[len(name):]):
                best = name
    return best


def address(turn, names):
    """Resolve the addressee. ``names`` maps known names/nicknames (2+ chars) to uids."""
    if turn.private:
        return SELF, BOT_NAME
    others = [m for m in turn.mentions if m.get("id") != SELF]
    if any(m.get("id") == SELF for m in turn.mentions):
        return SELF, BOT_NAME
    if others:
        return others[0]["id"], others[0].get("name", "")
    if turn.reply_sender:
        return turn.reply_sender, BOT_NAME if turn.reply_sender == SELF else turn.quote.get("name", "")
    vocative = leading_name(turn.text, [*names, BOT_NAME])
    if vocative:
        if vocative == BOT_NAME:
            return SELF, BOT_NAME
        uid = names[vocative]
        if uid != turn.user_id:
            return uid, vocative
    pet = PET_NAME.match(turn.text)
    if pet and BOT_NAME not in pet.group(1):
        uid = names.get(pet.group(1))
        return (uid or OTHER), pet.group(1)
    if BOT_NAME in turn.text:
        return SELF, BOT_NAME
    return "", ""
