"""Bind a stateless model call to the source it summarizes when supported."""


def for_source(model, source):
    bind = getattr(model, "__dict__", {}).get("for_source")
    return bind(source) if bind else model
