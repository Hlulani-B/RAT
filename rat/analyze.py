"""History analysis: one pass over ``git log`` → all metric-bearing rows.

The analyzer walks every non-merge commit once (streaming, newest first),
resolves author identities (post-mailmap) and paths (with parent-directory
chains), stores raw file-level deltas and pre-aggregates per-commit
*directory* deltas for every ancestor of each changed file.

With that pre-aggregation, every metric in the spec reduces to a simple
indexed SQL aggregation over the filtered commit set — no matter how large
the history is.
"""

from __future__ import annotations

from typing import Callable, List, Optional

from . import githistory

BATCH_COMMITS = 2000  # commits per flush (keeps WAL growth and fsyncs low)


class Analyzer:
    """Streams a repository's history into the store."""

    def __init__(self, store, repo_id: int, repo_dir: str,
                 progress: Optional[Callable[[int, int], None]] = None):
        self.store = store
        self.repo_id = repo_id
        self.repo_dir = repo_dir
        self.progress = progress or (lambda done, total: None)
        self.c = store.conn()
        self._identities = {}  # (name, email) -> identity id
        self._paths = {}       # (kind, path) -> path id
        self._root = None      # root directory path id

    # ------------------------------------------------------------------ run

    def run(self) -> int:
        """Analyse the repository; returns the number of commits stored."""
        total = githistory.count_commits(self.repo_dir)
        self._root = self._ensure_path(1, "")
        done = 0
        file_rows: List[tuple] = []
        dir_rows: List[tuple] = []
        n_pending = 0
        try:
            for commit in githistory.iter_history(self.repo_dir):
                n_pending += self._stage_commit(commit, file_rows, dir_rows)
                done += 1
                if n_pending >= BATCH_COMMITS:
                    self._flush(file_rows, dir_rows)
                    n_pending = 0
                    self.progress(done, total)
            self._flush(file_rows, dir_rows)
            self.progress(done, total)
        except Exception:
            try:
                self.c.rollback()
            except Exception:
                pass
            raise
        return done

    # -------------------------------------------------------------- staging

    def _stage_commit(self, commit: dict, file_rows: List[tuple],
                      dir_rows: List[tuple]) -> int:
        """Insert one commit + its file deltas + rolled-up directory deltas."""
        identity_id = self._ensure_identity(commit["name"], commit["email"])
        file_vals = []           # (path_id, added, removed)
        dir_acc = {}             # dir path id -> [added, removed]
        total_added = total_removed = 0

        for added, removed, path in commit["entries"]:
            path_id = self._ensure_path(0, path)
            file_vals.append((path_id, added, removed))
            total_added += added
            total_removed += removed
            # Roll up into every ancestor directory (root included) so that
            # directory metrics are subtree sums of the file deltas.
            d = path.rsplit("/", 1)[0] if "/" in path else ""
            while True:
                did = self._root if d == "" else self._ensure_path(1, d)
                t = dir_acc.get(did)
                if t is None:
                    dir_acc[did] = [added, removed]
                else:
                    t[0] += added
                    t[1] += removed
                if d == "":
                    break
                d = d.rsplit("/", 1)[0] if "/" in d else ""

        cid = self.c.execute(
            "INSERT INTO commits (repo_id, hash, ts, identity_id, subject,"
            " added, removed) VALUES (?,?,?,?,?,?,?)",
            (self.repo_id, commit["hash"], commit["ts"], identity_id,
             commit["subject"], total_added, total_removed),
        ).lastrowid

        for path_id, added, removed in file_vals:
            file_rows.append((self.repo_id, cid, path_id, added, removed))
        for did, (added, removed) in dir_acc.items():
            dir_rows.append((self.repo_id, cid, did, added, removed))
        return 1

    def _flush(self, file_rows: List[tuple], dir_rows: List[tuple]) -> None:
        c = self.c
        if file_rows:
            c.executemany(
                "INSERT INTO file_deltas (repo_id, commit_id, path_id, added,"
                " removed) VALUES (?,?,?,?,?)", file_rows)
        if dir_rows:
            c.executemany(
                "INSERT INTO dir_deltas (repo_id, commit_id, path_id, added,"
                " removed) VALUES (?,?,?,?,?)", dir_rows)
        c.commit()
        file_rows.clear()
        dir_rows.clear()

    # ------------------------------------------------------------- helpers

    def _ensure_identity(self, name: str, email: str) -> int:
        key = (name, email)
        iid = self._identities.get(key)
        if iid is not None:
            return iid
        c = self.c
        iid = c.execute(
            "INSERT INTO identities (repo_id, name, email) VALUES (?,?,?)",
            (self.repo_id, name, email),
        ).lastrowid
        # Every identity starts life as its own canonical author; manual
        # merges later simply re-point identity_map rows.
        aid = c.execute(
            "INSERT INTO authors (repo_id, name) VALUES (?,?)",
            (self.repo_id, name or email),
        ).lastrowid
        c.execute(
            "INSERT INTO identity_map (identity_id, repo_id, author_id)"
            " VALUES (?,?,?)", (iid, self.repo_id, aid))
        self._identities[key] = iid
        return iid

    def _ensure_path(self, kind: int, path: str) -> int:
        key = (kind, path)
        pid = self._paths.get(key)
        if pid is not None:
            return pid
        if path == "":
            parent, name = 0, ""
        else:
            i = path.rfind("/")
            name = path[i + 1:]
            parent = self._ensure_path(1, path[:i] if i >= 0 else "")
        pid = self.c.execute(
            "INSERT INTO paths (repo_id, kind, path, name, parent_id)"
            " VALUES (?,?,?,?,?)",
            (self.repo_id, kind, path, name, parent),
        ).lastrowid
        self._paths[key] = pid
        return pid
