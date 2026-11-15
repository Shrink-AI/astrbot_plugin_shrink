import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.evaluate_decisions import evaluate, samples


def test_d01_offline_script_scores_synthetic_backends_without_database(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([
        sys.executable, str(root / "tools/evaluate_decisions.py"),
        str(root / "tests/fixtures/decisions.jsonl"),
        "--fixtures", str(root / "tests/fixtures/decision_responses.json"),
    ], cwd=tmp_path, text=True, capture_output=True, check=True, encoding="utf-8")
    report = json.loads(result.stdout)
    count = len(samples(root / "tests/fixtures/decisions.jsonl"))
    assert count >= 13
    assert report["haiku"]["samples"] == count
    assert set(report["haiku"]["accuracy"].values()) == {1.0}
    assert report["laya"]["accuracy"]["association"] == pytest.approx((count - 1) / count)
    assert report["laya"]["accuracy"]["notify"] == pytest.approx((count - 1) / count)
    assert report["haiku"]["latency_seconds"]["max"] >= report["haiku"]["latency_seconds"]["p50"]
    assert list(tmp_path.iterdir()) == []


async def test_offline_errors_count_as_failed_predictions():
    rows = samples(Path(__file__).parent / "fixtures/decisions.jsonl")
    async def invalid(payload):
        return "not json"
    report = await evaluate(rows, {"invalid": invalid})
    assert report["invalid"]["failures"] == len(rows)
    assert set(report["invalid"]["accuracy"].values()) == {0}


async def test_offline_backend_sees_rules_but_not_expected_labels():
    rows = samples(Path(__file__).parent / "fixtures/decisions.jsonl")
    seen = []
    async def observe(payload):
        seen.append(payload)
        return "invalid"
    await evaluate(rows, {"observe": observe})
    assert seen[0]["rules"]["direct"] is True
    assert seen[2]["rules"]["may_create_task"] is True
    assert all("expected" not in payload for payload in seen)
