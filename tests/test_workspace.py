"""Tests for workspace containment and the read-before-write rule.

The escape attempts are the reason this file exists. All three ways out of a
directory must fail the same single check.
"""

from __future__ import annotations

import pytest

from ai_agent.edit import EditError
from ai_agent.workspace import (
    NotReadError,
    PathEscape,
    ReadOnly,
    Workspace,
    WorkspaceError,
)


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "util.py").write_text("y = 2\n", encoding="utf-8")
    return Workspace(tmp_path)


# ----------------------------------------------------------------- escapes ---
def test_dot_dot_cannot_escape(ws):
    with pytest.raises(PathEscape):
        ws.read_file("../secrets.txt")


def test_deeply_nested_dot_dot_cannot_escape(ws):
    with pytest.raises(PathEscape):
        ws.read_file("pkg/../../../../etc/passwd")


def test_an_absolute_path_cannot_escape(ws):
    with pytest.raises(PathEscape):
        ws.read_file("/etc/passwd")


def test_a_symlink_pointing_out_cannot_escape(ws, tmp_path):
    """resolve() follows the link, so it is rejected like any outside path."""
    outside = tmp_path.parent / "outside_secret.txt"
    outside.write_text("secret\n", encoding="utf-8")
    (ws.root / "link.txt").symlink_to(outside)

    with pytest.raises(PathEscape):
        ws.read_file("link.txt")


def test_a_symlink_inside_the_root_is_fine(ws):
    (ws.root / "alias.py").symlink_to(ws.root / "main.py")
    assert ws.read_file("alias.py") == "x = 1\n"


def test_writing_through_an_escaping_path_is_refused(ws, tmp_path):
    """Containment covers writes, not just reads."""
    with pytest.raises(PathEscape):
        ws.create_file("../escaped.py", "oops")
    assert not (tmp_path.parent / "escaped.py").exists()


def test_the_error_says_how_to_write_the_path(ws):
    with pytest.raises(PathEscape) as exc:
        ws.read_file("../x")
    assert "relative to the workspace root" in str(exc.value)


def test_an_absolute_path_inside_the_root_is_accepted(ws):
    """Containment is about where a path lands, not how it is spelled."""
    assert ws.read_file(str(ws.root / "main.py")) == "x = 1\n"


def test_the_escape_message_does_not_ban_what_the_check_allows(ws):
    """The error text and the check must agree about absolute paths."""
    with pytest.raises(PathEscape) as exc:
        ws.read_file("../x")
    assert "leading slash" not in str(exc.value)


def test_a_normal_nested_path_works(ws):
    assert ws.read_file("pkg/util.py") == "y = 2\n"


def test_root_must_be_a_directory(tmp_path):
    f = tmp_path / "not_a_dir.txt"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(WorkspaceError):
        Workspace(f)


# -------------------------------------------------------- read before write --
def test_editing_without_reading_is_refused(ws):
    with pytest.raises(NotReadError) as exc:
        ws.edit_file("main.py", "x = 1", "x = 2")
    assert "read_file first" in str(exc.value)
    assert ws.resolve("main.py").read_text() == "x = 1\n"  # unchanged


def test_editing_after_reading_works(ws):
    ws.read_file("main.py")
    result = ws.edit_file("main.py", "x = 1", "x = 2")
    assert ws.resolve("main.py").read_text() == "x = 2\n"
    assert "+x = 2" in result.diff


def test_reading_one_file_does_not_unlock_another(ws):
    ws.read_file("main.py")
    with pytest.raises(NotReadError):
        ws.edit_file("pkg/util.py", "y = 2", "y = 3")


def test_a_failed_edit_leaves_the_file_alone(ws):
    ws.read_file("main.py")
    with pytest.raises(EditError):
        ws.edit_file("main.py", "not there", "whatever")
    assert ws.resolve("main.py").read_text() == "x = 1\n"


def test_read_paths_records_what_was_read(ws):
    assert ws.read_paths == set()
    ws.read_file("main.py")
    assert ws.resolve("main.py") in ws.read_paths


# --------------------------------------------------------------- create ------
def test_create_file_writes_and_returns_a_relative_path(ws):
    assert ws.create_file("new.py", "z = 3\n") == "new.py"
    assert ws.resolve("new.py").read_text() == "z = 3\n"


def test_create_file_makes_parent_directories(ws):
    ws.create_file("a/b/c.py", "deep = True\n")
    assert ws.resolve("a/b/c.py").is_file()


def test_create_file_never_overwrites(ws):
    with pytest.raises(WorkspaceError) as exc:
        ws.create_file("main.py", "clobbered")
    assert "already exists" in str(exc.value)
    assert ws.resolve("main.py").read_text() == "x = 1\n"


def test_a_created_file_can_be_edited_without_a_separate_read(ws):
    """We wrote it, so we know its exact bytes."""
    ws.create_file("new.py", "z = 3\n")
    ws.edit_file("new.py", "z = 3", "z = 4")
    assert ws.resolve("new.py").read_text() == "z = 4\n"


def test_editing_a_missing_file_is_refused(ws):
    with pytest.raises(WorkspaceError):
        ws.edit_file("ghost.py", "a", "b")


# ------------------------------------------------------------- read-only -----
def test_a_read_only_workspace_can_read(ws):
    reviewer = Workspace(ws.root, read_only=True)
    assert reviewer.read_file("main.py") == "x = 1\n"


def test_a_read_only_workspace_refuses_to_edit(ws):
    reviewer = Workspace(ws.root, read_only=True)
    reviewer.read_file("main.py")
    with pytest.raises(ReadOnly):
        reviewer.edit_file("main.py", "x = 1", "x = 2")


def test_a_read_only_workspace_refuses_to_create(ws):
    reviewer = Workspace(ws.root, read_only=True)
    with pytest.raises(ReadOnly):
        reviewer.create_file("new.py", "z = 3")


# ------------------------------------------------------------- listing -------
def test_list_files_returns_relative_paths(ws):
    assert set(ws.list_files()) == {"main.py", "pkg/util.py"}


def test_list_files_takes_a_glob(ws):
    (ws.root / "notes.md").write_text("hi", encoding="utf-8")
    assert ws.list_files("**/*.md") == ["notes.md"]


def test_list_files_skips_noise_directories(ws):
    (ws.root / "__pycache__").mkdir()
    (ws.root / "__pycache__" / "main.cpython-313.pyc").write_bytes(b"\x00")
    (ws.root / ".git").mkdir()
    (ws.root / ".git" / "config").write_text("[core]", encoding="utf-8")

    listed = ws.list_files()
    assert all("__pycache__" not in p and ".git" not in p for p in listed)
