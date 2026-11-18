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

*Planned.* Splitting the work across roles, then handing the tools over to a production harness — and an honest account of where I stop.

## v0.5.0 — Part 5: Quality Gates for Prose and Data, Not Just Code

*Planned.* The same idea taken beyond code: prose and data get checks of their own.

## v0.4.0 — Part 4: Tests as the Verdict

*Planned.* The agent runs the tests, and the loop ends on an exit code rather than on a model's say-so.

## v0.3.0 — Part 3: Knowing What Your Agent Did (and What It Cost)

*Planned.* An audit trail of every action, and what the run actually cost.

## v0.2.0 — Part 2: One Agent, Any Model

*Planned.* The provider becomes a config value — hosted or local, with no change to the agent.

## v0.1.2 — Part 1 patch: a binary file no longer ends the run

*Released 2026-11-18.* `v0.1.1` is left as published.

- **Non-text files are refused, not fatal.** Reading a file that is not valid UTF-8 (an
  image, an archive) raised `UnicodeDecodeError`, which is not a `WorkspaceError`, so it
  escaped the tool as a crash instead of coming back to the model as a `ModelRetry`. It is
  now a refusal that names the file and says the agent works only with text. The same guard
  covers a file that turns binary between the read and the edit.
- 3 new tests (90 in all): a binary read is refused and does not unlock editing; a file that
  turns binary after reading is refused on edit; and, end to end, the model is shown the
  refusal and the run carries on.

## v0.1.1 — Part 1 patch: reproducibility and contract fixes

*Released 2026-11-18.* The code that Part 1 describes, corrected where checking it against
a clean install showed it was wrong. `v0.1.0` is left as published.

- **Python 3.11 or newer.** `pydantic-ai` does not support 3.10, so `requires-python` is
  now `>=3.11`. `v0.1.0` said 3.10, which a fresh install on 3.10 cannot satisfy.
- **Local models need `OLLAMA_BASE_URL`.** Building an `ollama:` agent without it raises
  `UserError`. The README and the `agent.py` docstrings now say so.
- **The path error now matches the check.** `Workspace.resolve()` accepts an absolute path
  that lands inside the root, but its error message told the model "no leading slash". The
  message now says what is actually forbidden: `..` and anything that leads outside.
- **Docstrings:** `agent.py` said "three tools"; there are four. `build_agent` now says that
  `retries` is a per-tool correction budget, not a budget for the whole run.
- 3 new tests (87 in all): an absolute path inside the root is accepted; the escape
  message does not ban it; tool retries do not cap a run, only usage limits do.

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
