"""Task failure policy, independent of host exception types."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Failure:
    category: str
    state: str
    retryable: bool = False


class TaskFailure(Exception):
    def __init__(self, category, message=""):
        super().__init__(message or category)
        self.category = category


POLICY = {
    "network": Failure("network", "retry_wait", True),
    "invalid_request": Failure("invalid_request", "needs_attention"),
    "unknown_write": Failure("unknown_write", "reconciling"),
    "permission": Failure("permission", "waiting_user"),
    "output_format": Failure("output_format", "retry_wait", True),
    "external_running": Failure("external_running", "waiting_external"),
    "unknown_send": Failure("unknown_send", "reconciling"),
    "unavailable": Failure("unavailable", "needs_attention"),
}


def classify_error(error, *, unclosed_tools=False, exhausted=False):
    if unclosed_tools:
        return POLICY["unknown_write"]
    category = getattr(error, "category", None)
    code = getattr(error, "status_code", getattr(error, "code", getattr(getattr(error, "response", None), "status_code", None)))
    name = type(error).__name__
    if category not in POLICY:
        if isinstance(error, PermissionError) or code in {401, 403}:
            category = "permission"
        elif isinstance(error, (TimeoutError, ConnectionError, OSError)) or name in {
                "TimeoutException", "ReadTimeout", "ConnectTimeout", "ConnectError",
                "ReadError", "APIConnectionError", "APITimeoutError", "RateLimitError"
        } or code in {408, 409, 429} or isinstance(code, int) and code >= 500:
            category = "network"
        elif name in {"EmptyModelOutputError", "JSONDecodeError"}:
            category = "output_format"
        elif isinstance(error, (ValueError, TypeError, KeyError)) or code in {400, 404, 422}:
            category = "invalid_request"
        else:
            category = "unavailable"
    failure = POLICY[category]
    return POLICY["unavailable"] if exhausted and failure.retryable else failure
