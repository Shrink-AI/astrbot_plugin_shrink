"""Composition root. Features depend on explicit repositories and callbacks, not this object."""

from pathlib import Path

from .persistence.context import DigestRepository
from .persistence.database import Database, StateRepository
from .persistence.expressions import ExpressionRepository
from .persistence.learning import LearningRepository
from .persistence.media import MemeRepository
from .persistence.memory import MemoryRepository
from .persistence.messages import MessageRepository
from .persistence.people import PeopleRepository
from .persistence.persona import PersonaRepository
from .persistence.review import ReviewRepository
from .persistence.runs import RunRepository
from .persistence.sessions import SessionRepository
from .persistence.skills import SkillRepository
from .persistence.status import StatusRepository
from .persistence.teach import TeachRepository
from .ports import ChatModel, Embedder, PersonaSync, Searcher
from .runtime import Scheduler, Tasks
from .services.consolidation import ConsolidationService
from .services.context import ContextService
from .services.digest import DigestService
from .services.expression import ExpressionService
from .services.outbound import Outbound
from .services.glossary import GlossaryService
from .services.learning import LearningService
from .services.memes import MemeService
from .services.prompts import PromptService
from .persistence.prompts import PromptRepository
from .persistence.turn_traces import TurnTraceRepository
from .services.memory import MemoryService
from .services.materials import Materials
from .services.attachments import Attachments
from .services.participation import Participation
from .services.recent import RecentInteractions
from .services.people import PeopleService
from .services.persona import PersonaService
from .services.skills import SkillService
from .services.teach import TeachService
from .services.tools import ToolService
from .services.scheduling import Scheduling


