# ai_agent

A Python AI agent that reads, edits and creates files, runs its own checks, and stops when
a test — not a model — says the work is done.

> ### 📖 Start here: [A Minimal AI Agent That Edits Your Files](https://daehnhardt.com/blog/2026/11/19/minimal-ai-agent-that-edits-your-files/)
>
> Part 1 of the **[Build Your Own AI Agent](https://daehnhardt.com/series/build-your-own-ai-agent/)**
> series. It walks through the code at tag `v0.1.1`: why a targeted edit beats a whole-file
> write, why an anchor must match exactly once, how one check keeps the agent inside a single
> directory, and why there is no shell tool. The repo is the code; the posts are the reasoning.

Sequel to
[Two AI Agents, One Blog Post](https://daehnhardt.com/blog/2026/07/13/writer-editor-agents/),
which built a writer and an editor that passed a draft back and forth. That project could not
damage anything. Giving an agent write access to files changes the engineering completely —
not the prompt, everything around it.

## Which version goes with which post

Every tag is a working agent. Check one out to get exactly the code its post describes:

```bash
git checkout v0.1.1
```

| Part | Tag | Post | The agent gains |
|---|---|---|---|
| 1 | `v0.1.1` | A Minimal AI Agent That Edits Your Files | `read_file`, `edit_file`, `create_file`; exact-match edits; path containment |
| 2 | `v0.2.0` | One Agent, Any Model | Anthropic, OpenAI, Ollama and any OpenAI-compatible endpoint, from config |
| 3 | `v0.3.0` | Knowing What Your Agent Did (and What It Cost) | Per-action CSV trail, token and cost accounting |
| 4 | `v0.4.0` | Tests as the Verdict | `run_tests`, accept/revert loop, backups, round cap |
| 5 | `v0.5.0` | Quality Gates for Prose and Data | `Verifier`/`Gate`: links, schemas, citations |
| 6 | `v1.0.0` | Off the Leash | Roles, `pydantic-ai-harness`, honest limits |

See [CHANGELOG.md](CHANGELOG.md) for what changed in each version. The reasoning behind
every decision lives in the posts — that is what they are for.

## Any model, including local ones

The agent logic never learns which model it is talking to:

```python
Agent('anthropic:claude-opus-5-5', ...)   # hosted
Agent('openai:gpt-6.1-sol', ...)          # hosted
Agent('ollama:qwen2.5-coder', ...)        # local, no API key, no per-token cost
```

Anything else — a `llama.cpp` server, for instance — goes through
`OpenAICompatibleProvider`. There is no in-process `transformers` backend: run local models
through Ollama or vLLM, which is faster anyway.

The checks in Parts 4–5 are provider-independent by construction. A `pytest` exit code and
an HTTP status do not care which model wrote the thing, which is exactly what makes a
smaller local model usable: the gate catches what the model gets wrong instead of trusting it.

## The six invariants

The safety rules the agent is built around, each introduced in the part that needs it:

1. Every path resolves inside the workspace root — `..`, absolute paths and symlinks
   pointing out all fail one check.
2. No edit without a prior read in the same session: an exact-match anchor must come from
   real bytes.
3. Edits must match **exactly once**. Zero or several is an error returned to the model —
   never a guess, never a fuzzy match.
4. Every write is backed up, and the run happens on its own git branch.
5. A hard round cap and a token budget.
6. `run_tests` is the only execution tool, and it runs a fixed command. Not `bash`.

Number 6 matters more than it looks: an agent with `bash` has every capability there is, and
path containment becomes decorative the moment a shell is in the toolset.

## What this is not

Not a sandbox. Path containment is a functional guardrail — a symlink swapped between the
check and the use is not caught, and a shell would bypass it entirely. Run untrusted work in
a real sandbox.

A green gate is a floor, not a ceiling: tests can pass for the wrong reason and data can be
schema-valid and wrong. Facts are the honest limit — the agent can check that a claim
carries a citation and that the citation resolves, but not that the source says the thing.
Human review stays mandatory.

## Install and run

Needs **Python 3.11+** (`pydantic-ai` does not support 3.10; this repo is developed on
3.13).

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
```

```bash
.venv/bin/python -m ai_agent.agent --root ./sandbox --task "Use math.pi instead of 3.14"
```

Pick a different model with `--model`, including a local one that needs no API key. A local
model needs a running Ollama server and one environment variable, because `pydantic-ai`
will not guess the endpoint (without it, building the agent raises a `UserError`):

```bash
ollama pull qwen2.5-coder
ollama serve                      # if Ollama is not already running; in another terminal:
export OLLAMA_BASE_URL=http://localhost:11434/v1
.venv/bin/python -m ai_agent.agent --model ollama:qwen2.5-coder --root ./sandbox --task "..."
```

Local inference has no per-token charge, but it still uses your own computing resources.
Whether a particular local model copes with exact-match anchors is a question for Part 2.

Set `PYDANTIC_AI_NO_BANNER=1` to quiet the startup banner.

## Development

```bash
.venv/bin/python -m pytest tests/ -q
```

87 tests, none of which call a model: `FunctionModel` plays the model's part, so the
self-correction path — a refused edit coming back as an error the model acts on — is
tested for free.

Two things worth knowing if you are reading the code:

- **Providers are constructed eagerly.** `build_agent('openai:…')` raises at build time
  without `OPENAI_API_KEY`, which is usually what you want — you find out before the agent
  has read half a repository. Pass `defer_model_check=True` to build one anyway.
- **`result.usage` is a property, not a method.**

`agent_run_log.py` is maintained in a shared internal library and vendored here, so its
version is pinned in its own module docstring rather than tracked by this repo's tags.

## Licence and citation

MIT — see [LICENSE](LICENSE). You are free to use this code, including commercially; the
only condition is that the copyright notice travels with it.

If you reference the project in writing, [CITATION.cff](CITATION.cff) gives the preferred
form (GitHub turns it into a "Cite this repository" button, with APA and BibTeX). A link
back to the post a snippet came from is always welcome.
