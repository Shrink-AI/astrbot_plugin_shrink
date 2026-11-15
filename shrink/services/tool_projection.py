"""Compact evidence for model requests; original receipts are never rewritten."""
import json


def brief(text, limit=600):
    value = str(text or "")
    return value if len(value) <= limit else value[:limit] + "…[其余见原记录]"


def receipt(text, reference="", limit=600):
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        value = text
    result = {"record_ref": reference, "receipt": "received"}
    if isinstance(value, dict):
        for key in ("status", "state", "task_id", "id", "currentRevisionId", "error", "isError",
                    "success", "completed", "result_ref", "resultId"):
            if key in value:
                item = value[key]
                result[key] = brief(item, 160) if isinstance(item, str) else item if isinstance(
                    item, (bool, int, float, type(None))) else "[见原记录]"
        # A tool_end envelope is transport evidence, not proof the requested job succeeded.
        inner = value.get("summary_unverified", value.get("result"))
        if isinstance(inner, str):
            result["summary_unverified"] = brief(inner, limit)
        elif not any(k in result for k in ("status", "state", "error", "isError")):
            result["fields"] = (value["fields"][:12] if value.get("receipt") == "received"
                                and isinstance(value.get("fields"), list) else list(value)[:12])
    else:
        result["summary_unverified"] = brief(value, limit)
    return result


def expression_facts(facts):
    """Bound material for expression, while Compositions keeps its full source separately."""
    if not isinstance(facts, dict):
        return {"summary_unverified": brief(facts, 4000)}
    output = dict(facts)
    if isinstance(output.get("tool_records"), list):
        rows = output["tool_records"]
        output.setdefault("tool_record_count", len(rows))
        output["tool_records"] = [
            {"action": r.get("action"), **receipt(r.get("result", ""), r.get("record_ref", ""), 180)}
            for r in rows[-4:]]
    for key in ("result_summary_unverified", "answer", "text", "final_answer"):
        if key in output:
            output[key] = brief(output[key], 2500)
    if len(json.dumps(output, ensure_ascii=False)) > 6000:
        output = {k: output[k] for k in ("status", "task_id", "result_ref", "execution_status",
                  "business_verified") if k in output}
        output["summary_unverified"] = brief(facts.get("final_answer", facts.get(
            "result_summary_unverified", facts.get("text", ""))), 3500)
        output["omitted"] = True
    return output
