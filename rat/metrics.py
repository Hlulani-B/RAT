"""Metric engine (spec §2): file / directory / repository / commit-set / author.

Everything is computed from pre-aggregated tables via indexed SQL:

  commits      per-commit totals (added, removed) → repository & timeline KPIs
  file_deltas  per (commit, file) rows            → file metrics + authorship
  dir_deltas   per (commit, ancestor dir) rows    → rolled-up directory metrics

A commit set ``H`` is described by a filter dict and materialised as a CTE
joining ``commits → identity_map``, so an author filter *restricts H itself*:

  H_t      = { h | ts_from ≤ h[cd] }               (time → present)
  H_i,j    = { h | i ≤ h[cd] < j }                 (period of time)
  manual   = an explicitly selected subset of H̄
  author a = { h | a = h[a] }                      (intersection)
  scope    = H̄ reachable from a non-HEAD h_r      (stored in ``scope``)

Derived metrics: growth δ = l⁺−l⁻, churn λ = l⁺+l⁻, modifications n
(commits with λ>0 on the object), frequency η = n/|H|, churn rate ρ = λ/|H|
and ownership ω = λ_a/λ.  All of these reduce to SUM / COUNT(DISTINCT …)
over the same CTE, so filters and author merges stay cheap at any size.

Results are memoised per (repo, epoch, query); the repo epoch is bumped
whenever author merges invalidate the cached aggregates.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from typing import Dict, List, Optional

MAX_HASHES = 5000        # cap for manually-selected commit sets
MAX_MULTI_IDS = 200      # cap for multi-object author breakdowns


class Metrics:
    def __init__(self, store):
        self.store = store
        self._lock = threading.Lock()
        self._cache: "OrderedDict[str, object]" = OrderedDict()
        self._cache_cap = 96

    # ------------------------------------------------------------- plumbing

    def _key(self, repo_id: int, name: str, f: dict, extra: str = "") -> str:
        epoch = self.store.get_repo(repo_id)["epoch"]
        h = f.get("hashes")
        hkey = hashlib.md5((",".join(sorted(h))).encode()).hexdigest() if h is not None else "-"
        return json.dumps([
            repo_id, epoch, name, extra,
            f.get("ts_from"), f.get("ts_to"), f.get("author_id"), hkey,
        ])

    def _get(self, key: str):
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        return None

    def _put(self, key: str, value):
        with self._lock:
            self._cache[key] = value
            while len(self._cache) > self._cache_cap:
                self._cache.popitem(last=False)
        return value

    def _sel(self, repo_id: int, f: dict):
        """Build the ``WITH sel AS (…)`` fragment + params for filter set f."""
        conn = self.store.conn()
        where = ["c.repo_id = ?"]
        args: List = [repo_id]
        if f.get("ts_from") is not None:
            where.append("c.ts >= ?")
            args.append(int(f["ts_from"]))
        if f.get("ts_to") is not None:
            where.append("c.ts < ?")
            args.append(int(f["ts_to"]))
        if f.get("author_id") is not None:
            where.append("im.author_id = ?")
            args.append(int(f["author_id"]))
        hashes = f.get("hashes")
        if hashes is not None:
            hashes = list(dict.fromkeys(hashes))[:MAX_HASHES]
            if not hashes:
                where.append("0")  # empty manual selection → empty H
            else:
                conn.execute("CREATE TEMP TABLE IF NOT EXISTS tmp_manual"
                             " (hash TEXT PRIMARY KEY)")
                conn.execute("DELETE FROM tmp_manual")
                conn.executemany(
                    "INSERT OR IGNORE INTO tmp_manual (hash) VALUES (?)",
                    [(h,) for h in hashes])
                conn.commit()  # release the writer lock before heavy reads
                where.append("c.hash IN (SELECT hash FROM tmp_manual)")
        if conn.execute("SELECT 1 FROM scope WHERE repo_id = ? LIMIT 1",
                        (repo_id,)).fetchone() is not None:
            # H̄ reachable from a non-HEAD reference commit h_r
            where.append("c.hash IN (SELECT hash FROM scope WHERE repo_id = ?)")
            args.append(repo_id)
        sql = ("WITH sel AS (SELECT c.id AS id, c.ts AS ts,"
               " im.author_id AS author_id, c.added AS added,"
               " c.removed AS removed"
               " FROM commits c"
               " JOIN identity_map im ON im.identity_id = c.identity_id"
               " WHERE " + " AND ".join(where) + ") ")
        return sql, args

    def _path_rows(self, repo_id: int) -> List[dict]:
        key = self._key(repo_id, "paths", {})
        cached = self._get(key)
        if cached is None:
            rows = self.store.conn().execute(
                "SELECT id, kind, path, name, parent_id FROM paths"
                " WHERE repo_id = ? ORDER BY path", (repo_id,)).fetchall()
            cached = self._put(key, [dict(r) for r in rows])
        return cached

    def _author_names(self, repo_id: int) -> Dict[int, str]:
        key = self._key(repo_id, "author_names", {})
        cached = self._get(key)
        if cached is None:
            rows = self.store.conn().execute(
                "SELECT id, name FROM authors WHERE repo_id = ?",
                (repo_id,)).fetchall()
            cached = self._put(key, {r["id"]: r["name"] for r in rows})
        return cached

    def _h(self, repo_id: int, f: dict) -> int:
        s = self.summary(repo_id, f)
        return s["commits"]

    def _aggs(self, repo_id: int, f: dict, kind: int):
        """Aggregate every object of a kind over H: {pid: {a, r, n}}, |H|."""
        key = self._key(repo_id, "aggs", f, str(kind))
        cached = self._get(key)
        if cached is not None:
            return cached
        table = "file_deltas" if kind == 0 else "dir_deltas"
        sel, args = self._sel(repo_id, f)
        conn = self.store.conn()
        h = conn.execute(sel + "SELECT COUNT(*) FROM sel", args).fetchone()[0]
        rows = conn.execute(
            sel + f"SELECT d.path_id AS pid, COALESCE(SUM(d.added),0) AS a,"
            f" COALESCE(SUM(d.removed),0) AS r,"
            f" COUNT(DISTINCT CASE WHEN d.added + d.removed > 0"
            f" THEN d.commit_id END) AS n"
            f" FROM {table} d JOIN sel s ON s.id = d.commit_id"
            f" GROUP BY d.path_id", args).fetchall()
        out = {r["pid"]: {"a": r["a"], "r": r["r"], "n": r["n"]} for r in rows}
        return self._put(key, (h, out))

    # ------------------------------------------------------------ endpoints

    def summary(self, repo_id: int, f: dict) -> dict:
        key = self._key(repo_id, "summary", f)
        cached = self._get(key)
        if cached is not None:
            return cached
        sel, args = self._sel(repo_id, f)
        conn = self.store.conn()
        row = conn.execute(
            sel + "SELECT COUNT(*) AS n, COALESCE(SUM(added),0) AS a,"
            " COALESCE(SUM(removed),0) AS r, MIN(ts) AS mn, MAX(ts) AS mx,"
            " COUNT(DISTINCT author_id) AS authors FROM sel", args).fetchone()
        ft = conn.execute(
            sel + "SELECT COUNT(DISTINCT d.path_id) AS ft FROM file_deltas d"
            " JOIN sel s ON s.id = d.commit_id", args).fetchone()[0]
        added, removed = row["a"], row["r"]
        out = {
            "commits": row["n"],
            "added": added,
            "removed": removed,
            "growth": added - removed,
            "churn": added + removed,
            "authors": row["authors"],
            "files_touched": ft,
            "ts_min": row["mn"],
            "ts_max": row["mx"],
        }
        return self._put(key, out)

    def timeline(self, repo_id: int, f: dict, bucket: Optional[int] = None) -> dict:
        sel, args = self._sel(repo_id, f)
        conn = self.store.conn()
        if bucket is None:
            head = conn.execute(
                sel + "SELECT MIN(ts) AS mn, MAX(ts) AS mx, COUNT(*) AS n"
                " FROM sel", args).fetchone()
            if not head["n"]:
                return {"bucket": 86400, "rows": []}
            span = max(head["mx"] - head["mn"], 1)
            bucket = 365 * 86400
            for size in (3600, 21600, 86400, 7 * 86400, 30 * 86400,
                         90 * 86400, 365 * 86400):
                if span / size <= 120:
                    bucket = size
                    break
        rows = conn.execute(
            sel + "SELECT (ts / ?) * ? AS bucket, COUNT(*) AS commits,"
            " COALESCE(SUM(added),0) AS added,"
            " COALESCE(SUM(removed),0) AS removed"
            " FROM sel GROUP BY bucket ORDER BY bucket",
            args + [bucket, bucket]).fetchall()
        return {"bucket": bucket, "rows": [dict(r) for r in rows]}

    def object_metrics(self, repo_id: int, f: dict, kind: int,
                       path_id: int) -> Optional[dict]:
        table = "file_deltas" if kind == 0 else "dir_deltas"
        sel, args = self._sel(repo_id, f)
        conn = self.store.conn()
        h = conn.execute(sel + "SELECT COUNT(*) FROM sel", args).fetchone()[0]
        row = conn.execute(
            sel + f"SELECT COALESCE(SUM(d.added),0) AS a,"
            f" COALESCE(SUM(d.removed),0) AS r,"
            f" COUNT(DISTINCT CASE WHEN d.added + d.removed > 0"
            f" THEN d.commit_id END) AS n"
            f" FROM {table} d JOIN sel s ON s.id = d.commit_id"
            f" WHERE d.path_id = ?", args + [path_id]).fetchone()
        authors = conn.execute(
            sel + f"SELECT s.author_id AS aid, COALESCE(SUM(d.added),0) AS a,"
            f" COALESCE(SUM(d.removed),0) AS r,"
            f" COUNT(DISTINCT CASE WHEN d.added + d.removed > 0"
            f" THEN d.commit_id END) AS n"
            f" FROM {table} d JOIN sel s ON s.id = d.commit_id"
            f" WHERE d.path_id = ? GROUP BY s.author_id"
            f" ORDER BY SUM(d.added) + SUM(d.removed) DESC",
            args + [path_id]).fetchall()
        names = self._author_names(repo_id)
        added, removed = row["a"], row["r"]
        churn = added + removed
        by_author = []
        for a in authors:
            achurn = a["a"] + a["r"]
            by_author.append({
                "author_id": a["aid"],
                "name": names.get(a["aid"], "?"),
                "added": a["a"],
                "removed": a["r"],
                "churn": achurn,
                "modifications": a["n"],
                "ownership": (achurn / churn) if churn else 0,
            })
        return {
            "path_id": path_id,
            "commits": h,
            "added": added,
            "removed": removed,
            "growth": added - removed,
            "churn": churn,
            "modifications": row["n"],
            "frequency": (row["n"] / h) if h else 0,
            "churn_rate": (churn / h) if h else 0,
            "by_author": by_author,
        }

    def children(self, repo_id: int, f: dict, dir_id: int) -> List[dict]:
        """Immediate children (files + subdirectories) of a directory with
        rolled-up metrics over H."""
        paths = self._path_rows(repo_id)
        h, file_aggs = self._aggs(repo_id, f, 0)
        _, dir_aggs = self._aggs(repo_id, f, 1)
        out = []
        for p in paths:
            if p["parent_id"] != dir_id:
                continue
            m = (file_aggs if p["kind"] == 0 else dir_aggs).get(p["id"])
            added = m["a"] if m else 0
            removed = m["r"] if m else 0
            n = m["n"] if m else 0
            churn = added + removed
            out.append({
                "id": p["id"], "kind": p["kind"], "path": p["path"],
                "name": p["name"],
                "added": added, "removed": removed,
                "growth": added - removed, "churn": churn,
                "modifications": n,
                "frequency": (n / h) if h else 0,
                "churn_rate": (churn / h) if h else 0,
            })
        out.sort(key=lambda r: r["churn"], reverse=True)
        return out

    def root_dir_id(self, repo_id: int) -> Optional[int]:
        for p in self._path_rows(repo_id):
            if p["kind"] == 1 and p["path"] == "":
                return p["id"]
        return None

    def resolve_path(self, repo_id: int, path: str, kind: int) -> Optional[dict]:
        for p in self._path_rows(repo_id):
            if p["kind"] == kind and p["path"] == path:
                return p
        return None

    def path_by_id(self, repo_id: int, path_id: int) -> Optional[dict]:
        for p in self._path_rows(repo_id):
            if p["id"] == path_id:
                return p
        return None

    def files(self, repo_id: int, f: dict, dir_path: str = "", q: str = "",
              sort: str = "churn", desc: bool = True,
              limit: int = 100, offset: int = 0) -> dict:
        h, aggs = self._aggs(repo_id, f, 0)
        paths = {p["id"]: p for p in self._path_rows(repo_id) if p["kind"] == 0}
        prefix = (dir_path + "/") if dir_path else ""
        rows = []
        ql = q.lower()
        for pid, m in aggs.items():
            p = paths.get(pid)
            if p is None:
                continue
            if prefix and not p["path"].startswith(prefix):
                continue
            if ql and ql not in p["path"].lower():
                continue
            added, removed = m["a"], m["r"]
            churn = added + removed
            rows.append({
                "id": pid, "path": p["path"],
                "added": added, "removed": removed,
                "growth": added - removed, "churn": churn,
                "modifications": m["n"],
                "frequency": (m["n"] / h) if h else 0,
                "churn_rate": (churn / h) if h else 0,
            })
        keys = {
            "churn": lambda r: (r["churn"], r["path"]),
            "added": lambda r: (r["added"], r["path"]),
            "removed": lambda r: (r["removed"], r["path"]),
            "growth": lambda r: (abs(r["growth"]), r["path"]),
            "modifications": lambda r: (r["modifications"], r["path"]),
            "churn_rate": lambda r: (r["churn_rate"], r["path"]),
            "path": lambda r: r["path"],
        }
        rows.sort(key=keys.get(sort, keys["churn"]), reverse=desc and sort != "path")
        total = len(rows)
        return {"h": h, "total": total,
                "rows": rows[offset:offset + limit]}

    def multi_authors(self, repo_id: int, f: dict, kind: int,
                      path_ids: List[int], top: int = 3) -> Dict[int, list]:
        """Ownership breakdown (top authors by churn) for many objects at
        once — used to draw stacked ownership bars in tables."""
        ids = [int(i) for i in path_ids][:MAX_MULTI_IDS]
        if not ids:
            return {}
        table = "file_deltas" if kind == 0 else "dir_deltas"
        sel, args = self._sel(repo_id, f)
        marks = ",".join("?" * len(ids))
        rows = self.store.conn().execute(
            sel + f"SELECT d.path_id AS pid, s.author_id AS aid,"
            f" SUM(d.added + d.removed) AS churn"
            f" FROM {table} d JOIN sel s ON s.id = d.commit_id"
            f" WHERE d.path_id IN ({marks})"
            f" GROUP BY d.path_id, s.author_id"
            f" ORDER BY churn DESC", args + ids).fetchall()
        names = self._author_names(repo_id)
        out: Dict[int, list] = {}
        for r in rows:
            bucket = out.setdefault(r["pid"], [])
            total = sum(x["churn"] for x in bucket)
            if len(bucket) < top:
                bucket.append({"author_id": r["aid"],
                               "name": names.get(r["aid"], "?"),
                               "churn": r["churn"]})
        # compute shares
        for pid, bucket in out.items():
            total = sum(x["churn"] for x in bucket)
            for x in bucket:
                x["share"] = (x["churn"] / total) if total else 0
        return out

    def authors_ranked(self, repo_id: int, f: dict) -> List[dict]:
        key = self._key(repo_id, "authors_ranked", f)
        cached = self._get(key)
        if cached is not None:
            return cached
        sel, args = self._sel(repo_id, f)
        rows = self.store.conn().execute(
            sel + "SELECT s.author_id AS aid, COUNT(*) AS commits,"
            " COALESCE(SUM(s.added),0) AS a, COALESCE(SUM(s.removed),0) AS r,"
            " SUM(CASE WHEN s.added + s.removed > 0 THEN 1 ELSE 0 END) AS n"
            " FROM sel s GROUP BY s.author_id", args).fetchall()
        names = self._author_names(repo_id)
        total_churn = sum(r["a"] + r["r"] for r in rows)
        out = []
        for r in rows:
            churn = r["a"] + r["r"]
            out.append({
                "author_id": r["aid"],
                "name": names.get(r["aid"], "?"),
                "commits": r["commits"],
                "added": r["a"], "removed": r["r"],
                "churn": churn, "modifications": r["n"],
                "ownership": (churn / total_churn) if total_churn else 0,
            })
        out.sort(key=lambda x: x["churn"], reverse=True)
        return self._put(key, out)

    def commits_page(self, repo_id: int, f: dict, q: str = "",
                     limit: int = 100, offset: int = 0) -> dict:
        sel, args = self._sel(repo_id, f)
        conn = self.store.conn()
        where, wargs = "", []
        if q:
            where = (" AND (LOWER(c.subject) LIKE ? OR c.hash LIKE ?"
                     " OR LOWER(a.name) LIKE ?)")
            like = f"%{q.lower()}%"
            wargs = [like, q.lower() + "%", like]
        total = conn.execute(
            sel + "SELECT COUNT(*) FROM sel s"
            " JOIN commits c ON c.id = s.id"
            " JOIN identity_map im ON im.identity_id = c.identity_id"
            " JOIN authors a ON a.id = im.author_id WHERE 1=1" + where,
            args + wargs).fetchone()[0]
        rows = conn.execute(
            sel + "SELECT c.hash AS hash, s.ts AS ts, c.subject AS subject,"
            " a.name AS author, s.added AS added, s.removed AS removed"
            " FROM sel s"
            " JOIN commits c ON c.id = s.id"
            " JOIN identity_map im ON im.identity_id = c.identity_id"
            " JOIN authors a ON a.id = im.author_id"
            " WHERE 1=1" + where +
            " ORDER BY s.ts DESC LIMIT ? OFFSET ?",
            args + wargs + [limit, offset]).fetchall()
        return {"total": total, "rows": [dict(r) for r in rows]}

    def history(self, repo_id: int, f: dict, kind: int, path_id: int,
                limit: int = 100) -> List[dict]:
        table = "file_deltas" if kind == 0 else "dir_deltas"
        sel, args = self._sel(repo_id, f)
        rows = self.store.conn().execute(
            sel + f"SELECT c.hash AS hash, s.ts AS ts, c.subject AS subject,"
            f" a.name AS author, d.added AS added, d.removed AS removed"
            f" FROM {table} d"
            f" JOIN sel s ON s.id = d.commit_id"
            f" JOIN commits c ON c.id = d.commit_id"
            f" JOIN identity_map im ON im.identity_id = c.identity_id"
            f" JOIN authors a ON a.id = im.author_id"
            f" WHERE d.path_id = ? ORDER BY s.ts DESC LIMIT ?",
            args + [path_id, limit]).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------- author mangmt

    def identities(self, repo_id: int) -> List[dict]:
        # Single-pass aggregate. The previous per-identity correlated
        # subqueries filtered commits by identity_id alone, which cannot use
        # the composite index commits(repo_id, identity_id) — they degraded
        # to two full commit scans per identity (minutes on 2.5k-identity
        # repositories) and blocked the dashboard render for large repos.
        rows = self.store.conn().execute(
            "SELECT i.id AS id, i.name AS name, i.email AS email,"
            " im.author_id AS author_id,"
            " COALESCE(agg.cnt, 0) AS commits,"
            " COALESCE(agg.churn, 0) AS churn"
            " FROM identities i"
            " JOIN identity_map im ON im.identity_id = i.id"
            " LEFT JOIN (SELECT identity_id, COUNT(*) AS cnt,"
            "                   SUM(added + removed) AS churn"
            "            FROM commits WHERE repo_id = ?"
            "            GROUP BY identity_id) agg"
            "   ON agg.identity_id = i.id"
            " WHERE i.repo_id = ? ORDER BY commits DESC, i.name",
            (repo_id, repo_id)).fetchall()
        return [dict(r) for r in rows]

    def author_list(self, repo_id: int) -> List[dict]:
        # Single-pass aggregates (see identities() above): per-author
        # correlated subqueries over commits become one grouped scan each.
        rows = self.store.conn().execute(
            "SELECT a.id AS id, a.name AS name,"
            " COALESCE(imc.n, 0) AS identities,"
            " COALESCE(agg.commits, 0) AS commits,"
            " COALESCE(agg.churn, 0) AS churn"
            " FROM authors a"
            " LEFT JOIN (SELECT author_id, COUNT(*) AS n FROM identity_map"
            "            GROUP BY author_id) imc ON imc.author_id = a.id"
            " LEFT JOIN (SELECT im.author_id AS aid, COUNT(*) AS commits,"
            "                   SUM(c.added + c.removed) AS churn"
            "            FROM commits c"
            "            JOIN identity_map im ON im.identity_id = c.identity_id"
            "            WHERE c.repo_id = ?"
            "            GROUP BY im.author_id) agg ON agg.aid = a.id"
            " WHERE a.repo_id = ? ORDER BY commits DESC",
            (repo_id, repo_id)).fetchall()
        return [dict(r) for r in rows]

    def repo_stats(self, repo_id: int) -> dict:
        conn = self.store.conn()
        authors = conn.execute(
            "SELECT COUNT(DISTINCT author_id) AS n FROM identity_map"
            " WHERE repo_id = ?", (repo_id,)).fetchone()["n"]
        files = conn.execute(
            "SELECT COUNT(*) AS n FROM paths WHERE repo_id = ? AND kind = 0",
            (repo_id,)).fetchone()["n"]
        return {"authors": authors, "files": files}
