#!/usr/bin/env python3
"""Does clearing the conversation between tasks actually save tokens?

An A/B experiment. The same four-phase job is run twice:

  A  "no clear"  - one conversation, history carried across all four phases
  B  "cleared"   - a fresh conversation per phase, as /clear would give you

Both conditions do identical work on an identical starting workspace, with the
same model and the same tools, so the only difference is whether the earlier
phases are still in the prefix.

The fourth phase deliberately *needs* what the first three did. That is the
honest part of the test: clearing is not free, because a cleared agent has to
re-read what it already knew. If re-establishing context costs more than the
re-reading saved, clearing is the wrong advice, and this will say so.

    python3 experiments/clear_ab.py --trials 3

Runs against the Anthropic API with your own key, on Haiku by default -- a few
pennies per trial, and nothing to do with a Claude Code subscription.
"""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_agent.agent import Deps, build_agent  # noqa: E402
from ai_agent.agent_run_log import CostTracker, Usage  # noqa: E402
from ai_agent.workspace import Workspace  # noqa: E402

MODEL = "anthropic:claude-haiku-5-5"

PRICING = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}

# The starting workspace: a small module with room to grow.
FIXTURE = {
    "shapes.py": '''"""Area helpers."""

import math


def circle_area(radius):
    return math.pi * radius * radius
''',
    "README.md": """# shapes

A tiny geometry helper module.

## Functions

- `circle_area(radius)` — area of a circle.
""",
}

# Three independent additions, then one phase that needs all three.
PHASES = [
    "Add a function square_area(side) to shapes.py that returns the area of a "
    "square. Match the style of the function already there.",
    "Add a function triangle_area(base, height) to shapes.py that returns the "
    "area of a triangle. Match the style of the functions already there.",
    "Add a function rectangle_area(width, height) to shapes.py returning the "
    "area of a rectangle. Match the style of the functions already there.",
    # This one is the test: it needs to know what phases 1-3 produced.
    "Update the Functions list in README.md so it documents every function that "
    "shapes.py now contains, in the same one-line style as the existing entry.",
]


@dataclass
class Result:
    condition: str
    trial: int
    usage: Usage
    cost: float
    seconds: float
    phase_usage: list[Usage] = field(default_factory=list)
    final_readme: str = ""
    functions_found: int = 0


def make_workspace(root: Path) -> Workspace:
    for name, text in FIXTURE.items():
        (root / name).write_text(text, encoding="utf-8")
    return Workspace(root)


def score(root: Path) -> tuple[str, int]:
    """How much of the job actually got done, so a cheap run that failed is visible."""
    shapes = (root / "shapes.py").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")
    wanted = ("square_area", "triangle_area", "rectangle_area")
    documented = sum(1 for w in wanted if w in readme)
    implemented = sum(1 for w in wanted if f"def {w}" in shapes)
    return readme, documented * 10 + implemented


def run_condition(condition: str, trial: int, model: str, root: Path) -> Result:
    """condition 'A' keeps one conversation; 'B' starts fresh each phase."""
    workspace = make_workspace(root)
    deps = Deps(workspace=workspace)
    tracker = CostTracker(PRICING)
    history = None
    per_phase: list[Usage] = []
    started = time.monotonic()

    for phase in PHASES:
        agent = build_agent(model, retries=3)
        if condition == "A":
            result = agent.run_sync(phase, deps=deps, message_history=history)
            history = result.all_messages()
        else:
            # A fresh conversation, exactly what /clear leaves you with. The
            # workspace on disk carries over -- clearing never touches files.
            result = agent.run_sync(phase, deps=deps)
            deps = Deps(workspace=Workspace(root))  # read-before-write resets too

        usage = Usage.from_obj(result.usage)
        per_phase.append(usage)
        tracker.add(model.split(":", 1)[-1], usage, agent=f"phase{len(per_phase)}")

    seconds = time.monotonic() - started
    readme, done = score(root)
    total = Usage()
    for u in per_phase:
        total += u
    return Result(condition, trial, total, tracker.total_cost, seconds,
                  per_phase, readme, done)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--out", type=Path, default=Path("experiments/clear_ab_results.json"))
    args = parser.parse_args(argv)

    results: list[Result] = []
    for trial in range(1, args.trials + 1):
        for condition in ("A", "B"):
            root = Path(tempfile.mkdtemp(prefix=f"clearab_{condition}{trial}_"))
            try:
                r = run_condition(condition, trial, args.model, root)
                results.append(r)
                print(f"  trial {trial} {condition}: "
                      f"{r.usage.total_tokens:>9,} tokens  ${r.cost:.4f}  "
                      f"{r.seconds:5.1f}s  completeness {r.functions_found}/33")
            finally:
                shutil.rmtree(root, ignore_errors=True)

    print()
    summary = {}
    for condition in ("A", "B"):
        rows = [r for r in results if r.condition == condition]
        if not rows:
            continue
        summary[condition] = {
            "trials": len(rows),
            "median_tokens": int(statistics.median(r.usage.total_tokens for r in rows)),
            "median_cost": round(statistics.median(r.cost for r in rows), 5),
            "median_seconds": round(statistics.median(r.seconds for r in rows), 1),
            "median_input": int(statistics.median(r.usage.input_tokens for r in rows)),
            "median_output": int(statistics.median(r.usage.output_tokens for r in rows)),
            "median_cache_read": int(statistics.median(r.usage.cache_read_tokens for r in rows)),
            "completeness": [r.functions_found for r in rows],
        }

    label = {"A": "one conversation", "B": "cleared each phase"}
    print(f"{'':22} {'tokens':>10} {'input':>9} {'output':>8} {'cache rd':>9} {'cost':>9}")
    for condition, s in summary.items():
        print(f"{condition} {label[condition]:<20} {s['median_tokens']:>10,} "
              f"{s['median_input']:>9,} {s['median_output']:>8,} "
              f"{s['median_cache_read']:>9,} {s['median_cost']:>9.4f}")

    if "A" in summary and "B" in summary:
        a, b = summary["A"]["median_tokens"], summary["B"]["median_tokens"]
        change = 100 * (b - a) / a
        verdict = "CHEAPER" if b < a else "DEARER"
        print(f"\nClearing is {abs(change):.1f}% {verdict} than one conversation, "
              f"on this task with this model.")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "model": args.model,
        "phases": len(PHASES),
        "summary": summary,
        "runs": [
            {"condition": r.condition, "trial": r.trial, "seconds": round(r.seconds, 2),
             "cost_usd": round(r.cost, 6), "completeness": r.functions_found,
             "total_tokens": r.usage.total_tokens,
             "per_phase_tokens": [u.total_tokens for u in r.phase_usage]}
            for r in results
        ],
    }, indent=2), encoding="utf-8")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
