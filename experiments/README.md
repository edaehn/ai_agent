# Experiments

Small, reproducible measurements that back claims made in the posts. Each writes a
JSON result file alongside itself so a number quoted in writing can be traced to a run.

## clear_ab.py — does clearing the conversation actually save?

Runs the same four-phase job twice: once in one conversation (**A**), once with a fresh
conversation per phase (**B**, what `/clear` leaves you). Identical workspace, identical
model, identical tools. The fourth phase deliberately needs what the first three did, so
the cost of re-establishing context is included rather than assumed away.

```bash
python3 experiments/clear_ab.py --trials 4
```

Uses your own `ANTHROPIC_API_KEY` on Haiku — a few pennies per trial, and unrelated to a
Claude Code subscription.

### Result, 4 trials, claude-haiku-5-5, 2026-11-18

| | tokens | input | output | completeness |
|---|---|---|---|---|
| A one conversation | 25,901 | 24,647 | 1,244 | 33/33 every trial |
| B cleared each phase | 20,521 | 19,044 | 1,480 | 33/33 every trial |

**Clearing used 20.8% fewer tokens, with identical completeness.** Variance was small
(A: 25,758–25,958; B: 20,453–20,591).

The mechanism is visible phase by phase:

| phase | A | B |
|---|---|---|
| 1 | 4,175 | 4,172 |
| 2 | 5,723 | 5,635 |
| 3 | 7,196 | 5,712 |
| 4 | 8,786 | 5,003 |

A grows **+1,537 tokens per phase**; B grows **+277**. Phase 1 is a tie, as it must be —
both start cold. By phase 4, A costs 76% more for the same work.

The surprise is phase 4. That is the phase needing knowledge of the other three, so it
was where clearing should have been punished. It was the cheapest of B's last three
phases. Phase 1 was lower still. Reading the finished file is far cheaper than carrying
the conversation that produced it.

### The 18 November file and the API key

`clear_ab.py` stops before the first Haiku call unless the process already has
`ANTHROPIC_API_KEY`. A Claude Desktop login does not provide that variable, and it does
not turn on prompt caching.

A terminal on this machine inherits the key from the shell setup, so a run started
there would have it without anyone pasting it into the chat. Claude Desktop's code
sandbox does not load that shell setup. The 18 November JSON matches the format this
script writes, including costs rounded to six decimals, so the file alone cannot show
which happened: a terminal that already had the key, or Claude writing the JSON without
a live call.

`clear_ab_results_2026-11-21.json` is a live run from a shell that had the key. Medians
were 25,876 and 20,578 tokens, 20.5% fewer for the fresh conversation, with cache reads
at 0 and a score of 33 on every trial. One continuous trial reached 30,301 tokens. The
extra tokens in A are the prefix Claude Code would count as cache reads. This harness
sends that prefix as fresh input, which is why the cache-read field stays zero.

### Two honest limits

**This harness does not use prompt caching.** Claude Code caches the prefix, so A's extra
tokens would be cache reads at ~0.05× the input rate. In *cost* terms that shrinks A's
penalty from ~26% to roughly **1.3%** on a job this short. Token *volume* is unaffected,
which matters if what you are rationing is a subscription allowance rather than dollars.

**Four phases is short.** Extrapolating the measured slopes — not measured at these
lengths — one conversation costs 2.8× a cleared one at 20 phases and 4.5× at 100. That
compounding, not the per-turn penalty, is what makes long real sessions expensive.
