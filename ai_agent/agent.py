"""The agent: four tools, one workspace, any model.

The whole file-editing agent is the four tools below plus the instructions that
tell the model how to use them. There is no loop here - ``pydantic_ai`` runs it,
calling tools until the model stops asking - which is the point: the interesting
decisions are all in the tool contract, not in loop plumbing.

Every refusal from ``workspace.py`` or ``edit.py`` is re-raised as ``ModelRetry``.
That is the self-correction channel: the model gets the error text back as a tool
result and tries again, so "your anchor matched twice, add more context" becomes
a better second attempt instead of a crash.

Run it:

    python -m ai_agent.agent --root ./sandbox --task "Add a docstring to parse()"
    python -m ai_agent.agent --model ollama:qwen2.5-coder --root ./sandbox --task "..."

A local ``ollama:`` model needs a running Ollama server and one environment
variable first, because ``pydantic_ai`` will not guess the endpoint:

    ollama serve                      # if it is not already running
    export OLLAMA_BASE_URL=http://localhost:11434/v1
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from pydantic_ai import Agent, ModelRetry, RunContext

from .edit import EditError
from .workspace import Workspace, WorkspaceError

__all__ = ["Deps", "build_agent", "DEFAULT_MODEL", "INSTRUCTIONS"]

DEFAULT_MODEL = "anthropic:claude-opus-5-5"

INSTRUCTIONS = """\
You are a careful software engineer working inside one directory.

How to work:

1. Use list_files to find your way around, and read_file before changing
   anything. You cannot edit a file you have not read - the tool will refuse.
2. Make one small edit at a time with edit_file. Each edit replaces an exact
   piece of text, so copy the anchor verbatim from what read_file returned,
   including its indentation. Do not retype it from memory.
3. The anchor must match exactly one place in the file. If a short anchor would
   be ambiguous, include more surrounding context rather than guessing.
4. Never rewrite a whole file to make a small change. Targeted edits are the
   only safe way to change code you did not write.
5. Use create_file only for a genuinely new file. It will not overwrite.

If a tool returns an error, read it: it says what to do differently. Fix your
input and try again rather than repeating the same call.

When the task is done, briefly state what you changed and why. If you could not
do it, say so plainly and explain what stopped you - do not claim a change you
did not make.
"""


@dataclass
class Deps:
    """What the tools need at run time."""

    workspace: Workspace


def build_agent(
    model: str = DEFAULT_MODEL,
    *,
    retries: int = 3,
    defer_model_check: bool = False,
) -> Agent[Deps, str]:
    """Build the file-editing agent.

    ``model`` is a ``provider:name`` string, so the same agent runs on a hosted
    model or a local one with nothing else changed:

        anthropic:claude-opus-5-5
        openai:gpt-6.1-sol
        ollama:qwen2.5-coder          (local, no API key, needs OLLAMA_BASE_URL)

    Note how little the agent knows about any of that. The July writer-editor
    project needed an abstract backend class and three implementations to support
    exactly these three; here the provider is a string and the tools never learn
    which model is calling them.

    The provider is built **eagerly**, so a missing API key raises here rather
    than on the first request. That is usually what you want - you find out
    before the agent has read half a repository - but it means you cannot
    construct an agent for a provider you have no credentials for. Pass
    ``defer_model_check=True`` when you need to build one anyway, as the tests do.

    ``retries`` is per tool, and it is the budget for self-correction: each
    refused edit costs one. Three is enough for "anchor matched twice, add
    context" to be recoverable while still stopping a model that is flailing.

    It is not a budget for the whole run. Successful tool calls are not
    retries, so a model that keeps calling tools successfully is bounded only
    by the usage limits passed to ``run_sync``. ``pydantic_ai`` applies a default
    request limit; a token or cost limit is something you add with
    ``UsageLimits`` (see ``tests/test_agent.py``).
    """
    agent: Agent[Deps, str] = Agent(
        model,
        deps_type=Deps,
        instructions=INSTRUCTIONS,
        retries=retries,
        defer_model_check=defer_model_check,
    )

    @agent.tool
    def list_files(ctx: RunContext[Deps], pattern: str = "**/*") -> list[str]:
        """List files in the workspace. `pattern` is a glob, e.g. '**/*.py'."""
        try:
            return ctx.deps.workspace.list_files(pattern)
        except WorkspaceError as exc:
            raise ModelRetry(str(exc)) from exc

    @agent.tool
    def read_file(ctx: RunContext[Deps], path: str) -> str:
        """Read a file and return its exact contents. Required before editing it."""
        try:
            return ctx.deps.workspace.read_file(path)
        except WorkspaceError as exc:
            raise ModelRetry(str(exc)) from exc

    @agent.tool
    def edit_file(
        ctx: RunContext[Deps], path: str, old_text: str, new_text: str
    ) -> str:
        """Replace `old_text` with `new_text` in `path`.

        `old_text` must appear exactly once in the file - copy it verbatim from
        read_file, including indentation. Returns a unified diff of the change.
        """
        try:
            result = ctx.deps.workspace.edit_file(path, old_text, new_text)
        except (WorkspaceError, EditError) as exc:
            raise ModelRetry(str(exc)) from exc
        return result.diff or f"edited {path} (no textual diff)"

    @agent.tool
    def create_file(ctx: RunContext[Deps], path: str, content: str) -> str:
        """Create a new file with `content`. Fails if the file already exists."""
        try:
            return f"created {ctx.deps.workspace.create_file(path, content)}"
        except WorkspaceError as exc:
            raise ModelRetry(str(exc)) from exc

    return agent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="An AI agent that reads, edits and creates files in one directory."
    )
    parser.add_argument("--root", required=True, help="the directory the agent works in")
    parser.add_argument("--task", required=True, help="what you want done")
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"provider:name, e.g. ollama:qwen2.5-coder (default: {DEFAULT_MODEL})",
    )
    args = parser.parse_args(argv)

    try:
        workspace = Workspace(Path(args.root))
    except WorkspaceError as exc:
        print(f"error: {exc}")
        return 2

    agent = build_agent(args.model)
    result = agent.run_sync(args.task, deps=Deps(workspace=workspace))

    print(result.output)
    usage = result.usage  # a property, not a method
    print(
        f"\n[{args.model}] {usage.input_tokens:,} in / {usage.output_tokens:,} out, "
        f"{usage.requests} request(s)"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
