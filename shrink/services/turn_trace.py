"""Where one foreground turn spent its time. Pure bookkeeping; storage and logging are injected."""
import time
import uuid

# Each phase ends where the next begins, so the phases add up to the turn's total.
PHASES = ("debounce", "follow_up", "lock", "context", "model", "tool", "expression", "send")


class TurnTrace:
    def __init__(self, input_id, umo, source, sender, reason, clock=time.monotonic):
        self.run_id = uuid.uuid4().hex[:12]
        self.input_id, self.umo, self.source, self.sender = input_id, umo, source, sender
        self.reason = reason
        self.clock = clock
        self.started = time.time()
        self.begin = self.mark = clock()
        self.phase = "debounce"
        self.phases = {}
        self.tools = []
        self.model_calls = 0
        self.task_id = ""
        self.outcome = ""
        self.closed = False

    def enter(self, phase):
        """Charge the time since the last mark to the phase that just ended."""
        now = self.clock()
        self.phases[self.phase] = self.phases.get(self.phase, 0.0) + now - self.mark
        self.phase, self.mark = phase, now

    def tool(self, name):
        self.tools.append(name)
        self.enter("tool")

    def finish(self, outcome=""):
        if self.closed:
            return None
        self.closed = True
        self.enter("done")
        self.outcome = self.outcome or outcome
        return self.record()

    def record(self):
        return {
            "run_id": self.run_id, "input_id": self.input_id, "umo": self.umo,
            "source": self.source, "sender": self.sender, "reason": self.reason,
            "outcome": self.outcome, "task_id": self.task_id, "started": self.started,
            "total": round(self.clock() - self.begin, 3),
            "phases": {k: round(v, 3) for k, v in self.phases.items()},
            "tools": list(self.tools), "model_calls": self.model_calls,
        }


def percentile(values, fraction):
    values = sorted(values)
    if not values:
        return 0.0
    return values[min(len(values) - 1, int(round(fraction * (len(values) - 1))))]


def distribution(rows):
    """p50/p95/max per phase over stored turns; a phase a turn never entered counts as zero."""
    report = {"turns": len(rows)}
    for name in ("total", *PHASES):
        values = [r["total"] if name == "total" else r["phases"].get(name, 0.0) for r in rows]
        report[name] = {"p50": round(percentile(values, 0.5), 3),
                        "p95": round(percentile(values, 0.95), 3),
                        "max": round(max(values, default=0.0), 3)}
    return report
