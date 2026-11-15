"""Upgrade names and operator instructions describe their purpose."""
import re
from pathlib import Path

import pytest

from shrink.services.outbound_guard import unsafe

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("length", [20, 80, 120])
def test_readable_fallback_rejects_exact_fact_at_every_length(length):
    text = "合成" * length
    assert unsafe(text, {"content": text}, readable_fallback=True)
    if len(text) >= 80:
        assert not unsafe("提醒你：" + text, {"content": text}, readable_fallback=True)


def test_upgrade_entrypoints_and_version_describe_content():
    from shrink.services.upgrades import VERSION
    assert not re.search(r"(?:batch\d|m0\d)", VERSION, re.I)
    assert (ROOT / "tools/upgrade_preflight.py").is_file()
    assert (ROOT / "docs/upgrade.md").is_file()
    assert not [p.name for p in (ROOT / "tests").glob("test_*.py") if re.search(r"batch\d", p.name)]


def test_operator_upgrade_document_contains_only_operational_sections():
    path = ROOT / "docs/upgrade.md"
    text = path.read_text("utf-8")
    assert "## 验证范围" not in text
    assert "passed" not in text and "开工" not in text and "先写回归" not in text
    assert "污染待决定项" not in text


def test_acceptance_contract_keeps_name_without_process_or_followup_tail():
    from shrink.prompts import CATALOG
    contract = CATALOG["outbound.contract"]["text"]
    acceptance, name = contract.splitlines()[-2:]
    assert "一句极简短句" in acceptance
    assert "不介绍同构体或派工过程" in acceptance
    assert "不附加“等有结果再回你”" in acceptance
    assert "重复受理保持安静" in acceptance
    assert "提到子代理时叫“我的同构体”" in name
    assert "不把尚未派出的任务说成已交出去" in name
    assert not [key for key, entry in CATALOG.items()
                if "可以说有结果再回" in entry["text"]]


def test_acceptance_decision_is_recorded_without_changing_persona_choice():
    plan = (ROOT / "docs/agent-architecture-plan.md").read_text("utf-8")
    assert "可以把子代理称为“同构体”" in plan
    assert "实际已派工：“我让我的同构体去看看，有结果再回你。”" not in plan
    assert "2026-11-15" in plan.split("## 14. 已定的设计选择")[1]
    assert "不介绍同构体或派工过程，不附加等有结果再回你" in plan
    assert "把子代理称为同构体是人格设定" in plan
    for name in ("upgrade.md", "architecture.md", "development.md"):
        text = (ROOT / "docs" / name).read_text("utf-8")
        assert "不介绍同构体或派工过程" in text
        assert "是否作为个人偏好保留待用户决定" not in text
        assert "冲突见 [上线步骤]" not in text
