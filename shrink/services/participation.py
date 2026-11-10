"""Decide whether 石英 should speak: called, continuing a conversation, or chiming in."""

import re
import time
from collections import defaultdict, deque

from .perception import OTHER, SELF

QUESTION_TO_ALL = re.compile(r"有没有人|有人知道|谁知道|谁懂|求助|大家觉得|你们觉得|有人用过")
BOT_REPLY_WINDOW = 120
FOLLOWUP_SECONDS = 90


class Participation:
    def __init__(self, state, last_bot_reply_to, interest):
        """``last_bot_reply_to(source, uid)`` and ``interest(turn)`` are injected lookups."""
        self.state = state
        self.last_bot_reply_to = last_bot_reply_to
        self.interest = interest
        self.spontaneous = defaultdict(deque)
        self.other_conversations = {}
        self.bot_replies = defaultdict(deque)

    def _group_blacklisted(self, turn):
        configured = {str(group).strip() for group in self.state.settings.group_blacklist if str(group).strip()}
        if not configured or turn.private:
            return False
        source = str(turn.source or "")
        group_id = str(turn.source_name or "")
        if ":group:" in source:
            group_id = source.rsplit(":group:", 1)[1]
        return source in configured or group_id in configured

    def permit_spontaneous(self, source, random_value, boost=1.0):
        settings = self.state.settings
        now = time.time()
        queue = self.spontaneous[source]
        while queue and queue[0] < now - 300:
            queue.popleft()
        probability = min(1.0, settings.spontaneous_probability * boost)
        if len(queue) >= settings.spontaneous_limit or random_value >= probability:
            return False
        queue.append(now)
        return True

    def _bot_budget(self, source):
        now = time.time()
        queue = self.bot_replies[source]
        while queue and queue[0] < now - BOT_REPLY_WINDOW:
            queue.popleft()
        if queue:
            return False
        queue.append(now)
        return True

    def decide(self, turn, random_value):
        now = time.time()
        self.other_conversations = {
            k: v for k, v in self.other_conversations.items() if v > now
        }
        key = (turn.source, turn.user_id)
        addressed_self = turn.addressee == SELF or (turn.wake and not turn.addressee)
        if turn.speaker_bot:
            # Other bots get an answer when they call 石英, at most once per window, never chatter.
            if addressed_self and self._bot_budget(turn.source):
                return "direct"
            return "bot_quiet"
        if turn.private or addressed_self:
            self.other_conversations.pop(key, None)
            return "direct"
        if turn.addressee and turn.addressee != SELF:
            self.other_conversations[key] = now + FOLLOWUP_SECONDS
            return "addressed_other"
        if self._group_blacklisted(turn):
            return "group_blacklisted"
        # The latest addressing wins: after turning to someone else, follow-ups belong to them.
        if key in self.other_conversations:
            return "other_conversation_followup"
        engaged = self.state.settings.engaged_seconds
        if engaged and now - self.last_bot_reply_to(turn.source, turn.user_id) < engaged:
            return "engaged"
        boost = 2.0 if self.interest(turn) or QUESTION_TO_ALL.search(turn.text) else 1.0
        if self.permit_spontaneous(turn.source, random_value, boost):
            return "spontaneous"
        return "quiet"


def should_quote(turn, newer_messages):
    """Quote the message being answered when it is no longer obvious which one that is."""
    if turn.private:
        return False
    # Chiming in needs an anchor; otherwise quote only once others have spoken since.
    return turn.reason == "spontaneous" or newer_messages > 0


__all__ = ["Participation", "OTHER", "SELF", "should_quote"]
