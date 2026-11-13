"""One native provider request per durable background attempt."""
import copy
from types import MethodType

from ..services.task_errors import TaskFailure


def task_provider(provider):
    scoped = copy.copy(provider)
    client = getattr(provider, "client", None)
    if client is not None and hasattr(client, "with_options"):
        scoped.client = client.with_options(max_retries=0)
    if hasattr(provider, "_handle_api_error"):
        async def propagate(self, error, *args, **kwargs):
            raise error
        scoped._handle_api_error = MethodType(propagate, scoped)
    if hasattr(provider, "_prepare_query_config"):
        prepare = type(provider)._prepare_query_config
        async def config(self, *args, **kwargs):
            # Gemini also repairs errors inside _query; do not let that loop multiply retries.
            if self._task_config_prepared:
                raise TaskFailure("output_format", "提供者需要修正请求，交由任务调度器恢复")
            self._task_config_prepared = True
            value = await prepare(self, *args, **kwargs)
            from google.genai import types
            options = value.http_options.model_copy() if value.http_options else types.HttpOptions()
            options.retry_options = types.HttpRetryOptions(attempts=1)
            value.http_options = options
            return value
        scoped._prepare_query_config = MethodType(config, scoped)
    scoped._task_config_prepared = False
    return scoped
