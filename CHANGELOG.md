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

- `agent_run_log` v1.0.0 vendored from a shared internal library (34 tests), ahead of
  v0.3.0 where it gets wired into the agent.

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

*Released 2026-11-18.* The smallest agent that genuinely works.

- `edit.py` — `apply_edit()` replaces an exact span that must match **exactly once**. Zero
  matches, several matches, an empty anchor and a no-op edit are all refused. No fuzzy
  matching. Error messages are written for the model to read: that text is the
  self-correction channel.
- `workspace.py` — one containment check that `..`, absolute paths and symlinks pointing
  out all fail together, because `resolve()` runs before the test. Plus read-before-write,
  a `create_file` that never overwrites, and `read_only=True` for a reviewer that cannot
  write.
- `agent.py` — four tools (`list_files`, `read_file`, `edit_file`, `create_file`) on
  `pydantic-ai`, with every refusal re-raised as `ModelRetry`. The model is a
  `provider:name` string. **No shell tool**, and a test that asserts so.
- 84 tests, none of which call a model — `FunctionModel` plays the model's part, so the
  retry-after-refusal path is tested for free. Verified end to end against
  `anthropic:claude-haiku-5-5` for about $0.0007.
- Needs Python 3.10+ (`pydantic-ai` does not support 3.9).

Two findings worth knowing: providers are constructed **eagerly**, so a missing API key
raises at build time rather than on the first request (pass `defer_model_check=True` to
build one anyway); and `result.usage` is a property, not a method.
