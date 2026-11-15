import copy
import json

import pytest
from pydantic import ValidationError

from shrink.adapters.decision_backends import instruction
from shrink.services.decisions import DecisionContext, validate_decision


@pytest.fixture
def decision_input():
    event = {"id": "input-current", "session": "session-current"}
    candidates = {
        "tasks": [{"id": "task-current", "status": "running"},
                  {"id": "task-finished", "status": "completed"}],
        "topics": [{"id": "topic-current"}],
        "people": [{"id": "person-current"}],
        "capabilities": [{"name": "lookup"}],
    }
    value = {
        "intent": "query", "route": "reply", "conversation": event["session"],
        "topic": None, "task": None, "evidence": [event["id"]],
        "capability_candidates": [], "notify": "normal", "confidence": .8,
    }
    return event, candidates, value


@pytest.mark.parametrize("evidence,expected", [
    ([], ["input-current"]),
    (["person-current"], ["input-current", "person-current"]),
    (["invented"], ["input-current"]),
    (["input-current", "invented", "topic-current", "task-current", "person-current"],
     ["input-current", "topic-current", "task-current", "person-current"]),
])
@pytest.mark.parametrize("serialized", [False, True])
def test_evidence_normalizes_only_known_ids_and_current_input(decision_input, evidence, expected, serialized):
    event, candidates, value = decision_input
    value["evidence"] = evidence
    before = copy.deepcopy(value)
    result = validate_decision(json.dumps(value) if serialized else value,
                               event, candidates, DecisionContext())
    assert result.evidence == expected
    assert value == before


def test_current_input_is_preserved_at_evidence_limit(decision_input):
    event, candidates, value = decision_input
    candidates["people"] = [{"id": f"person-{i}"} for i in range(32)]
    value["evidence"] = [person["id"] for person in candidates["people"]]
    result = validate_decision(value, event, candidates, DecisionContext())
    assert result.evidence == [event["id"], *value["evidence"][:31]]


@pytest.mark.parametrize("names,expected", [
    (["unknown"], []),
    (["lookup", "unknown"], ["lookup"]),
    ([], []),
])
def test_capabilities_drop_unknown_names_without_adding_choices(decision_input, names, expected):
    event, candidates, value = decision_input
    value["capability_candidates"] = names
    assert validate_decision(value, event, candidates, DecisionContext()).capability_candidates == expected


@pytest.mark.parametrize("notify", ["merge", "normal", "action"])
def test_silent_route_only_lowers_notifications(decision_input, notify):
    event, candidates, value = decision_input
    value.update(route="silent", notify=notify)
    result = validate_decision(value, event, candidates, DecisionContext())
    assert result.route == "silent" and result.notify == "silent"


@pytest.mark.parametrize("updates,context,error", [
    ({"conversation": "elsewhere"}, {}, "模型不能覆盖会话"),
    ({"task": "unknown"}, {}, "任务不在候选中"),
    ({"task": None}, {"fixed_task": "task-current"}, "模型不能覆盖已有任务关联"),
    ({"task": "task-finished"}, {"fixed_task": "task-current"}, "模型不能覆盖已有任务关联"),
    ({"topic": "unknown"}, {}, "话题不在候选中"),
    ({"route": "stop"}, {"admin": True}, "停止只能由规则确认"),
    ({"route": "create_task"}, {}, "模型不能授予任务权限"),
    ({"route": "attach_task", "task": "task-current"}, {}, "模型不能授予任务权限"),
    ({"route": "attach_task"}, {"admin": True}, "补充目标必须是可接收输入的已有任务"),
    ({"route": "attach_task", "task": "task-finished"}, {"admin": True},
     "补充目标必须是可接收输入的已有任务"),
    ({"intent": "supplement", "route": "create_task"}, {"admin": True}, "补充要求不能新建任务"),
    ({"task": "task-current", "route": "create_task"}, {"admin": True}, "已有任务关联不能新建任务"),
    ({"route": "silent"}, {"direct": True}, "明确呼叫不能静默"),
    ({"route": "silent"}, {"private": True}, "明确呼叫不能静默"),
])
def test_authority_rejections_survive_format_normalization(decision_input, updates, context, error):
    event, candidates, value = decision_input
    value.update(evidence=["unknown"], capability_candidates=["unknown"], **updates)
    with pytest.raises(ValueError) as caught:
        validate_decision(value, event, candidates, DecisionContext(**context))
    assert str(caught.value) == error


@pytest.mark.parametrize("updates", [
    {"evidence": "input-current"}, {"evidence": [1]}, {"evidence": None},
    {"evidence": ["input-current"] * 33}, {"capability_candidates": [1]},
    {"notify": "invented"}, {"permission": "admin"},
])
def test_normalization_does_not_relax_schema_types_or_authority_fields(decision_input, updates):
    event, candidates, value = decision_input
    value.update(updates)
    with pytest.raises(ValidationError):
        validate_decision(value, event, candidates, DecisionContext())


def test_instruction_specifies_references_and_empty_candidates():
    prompt = instruction()
    for required in ("event.id", "candidates.tasks", "candidates.topics", "candidates.people",
                     "candidates.capabilities", "null", "[]", "fixed_task", "may_create_task"):
        assert required in prompt


@pytest.mark.parametrize("prefix,suffix", [
    ("```json\n", "\n```"),
    ("分类结果：\n", "\n分类结束。"),
    ("", "\n以上为分类结果。"),
])
def test_unique_json_object_can_have_fence_or_surrounding_text(decision_input, prefix, suffix):
    event, candidates, value = decision_input
    raw = prefix + json.dumps(value, ensure_ascii=False) + suffix
    assert validate_decision(raw, event, candidates, DecisionContext()).model_dump() == value


@pytest.mark.parametrize("template", [
    "{value}\n{value}", "```json\n{value}\n```\n另一个：{value}", "没有对象",
    '{{"broken": {value}', '[{value}]',
])
def test_ambiguous_or_malformed_json_is_not_salvaged(decision_input, template):
    event, candidates, value = decision_input
    raw = template.replace("{value}", json.dumps(value))
    with pytest.raises((json.JSONDecodeError, ValidationError)):
        validate_decision(raw, event, candidates, DecisionContext())


def test_json_fence_does_not_bypass_identity_rejection(decision_input):
    event, candidates, value = decision_input
    value["conversation"] = "other-session"
    with pytest.raises(ValueError, match="模型不能覆盖会话"):
        validate_decision("```json\n" + json.dumps(value) + "\n```", event, candidates, DecisionContext())
