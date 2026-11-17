"""Tests for the edit primitive. The failure cases are the interesting ones."""

from __future__ import annotations

import pytest

from ai_agent.edit import EditError, apply_edit, unified_diff

SOURCE = '''def greet(name):
    return "hello " + name


def farewell(name):
    return "bye " + name
'''


def test_a_unique_anchor_is_replaced():
    result = apply_edit(SOURCE, '"hello " + name', 'f"hello {name}"')
    assert 'f"hello {name}"' in result.text
    assert '"bye " + name' in result.text  # the other function is untouched


def test_only_the_matched_text_changes():
    """The whole point: an edit cannot touch what it did not match."""
    result = apply_edit(SOURCE, '"hello " + name', 'f"hello {name}"')
    before_lines = SOURCE.splitlines()
    after_lines = result.text.splitlines()
    changed = [
        i for i, (a, b) in enumerate(zip(before_lines, after_lines)) if a != b
    ]
    assert len(changed) == 1


def test_a_missing_anchor_is_an_error_not_a_no_op():
    with pytest.raises(EditError) as exc:
        apply_edit(SOURCE, "def nonexistent():", "def something():")
    assert "not found" in str(exc.value)


def test_an_ambiguous_anchor_is_refused_with_its_count():
    """'+ name' appears twice. Replacing the first would be a coin flip."""
    with pytest.raises(EditError) as exc:
        apply_edit(SOURCE, "+ name", "+ str(name)")
    message = str(exc.value)
    assert "2 times" in message
    assert "ambiguous" in message


def test_disambiguating_with_more_context_works():
    """The fix the error message asks for actually resolves it."""
    result = apply_edit(SOURCE, '"bye " + name', '"bye " + str(name)')
    assert '"bye " + str(name)' in result.text


def test_whitespace_must_match_exactly():
    """No fuzzy matching: a retyped anchor with the wrong indent fails."""
    with pytest.raises(EditError):
        apply_edit(SOURCE, 'return  "hello " + name', "pass")


def test_an_empty_anchor_is_refused():
    with pytest.raises(EditError) as exc:
        apply_edit(SOURCE, "", "anything")
    assert "empty" in str(exc.value)


def test_a_no_op_edit_is_refused():
    """Identical old and new text would waste a round and report false success."""
    with pytest.raises(EditError) as exc:
        apply_edit(SOURCE, "def greet(name):", "def greet(name):")
    assert "identical" in str(exc.value)


def test_error_messages_tell_the_model_what_to_do():
    """The message is the self-correction channel, so it must be actionable."""
    with pytest.raises(EditError) as missing:
        apply_edit(SOURCE, "nope", "x")
    assert "Read the file again" in str(missing.value)

    with pytest.raises(EditError) as ambiguous:
        apply_edit(SOURCE, "+ name", "x")
    assert "more surrounding context" in str(ambiguous.value)


def test_applying_the_same_edit_twice_fails_the_second_time():
    """Not idempotent, and that is correct: the anchor is gone after the first."""
    once = apply_edit(SOURCE, '"hello " + name', 'f"hello {name}"')
    with pytest.raises(EditError):
        apply_edit(once.text, '"hello " + name', 'f"hello {name}"')


def test_a_multiline_anchor_works():
    old = 'def farewell(name):\n    return "bye " + name\n'
    result = apply_edit(SOURCE, old, 'def farewell(name):\n    return "goodbye"\n')
    assert '"goodbye"' in result.text
    assert "farewell" in result.text


def test_crlf_content_is_not_silently_rewritten():
    """We touch only the matched span, so other line endings survive."""
    crlf = 'a = 1\r\nb = 2\r\nc = 3\r\n'
    result = apply_edit(crlf, "b = 2", "b = 22")
    assert result.text == 'a = 1\r\nb = 22\r\nc = 3\r\n'


def test_a_missing_trailing_newline_stays_missing():
    result = apply_edit("x = 1", "1", "2")
    assert result.text == "x = 2"


def test_the_diff_names_the_path_and_shows_both_sides():
    result = apply_edit(SOURCE, '"bye " + name', '"goodbye"', path="greet.py")
    assert "a/greet.py" in result.diff
    assert "b/greet.py" in result.diff
    assert '-    return "bye " + name' in result.diff
    assert '+    return "goodbye"' in result.diff


def test_unified_diff_of_identical_text_is_empty():
    assert unified_diff("same", "same") == ""
