"""Repository ingestion: zip upload extraction and remote URL cloning.

Both flows funnel into :class:`rat.analyze.Analyzer`:

    zip  : safe extract → locate repo root → validate → analyze
    url  : git clone (full history, progress reported) → validate → analyze
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import zipfile
from typing import Callable, Optional

from . import githistory
from .analyze import Analyzer

URL_RE = re.compile(r"^(https?://|git://|ssh://|[\w.+-]+@[\w.-]+:).+", re.S)


class IngestError(RuntimeError):
    """Raised for user-facing ingestion failures."""


# --------------------------------------------------------------------- zip

def safe_extract(zip_path: str, dest: str) -> None:
    """Extract a zip, rejecting absolute paths, ``..`` and symlinks."""
    os.makedirs(dest, exist_ok=True)
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise IngestError("not a valid zip file") from exc
    with zf:
        for info in zf.infolist():
            name = info.filename
            parts = name.split("/")
            if name.startswith(("/", "\\")) or ".." in parts or (
                    len(name) > 1 and parts[0].endswith(":")):
                raise IngestError(f"unsafe path in zip: {name!r}")
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise IngestError(f"zip contains a symlink: {name!r}")
        zf.extractall(dest)


def find_repo_root(dest: str, max_depth: int = 2) -> Optional[str]:
    """Shallowest directory under ``dest`` that contains a ``.git`` entry."""
    def has_git(d: str) -> bool:
        return (os.path.isdir(os.path.join(d, ".git"))
                or os.path.isfile(os.path.join(d, ".git")))

    def subdirs(d: str):
        try:
            return [os.path.join(d, n) for n in sorted(os.listdir(d))
                    if os.path.isdir(os.path.join(d, n))]
        except OSError:
            return []

    level = [dest]
    for _ in range(max_depth + 1):
        for d in level:
            if has_git(d):
                return d
        nxt = []
        for d in level:
            nxt.extend(subdirs(d))
        level = nxt
    return None


def ingest_zip(store, repo_id: int, zip_path: str, set_progress: Callable) -> None:
    """Job body for a zip upload."""
    repo = store.get_repo(repo_id)
    dest = repo["dir"]
    store.update_repo(repo_id, status="analyzing", error=None)
    set_progress(0.05, "extracting zip")
    safe_extract(zip_path, dest)
    try:
        os.remove(zip_path)
    except OSError:
        pass

    root = find_repo_root(dest)
    if not root:
        raise IngestError(
            "no git repository found in the zip (expected a .git directory)")
    if not githistory.is_git_repo(root):
        raise IngestError(
            "found a .git entry but it has no commits or is not self-contained")

    set_progress(0.15, "reading history")
    _analyze(store, repo_id, root, set_progress, base=0.15, span=0.85)


# --------------------------------------------------------------------- url

def _clone(url: str, dest: str, set_progress: Callable) -> None:
    cmd = ["git", "clone", "--progress", "--", url, dest]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE)
    assert proc.stderr is not None
    pct = re.compile(rb"(\d+)%")
    last = 0.0
    tail = []
    for raw in proc.stderr:
        line = raw.decode("utf-8", "replace").strip()
        if not line:
            continue
        tail.append(line)
        tail = tail[-8:]
        m = pct.search(raw)
        if m:
            frac = int(m.group(1)) / 100.0
            if b"Receiving objects" in raw:
                overall = 0.05 + 0.50 * frac
            elif b"Resolving deltas" in raw:
                overall = 0.55 + 0.05 * frac
            elif b"Counting objects" in raw:
                overall = 0.02 * frac
            else:
                overall = last
            if overall >= last:
                last = overall
                set_progress(overall, line)
    code = proc.wait()
    if code != 0:
        raise IngestError("git clone failed: " + " | ".join(tail[-3:]))


def ingest_url(store, repo_id: int, url: str, set_progress: Callable) -> None:
    """Job body for a remote clone."""
    repo = store.get_repo(repo_id)
    dest = repo["dir"]
    store.update_repo(repo_id, status="cloning", error=None)
    set_progress(0.01, f"cloning {url}")
    _clone(url, dest, set_progress)
    if not githistory.is_git_repo(dest):
        raise IngestError("cloned repository has no commits (empty)")
    set_progress(0.60, "reading history")
    _analyze(store, repo_id, dest, set_progress, base=0.60, span=0.40)


# ----------------------------------------------------------------- common

def _analyze(store, repo_id: int, root: str, set_progress: Callable,
             base: float = 0.15, span: float = 0.85) -> None:
    """Shared tail: validate, stream history, finalise repo metadata."""
    store.update_repo(repo_id, status="analyzing", error=None)
    total = githistory.count_commits(root)

    def progress(done: int, total_n: int) -> None:
        frac = done / total_n if total_n else 1.0
        set_progress(min(base + span * frac, 0.999),
                     f"analyzing commit {done:,}/{total_n:,}")

    analyzer = Analyzer(store, repo_id, root, progress)
    stored = analyzer.run()
    head_hash, head_ts = githistory.head_info(root)
    store.update_repo(repo_id, status="ready", error=None,
                      head_hash=head_hash, head_ts=head_ts,
                      commit_count=stored, root=root)
    set_progress(1.0, f"ready — {stored:,} commits analysed")