class Application:
    def __init__(
        self,
        root: Path,
        model: ChatModel,
        embed: Embedder,
        sync_persona: PersonaSync,
        search: Searcher | None = None,
        *,
        learning_model: ChatModel | None = None,
        summary_model: ChatModel | None = None,
        compression_model: ChatModel | None = None,
    ):
        from .services.upgrades import startup_upgrade
        fresh_database = startup_upgrade(root / "shrink.db")["status"] == "new"
        self.database = Database(root)
        self.closed = False
        self.started = False
        try:
            self.state = StateRepository(self.database)
            self.messages = MessageRepository(self.database, self.state)
            self.people_repository = PeopleRepository(self.database)
            self.memory_repository = MemoryRepository(self.database, self.state)
            self.persona_repository = PersonaRepository(self.database, self.state)
            self.learning_repository = LearningRepository(self.database, self.state)
            self.meme_repository = MemeRepository(self.database)
            self.sessions = SessionRepository(self.database)
            self.digests = DigestRepository(self.database)
            self.expressions = ExpressionRepository(self.database)
            self.runs = RunRepository(self.database)
            self.review = ReviewRepository(self.database)
            self.teach_repository = TeachRepository(self.database)
            self.skill_repository = SkillRepository(self.database)
            self.prompt_repository = PromptRepository(self.database)
            self.turn_traces = TurnTraceRepository(self.database)
            self.status = StatusRepository(self.database, self.state, self.learning_repository)
            self.tasks = Tasks(self.state)
            error = self.tasks.error
            self.scheduling = Scheduling()
            expression_model = model
            model = self.scheduling.wrap(model, "model")
            learning_model = self.scheduling.wrap(learning_model, "model") if learning_model else model
            summary_model = self.scheduling.wrap(summary_model, "model") if summary_model else model
            compression_model = self.scheduling.wrap(compression_model, "model") if compression_model else model
            embed = self.scheduling.wrap(embed, "embedding")
            from .persistence.history import HistoryRepository
            from .persistence.inbox import InboxRepository
            from .services.history import ConversationHistory
            from .services.inbox import Inbox
            self.history = ConversationHistory(HistoryRepository(self.database), self.messages, compression_model, error)
            self.history.spawn = self.tasks.spawn
            self.history.scheduling = self.scheduling
            self.inbox = Inbox(InboxRepository(self.database), self.messages, self.history)
            from .persistence.authorizations import Authorizations
            from .services.authorizations import AuthorizationService
            self.authorizations = AuthorizationService(Authorizations(self.database), self.inbox.repository)
            self.authorizations.inbox_service = self.inbox
            from .persistence.decisions import DecisionRepository
            from .services.decisions import Decisions
            self.decisions = Decisions(DecisionRepository(self.database), self.inbox.repository,
                                       self.messages, self.state)
            self.decisions.scheduling = self.scheduling
            self.people = PeopleService(self.people_repository, self.messages)
            self.memory = MemoryService(
                self.memory_repository, self.state, embed, error, self.people, self.review
            )
            self.memory.compute = self.scheduling.compute
            self.persona = PersonaService(self.persona_repository, self.state, sync_persona)
            self.prompts = PromptService(self.prompt_repository, self.persona)
            self.prompts.load()
            self.recent = RecentInteractions(self.messages)
            self.context = ContextService(
                self.state, self.messages, self.digests, self.people, self.recent)
            self.participation = Participation(
                self.state, self.messages.last_bot_reply_to, self.interest
            )
            self.expression = ExpressionService(
                self.state, self.persona_repository, self.expressions, expression_model, error
            )
            self.outbound = Outbound(self)
            from .persistence.outbox import OutboxRepository
            from .persistence.notifications import NotificationRepository
            from .services.notification_outbox import NotificationOutbox
            self.notification_outbox = NotificationOutbox(OutboxRepository(self.database),
                NotificationRepository(self.database), self.inbox, self.decisions, error)
            self.notification_outbox.scheduling = self.scheduling
            self.notification_outbox.record = self.outbound.record
            self.notification_outbox.persona = self.outbound.persona
            self.expression.outbound = self.outbound
            self.materials = Materials(self)
            self.attachments = Attachments(self)
            self.history.attachments = self.attachments
            self.glossary = GlossaryService(self.state, self.memory, search, model, error, self.review)
            self.consolidation = ConsolidationService(
                state=self.state, messages=self.messages, digests=self.digests, people=self.people,
                memory=self.memory, review=self.review, runs=self.runs, model=model, error=error,
            )
            self.consolidation.compute = self.scheduling.compute
            self.digest = DigestService(
                state=self.state, messages=self.messages, digests=self.digests, people=self.people,
                memory=self.memory, glossary=self.glossary, review=self.review, runs=self.runs,
                model=summary_model, error=error, profile_hook=self.consolidation.profile,
            )
            self.learning = LearningService(
                self.learning_repository, self.messages, self.state, self.persona,
                self.expressions, learning_model, error,
            )
            self.memes = MemeService(self.meme_repository, self.state, model, error)
            self.teach = TeachService(
                repository=self.teach_repository, people=self.people, memory=self.memory,
                review=self.review, runs=self.runs, messages=self.messages, model=model, error=error,
            )
            self.skills = SkillService(
                self.skill_repository, self.messages, self.people, self.runs, self.state,
                embed, model, error,
            )
            self.tools = ToolService(
                messages=self.messages, digests=self.digests, people=self.people,
                memory=self.memory, memes=self.memes, state=self.state, error=error,
                recent=self.recent,
            )
            self.tools.attachments = self.attachments
            self.scheduler = Scheduler(
                self.state, self.learning, self.memory, error,
                digest=self.digest, consolidation=self.consolidation, skills=self.skills,
                scheduling=self.scheduling,
            )
            if fresh_database:
                from .services.upgrades import record_new_database
                record_new_database(self.database)
        except BaseException:
            self.database.close()
            raise

    def interest(self, turn):
        """A message is interesting when it touches something 石英 knows about."""
        text = turn.text or ""
        if any(term in text for term in self.memory_repository.glossary_terms()):
            return True
        return any(
            uid != turn.user_id and self.people_repository.profile(uid)
            for uid in self.people.mentioned(text, turn.source)
        )

    def undo(self, run_id):
        touched = self.runs.undo(run_id)
        self.memory_repository.refresh_vectors(touched)
        self.people.invalidate()
        return touched

    def start(self):
        if self.closed:
            raise RuntimeError("Application is closed")
        if not self.started:
            self.learning_repository.recover_interrupted()
            self.runs.recover_interrupted()
            self.tasks.spawn(self.scheduler.run())
            self.history.start()
            self.attachments.resume()
            self.started = True

    async def close(self):
        if self.closed:
            return
        try:
            await self.tasks.close()
        finally:
            self.scheduling.close()
            self.database.close()
            self.closed = True
