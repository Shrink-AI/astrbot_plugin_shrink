"""All model prompts live here, each with an id and version recorded in run diagnostics."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Prompt:
    id: str
    version: int
    system: str
    text: str

    @property
    def tag(self):
        return f"{self.id}@{self.version}"


def block(name, body):
    """Wrap untrusted material so the model can tell data from instructions."""
    body = (body or "").strip()
    return f"<{name}>\n{body}\n</{name}>" if body else ""


def join(*parts):
    return "\n\n".join(p for p in parts if p and p.strip())
