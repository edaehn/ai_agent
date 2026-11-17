"""The edit primitive: exact-match, uniqueness-checked string replacement.

Pure string functions, no file IO - ``workspace.py`` does the reading and writing.
Keeping them apart is what makes the interesting rules testable without a disk.

Why not simply write the whole file? Because asking a model to return a 600-line
module with one function changed gets you 600 regenerated lines: a dropped
docstring, three reformatted functions you did not ask for, and no signal that
anything was lost. The damage is invisible in the model's own output and only
shows up in ``git diff``. A targeted replacement can only change what it matches.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass

__all__ = ["EditError", "EditResult", "apply_edit", "unified_diff"]


class EditError(ValueError):
    """An edit that must not be applied.

    The message is written for the *model* to read, not just a human: it says
    what was wrong and what to do differently. That text is the whole
    self-correction mechanism, so it is part of the design rather than an
    afterthought.
    """


@dataclass(frozen=True)
class EditResult:
    """The outcome of a successful edit."""

    text: str
    """The full new file contents."""

    diff: str
    """A unified diff, for logging and for showing a human."""


def unified_diff(before: str, after: str, path: str = "file") -> str:
    """A unified diff between two versions of a file."""
    lines = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        n=3,
    )
    return "".join(lines)


def apply_edit(
    text: str,
    old_text: str,
    new_text: str,
    *,
    path: str = "file",
) -> EditResult:
    """Replace ``old_text`` with ``new_text``, which must match exactly once.

    The uniqueness rule is the point. Zero matches and several matches are both
    errors, and both raise ``EditError`` rather than guessing:

    * **Zero matches** usually means the model is editing from memory of the file
      rather than from its bytes - a retyped anchor with different whitespace, or
      a line that is not there at all. Silently doing nothing would let the agent
      report success having changed nothing.
    * **Several matches** means the anchor is ambiguous. Replacing the first
      occurrence is a coin flip, and replacing all of them is a different edit
      from the one that was asked for.

    There is deliberately no fuzzy matching and no "closest match" fallback. An
    agent that can approximately match anchors can approximately destroy code.
    """
    if not old_text:
        raise EditError(
            "old_text is empty. To create a new file use create_file; to change "
            "an existing one, pass the exact text you want replaced."
        )

    if old_text == new_text:
        raise EditError(
            "old_text and new_text are identical, so this edit would change "
            "nothing. If the file already says what you want, move on to the "
            "next step instead of re-editing it."
        )

    count = text.count(old_text)

    if count == 0:
        raise EditError(
            f"old_text was not found in {path}. Read the file again and copy the "
            "exact text, including indentation and line breaks - do not retype it "
            "from memory. Whitespace is the usual culprit."
        )

    if count > 1:
        raise EditError(
            f"old_text appears {count} times in {path}, so it is ambiguous. "
            "Include more surrounding context - the enclosing function signature, "
            "a nearby comment - so the text you pass matches exactly one place."
        )

    new_full_text = text.replace(old_text, new_text, 1)
    return EditResult(
        text=new_full_text,
        diff=unified_diff(text, new_full_text, path),
    )
