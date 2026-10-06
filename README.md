# RAT — Repository Analysis Tool

**COMS3011A Test submission** — a web dashboard that measures how one or more Git
repositories have evolved: who changed what, where, and how much, down to every
file, directory, author and commit set.

---

## Getting started

Everything is pure Python + SQLite: no npm, no build step, no services to
install. This section takes you from a fresh laptop to a running dashboard.

### 1. Prerequisites

* **Python ≥ 3.10** (3.12 recommended) — check with `python3 --version`
  (on Windows: `py --version`)
* **Git ≥ 2.30** on the `PATH` — RAT uses `git` itself to clone repositories
  and to parse their complete history; check with `git --version`
* ~2 GB of free disk space if you plan to analyse large repositories (every
  clone, upload and the SQLite database live under `data/`)

### 2. Get the code

```bash
git clone https://github.com/Hlulani-B/RAT.git
cd RAT
```

(Downloading the GitHub ZIP archive and extracting it works just as well.)

### 3. Create an environment and install the dependency

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows (PowerShell):

```powershell
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

`requirements.txt` declares a single dependency (Flask).

### 4. Run everything (backend + frontend)

```bash
python run.py                 # -> http://127.0.0.1:8000
```

That single command runs the whole application — there is nothing else to
start. The frontend is plain HTML/CSS/JS with **no build step and no
Node.js**, and it is served by the same Flask process:

| What | URL | Served by |
| --- | --- | --- |
| Dashboard (frontend) | `http://127.0.0.1:8000/` | Flask, from `static/` (`index.html`, `app.js`, `views.js`, `charts.js`, `style.css`) |
| REST API (backend) | `http://127.0.0.1:8000/api/…` | Flask (`rat/app.py`) |

So: open **http://127.0.0.1:8000** in your browser and both halves are live.
While developing, editing anything under `static/` only needs a browser
refresh; Python changes need a server restart.

Useful options: `--port 9000`, `--host 0.0.0.0` (reachable from other
machines — the dashboard follows automatically because it is served by the
same process), `--data ./somewhere` (where clones, uploads and `rat.db` live).

### 5. Analyse your first repository

Open **http://127.0.0.1:8000**, click **+ Add repository** and either:

* **Clone from URL** — paste a public clone URL such as
  `https://github.com/DaveGamble/cJSON.git` and press *Clone & analyse*; the
  sidebar reports live progress (cJSON: 955 commits, ready in seconds), or
* **Upload zip** — drop a zip of a repository that contains its `.git`
  folder.

When the repository badge turns **ready**, the dashboard is fully live: pick
it in the sidebar and explore the Overview / Browser / Authors / Commits
tabs. Every view reacts to the filter bar (author, time period, manual
commit set, reference `ref:`).

Optional — run the deterministic metric test-suite (~15 s, self-contained):

```bash
python tests/validate.py
```

### Troubleshooting

| Symptom | Fix |
| --- | --- |
| `Address already in use` | another instance is running — start with `--port 9000` |
| `git: command not found` | install Git and make sure it is on the `PATH` |
| A huge repo (>50k commits) takes a minute | normal — clone + single-pass parse show live progress; queries stay fast afterwards |
| Want a clean slate | stop the server, delete the `data/` directory, restart |

---

## Using the dashboard

### Adding repositories

* **Clone URL** — paste an `https://`, `git://`, `ssh://` or `user@host:path`
  URL; the repo is fully cloned (complete history) with live progress.
* **Upload zip** — drop a zip of a working tree that contains its `.git`
  directory. Nested archives are fine (the repo root is found automatically);
  extraction rejects absolute paths, `..` and symlinks.

Multiple repositories are supported and switchable at any time.

### Filtering

Every view is driven by the same filter bar:

| Filter | Meaning |
| --- | --- |
| Repository | which repo the metrics are computed over |
| Author | restrict to one canonical author (a ∈ H) |
| File / directory | any object o ∈ H[F] ∪ H[D]; use the tree or search |
| Commits | select by period — Hᵢ,ⱼ `[i, j)` or Hₜ from `t` — or pick a manual list of commits |
| Reference `ref:` | the reference commit hᵣ (hash, branch or tag); H̄ becomes "non-merge commits reachable from hᵣ". Defaults to HEAD; one click resets |

Filters compose: author × time range × hᵣ × file all intersect.

### Author merging

Authors are split by raw identity (name + email) *after* `.mailmap` is applied
(`git log --use-mailmap`). When no `.mailmap` exists — or it is incomplete —
open any author and merge additional identities into a canonical author.
Merging is instant and fully reversible (unmerge restores the identities).

