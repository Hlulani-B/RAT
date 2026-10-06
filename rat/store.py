"""SQLite persistence layer.

One database (``<data>/rat.db``) holds every repository's parsed history:

  repos         one row per ingested repository
  identities    raw author identities (post-mailmap), unique per (name, email)
  authors       *canonical* authors; identities are mapped onto authors and
                manual merges simply re-point ``identity_map`` rows
  commits       non-merge commits with per-commit totals (a + r) and subject
  paths         files and directories, with parent links for tree queries
  file_deltas   (commit, file) → lines added / removed
  dir_deltas    (commit, directory) → rolled-up lines added / removed

Directory rows are pre-aggregated for every ancestor of a changed file, so
every metric — including per-commit "was this object modified" — reduces to an
indexed SQL aggregation over the filtered commit set.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS repos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  source TEXT NOT NULL,                -- 'zip' | 'url'
  source_url TEXT,
  dir TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',  -- queued|cloning|analyzing|ready|error
  error TEXT,
  created_at INTEGER NOT NULL,
  head_hash TEXT,
  head_ts INTEGER,
  commit_count INTEGER NOT NULL DEFAULT 0,
  root TEXT,                           -- resolved git root (zip uploads may nest)
  ref TEXT,                            -- reference commit h_r (NULL = HEAD)
  epoch INTEGER NOT NULL DEFAULT 0     -- bumped when caches must be invalidated
);
CREATE TABLE IF NOT EXISTS scope (
  repo_id INTEGER NOT NULL,
  hash TEXT NOT NULL,
  PRIMARY KEY (repo_id, hash)
);
CREATE TABLE IF NOT EXISTS identities (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  email TEXT NOT NULL,
  UNIQUE (repo_id, name, email)
);
CREATE TABLE IF NOT EXISTS authors (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_id INTEGER NOT NULL,
  name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS identity_map (
  identity_id INTEGER PRIMARY KEY,
  repo_id INTEGER NOT NULL,
  author_id INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_imap_author ON identity_map(repo_id, author_id);
CREATE TABLE IF NOT EXISTS commits (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_id INTEGER NOT NULL,
  hash TEXT NOT NULL,
  ts INTEGER NOT NULL,
  identity_id INTEGER NOT NULL,
  subject TEXT NOT NULL DEFAULT '',
  added INTEGER NOT NULL DEFAULT 0,
  removed INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_commits_hash ON commits(repo_id, hash);
CREATE INDEX IF NOT EXISTS idx_commits_ts ON commits(repo_id, ts);
CREATE INDEX IF NOT EXISTS idx_commits_ident ON commits(repo_id, identity_id);
CREATE TABLE IF NOT EXISTS paths (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_id INTEGER NOT NULL,
  kind INTEGER NOT NULL,               -- 0 = file, 1 = directory
  path TEXT NOT NULL,                  -- '' = root directory
  name TEXT NOT NULL,
  parent_id INTEGER NOT NULL DEFAULT 0,
  UNIQUE (repo_id, kind, path)
);
CREATE INDEX IF NOT EXISTS idx_paths_parent ON paths(repo_id, parent_id);
CREATE TABLE IF NOT EXISTS file_deltas (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_id INTEGER NOT NULL,
  commit_id INTEGER NOT NULL,
  path_id INTEGER NOT NULL,
  added INTEGER NOT NULL,
  removed INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fd_commit ON file_deltas(commit_id);
CREATE INDEX IF NOT EXISTS idx_fd_path ON file_deltas(repo_id, path_id);
CREATE TABLE IF NOT EXISTS dir_deltas (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_id INTEGER NOT NULL,
  commit_id INTEGER NOT NULL,
  path_id INTEGER NOT NULL,
  added INTEGER NOT NULL,
  removed INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dd_commit ON dir_deltas(commit_id);
CREATE INDEX IF NOT EXISTS idx_dd_path ON dir_deltas(repo_id, path_id);
"""

_REPO_FIELDS = {
    "name", "source", "source_url", "dir", "status", "error",
    "head_hash", "head_ts", "commit_count", "root", "ref", "epoch",
}


