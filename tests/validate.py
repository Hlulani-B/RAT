#!/usr/bin/env python3
"""Validation suite for the RAT metric engine.

Builds a deterministic scratch repository (real binary files, rename+edit,
deletion, empty file, spaces + non-ASCII paths, a merge commit and a mailmap),
analyses it through the real pipeline, and asserts every metric category
against hand-computed values.  It additionally cross-checks the engine's
repository totals against an independent ``git log --numstat`` summation.

Run:  python tests/validate.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from rat import githistory                      # noqa: E402
from rat.analyze import Analyzer                # noqa: E402
from rat.metrics import Metrics                 # noqa: E402
from rat.store import Store                     # noqa: E402

DAY = 86400
T0 = 1577836800  # 2020-01-01T00:00:00Z

FAILED = []


def check(name: str, actual, expected):
    ok = actual == expected
    if not ok:
        FAILED.append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: got {actual!r},"
          f" expected {expected!r}")


def iso(epoch: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+0000", time.gmtime(epoch))


class ScratchRepo:
    def __init__(self, path: str):
        self.path = path
        self.home = os.path.join(path, "_home")
        os.makedirs(self.home, exist_ok=True)
        shutil.rmtree(os.path.join(path, "repo"), ignore_errors=True)
        self.repo = os.path.join(path, "repo")
        os.makedirs(self.repo)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Alice A")
        self.git("config", "user.email", "alice@example.com")

    def git(self, *args, when: int = T0, name=None, email=None):
        env = dict(os.environ,
                   HOME=self.home,
                   GIT_CONFIG_NOSYSTEM="1",
                   GIT_AUTHOR_DATE=iso(when),
                   GIT_COMMITTER_DATE=iso(when))
        if name:
            env["GIT_AUTHOR_NAME"] = name
        if email:
            env["GIT_AUTHOR_EMAIL"] = email
        return subprocess.run(["git", *args], cwd=self.repo, env=env,
                              check=True, capture_output=True, text=True)

    def write(self, rel: str, data):
        full = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        mode = "wb" if isinstance(data, bytes) else "w"
        with open(full, mode) as fh:
            fh.write(data)

    def commit(self, msg: str, when: int, name=None, email=None) -> str:
        self.git("add", "-A", when=when, name=name, email=email)
        self.git("commit", "-q", "-m", msg, when=when, name=name, email=email)
        return self.git("rev-parse", "HEAD", when=when).stdout.strip()

    def rm(self, rel: str):
        self.git("rm", "-q", rel)


def build_repo(path: str) -> ScratchRepo:
    """Deterministic history.  H̄ = c1..c7 (merge c8 excluded).

    Totals over H̄:  added 20, removed 8, churn 28, growth 12, |H| 7.
    (c7 adds .mailmap itself, +1 line, on top of the README edit.)
    """
    r = ScratchRepo(path)
    # c1 —— create a.txt(3), src/b.py(5), blob.bin(binary → not measured)
    r.write("a.txt", "line1\nline2\nline3\n")
    r.write("src/b.py", "x\ny\nz\nw\nv\n")
    r.write("blob.bin", b"a\x00b\x00c\n")
    h1 = r.commit("c1 init", T0)

    # c2 —— modify a.txt(+2,-1); rename src/b.py → lib/b.py with edit(+2,-1)
    r.write("a.txt", "line1\nnew1\nnew2\nline3\n")
    os.makedirs(os.path.join(r.repo, "lib"), exist_ok=True)
    os.rename(os.path.join(r.repo, "src/b.py"),
              os.path.join(r.repo, "lib/b.py"))
    r.write("lib/b.py", "x\ny\nz2\nw\nv\nq\n")
    h2 = r.commit("c2 rename+edit", T0 + DAY)

    # c3 —— delete a.txt(0,-4); add README.md(2)
    r.rm("a.txt")
    r.write("README.md", "readme l1\nreadme l2\n")
    h3 = r.commit("c3 delete+add", T0 + 2 * DAY)

    # branch + merge (merge must be excluded from H̄)
    r.git("checkout", "-q", "-b", "feat", when=T0 + 2 * DAY)
    r.write("lib/b.py", "x\ny\nz2\nw2\nv\nq\n")
    h4 = r.commit("c4 branch", T0 + 3 * DAY)
    r.git("checkout", "-q", "main", when=T0 + 3 * DAY)
    r.write("README.md", "readme l1\nreadme l2\nreadme l3\n")
    h5 = r.commit("c5 main", T0 + 4 * DAY)

    # c6 —— binary + special filenames, by bob
    r.write("real.bin", b"\x00\x01\x02binary\x00\n")
    r.write("empty.txt", "")
    r.write("sp ace.txt", "spaces\n")
    r.write("\u00fcni.txt", "uni\n")
    h6 = r.commit("c6 binary+special", T0 + 5 * DAY,
                  name="Bob B", email="bob@example.com")

    # c7 —— .mailmap (+1, merges bob2 → bob), commit by bob2; README +1,-1
    r.write(".mailmap", "Bob B <bob@example.com> <bob2@example.com>\n")
    r.write("README.md", "readme l1\nreadme l2\nreadme l4\n")
    h7 = r.commit("c7 mailmap", T0 + 6 * DAY,
                  name="Bob B", email="bob2@example.com")

    r.git("merge", "-q", "--no-ff", "feat", "-m", "c8 merge",
          when=T0 + 7 * DAY)
    r.hashes = {"c1": h1, "c2": h2, "c3": h3, "c4": h4, "c5": h5,
                "c6": h6, "c7": h7}
    return r


def raw_totals(repo_dir: str):
    """Independent summation straight from git (binaries skipped)."""
    out = subprocess.run(
        ["git", "-C", repo_dir, "log", "--no-merges", "-M50%", "--numstat",
         "--format="],
        capture_output=True, text=True, check=True).stdout
    added = removed = 0
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[0] != "-" and parts[1] != "-":
            added += int(parts[0])
            removed += int(parts[1])
    return added, removed


def main():
    work = os.path.join(ROOT, "data", f"validate-{int(time.time())}")
    os.makedirs(work, exist_ok=True)
    print(f"scratch workspace: {work}")

    print("\n== build deterministic repository ==")
    scratch = build_repo(work)
    raw_added, raw_removed = raw_totals(scratch.repo)
    print(f"raw git totals: added={raw_added} removed={raw_removed}")

    print("\n== analyse through the real pipeline ==")
    store = Store(os.path.join(work, "ratdata"))
    repo_id = store.create_repo("scratch", "zip", None, scratch.repo)
    t0 = time.time()
    stored = Analyzer(store, repo_id, scratch.repo).run()
    dt = time.time() - t0
    head_hash, head_ts = githistory.head_info(scratch.repo)
    store.update_repo(repo_id, status="ready", head_hash=head_hash,
                      head_ts=head_ts, commit_count=stored)
    print(f"analysed {stored} commits in {dt:.2f}s")

    m = Metrics(store)
    h = store.conn().execute(
        "SELECT hash, ts FROM commits WHERE repo_id = ? ORDER BY ts",
        (repo_id,)).fetchall()
    by_ts = {r["ts"]: r["hash"] for r in h}

    # ---------------------------------------------------------------- KPI
    print("\n== repository metrics (all of H̄) ==")
    s = m.summary(repo_id, {})
    check("|H̄|", s["commits"], 7)
    check("H̄ added", s["added"], 20)
    check("H̄ removed", s["removed"], 8)
    check("H̄ growth", s["growth"], 12)
    check("H̄ churn", s["churn"], 28)
    check("H̄ added == raw git", s["added"], raw_added)
    check("H̄ removed == raw git", s["removed"], raw_removed)
    check("H̄ authors", s["authors"], 2)

    # ------------------------------------------------------- file metrics
    print("\n== file metrics ==")
    paths = m._path_rows(repo_id)
    pid = {(p["kind"], p["path"]): p["id"] for p in paths}

    def obj_metrics(kind, path):
        return m.object_metrics(repo_id, {}, kind, pid[(kind, path)])

    atxt = obj_metrics(0, "a.txt")
    check("a.txt added", atxt["added"], 5)
    check("a.txt removed", atxt["removed"], 5)
    check("a.txt growth", atxt["growth"], 0)
    check("a.txt churn", atxt["churn"], 10)
    check("a.txt modifications", atxt["modifications"], 3)
    check("a.txt frequency", round(atxt["frequency"], 6), round(3 / 7, 6))
    check("a.txt churn rate", round(atxt["churn_rate"], 6), round(10 / 7, 6))
    check("a.txt ownership", atxt["by_author"], [{
        "author_id": atxt["by_author"][0]["author_id"], "name": "Alice A",
        "added": 5, "removed": 5, "churn": 10, "modifications": 3,
        "ownership": 1.0,
    }])

    libb = obj_metrics(0, "lib/b.py")
    check("lib/b.py rename edit added", libb["added"], 3)
    check("lib/b.py rename edit removed", libb["removed"], 2)
    check("lib/b.py modifications", libb["modifications"], 2)
    srcb = obj_metrics(0, "src/b.py")
    check("src/b.py creation", (srcb["added"], srcb["modifications"]), (5, 1))

    empty = obj_metrics(0, "empty.txt")
    check("empty.txt no changes", (empty["added"], empty["churn"],
                                   empty["modifications"]), (0, 0, 0))

    files = m.files(repo_id, {})["rows"]
    fpaths = {r["path"] for r in files}
    check("binary blob.bin excluded", "blob.bin" in fpaths, False)
    check("binary real.bin excluded", "real.bin" in fpaths, False)
    check("empty.txt present", "empty.txt" in fpaths, True)
    check("space path present", "sp ace.txt" in fpaths, True)
    check("unicode path present", "üni.txt" in fpaths, True)

    # -------------------------------------------------- directory metrics
    print("\n== directory metrics ==")
    root = obj_metrics(1, "")
    check("root == repo totals", (root["added"], root["removed"]), (20, 8))
    check("root modifications", root["modifications"], 7)
    check("lib dir", (obj_metrics(1, "lib")["added"],
                      obj_metrics(1, "lib")["removed"],
                      obj_metrics(1, "lib")["modifications"]), (3, 2, 2))
    check("src dir", (obj_metrics(1, "src")["added"],
                      obj_metrics(1, "src")["modifications"]), (5, 1))
    kids = {c["path"]: c for c in m.children(repo_id, {},
                                             m.root_dir_id(repo_id))}
    check("root children include lib+src",
          {"lib", "src"} <= set(kids), True)
    check("root child a.txt churn", kids["a.txt"]["churn"], 10)
    check("root child a.txt mods", kids["a.txt"]["modifications"], 3)

    # ------------------------------------------------- commit-set metrics
    print("\n== commit-set metrics (time ranges, manual selection) ==")
    r23 = m.summary(repo_id, {"ts_from": T0 + DAY, "ts_to": T0 + 3 * DAY})
    check("H[c2..c3) style range", (r23["commits"], r23["added"],
                                    r23["removed"]), (2, 6, 6))
    rt = m.summary(repo_id, {"ts_from": T0 + 6 * DAY})
    check("H_t open range", (rt["commits"], rt["added"], rt["removed"]),
          (1, 2, 1))
    manual = m.summary(repo_id, {"hashes": [by_ts[T0 + DAY]]})
    check("manual single commit", (manual["commits"], manual["added"],
                                   manual["removed"]), (1, 4, 2))
    manual_multi = m.summary(repo_id, {"hashes": [by_ts[T0], by_ts[T0 + 2 * DAY]]})
    check("manual multi commit", (manual_multi["commits"],
                                  manual_multi["added"],
                                  manual_multi["removed"]), (2, 10, 4))
    empty_sel = m.summary(repo_id, {"hashes": []})
    check("empty manual selection", (empty_sel["commits"], empty_sel["churn"]),
          (0, 0))
    mm = m.object_metrics(repo_id, {"hashes": [by_ts[T0 + DAY]]},
                          0, pid[(0, "a.txt")])
    check("a.txt in manual set", (mm["added"], mm["removed"],
                                  mm["modifications"], mm["frequency"]),
          (2, 1, 1, 1.0))

    # ------------------------------------------------------- reference h_r
    print("\n== reference commit h_r (H̄ reachable from a commit) ==")
    h2 = scratch.hashes["c2"]
    ref_h = githistory.ref_hashes(scratch.repo, h2)
    check("rev-list reachable from c2", len(ref_h), 2)
    store.set_reference(repo_id, h2, ref_h)
    ref_s = m.summary(repo_id, {})
    check("H̄ @ c2 totals", (ref_s["commits"], ref_s["added"], ref_s["removed"]),
          (2, 12, 2))
    ref_a = m.object_metrics(repo_id, {}, 0, pid[(0, "a.txt")])
    check("H̄ @ c2 a.txt", (ref_a["added"], ref_a["removed"],
                            ref_a["modifications"]), (5, 1, 2))
    ref_ts = m.summary(repo_id, {"ts_from": T0 + 6 * DAY})
    check("H̄ @ c2 ∩ H_t (future commits hidden)", ref_ts["commits"], 0)
    store.set_reference(repo_id, None, [])
    back_s = m.summary(repo_id, {})
    check("reference reset → HEAD", (back_s["commits"], back_s["added"]),
          (7, 20))

    # ----------------------------------------------------- author metrics
    print("\n== author metrics (mailmap + merging) ==")
    authors = m.authors_ranked(repo_id, {})
    names = {a["name"]: a for a in authors}
    check("mailmap merged Bob into one author", len(authors), 2)
    bob = names.get("Bob B")
    check("Bob commits", bob["commits"], 2)
    check("Bob churn", bob["churn"], 5)
    alice = names["Alice A"]
    check("Alice commits", alice["commits"], 5)
    check("Alice churn", alice["churn"], 23)
    check("Alice ownership", round(alice["ownership"], 6), round(23 / 28, 6))
    filtered = m.summary(repo_id, {"author_id": bob["author_id"]})
    check("author filter restricts H",
          (filtered["commits"], filtered["added"], filtered["removed"]),
          (2, 4, 1))
    aown = m.object_metrics(repo_id, {"author_id": bob["author_id"]},
                            0, pid[(0, "README.md")])
    check("README by Bob only", (aown["added"], aown["removed"],
                                 aown["modifications"]), (1, 1, 1))

    ident = {i["id"]: i for i in m.identities(repo_id)}
    bob_ident_ids = [i["id"] for i in ident.values()
                     if i["author_id"] == bob["author_id"]]
    check("Bob has exactly one identity (mailmap)", len(bob_ident_ids), 1)

    merged_id = store.merge_identities(
        repo_id, "Alice+B", [ident[i]["id"] for i in ident])
    merged = m.authors_ranked(repo_id, {})
    check("manual merge → one author", len(merged), 1)
    check("merged commits", merged[0]["commits"], 7)
    check("merged churn", merged[0]["churn"], 28)
    store.unmerge_author(repo_id, merged_id)
    back = m.authors_ranked(repo_id, {})
    check("unmerge → two authors",
          sorted(a["commits"] for a in back), [2, 5])
    store.rename_author(repo_id, back[0]["author_id"], "Renamed")
    check("rename author",
          "Renamed" in {a["name"] for a in m.authors_ranked(repo_id, {})},
          True)

    # ---------------------------------------------------------- timeline
    print("\n== timeline ==")
    tl = m.timeline(repo_id, {})
    check("timeline commit sum", sum(r["commits"] for r in tl["rows"]), 7)
    check("timeline added sum", sum(r["added"] for r in tl["rows"]), 20)

    # ------------------------------------------------------- commit list
    page = m.commits_page(repo_id, {})
    check("commit list count", page["total"], 7)
    hist = m.history(repo_id, {}, 0, pid[(0, "a.txt")])
    check("a.txt history length", len(hist), 3)

    print(f"\n{'=' * 52}")
    if FAILED:
        print(f"{len(FAILED)} CHECK(S) FAILED:")
        for f in FAILED:
            print(f"  - {f}")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
