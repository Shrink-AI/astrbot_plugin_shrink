from pydantic import BaseModel, ConfigDict, Field

from .prompts.chat import fixed_persona, persona_prompt  # noqa: F401  (public re-export)

BOT_NAME = "石英"


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_provider: str = ""
    embedding_provider: str = ""
    persona_id: str = "default"
    spontaneous_probability: float = Field(0.06, ge=0, le=1)
    spontaneous_limit: int = Field(2, ge=0, le=10)
    group_blacklist: list[str] = []
    learning_enabled: bool = True
    learning_hours: float = Field(6, ge=0.25, le=168)
    learning_min_messages: int = Field(60, ge=10, le=1000)
    learning_batch_size: int = Field(120, ge=10, le=1000)
    collect_enabled: bool = True
    collect_groups: list[str] = []
    meme_enabled: bool = True
    meme_cooldown: int = Field(120, ge=0, le=86400)
    meme_repeat_cooldown: int = Field(1800, ge=0, le=604800)
    recall_count: int = Field(6, ge=1, le=20)
    # 0.4: conversation context, memory upkeep and human-like delivery.
    context_budget: int = Field(4000, ge=1000, le=20000)
    memory_budget: int = Field(800, ge=200, le=4000)
    digest_enabled: bool = True
    verify_enabled: bool = True
    glossary_search: bool = True
    glossary_per_run: int = Field(3, ge=0, le=10)
    split_probability: float = Field(0.15, ge=0, le=1)
    stale_seconds: int = Field(60, ge=10, le=3600)
    engaged_seconds: int = Field(180, ge=0, le=1800)
    debounce_enabled: bool = True
    debounce_seconds: float = Field(1.5, ge=0.1, le=5)
    debounce_max_seconds: float = Field(6, ge=1, le=15)
    # 0.6: tools and experience.
    skills_enabled: bool = True
    sandbox_url: str = "http://shrink-sandbox:8765"