class Store:
    """Thread-safe-enough SQLite helper: one connection per thread (WAL)."""

    def __init__(self, data_dir: str):
        self.data_dir = os.path.abspath(data_dir)
        self.repos_dir = os.path.join(self.data_dir, "repos")
        os.makedirs(self.repos_dir, exist_ok=True)
        self.db_path = os.path.join(self.data_dir, "rat.db")
        self._local = threading.local()
        with self.conn() as c:
            c.executescript(SCHEMA)
            cols = {r["name"] for r in c.execute("PRAGMA table_info(repos)")}
            for col in ("root", "ref"):
                if col not in cols:
                    c.execute(f"ALTER TABLE repos ADD COLUMN {col} TEXT")

    # ------------------------------------------------------------------ conn

    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.db_path, timeout=30)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = c
        return c

    # ----------------------------------------------------------------- repos

    def create_repo(self, name: str, source: str, source_url: Optional[str],
                    directory: str) -> int:
        with self.conn() as c:
            cur = c.execute(
                "INSERT INTO repos (name, source, source_url, dir, status, created_at)"
                " VALUES (?, ?, ?, ?, 'queued', ?)",
                (name, source, source_url, directory, int(time.time())),
            )
            return cur.lastrowid

    def update_repo(self, repo_id: int, **fields) -> None:
        bad = set(fields) - _REPO_FIELDS
        if bad:
            raise ValueError(f"unknown repo fields: {bad}")
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self.conn() as c:
            c.execute(
                f"UPDATE repos SET {cols} WHERE id = ?",
                (*fields.values(), repo_id),
            )

    def bump_epoch(self, repo_id: int) -> None:
        self.conn().execute(
            "UPDATE repos SET epoch = epoch + 1 WHERE id = ?", (repo_id,)
        )
        self.conn().commit()

    def get_repo(self, repo_id: int) -> Optional[dict]:
        row = self.conn().execute(
            "SELECT * FROM repos WHERE id = ?", (repo_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_repos(self) -> List[dict]:
        rows = self.conn().execute(
            "SELECT * FROM repos ORDER BY id DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_repo(self, repo_id: int) -> None:
        c = self.conn()
        with c:
            for table in ("file_deltas", "dir_deltas", "commits", "paths",
                          "identity_map", "identities", "authors", "scope"):
                c.execute(f"DELETE FROM {table} WHERE repo_id = ?", (repo_id,))
            c.execute("DELETE FROM repos WHERE id = ?", (repo_id,))

    # -------------------------------------------------------- reference

    def set_reference(self, repo_id: int, ref: Optional[str],
                      hashes: List[str]) -> None:
        """Point H̄ at a reference commit h_r (ref=None restores HEAD).

        ``hashes`` is the non-merge rev-list of h_r; the metric engine
        intersects every filter with this scope.
        """
        c = self.conn()
        with c:
            c.execute("DELETE FROM scope WHERE repo_id = ?", (repo_id,))
            if ref and hashes:
                c.executemany(
                    "INSERT OR IGNORE INTO scope (repo_id, hash) VALUES (?, ?)",
                    [(repo_id, h) for h in hashes])
            c.execute("UPDATE repos SET ref = ?, epoch = epoch + 1 WHERE id = ?",
                      (ref, repo_id))

    # ------------------------------------------------------------ authors

    def merge_identities(self, repo_id: int, name: str,
                         identity_ids: List[int]) -> int:
        """Merge raw identities into one canonical author (manual merge)."""
        with self.conn() as c:
            aid = c.execute(
                "INSERT INTO authors (repo_id, name) VALUES (?, ?)",
                (repo_id, name),
            ).lastrowid
            marks = ",".join("?" * len(identity_ids))
            c.execute(
                f"UPDATE identity_map SET author_id = ?"
                f" WHERE repo_id = ? AND identity_id IN ({marks})",
                (aid, repo_id, *identity_ids),
            )
            c.execute("UPDATE repos SET epoch = epoch + 1 WHERE id = ?",
                      (repo_id,))
        return aid

    def unmerge_author(self, repo_id: int, author_id: int) -> None:
        """Split a canonical author back into one author per identity."""
        c = self.conn()
        rows = c.execute(
            "SELECT im.identity_id AS iid, i.name AS name, i.email AS email"
            " FROM identity_map im"
            " JOIN identities i ON i.id = im.identity_id"
            " WHERE im.repo_id = ? AND im.author_id = ?",
            (repo_id, author_id)).fetchall()
        with c:
            for r in rows:
                aid = c.execute(
                    "INSERT INTO authors (repo_id, name) VALUES (?, ?)",
                    (repo_id, r["name"] or r["email"])).lastrowid
                c.execute(
                    "UPDATE identity_map SET author_id = ? WHERE identity_id = ?",
                    (aid, r["iid"]))
            c.execute("DELETE FROM authors WHERE id = ? AND repo_id = ?",
                      (author_id, repo_id))
            c.execute("UPDATE repos SET epoch = epoch + 1 WHERE id = ?",
                      (repo_id,))

    def rename_author(self, repo_id: int, author_id: int, name: str) -> None:
        with self.conn() as c:
            c.execute("UPDATE authors SET name = ? WHERE id = ? AND repo_id = ?",
                      (name, author_id, repo_id))
            c.execute("UPDATE repos SET epoch = epoch + 1 WHERE id = ?",
                      (repo_id,))