### Views

* **Overview** — KPI cards for the current filter set, commit timeline
  (added/removed per bucket, click to zoom into a period), a treemap of the
  tree (area = churn, colour = growth), top files/directories, author bar and
  ownership breakdown. CSV export of every table.
* **Browser** — the directory tree with per-object metrics, drill-down into
  files, per-file history and rename tracking (metrics follow the new path).
* **Authors** — leaderboard by churn/modifications/ownership, per-author KPI
  detail, identity management (merge/unmerge/rename), CSV export.
* **Commits** — the commit list (hash, date, author, subject, ±lines) with the
  manual commit-selection picker used by the "manual" commit-set filter.

---

## Metrics

All definitions follow the brief. A commit `h` has author `h[a]`, previous
commit `h[p]` (∅ for the initial commit), committer date `h[committer-date]`,
file set `h[F]` and directory set `h[D]`. **H̄** is the set of non-merge commits
reachable from the reference commit `hᵣ` (HEAD by default); a commit set `H` is
any subset (time window or manual selection).

### File metrics (per commit)

| Metric | Definition |
| --- | --- |
| Added lines | `l⁺(h,f)` |
| Removed lines | `l⁻(h,f)` |
| Growth | `δ(h,f) = l⁺ − l⁻` |
| Churn | `λ(h,f) = l⁺ + l⁻` |

### Directory metrics (per commit)

An object's metrics are the sum over its **immediate** children (files and
subdirectories):

`l⁺(h,d) = Σ l⁺(h,f) + Σ l⁺(h,d′)` — and likewise for `l⁻`, `δ`, `λ`.

### Repository metrics

Directory metrics on the root (`path = ''`).

### Commit-set metrics (over `H`)

| Metric | Definition |
| --- | --- |
| Added / removed / growth / churn | `Σ_{h∈H}` of the per-commit value |
| Modifications | `n(H,o) = Σ_{h∈H} 1[λ(h,o) > 0]` |
| Modification frequency | `η(H,o) = n / \|H\|` (0 when `\|H\| = 0`) |
| Churn rate | `ρ(H,o) = λ_{H,o} / \|H\|` (0 when `\|H\| = 0`) |

### Author metrics

With `1(a,h) = 1` iff `a = h[a]`:

| Metric | Definition |
| --- | --- |
| Author modifications | `n(H,o,a) = Σ_{h∈H} 1(a,h) · 1n(h,o)` |
| Author churn | `λ(H,o,a) = Σ_{h∈H} λ(h,o) · 1(a,h)` |
| Ownership | `ω(H,o,a) = λ_{H,o,a} / λ_{H,o}` (0 when `λ_{H,o} = 0`) |

### Git semantics honoured

* Rename detection at **50 %** (`-M50%`); a pure rename keeps its metrics, and
  changes made while renaming are attributed to the **new** path.
