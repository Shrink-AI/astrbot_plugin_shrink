"""Async boundaries implemented by the host or by isolated test doubles."""

from typing import Protocol


class ProviderUnavailable(RuntimeError):
    """The host has not loaded the configured model (yet); retry soon rather than back off."""


class ChatModel(Protocol):
    async def __call__(
        self, prompt: str, system: str = "", images: list[str] | None = None
    ) -> str: ...


class Embedder(Protocol):
    async def __call__(self, text: str) -> list[float] | None: ...


class PersonaSync(Protocol):
    async def __call__(self, prompt: str) -> None: ...


class ErrorSink(Protocol):
    def __call__(self, stage: str, error: BaseException) -> None: ...


class Searcher(Protocol):
    async def __call__(self, query: str, source: str = "") -> list[dict]: ...
