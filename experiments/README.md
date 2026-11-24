# Experiments

Small, reproducible measurements that back claims made in the posts. Each writes a
JSON result file alongside itself so a number quoted in writing can be traced to a run.

## clear_ab.py — does clearing the conversation actually save?

Runs the same four-phase job under three conditions: one conversation (**A**), a fresh
conversation per phase (**B**, what `/clear` leaves you, with the agent's read-before-write
state reset too), and a fresh conversation per phase that keeps that state (**C**, a
control that separates the forgotten conversation from the reset). Identical workspace,
model and tools. The fourth phase deliberately needs what the first three did, so the cost
of re-establishing context is included rather than assumed away. The order of the three
conditions is shuffled inside each trial; the seed is recorded in the result file.

Each finished workspace is checked twice: structurally (is each new function named in
`shapes.py` and `README.md`, a score out of 33) and behaviourally (a subprocess calls each
function on eight inputs).

## Running it

Python 3.11 or newer and an Anthropic API key. A run costs a few pennies on Haiku and is
unrelated to a Claude Code subscription.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q
export ANTHROPIC_API_KEY="sk-ant-..."   # this terminal only; never commit it

# One trial first, written outside the repository.
python experiments/clear_ab.py --trials 1 --seed 1 --out /tmp/clear_ab_smoke.json

# The run described below.
python experiments/clear_ab.py --trials 6 --seed 20261124 \
  --out experiments/clear_ab_results_2026-11-24.json
```

The script stops before the first Haiku call unless the process has `ANTHROPIC_API_KEY`.
A Claude Desktop login does not provide it. The result file is rewritten after every
finished run, with `"complete": false` until the last one, and a run that hits a dropped
connection is repeated (up to three attempts, recorded under `retried_runs`).

## Result: 6 trials per condition, claude-haiku-5-5, 2026-11-24

Medians. Python 3.13.5, pydantic-ai 2.69.0. Full data:
`clear_ab_results_2026-11-24.json`.

| | tokens | range | input | output | time | cost |
|---|---|---|---|---|---|---|
| A one conversation | 25,724 | 22,987–28,295 | 24,481 | 1,262 | 9.1 s | $0.00307 |
| B fresh each phase | 19,936 | 19,300–22,002 | 18,464 | 1,506 | 11.0 s | $0.00258 |
| C fresh, state kept | 19,445 | 19,340–21,980 | 17,927 | 1,517 | 10.9 s | $0.00255 |

All 18 runs scored 33/33 on names and passed all three functions on the behavioural
check. Cache reads were 0 in every run, because this harness sends the history as fresh
input.

**A fresh conversation used 22.5% fewer tokens (C: 24.4%).** B used fewer tokens than A in
every one of the six trials, by 9% to 31%. B and C differ by 2.5%, well inside the spread
of each, so the state reset is not what drives the saving. The cost of forgetting shows up
elsewhere: B wrote 19.3% more output (C: 20.2%) and took 20.9% longer (C: 19.8%), mostly in
phase four, where the fresh agent has to look at the file (about 554 output tokens against
347).

| phase | A | B | C |
|---|---|---|---|
| 1 | 4,173 | 4,173 | 4,168 |
| 2 | 5,679 | 4,490 | 4,487 |
| 3 | 7,133 | 5,710 | 5,708 |
| 4 | 8,742 | 5,058 | 5,061 |

A's median step is about +1,500 tokens per phase. Phase 1 is a tie, as it must be: both
start cold. By phase 4, A costs about 73% more for the same work. Phase 4 is the phase
needing knowledge of the other three, so it was where clearing should have been punished.
It cost the fresh run 5,058 tokens, less than its own phase three at 5,710. Individual
phases land on one of two levels about 1,100–1,400 tokens apart, which looks like one extra
tool round (tool calls are not logged), so phase-level medians are less stable than totals.

### Honest limits

**This harness does not use prompt caching.** Claude Code caches the prefix, so A's extra
tokens would be cache reads at a tenth of Haiku 5.5's input price (up to a 100K prompt).
Repricing only A's extra 6,017 input tokens that way gives about $0.00253 for A against
$0.00258 for B: A comes out slightly cheaper, because B's extra output costs more than
the discounted prefix it avoided. That is a sensitivity analysis, not a measured cache hit.
Token *volume* is unaffected, which matters if what you are rationing is a subscription
allowance rather than dollars.

**Costs are calculated, not billed.** They come from the harness's own pricing table.

**Small and short.** One model, one task, four phases, six trials per condition, and a
small behavioural check. It shows a mechanism, not a universal percentage. Longer runs
were not measured.
