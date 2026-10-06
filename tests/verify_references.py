#!/usr/bin/env python3
"""Replay the frozen metric dumps in repo-references/ against a live server.

``repo-references/*.csv`` hold complete metric snapshots (every repository,
author and object metric) for the three official test repositories.  This
script re-derives every numeric field from the RUNNING REST API and fails on
any mismatch — an end-to-end regression harness that exercises the whole
backend stack (HTTP -> metric engine -> SQLite) with thousands of comparisons.

Usage:
    python run.py                       # in another terminal: server must run
    python tests/verify_references.py [--url http://127.0.0.1:8000]
                                      [--only cJSON] [--sample 300]

Exit code 0 = every checked field matches; 1 = mismatches (first 25 printed).
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import os
import random
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REFS = os.path.join(HERE, "..", "repo-references")

REL_TOL = 1e-9          # float comparison tolerance (churn_rate, ownership …)


def get(url: str):
    with urllib.request.urlopen(url, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_author(cell: str):
    """'Max Bruckner <max@maxbruckner.de>' -> ('Max Bruckner', 'max@…')."""
    i = cell.rfind("<")
    if i > 0 and cell.rstrip().endswith(">"):
        return cell[:i].strip(), cell[i + 1:].rstrip()[:-1].strip()
    return cell.strip(), ""


class Resolver:
    """Join CSV author cells to API authors through identity emails.

    Matching by name alone is ambiguous whenever two identities share a name
    (Redis has four 'root' identities, git two 'Tom G. Christensen' with the
    same `added` totals) — an email join is exact.  Emails are compared
    case-sensitively: git prints %aE verbatim, and some identities differ
    only by email case (e.g. me@JonathonMah.com vs me@jonathonmah.com).
    """

    def __init__(self, identities):
        self.by_pair = {}   # (name, email) -> author_id
        self.by_email = {}  # email -> {author_id}
        self.by_name = {}   # name -> {author_id}
        for i in identities:
            name = i.get("name") or ""
            email = (i.get("email") or "").strip()
            self.by_pair[(name, email)] = i["author_id"]
            self.by_email.setdefault(email, set()).add(i["author_id"])
            self.by_name.setdefault(name, set()).add(i["author_id"])

    def author_id(self, cell: str):
        name, email = parse_author(cell)
        if (name, email) in self.by_pair:
            return self.by_pair[(name, email)]
        ids = self.by_email.get(email) if email else None
        if ids and len(ids) == 1:
            return next(iter(ids))
        ids = self.by_name.get(name) if not email else None
        if ids and len(ids) == 1:  # cell without an email: name-only fallback
            return next(iter(ids))
        return None


class Checker:
    def __init__(self):
        self.total = 0
        self.fails: list[str] = []

    def num(self, repo: str, what: str, got, want):
        self.total += 1
        ok = math.isclose(float(got), float(want), rel_tol=REL_TOL, abs_tol=REL_TOL)
        if not ok:
            self.fails.append(f"[{repo}] {what}: api={got!r} csv={want!r}")

    def eq(self, repo: str, what: str, got, want):
        self.total += 1
        if got != want:
            self.fails.append(f"[{repo}] {what}: api={got!r} csv={want!r}")

    def fail(self, repo: str, what: str):
        self.total += 1
        self.fails.append(f"[{repo}] {what}")


def check_authors(ck: Checker, name: str, rows, api_authors, label: str,
                  resolver: Resolver):
    """Compare per-author CSV rows against API rows (joined on email).

    Returns the set of API author_ids covered by the CSV rows.
    """
    api_by_id = {a["author_id"]: a for a in api_authors}
    matched = set()
    for r in rows:
        aid = resolver.author_id(r["author"])
        if aid is None:
            ck.fail(name, f"{label}{r['author']!r}: no identity matches "
                          f"this email")
            continue
        match = api_by_id.get(aid)
        if match is None:
            ck.fail(name, f"{label}{r['author']!r}: API has no row for "
                          f"author {aid}")
            continue
        matched.add(aid)
        for key in ("added", "removed", "churn", "modifications"):
            v = r.get(key, "")
            if v != "":
                ck.eq(name, f"{label}{r['author']}.{key}", match[key], int(v))
        ck.num(name, f"{label}{r['author']}.ownership", match["ownership"],
               float(r["ownership"]))
    return matched


def verify_repo(ck: Checker, base: str, repo_id: int, name: str,
                rows: list, sample_n: int):
    # ------------------------------------------------------ repository level
    repo_rows = [r for r in rows if r["object_type"] == "repository"]
    all_row = next(r for r in repo_rows if r["author"] == "ALL")
    s = get(f"{base}/api/repos/{repo_id}/summary")
    ck.eq(name, "summary.commits", s["commits"], int(all_row["commit_count"]))
    for key in ("added", "removed", "growth", "churn"):
        ck.eq(name, f"summary.{key}", s[key], int(all_row[key]))

    authors = get(f"{base}/api/repos/{repo_id}/authors")
    ident = get(f"{base}/api/repos/{repo_id}/authors/index")["identities"]
    resolver = Resolver(ident)
    author_rows = [r for r in repo_rows if r["author"] != "ALL"]
    matched = check_authors(ck, name, author_rows, authors, "author ", resolver)
    zero_only = []
    for a in authors:
        if a["author_id"] in matched:
            continue
        if a["churn"] == 0 and a["modifications"] == 0:
            zero_only.append(a["name"] or "?")
        else:
            ck.fail(name, f"author {a['name']!r} (id {a['author_id']}, "
                          f"churn {a['churn']}, mods {a['modifications']}): "
                          f"in the API but missing from the dump")
    if zero_only:
        # e.g. Redis 'gavinshark': one commit whose diff has no measured
        # lines (mode-only/binary) — the dump omits such zero authors,
        # which contributes nothing to any total.
        print(f"    note: {len(zero_only)} API author(s) with no measured "
              f"change are absent from the dump: {', '.join(sorted(zero_only))}")
    for key in ("added", "removed"):
        ck.eq(name, f"author {key} sum",
              sum(int(r[key]) for r in author_rows), int(all_row[key]))
    ck.eq(name, "modifications sum",
          sum(int(r["modifications"]) for r in author_rows),
          int(all_row["modifications"]))
    ck.eq(name, "api modifications sum",
          sum(a["modifications"] for a in authors), int(all_row["modifications"]))

    # ---------------------------------------------------------- object level
    paths = get(f"{base}/api/repos/{repo_id}/paths")["paths"]
    pmap = {(p["kind"], p["path"]): p["id"] for p in paths}
    groups: dict = {}
    for r in rows:
        if r["object_type"] != "repository":
            groups.setdefault((r["object_type"], r["path"]), []).append(r)

    def group_churn(g):
        for r in groups[g]:
            if r["author"] == "ALL":
                return int(r["churn"])
        return 0

    keys = sorted(groups)
    if len(keys) > sample_n:
        top = sorted(keys, key=group_churn, reverse=True)[:60]
        top_set = set(top)
        rest = [g for g in keys if g not in top_set]
        picked = top + random.Random(42).sample(rest, max(0, sample_n - len(top)))
    else:
        picked = keys

    checked = 0
    for otype, opath in sorted(picked):
        kind = 1 if otype == "directory" else 0
        pid = pmap.get((kind, opath))
        if pid is None:
            ck.fail(name, f"path missing from API: {opath}")
            continue
        try:
            m = get(f"{base}/api/repos/{repo_id}/objects"
                    f"?kind={kind}&id={pid}")["metrics"]
        except urllib.error.HTTPError as exc:
            ck.fail(name, f"objects?kind={kind}&id={pid} -> HTTP {exc.code}")
            continue
        grows = groups[(otype, opath)]
        allg = next(r for r in grows if r["author"] == "ALL")
        for key in ("added", "removed", "growth", "churn", "modifications"):
            ck.eq(name, f"{opath} .{key}", m[key], int(allg[key]))
        for key, col in (("frequency", "modification_frequency"),
                         ("churn_rate", "churn_rate")):
            v = allg.get(col, "")
            if v != "":
                ck.num(name, f"{opath} .{key}", m[key], float(v))
        check_authors(ck, name, [r for r in grows if r["author"] != "ALL"],
                      m["by_author"], f"{opath} ", resolver)
        checked += 1
        if checked % 100 == 0:
            print(f"    … {checked}/{len(picked)} objects checked", flush=True)
    print(f"    objects checked: {checked}/{len(keys)}"
          f"{'' if checked == len(keys) else ' (sampled)'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default="http://127.0.0.1:8000",
                    help="base URL of a running RAT server")
    ap.add_argument("--only", default=None, help="substring of a repo name")
    ap.add_argument("--sample", type=int, default=300,
                    help="max objects checked per repo (all if under)")
    args = ap.parse_args()
    base = args.url.rstrip("/")

    repos = get(base + "/api/repos")
    by_head = {r["head_hash"]: r for r in repos if r["status"] == "ready"}
    if not by_head:
        sys.exit("no ready repositories on the server")

    files = sorted(glob.glob(os.path.join(REFS, "*.csv")))
    if not files:
        sys.exit(f"no reference dumps found under {REFS}")

    ck = Checker()
    checked_any = False
    for path in files:
        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        name = rows[0]["repo"]
        ref = rows[0]["ref_sha"]
        if args.only and args.only.lower() not in name.lower():
            continue
        repo = by_head.get(ref)
        if repo is None:
            print(f"SKIP {name}: no ready repository with head {ref[:12]}…")
            continue
        checked_any = True
        before = len(ck.fails)
        n0 = ck.total
        print(f"{name}: verifying repository {repo['id']} "
              f"({repo['commit_count']} commits) against {os.path.basename(path)}")
        verify_repo(ck, base, repo["id"], name, rows, args.sample)
        print(f"{name}: {ck.total - n0} checks, {len(ck.fails) - before} failed")

    if not checked_any:
        sys.exit("no reference dump matched a ready repository")
    print(f"\n{ck.total} checks total — {len(ck.fails)} failed")
    for f in ck.fails[:25]:
        print("  FAIL", f)
    sys.exit(1 if ck.fails else 0)


if __name__ == "__main__":
    main()
