from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .prompts.chat import fixed_persona, persona_prompt  # noqa: F401  (public re-export)

BOT_NAME = "石英"


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_provider: str = ""
    embedding_provider: str = ""
    decision_backend: Literal["haiku", "laya"] = "haiku"
    decision_provider: str = "shrink/claude-haiku-5-5"
    decision_timeout: float = Field(3, ge=0.01, le=10)
    decision_capability_limit: int = Field(5, ge=1, le=20)
    decision_laya_endpoint: str = ""
    decision_laya_version: str = "unverified"
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
    foreground_retrieval_timeout: float = Field(
        10, gt=0, le=300, description="前台知识库检索预算（秒），线上测量后再调")
    foreground_rerank_timeout: float = Field(
        20, gt=0, le=300, description="前台空闲重排预算（秒），线上测量后再调")
    foreground_grilling_timeout: float = Field(
        0, ge=0, le=300, description="前台 grilling 准备预算（秒），0 不额外限时；线上测量后再调")
    # 0.4: conversation context, memory upkeep and human-like delivery.
    context_budget: int = Field(4000, ge=1000, le=20000)
    history_compress_tokens: int = Field(128000, ge=1000, le=2000000)
    history_retain_ratio: float = Field(.65, ge=.1, le=.9)
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
    # Ends a superseded or stopped run through the host's own stop instead of cancelling
    # its task. Off until verified against the real host: the host interrupts a running tool.
    cooperative_stop: bool = False
    # 0.6: tools and experience.
    skills_enabled: bool = True
    task_retry_limit: int = Field(3, ge=0, le=3)
    notification_max_delay: float = Field(
        60, ge=0, le=86400, description="必要通知因前台活跃而让路的最长时间（秒）")
    sandbox_url: str = "http://shrink-sandbox:8765"
