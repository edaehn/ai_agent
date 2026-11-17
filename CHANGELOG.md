# Changelog

One version per post in the **Build Your Own AI Agent** series. Each version is an
annotated git tag, and each tag is a working agent — check one out to get exactly the code
its post describes:

```bash
git checkout v0.3.0
```

Versions are tagged when the code is complete, before the post goes out. A fix that lands
after publication gets a patch release (`v0.3.1`) and a note here; a published tag is never
moved.

---

## Unreleased

Planning only — `PLAN.md`, `COMPARISON.md`, `TODO.csv`, and `agent_run_log.py` copied in
from the reuse library ahead of v0.3.0.

- Project scaffold: `git init -b master`, package layout, `.gitignore`.
- `agent_run_log` v1.0.0 adopted from
  `internal-library/agent_run_log/` (34 tests). The library holds the
  canonical copy; this repo keeps a copy and matches its version.

## v1.0.0 — Part 6: Off the Leash: Roles, Harnesses and Honest Limits

*Planned.* Planner/Coder/Verifier roles and delegation, then `pydantic-ai-harness`
(`FileSystem`, `Coder`) as the production answer. Deep Agents and the Claude Agent SDK as
alternatives, CrewAI as a read-only review crew. What not to automate, and measuring the
gate against deliberately bad outputs.

## v0.5.0 — Part 5: Quality Gates for Prose and Data, Not Just Code

*Planned.* The `Verifier`/`Gate` protocol: link checking, schema validation, citation
structure. Deterministic checks block; the model's score advises and never terminates the
loop. Refine loop with structured failures and no-progress detection.

## v0.4.0 — Part 4: Tests as the Verdict

*Planned.* `run_tests` instead of a shell, the accept/revert loop, per-file backups, a git
branch per run, round cap and token budget. The point where the agent becomes independent.

## v0.3.0 — Part 3: Knowing What Your Agent Did (and What It Cost)

*Planned.* `agent_run_log` wired in: per-action CSV trail, token and cost accounting with
cache tokens counted separately, per-agent and per-model breakdown.

## v0.2.0 — Part 2: One Agent, Any Model

*Planned.* Provider becomes a config value — `anthropic:`, `openai:`, `ollama:` and
anything OpenAI-compatible — with `config.py` and `prompts.json`. Local models run with no
API key.

## v0.1.0 — Part 1: A Minimal AI Agent That Edits Your Files

*Planned.* The smallest agent that genuinely works: `read_file`, `edit_file`,
`create_file`, exact-match editing that must match exactly once, and workspace path
containment. One provider, around 120 lines.
