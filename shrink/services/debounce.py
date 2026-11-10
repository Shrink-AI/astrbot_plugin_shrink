"""Merge short bursts from one sender; no external classifier or synthetic events."""
import asyncio
import time
from dataclasses import dataclass, field

RANK = {"spontaneous": 0, "engaged": 1, "direct": 2}
# A reply still unsent after this long has failed or timed out; it no longer absorbs new messages.
INFLIGHT_SECONDS = 150


@dataclass
class Burst:
    started: float
    latest: float
    revision: int = 0
    texts: list[str] = field(default_factory=list)
    components: list = field(default_factory=list)
    reason: str = "direct"


@dataclass
class Reply:
    """One reply being generated for a sender."""
    text: str
    reason: str
    started: float
    superseded: bool = False
    # Tools ran or the reply is being sent: it can no longer be withdrawn.
    committed: bool = False


class Debouncer:
    def __init__(self):
        self.pending = {}
        self.replies = {}

    async def collect(self, key, text, components, reason, *, quiet=1.5, maximum=6.0):
        now = time.monotonic()
        if reason == "group_blacklisted":
            return Burst(now, now, texts=[text], components=list(components), reason=reason)
        burst = self.pending.get(key)
        if burst is None:
            burst = self.pending[key] = Burst(now, now, reason=reason)
        burst.latest = now
        burst.revision += 1
        revision = burst.revision
        burst.texts.append(text)
        burst.components.extend(components)
        try:
            while True:
                remaining = min(burst.latest + quiet, burst.started + maximum) - time.monotonic()
                if remaining > 0:
                    await asyncio.sleep(remaining)
                if burst.revision != revision:
                    return None
                if time.monotonic() >= min(burst.latest + quiet, burst.started + maximum):
                    self.pending.pop(key, None)
                    return burst
        except asyncio.CancelledError:
            if burst.revision == revision:
                self.pending.pop(key, None)
            raise

    def begin(self, key, text, reason):
        """Start a reply. A newer message from the same sender withdraws the older unsent reply
        and answers both, so one person's follow-up never gets two separate answers."""
        now = time.monotonic()
        old = self.replies.get(key)
        if old and not old.committed and not old.superseded and now - old.started < INFLIGHT_SECONDS:
            old.superseded = True
            text = f"{old.text}\n{text}" if old.text else text
            if RANK.get(old.reason, 0) > RANK.get(reason, 0):
                reason = old.reason
        reply = self.replies[key] = Reply(text, reason, now)
        return reply

    @staticmethod
    def commit(reply):
        """Lock a reply for sending; False when a newer message already took it over."""
        if reply is None:
            return True
        if reply.superseded:
            return False
        reply.committed = True
        return True

    def done(self, key, reply):
        if reply is not None and self.replies.get(key) is reply:
            self.replies.pop(key, None)
