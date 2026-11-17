"""Tests for the agent wiring, with no model calls and no network.

``FunctionModel`` lets us play the part of the model and script exactly the tool
calls we want to test, so the self-correction path - a refused edit coming back
as an error the model can act on - is testable for free.
"""

from __future__ import annotations

import pytest
from pydantic_ai.exceptions import UnexpectedModelBehavior, UserError
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from ai_agent.agent import Deps, build_agent
from ai_agent.workspace import Workspace

SOURCE = '''def greet(name):
    return "hello " + name
'''


@pytest.fixture
def deps(tmp_path):
    (tmp_path / "main.py").write_text(SOURCE, encoding="utf-8")
    return Deps(workspace=Workspace(tmp_path))


# ------------------------------------------------------------- tool surface --
def test_the_agent_exposes_exactly_four_tools(deps):
    seen: list[str] = []

    def record(messages: list[ModelRequest], info: AgentInfo) -> ModelResponse:
        seen.extend(sorted(t.name for t in info.function_tools))
        return ModelResponse(parts=[TextPart("done")])

    build_agent(FunctionModel(record)).run_sync("hello", deps=deps)
    assert seen == ["create_file", "edit_file", "list_files", "read_file"]


def test_usage_is_readable_by_agent_run_log(deps):
    """`Usage.from_obj` must read pydantic-ai's RunUsage with no adapter.

    RunUsage names its fields input_tokens / output_tokens / cache_read_tokens /
    cache_write_tokens, which are exactly the primary aliases in agent_run_log.
    This test is what stops a pydantic-ai rename from silently zeroing the cost
    column in v0.3.0.
    """
    from ai_agent.agent_run_log import Usage

    def respond(messages: list[ModelRequest], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("done")])

    result = build_agent(FunctionModel(respond)).run_sync("hello", deps=deps)
    counted = Usage.from_obj(result.usage)

    assert counted.input_tokens == result.usage.input_tokens
    assert counted.output_tokens == result.usage.output_tokens
    assert counted.input_tokens > 0


def test_there_is_no_shell_tool(deps):
    """Invariant 6, enforced from v0.1.0: no general command execution.

    An agent with a shell has every capability there is, and path containment
    becomes decorative. v0.4.0 adds one fixed `run_tests` command - never a shell.
    """
    names: list[str] = []

    def record(messages: list[ModelRequest], info: AgentInfo) -> ModelResponse:
        names.extend(t.name for t in info.function_tools)
        return ModelResponse(parts=[TextPart("done")])

    build_agent(FunctionModel(record)).run_sync("hello", deps=deps)
    assert not any(
        word in name
        for name in names
        for word in ("shell", "bash", "exec", "run_command", "subprocess")
    )


# ------------------------------------------------------- the happy path ------
def test_read_then_edit_changes_the_file(deps):
    """The sequence the instructions ask for, scripted."""
    calls = iter(
        [
            ToolCallPart("read_file", {"path": "main.py"}),
            ToolCallPart(
                "edit_file",
                {
                    "path": "main.py",
                    "old_text": '"hello " + name',
                    "new_text": 'f"hello {name}"',
                },
            ),
        ]
    )

    def script(messages: list[ModelRequest], info: AgentInfo) -> ModelResponse:
        try:
            return ModelResponse(parts=[next(calls)])
        except StopIteration:
            return ModelResponse(parts=[TextPart("Used an f-string in greet().")])

    result = build_agent(FunctionModel(script)).run_sync("modernise greet", deps=deps)

    assert "f-string" in result.output
    assert 'f"hello {name}"' in (deps.workspace.root / "main.py").read_text()


