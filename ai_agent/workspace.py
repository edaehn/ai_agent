"""The workspace: one directory the agent cannot leave.

Containment is a single check applied to every path, and the three ways out of a
directory - ``..``, an absolute path, and a symlink pointing elsewhere - all fail
it, because ``Path.resolve()`` collapses all three into the same question: where
does this actually land?

**This is a guardrail, not a sandbox.** Two honest limits:

* It is time-of-check to time-of-use. A symlink swapped between ``resolve()`` and
  the ``open()`` that follows is not caught.
* It only governs the file tools. Give the same agent a shell and the containment
  becomes decorative, because a shell command can reach any path on the machine.
  That is why this project has no shell tool - see ``verify.py`` from v0.4.0,
  which runs one fixed command instead.

For untrusted work, use a real sandbox - a container or a VM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .edit import EditResult, apply_edit

__all__ = [
    "WorkspaceError",
    "PathEscape",
    "NotReadError",
    "ReadOnly",
    "Workspace",
]


class WorkspaceError(ValueError):
    """Base class for refusals. Messages are written for the model to read."""


class PathEscape(WorkspaceError):
    """A path that resolves outside the workspace root."""


class NotReadError(WorkspaceError):
    """An edit attempted on a file that has not been read in this session."""


class ReadOnly(WorkspaceError):
    """A write attempted on a read-only workspace."""


@dataclass
class Workspace:
    """A rooted view of a directory, with the agent's file operations on it.

    ``read_only=True`` is how a reviewer agent is built: it gets the same object
    with the write methods refusing, so there is no path by which a review can
    modify what it is reviewing.
    """

    root: Path
    read_only: bool = False
    _read: set[Path] = field(default_factory=set, repr=False)

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser().resolve()
        if not self.root.is_dir():
            raise WorkspaceError(f"workspace root {self.root} is not a directory")

    # ----------------------------------------------------------------- paths --
    def resolve(self, relpath: str | Path) -> Path:
        """Resolve ``relpath`` inside the root, or raise ``PathEscape``.

        ``resolve()`` is called before the containment test, not after, so a
        symlink inside the root that points outside it resolves to its real
        destination and is rejected like any other outside path.

        The check is about where a path lands, not how it is spelled, so an
        absolute path that already points inside the root is accepted. The
        model is still told to use relative paths, because that is what
        ``list_files`` returns.
        """
        candidate = (self.root / Path(relpath)).expanduser()
        resolved = candidate.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise PathEscape(
                f"{relpath!r} resolves to {resolved}, which is outside the "
                f"workspace {self.root}. Use a path relative to the workspace "
                "root, such as 'src/app.py'. Do not use '..' or a symlink that "
                "leads outside it."
            )
        return resolved

    def relative(self, path: Path) -> str:
        """The workspace-relative form of a resolved path, for logs and diffs."""
        return str(path.relative_to(self.root))

    # ------------------------------------------------------------------ read --
    def read_file(self, relpath: str) -> str:
        """Return the contents of a file, and remember that it was read.

        The bookkeeping matters: ``edit_file`` refuses to touch a file that has
        not been through here, because an exact-match anchor has to come from the
        bytes on disk rather than the model's recollection of them.
        """
        path = self.resolve(relpath)
        if not path.is_file():
            raise WorkspaceError(f"{relpath} does not exist or is not a file")
        text = path.read_text(encoding="utf-8")
        self._read.add(path)
        return text

    def list_files(self, pattern: str = "**/*") -> list[str]:
        """Workspace-relative paths of matching files, excluding noise."""
        skip = {".git", ".venv", "__pycache__", ".pytest_cache", "node_modules"}
        found = [
            self.relative(p)
            for p in sorted(self.root.glob(pattern))
            if p.is_file() and not (skip & set(p.relative_to(self.root).parts))
        ]
        return found

    @property
    def read_paths(self) -> set[Path]:
        """Files read in this session."""
        return set(self._read)

    # ----------------------------------------------------------------- write --
    def _check_writable(self) -> None:
        if self.read_only:
            raise ReadOnly(
                "this workspace is read-only, so no file can be changed. Report "
                "what you found instead of trying to fix it."
            )

    def edit_file(self, relpath: str, old_text: str, new_text: str) -> EditResult:
        """Apply one exact-match edit to an existing file.

        Raises ``EditError`` from ``apply_edit`` when the anchor is missing or
        ambiguous, and ``NotReadError`` when the file has not been read.
        """
        self._check_writable()
        path = self.resolve(relpath)
        if not path.is_file():
            raise WorkspaceError(f"{relpath} does not exist, so it cannot be edited")
        if path not in self._read:
            raise NotReadError(
                f"{relpath} has not been read in this session. Call read_file "
                "first and copy the anchor from what it returns."
            )

        result = apply_edit(
            path.read_text(encoding="utf-8"),
            old_text,
            new_text,
            path=relpath,
        )
        path.write_text(result.text, encoding="utf-8")
        return result

    def create_file(self, relpath: str, content: str) -> str:
        """Create a new file. Refuses to overwrite an existing one.

        Overwriting is ``edit_file``'s job, where the uniqueness check applies.
        Letting ``create_file`` clobber a file would be a whole-file write by
        another name, and would route around every protection in ``edit.py``.
        """
        self._check_writable()
        path = self.resolve(relpath)
        if path.exists():
            raise WorkspaceError(
                f"{relpath} already exists. Use read_file then edit_file to "
                "change it; create_file never overwrites."
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self._read.add(path)  # we know its exact contents, so editing is allowed
        return self.relative(path)
