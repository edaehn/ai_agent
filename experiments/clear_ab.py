#!/usr/bin/env python3
"""Does clearing the conversation between tasks actually save tokens?

An A/B/C experiment. The same four-phase job is run under three conditions:

  A  "no clear"       - one conversation, history carried across all four phases
  B  "cleared"        - a fresh conversation per phase, as /clear would give you.
                        The agent's read-before-write state resets too.
  C  "fresh, state    - a fresh conversation per phase, but the workspace's
      kept"             read-before-write state carries over. B minus C is the
                        effect of that reset; A minus C is the effect of the
                        forgotten conversation alone.

Every condition does identical work on an identical starting workspace, with the
same model and the same tools. The order of the conditions is shuffled inside
each trial (seeded, recorded in the result file) so that no condition always
runs first.

Each finished workspace is scored two ways: structurally (is each function named
in shapes.py and README.md) and behaviourally (does each function return the
right numbers). The behavioural check runs the generated code in a subprocess.

The fourth phase deliberately *needs* what the first three did. That is the
honest part of the test: clearing is not free, because a cleared agent has to
re-read what it already knew. If re-establishing context costs more than the
re-reading saved, clearing is the wrong advice, and this will say so.

    python3 experiments/clear_ab.py --trials 4

Runs against the Anthropic API with your own key, on Haiku by default -- a few
pennies per trial, and nothing to do with a Claude Code subscription.
"""

from __future__ import annotations

import argparse
import json
import platform
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import date
from importlib import metadata
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_agent.agent import Deps, build_agent  # noqa: E402
from ai_agent.agent_run_log import CostTracker, Usage  # noqa: E402
from ai_agent.workspace import Workspace  # noqa: E402

try:
    from pydantic_ai.exceptions import ModelConnectionError  # noqa: E402
except ImportError:  # an older pydantic_ai: fall back to the transport error
    from httpx import TransportError as ModelConnectionError  # type: ignore  # noqa: E402

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
    functions_correct: int = 0
    order: int = 0


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


# Run in a subprocess with -I, against the finished workspace. Prints how many of
# the three new functions return the right numbers.
CHECK = """
import sys
sys.path.insert(0, sys.argv[1])
import shapes
cases = {
    "square_area": [((2,), 4), ((5,), 25), ((0,), 0)],
    "triangle_area": [((3, 4), 6), ((10, 2), 10)],
    "rectangle_area": [((3, 4), 12), ((5, 5), 25), ((0, 7), 0)],
}
good = 0
for name, tests in cases.items():
    try:
        fn = getattr(shapes, name)
        good += all(abs(fn(*args) - want) < 1e-9 for args, want in tests)
    except Exception:
        pass
print(good)
"""


def check_behaviour(root: Path) -> int:
    """How many of the three new functions return correct values (0 to 3)."""
    try:
        out = subprocess.run(
            [sys.executable, "-I", "-c", CHECK, str(root)],
            capture_output=True, text=True, timeout=20, cwd=root,
        )
        return int(out.stdout.strip().splitlines()[-1])
    except (subprocess.SubprocessError, ValueError, IndexError):
        return 0


def run_condition(condition: str, trial: int, model: str, root: Path) -> Result:
    """'A' keeps one conversation; 'B' starts fresh each phase and resets the
    workspace state; 'C' starts fresh each phase but keeps that state."""
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
            if condition == "B":
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
                  per_phase, readme, done, check_behaviour(root))


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
            cwd=Path(__file__).resolve().parents[1], timeout=10,
        ).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _version(package: str) -> str:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return "unknown"


LABEL = {
    "A": "one conversation",
    "B": "fresh, state reset",
    "C": "fresh, state kept",
}


def summarise(results: list[Result], conditions: list[str]) -> dict:
    summary = {}
    for condition in conditions:
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
            "functions_correct": [r.functions_correct for r in rows],
            "median_per_phase_tokens": [
                int(statistics.median(r.phase_usage[i].total_tokens for r in rows))
                for i in range(len(PHASES))
            ],
        }
    return summary


