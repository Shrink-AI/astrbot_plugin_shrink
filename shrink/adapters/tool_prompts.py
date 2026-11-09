"""Keep the descriptions of 石英's registered AstrBot tools in step with the prompt catalog."""

from .. import prompts


def owned(tool, module):
    return bool(module) and getattr(tool, 'handler_module_path', None) == module


def push(func_list, module):
    """Rewrite description and parameter descriptions of every tool that has a ``tool.<name>``
    prompt. AstrBot only parses docstrings at import, so saved edits arrive this way."""
    updated = 0
    for tool in func_list:
        if not owned(tool, module):
            continue
        key = f"tool.{tool.name}"
        if key not in prompts.CATALOG:
            continue
        description, params = prompts.tool_parts(prompts.text(key))
        tool.description = description
        properties = (tool.parameters or {}).get("properties", {})
        for name, text in params.items():
            if name in properties:
                properties[name]["description"] = text
        updated += 1
    return updated
