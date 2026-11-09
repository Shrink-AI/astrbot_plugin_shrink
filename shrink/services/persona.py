import asyncio
import hashlib
import time

from ..config import persona_prompt
from ..persistence.database import StateRepository
from ..persistence.persona import PersonaRepository
from ..ports import PersonaSync


class PersonaService:
    def __init__(
        self, repository: PersonaRepository, state: StateRepository, sync_persona: PersonaSync
    ):
        self.repository, self.state, self.sync_persona = repository, state, sync_persona
        self.persona_lock = asyncio.Lock()

    async def set_persona(self, text, evidence, reason, expected_version=None):
        async with self.persona_lock:
            if (
                expected_version is not None
                and self.state.get("active_persona") != expected_version
            ):
                return None
            return await self.apply_persona(text, evidence, reason)

    async def apply_persona(self, text, evidence, reason, reject_digest=None):
        # Caller holds persona_lock across synchronization and durable publication.
        self.state.put("persona_sync", {"status": "pending", "at": time.time()})
        try:
            await self.sync_persona(persona_prompt(text))
            version = self.repository.add_version(text, evidence, reason, reject_digest)
        except BaseException:
            try:
                await self.sync_persona(persona_prompt(self.repository.learned_persona()))
                self.state.put("persona_sync", {"status": "restored", "at": time.time()})
            except BaseException:
                self.state.put("persona_sync", {"status": "recovery_required", "at": time.time()})
            raise
        self.state.put("persona_sync", {"status": "synced", "version": version, "at": time.time()})
        return version

    async def resync(self):
        """Push the current persona prompt to the host after its wording changed."""
        async with self.persona_lock:
            await self.sync_persona(persona_prompt(self.repository.learned_persona()))
            self.state.put("persona_sync", {"status": "synced", "at": time.time()})

    async def rollback(self, version_id):
        async with self.persona_lock:
            target = self.repository.get(version_id)
            if not target:
                raise ValueError("版本不存在")
            current = self.repository.learned_persona()
            return await self.apply_persona(
                target["text"],
                [version_id],
                "管理员回退",
                reject_digest=hashlib.sha256(current.encode()).hexdigest(),
            )