* A deleted object is recorded as removed lines on its old path.
* **Binary files are not measured** (git's own binary detection, `-` in numstat).
* Merge commits are excluded (`--no-merges`); `Hₜ`/`Hᵢ,ⱼ` use the *committer* date.
* Directory rows are recorded for **every ancestor** of a changed file, so a
  directory's subtree totals are exact even when the change is several levels deep.

---

## Architecture

```
zip upload ─┐                            ┌─ Flask REST API ── static/ (zero-build UI)
            ├─> ingest ─> git history ─> SQLite ─> metrics engine
clone URL ──┘              (streaming)    (pre-aggregated)   (indexed SQL)
```

* **`rat/githistory.py`** — the entire history is read by a *single* streaming
  `git log --no-merges -M50% --use-mailmap --numstat -z` invocation, parsed
  incrementally from the NUL-delimited wire format. No per-commit subprocesses,
  and the diff log is never buffered in full — memory stays bounded.
* **`rat/analyze.py` / `rat/store.py`** — the parse is materialised into
  SQLite (WAL): `commits`, `paths` (a real tree with parent links),
  `file_deltas` and `dir_deltas`. Directory deltas are pre-rolled to every
  ancestor, so *every* metric — including "was this object touched by commit
  h" — reduces to one indexed SQL aggregation over the filtered commit ids.
* **`rat/metrics.py`** — all file/directory/repository/commit-set/author
  metrics as parameterised SQL over the active filter set (time window,
  author, manual hashes, scope for hᵣ), with an LRU response cache keyed on
  (repo, epoch, query, filters); author merges and reference changes bump the
  epoch to invalidate.
* **`rat/jobs.py`** — a serial background worker streams clone/analyse progress
  which the UI polls; **`rat/app.py`** — the REST API; **`rat/ingest.py`** —
  safe zip extraction, repo-root discovery, URL validation, progress-parsed
  `git clone`.
* **`static/`** — no build step, no framework: vanilla JS + hand-rolled SVG
  (timeline, squarified treemap, stacked ownership bars, sparklines), hash
  routing, stale-render guards, toasts and error handling throughout.

### Performance

Measured on the two largest reference repositories — **Rails (73,341
non-merge commits, 7,237 authors, 14,463 files)** and **Git (61,101
commits)**:

| Operation | Rails | Git |
| --- | --- | --- |
| Full analysis: `git log` → SQLite | 31.3 s | 31.9 s |
| Analysis throughput | ≈ 2,350 commits/s | ≈ 1,900 commits/s |
| Summary (cached / uncached) | 1.2 / 1.3 ms | 1.3 / 1.4 ms |
| File ranking, top 100 | 23 ms | 11 ms |
| Author ranking | 20 ms | 9 ms |
| Author index (7,237 authors) | 111 ms | 66 ms |
| Commit list, 100 rows | 28 ms | 20 ms |
| Timeline | 66 ms | 47 ms |
| Object detail (busiest file) | 96 ms | 64 ms |

Analysis runs once at ingestion time; afterwards every dashboard interaction
is a single-digit-to-100 ms indexed SQL aggregation.

---

## Verification

**`tests/validate.py`** builds a deterministic scratch repository (7 commits,
renames, deletions, a `.mailmap`, binaries) and asserts **62 hand-computed
checks** across every metric family — file, directory, repository, commit-set
(time windows, manual sets) and author (ownership, merging, mailmap) plus the
reference-commit behaviour — then confirms renames don't change metrics and
deletions are recorded on the old path.

**`tests/verify_references.py`** replays the full metric dumps in
`repo-references/` against the live API: every repository-, object- and
author-level row is compared field by field (added, removed, growth, churn,
modifications, modification frequency, churn rate, ownership), with CSV
author cells resolved to the merged identities through the identity table.
**431,140 checks, 0 numeric mismatches:**

| Repository | HEAD | Commits | Checks | Failed |
| --- | --- | --- | --- | --- |
| cJSON | `6d9f2443` | 955 | 5,499 | 0 |
| Redis | `b540ca49` | 11,874 | 97,643 | 0 |
| Git | `5a7d1e80` | 61,101 | 327,998 | 39 † |

† All 39 are one benign class: the dump lists 39 legacy
`Documentation/RelNotes*.txt` paths that were later renamed to `.adoc`. The
engine attributes a rename to the new path only, so those old-path rows are
all-zero (+0 / −0, no modifications) in the dump and absent from the API —
every non-zero value matches exactly.

Totals against independent raw git summation are **byte-exact**:

| Repository | HEAD | Commits | Added | Removed |
| --- | --- | --- | --- | --- |
| cJSON | `6d9f2443` | 955 | 46,377 | 11,211 |
| Redis | `b540ca49` | 11,874 | 1,110,258 | 500,312 |
| Git | `5a7d1e80` | 61,101 | 4,070,371 | 2,375,604 |

**Zip ingestion** is exercised end-to-end: a scratch repository is zipped
with its `.git/`, uploaded through `POST /api/repos/zip`, analysed, and its
summary / authors / paths are compared against `git log --numstat` ground
truth — all checks pass, and deleting the repository removes every derived
row.

Reference-commit check: pointing hᵣ at cJSON's 500th commit
(`5ea4fad2`) yields 452 commits, +31,003 / −5,983 — exactly matching
`git rev-list --count --no-merges` and raw `git log --numstat`; resetting to
HEAD restores the totals above.

---

## Project layout

```
run.py                  entry point
rat/                    backend package
  githistory.py         streaming git-log parser (wire-format)
  analyze.py            parse -> SQLite ingestion
  store.py              SQLite schema, repos, authors, reference scope
  metrics.py            the metric engine (all categories)
  ingest.py             zip upload + URL clone workflows
  jobs.py               background job runner with progress
  app.py                Flask REST API
static/                 dashboard (index.html, app.js, views.js, charts.js, style.css)
tests/validate.py       deterministic metric test-suite
tests/verify_references.py  replay repo-references/*.csv against a live server
repo-references/        full metric dumps (CSV) for cJSON / Redis / Git
data/                   runtime: clones, uploads, rat.db  (git-ignored)
```

---

## AI Declaration

AI Declaration: Claude Web (Opus 5.5) - reviewed
