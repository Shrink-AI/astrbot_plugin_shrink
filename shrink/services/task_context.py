"""Task identity shared by orchestration and hooks without importing either adapter."""
from contextvars import ContextVar

CURRENT_TASK = ContextVar("shrink_developer_task", default="")