def write_results(path: Path, *, model: str, seed: int, orders: list[list[str]],
                  results: list[Result], conditions: list[str], retries: list[dict],
                  complete: bool) -> None:
    """Write the result file. Called after every run, so a crash loses nothing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "model": model,
        "date": date.today().isoformat(),
        "phases": len(PHASES),
        "complete": complete,
        "seed": seed,
        "condition_order_per_trial": orders,
        "environment": {
            "python": platform.python_version(),
            "pydantic_ai": _version("pydantic-ai"),
            "ai_agent_commit": _git_commit(),
        },
        "retried_runs": retries,
        "summary": summarise(results, conditions),
        "runs": [
            {"condition": r.condition, "trial": r.trial, "run_position": r.order,
             "seconds": round(r.seconds, 2),
             "cost_usd": round(r.cost, 6), "completeness": r.functions_found,
             "functions_correct": r.functions_correct,
             "total_tokens": r.usage.total_tokens,
             "input_tokens": r.usage.input_tokens,
             "output_tokens": r.usage.output_tokens,
             "per_phase_tokens": [u.total_tokens for u in r.phase_usage],
             "per_phase_input_tokens": [u.input_tokens for u in r.phase_usage],
             "per_phase_output_tokens": [u.output_tokens for u in r.phase_usage]}
            for r in results
        ],
    }, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--trials", type=int, default=4)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--conditions", default="A,B,C",
                        help="comma-separated subset of A,B,C")
    parser.add_argument("--seed", type=int, default=None,
                        help="seed for the per-trial condition order (default: random, recorded)")
    parser.add_argument("--max-attempts", type=int, default=3,
                        help="attempts per run when the connection drops (default 3)")
    parser.add_argument("--out", type=Path,
                        default=Path(f"experiments/clear_ab_results_{date.today().isoformat()}.json"))
    args = parser.parse_args(argv)

    conditions = [c.strip().upper() for c in args.conditions.split(",") if c.strip()]
    unknown = set(conditions) - set(LABEL)
    if unknown:
        parser.error(f"unknown condition(s): {', '.join(sorted(unknown))}")
    seed = args.seed if args.seed is not None else random.SystemRandom().randrange(2**31)
    rng = random.Random(seed)

    results: list[Result] = []
    orders: list[list[str]] = []
    retries: list[dict] = []
    for trial in range(1, args.trials + 1):
        order = conditions[:]
        rng.shuffle(order)
        orders.append(order)
        for position, condition in enumerate(order, start=1):
            for attempt in range(1, args.max_attempts + 1):
                root = Path(tempfile.mkdtemp(prefix=f"clearab_{condition}{trial}_"))
                try:
                    r = run_condition(condition, trial, args.model, root)
                    break
                except ModelConnectionError as exc:
                    # A dropped connection says nothing about the condition, so the
                    # whole run is repeated from a clean workspace and the retry is
                    # recorded in the result file.
                    retries.append({"trial": trial, "condition": condition,
                                    "attempt": attempt, "error": str(exc)[:200]})
                    print(f"  trial {trial} {condition}: connection dropped "
                          f"(attempt {attempt}/{args.max_attempts}), repeating the run")
                    if attempt == args.max_attempts:
                        write_results(args.out, model=args.model, seed=seed, orders=orders,
                                      results=results, conditions=conditions,
                                      retries=retries, complete=False)
                        print(f"partial results saved to {args.out}")
                        raise
                finally:
                    shutil.rmtree(root, ignore_errors=True)
            r.order = position
            results.append(r)
            print(f"  trial {trial} {condition} (run {position}): "
                  f"{r.usage.total_tokens:>9,} tokens  ${r.cost:.4f}  "
                  f"{r.seconds:5.1f}s  named {r.functions_found}/33  "
                  f"correct {r.functions_correct}/3")
            write_results(args.out, model=args.model, seed=seed, orders=orders,
                          results=results, conditions=conditions, retries=retries,
                          complete=False)

    write_results(args.out, model=args.model, seed=seed, orders=orders, results=results,
                  conditions=conditions, retries=retries, complete=True)
    summary = summarise(results, conditions)

    print()
    print(f"{'':26} {'tokens':>10} {'input':>9} {'output':>8} {'cache rd':>9} {'cost':>9} {'secs':>6}")
    for condition, sm in summary.items():
        print(f"{condition} {LABEL[condition]:<24} {sm['median_tokens']:>10,} "
              f"{sm['median_input']:>9,} {sm['median_output']:>8,} "
              f"{sm['median_cache_read']:>9,} {sm['median_cost']:>9.4f} {sm['median_seconds']:>6.1f}")

    if "A" in summary:
        a = summary["A"]["median_tokens"]
        for other in ("B", "C"):
            if other in summary:
                b = summary[other]["median_tokens"]
                change = 100 * (b - a) / a
                verdict = "fewer" if b < a else "more"
                print(f"\n{other} ({LABEL[other]}) used {abs(change):.1f}% {verdict} tokens "
                      f"than one conversation, on this task with this model.")
    if retries:
        print(f"\n{len(retries)} run(s) were repeated after a dropped connection.")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
