"""Streaming parser for ``git log --numstat -z`` output.

The full history of a repository is produced by a single ``git log``
invocation and parsed incrementally, so ~100k-commit repositories can be
analysed without buffering the entire diff log in memory.

Wire format (verified empirically against git 2.43):

    \\x01<hash>\\x1f<ctime>\\x1f<name>\\x1f<email>\\x1f<subject>\\x00\\n
    <added>\\t<removed>\\t<path>\\x00                    normal entry
    <added>\\t<removed>\\t\\x00<old>\\x00<new>\\x00      rename entry (detected at 50%)
    -\\t-\\t<path>\\x00                                  binary entry (not measured)

Flags used:
  * ``--no-merges``       H is the set of non-merge commits reachable from HEAD
  * ``-M50%``             rename detection at the 50% threshold
  * ``--use-mailmap``     .mailmap is applied before authors are observed
  * ``--numstat -z``      NUL-delimited, unquoted paths (spaces/UTF-8 safe)

Binary files are skipped entirely: "binary files are not measured".
"""

from __future__ import annotations

import re
import subprocess
from typing import Iterator, List, Optional, Tuple

COUNT_RE = re.compile(rb"^(\d+|-)\t(\d+|-)\t")
CHUNK = 1 << 20


class GitError(RuntimeError):
    """Raised when a git subprocess fails."""


def _dec(raw: bytes) -> str:
    return raw.decode("utf-8", "replace")


def _run(repo_dir: str, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", repo_dir, *args], capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise GitError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout


def is_git_repo(repo_dir: str) -> bool:
    """True if ``repo_dir`` contains a usable git repository with a HEAD."""
    try:
        _run(repo_dir, "rev-parse", "--verify", "HEAD")
        return True
    except GitError:
        return False


def head_info(repo_dir: str) -> Tuple[str, int]:
    """Return (head hash, head committer timestamp)."""
    out = _run(repo_dir, "log", "-1", "--format=%H%x1f%ct", "HEAD")
    hash_, ts = out.strip().split("\x1f")
    return hash_, int(ts)


def count_commits(repo_dir: str) -> int:
    """Number of non-merge commits reachable from HEAD (|H|)."""
    out = _run(repo_dir, "rev-list", "--count", "--no-merges", "HEAD")
    return int(out.strip() or 0)


REF_RE = re.compile(r"^[^\s]{1,200}$")


def resolve_ref(repo_dir: str, ref: str) -> str:
    """Resolve a commit-ish (hash / branch / tag) to a full commit hash.

    Raises :class:`GitError` when the ref cannot be resolved.
    """
    if not REF_RE.match(ref) or ref.startswith("-"):
        raise GitError(f"invalid ref name: {ref!r}")
    out = _run(repo_dir, "rev-parse", "--verify", f"{ref}^{{commit}}")
    return out.strip()


def ref_hashes(repo_dir: str, ref: str) -> List[str]:
    """Non-merge commits reachable from ``ref`` (the set H̄ for h_r = ref)."""
    out = _run(repo_dir, "rev-list", "--no-merges", ref)
    return out.split()


def default_branch(repo_dir: str) -> str:
    try:
        out = _run(repo_dir, "rev-parse", "--abbrev-ref", "HEAD")
        return out.strip() or "HEAD"
    except GitError:
        return "HEAD"


def iter_history(repo_dir: str) -> Iterator[dict]:
    """Yield one dict per non-merge commit, newest first.

    Each dict: ``hash``, ``ts`` (committer epoch), ``name``, ``email``,
    ``subject``, ``entries`` — a list of ``(added, removed, path)`` where
    ``path`` is the *new* path for renames and the old path for deletions.
    Binary files never appear in ``entries``.
    """
    cmd = [
        "git", "-C", repo_dir,
        "-c", "core.quotePath=false",
        "-c", "diff.renameLimit=8000",
        "log",
        "--no-merges",
        "-M50%",
        "--use-mailmap",
        "--numstat",
        "-z",
        "--format=%x01%H%x1f%ct%x1f%aN%x1f%aE%x1f%s",
        "HEAD",
    ]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    assert proc.stdout is not None and proc.stderr is not None
    try:
        yield from _feed(proc.stdout)
        code = proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    if code != 0:
        err = proc.stderr.read().decode("utf-8", "replace").strip()
        raise GitError(err or f"git log exited with code {code}")


def _feed(stream) -> Iterator[dict]:
    """Incremental tokenizer; see module docstring for the wire format."""
    buf = b""
    cur: Optional[dict] = None
    rename: Optional[List] = None  # [added, removed, old|None]
    bin_rename = 0  # remaining path tokens of a skipped binary rename

    while True:
        chunk = stream.read(CHUNK)
        if chunk:
            buf += chunk
        while True:
            idx = buf.find(b"\x00")
            if idx < 0:
                break
            tok = buf[:idx]
            buf = buf[idx + 1:]

            # A commit marker may share a token with a stray "\n" emitted
            # after a diff-less commit header.
            if b"\x01" in tok:
                i = tok.index(b"\x01")
                if cur is not None:
                    yield cur
                    cur = None
                fields = tok[i + 1:].split(b"\x1f")
                if len(fields) >= 2:
                    cur = {
                        "hash": _dec(fields[0]),
                        "ts": int(fields[1]) if fields[1].isdigit() else 0,
                        "name": _dec(fields[2]) if len(fields) > 2 else "",
                        "email": _dec(fields[3]) if len(fields) > 3 else "",
                        "subject": _dec(fields[4]) if len(fields) > 4 else "",
                        "entries": [],
                    }
                rename, bin_rename = None, 0
                continue

            if cur is None:
                continue

            if rename is not None:
                if rename[2] is None:
                    rename[2] = tok  # old path (metrics are attributed to new)
                    continue
                cur["entries"].append((rename[0], rename[1], _dec(tok)))
                rename = None
                continue

            if bin_rename:
                bin_rename -= 1
                continue

            s = tok.lstrip(b"\n")
            m = COUNT_RE.match(s)
            if not m:
                continue  # stray separator / unknown token
            rest = s[m.end():]
            if m.group(1) == b"-":  # binary file → not measured
                if rest == b"":
                    bin_rename = 2
                continue
            added, removed = int(m.group(1)), int(m.group(2))
            if rest == b"":  # rename: next two tokens are old and new paths
                rename = [added, removed, None]
            else:
                cur["entries"].append((added, removed, _dec(rest)))

        if not chunk:
            break

    if cur is not None:
        yield cur
