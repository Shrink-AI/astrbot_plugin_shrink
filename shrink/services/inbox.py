"""One identity rule for messages, task supplements and external events."""
from ..persistence.inbox import input_id


class Inbox:
    def __init__(self, repository, messages, history):
        self.repository, self.messages, self.history = repository, messages, history
        self.recovering = set()
        self.recovered = set()
        self.routes_recovered = set()

    def receive(self, message, *, session, sender, external_id, source="platform", route="chat",
                metadata=None, conversational=True):
        identity = input_id(session, sender, external_id, source)
        existing = self.repository.get(identity)
        if existing:
            return existing, False
        with self.repository.db.transaction():
            self.messages.ingest(message)
            row = self.repository.receive(session=session, sender=sender, external_id=external_id,
                                          source=source, message_id=message["id"], route=route,
                                          metadata=metadata)
            if conversational:
                self.history.repository.append(session, "message:" + message["id"], "user",
                                               message["id"], speaker=message["user_id"])
        return row, True

    def exclude(self, *, session, sender, external_id):
        """Command handlers own the body; retain only the platform identity for deduplication."""
        with self.repository.db.transaction():
            row = self.repository.receive(session=session, sender=sender, external_id=external_id,
                                          source="platform", message_id="", route="excluded")
            self.repository.complete([row["id"]])
        return row

    def event(self, *, session, sender, external_id, source, text, route, metadata=None):
        identity = input_id(session, sender, external_id, source)
        existing = self.repository.get(identity)
        if existing:
            return existing, False
        message_id = "input:" + identity
        with self.repository.db.transaction():
            self.messages.raw(message_id, session, text, sender=sender)
            return self.repository.receive(session=session, sender=sender, external_id=external_id,
                                           source=source, message_id=message_id, route=route,
                                           metadata=metadata), True

    async def recover(self, route, consumer):
        """One dispatcher owns recovery; accepted inputs remain pending until acknowledged."""
        if route not in self.routes_recovered:
            self.repository.recover(route)
            self.routes_recovered.add(route)
        for row in self.repository.pending(route):
            if row["id"] in self.recovering or row["id"] in self.recovered:
                continue
            self.recovering.add(row["id"])
            try:
                self.repository.claim([row["id"]])
                await consumer(row)
                self.recovered.add(row["id"])
            finally:
                self.recovering.discard(row["id"])
