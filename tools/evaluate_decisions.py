"""Offline decision comparison. Samples and responses never enter the application database."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shrink.adapters.decision_backends import HaikuDecisionBackend, LayaDecisionBackend  # noqa: E402
from shrink.services.decisions import DecisionContext, validate_decision  # noqa: E402
from shrink.services.turn_trace import percentile  # noqa: E402


def samples(path):
    return [json.loads(line) for line in Path(path).read_text("utf-8").splitlines() if line.strip()]


async def evaluate(rows, backends, timeout=3):
    report = {}
    for name, backend in backends.items():
        scores = dict.fromkeys(("participation", "association", "capabilities", "notify"), 0)
        latencies, failures = [], 0
        for row in rows:
            expected = row["expected"]
            context = DecisionContext(**row.get("context", {}))
            payload = {**row["input"], "rules": {
                "direct": context.direct, "private": context.private,
                "may_create_task": context.admin, "fixed_task": context.fixed_task}}
            started = time.monotonic()
            try:
                raw = await asyncio.wait_for(backend(payload), timeout)
                value = validate_decision(raw, payload["event"], payload["candidates"], context)
                actual = {
                    "participation": value.route != "silent",
                    "association": {"conversation": value.conversation, "topic": value.topic, "task": value.task},
                    "capabilities": sorted(set(value.capability_candidates)),
                    "notify": value.notify,
                }
                for key in scores:
                    target = sorted(set(expected[key])) if key == "capabilities" else expected[key]
                    scores[key] += actual[key] == target
            except Exception:
                failures += 1
            latencies.append(time.monotonic() - started)
        report[name] = {
            "samples": len(rows), "failures": failures,
            "accuracy": {key: count / len(rows) if rows else 0.0 for key, count in scores.items()},
            "latency_seconds": {"p50": percentile(latencies, .5), "p95": percentile(latencies, .95),
                                "max": max(latencies, default=0)},
        }
    return report


class OpenAIProvider:
    """An explicit offline endpoint supplies the host provider's text_chat boundary."""
    def __init__(self, endpoint, model, timeout):
        self.endpoint, self.model, self.timeout = endpoint, model, timeout

    async def text_chat(self, *, prompt, system_prompt, contexts):
        key = os.environ.get("DECISION_EVAL_API_KEY")
        headers = {"Authorization": "Bearer " + key} if key else {}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(self.endpoint, headers=headers, json={
                "model": self.model, "messages": [{"role": "system", "content": system_prompt},
                                                 {"role": "user", "content": prompt}]})
            response.raise_for_status()
            return SimpleNamespace(completion_text=response.json()["choices"][0]["message"]["content"])


async def main(args):
    rows = samples(args.samples)
    backends = {}
    if args.fixtures:
        fixtures = json.loads(Path(args.fixtures).read_text("utf-8"))
        for name, replies in fixtures.items():
            async def replay(payload, replies=replies):
                return replies[payload["event"]["id"]]
            backends[name] = replay
    else:
        state = SimpleNamespace(settings=SimpleNamespace(
            decision_provider=args.haiku_model, decision_timeout=args.timeout,
            decision_laya_endpoint=args.laya_endpoint, decision_laya_version=args.laya_version))
        if args.haiku_endpoint:
            provider = OpenAIProvider(args.haiku_endpoint, args.haiku_model, args.timeout)
            backends["haiku"] = HaikuDecisionBackend(SimpleNamespace(get_provider_by_id=lambda _: provider), state)
        if args.laya_endpoint:
            backends["laya"] = LayaDecisionBackend(state)
    if not backends:
        raise ValueError("需要显式指定评测端点或合成响应文件")
    print(json.dumps(await evaluate(rows, backends, args.timeout), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("samples")
    parser.add_argument("--fixtures", help="Synthetic replay responses; never a live model benchmark")
    parser.add_argument("--haiku-endpoint", default="", help="Complete OpenAI-compatible chat/completions URL")
    parser.add_argument("--haiku-model", default="shrink/claude-haiku-5-5")
    parser.add_argument("--laya-endpoint", default="")
    parser.add_argument("--laya-version", default="unverified")
    parser.add_argument("--timeout", type=float, default=3)
    asyncio.run(main(parser.parse_args()))