# ------------------------------------------------- self-correction channel ---
def test_an_ambiguous_anchor_comes_back_as_a_retryable_error(deps):
    """A refused edit must reach the model as text it can act on.

    The model first tries an anchor appearing twice, gets the error, then sends a
    longer anchor. Both the retry and the eventual success are the design working.
    """
    (deps.workspace.root / "twice.py").write_text(
        "a = 1 + n\nb = 2 + n\n", encoding="utf-8"
    )

    steps = iter(
        [
            ToolCallPart("read_file", {"path": "twice.py"}),
            # Ambiguous: "+ n" appears twice.
            ToolCallPart(
                "edit_file",
                {"path": "twice.py", "old_text": "+ n", "new_text": "+ int(n)"},
            ),
            # Disambiguated, as the error message asks.
            ToolCallPart(
                "edit_file",
                {"path": "twice.py", "old_text": "b = 2 + n", "new_text": "b = 2 + int(n)"},
            ),
        ]
    )
    errors_seen: list[str] = []

    def script(messages: list[ModelRequest], info: AgentInfo) -> ModelResponse:
        for part in messages[-1].parts:
            text = getattr(part, "content", "")
            if isinstance(text, str) and "ambiguous" in text:
                errors_seen.append(text)
        try:
            return ModelResponse(parts=[next(steps)])
        except StopIteration:
            return ModelResponse(parts=[TextPart("Disambiguated and edited.")])

    build_agent(FunctionModel(script)).run_sync("add int()", deps=deps)

    assert errors_seen, "the ambiguity error never reached the model"
    assert "2 times" in errors_seen[0]
    assert "more surrounding context" in errors_seen[0]
    assert (deps.workspace.root / "twice.py").read_text() == "a = 1 + n\nb = 2 + int(n)\n"


def test_editing_without_reading_comes_back_as_a_retryable_error(deps):
    steps = iter(
        [
            ToolCallPart(
                "edit_file",
                {"path": "main.py", "old_text": '"hello "', "new_text": '"hi "'},
            )
        ]
    )
    errors_seen: list[str] = []

    def script(messages: list[ModelRequest], info: AgentInfo) -> ModelResponse:
        for part in messages[-1].parts:
            text = getattr(part, "content", "")
            if isinstance(text, str) and "read_file first" in text:
                errors_seen.append(text)
        try:
            return ModelResponse(parts=[next(steps)])
        except StopIteration:
            return ModelResponse(parts=[TextPart("I should read it first.")])

    build_agent(FunctionModel(script)).run_sync("change the greeting", deps=deps)

    assert errors_seen, "the read-before-write refusal never reached the model"
    assert (deps.workspace.root / "main.py").read_text() == SOURCE  # untouched


def test_a_path_escape_comes_back_as_a_retryable_error(deps):
    steps = iter([ToolCallPart("read_file", {"path": "../../../etc/passwd"})])
    errors_seen: list[str] = []

    def script(messages: list[ModelRequest], info: AgentInfo) -> ModelResponse:
        for part in messages[-1].parts:
            text = getattr(part, "content", "")
            if isinstance(text, str) and "outside the workspace" in text:
                errors_seen.append(text)
        try:
            return ModelResponse(parts=[next(steps)])
        except StopIteration:
            return ModelResponse(parts=[TextPart("That is outside the workspace.")])

    build_agent(FunctionModel(script)).run_sync("read the password file", deps=deps)
    assert errors_seen, "the containment refusal never reached the model"


# ------------------------------------------------------------- any model -----
def test_the_same_agent_builds_for_any_provider_string():
    """The provider is a string; nothing in the agent depends on which one.

    `defer_model_check=True` because providers are constructed eagerly, so
    building an `openai:` agent without OPENAI_API_KEY would otherwise raise
    here rather than on the first request.
    """
    for model in ("anthropic:claude-opus-5-5", "openai:gpt-4o", "ollama:qwen2.5-coder"):
        assert build_agent(model, defer_model_check=True) is not None


def test_a_missing_api_key_fails_at_build_time_with_a_clear_message(monkeypatch):
    """Fail fast: you learn the key is missing before the agent does any work."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(UserError, match="OPENAI_API_KEY"):
        build_agent("openai:gpt-4o")


def test_arbitrary_tool_arguments_cannot_damage_a_file(deps):
    """TestModel calls every tool with generated junk. Nothing may be applied.

    The edit rules reject it on every path - the generated `old_text` is not in
    the file, or equals `new_text` - so the run ends in UnexpectedModelBehavior
    once the retries are spent. A model that cannot produce a valid anchor gets
    nowhere, which is the design: no fuzzy matching means no lucky guesses.
    """
    before = (deps.workspace.root / "main.py").read_text()

    with pytest.raises(UnexpectedModelBehavior):
        build_agent(TestModel()).run_sync("do something", deps=deps)

    assert (deps.workspace.root / "main.py").read_text() == before
