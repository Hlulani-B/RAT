"""Flask application: REST API + static dashboard for RAT.

Endpoint map (all JSON):

  GET    /api/repos                            list repositories + stats
  POST   /api/repos                            add by clone URL {url, name?}
  POST   /api/repos/zip                        add by zip upload (raw body)
  GET    /api/repos/<id>                       one repository
  DELETE /api/repos/<id>                       remove repository + files
  GET    /api/jobs/<id>                        ingestion job progress

  GET    /api/repos/<id>/summary               KPI totals over H
  GET    /api/repos/<id>/timeline              churn/commits per time bucket
  GET    /api/repos/<id>/tree?dir=<path_id>    children of a directory
  GET    /api/repos/<id>/objects?kind&id       one object's metrics + owners
  GET    /api/repos/<id>/files?dir&q&sort…     ranked files table
  GET    /api/repos/<id>/authors               ranked authors over H
  GET    /api/repos/<id>/authors/index         repo-wide authors + identities
  GET    /api/repos/<id>/identities            raw identities for merging
  POST   /api/repos/<id>/authors/merge         {name, identity_ids: []}
  POST   /api/repos/<id>/authors/unmerge       {author_id}
  POST   /api/repos/<id>/authors/rename        {author_id, name}
  GET    /api/repos/<id>/commits?q&limit…      commit list (manual selection)
  GET    /api/repos/<id>/history?kind&id       commits touching one object
  GET    /api/repos/<id>/paths                 full path table (tree builds)
  GET    /api/repos/<id>/authors_of?kind&ids   ownership breakdown for many
                                               objects at once
  POST   /api/repos/<id>/reference             set the reference commit h_r
                                               for H̄ (default HEAD)

Common filters (query params on metric endpoints):
  ts_from, ts_to   commit-set period   (i ≤ committer-date < j)
  author           canonical author id
  hashes           comma-separated manual commit selection
"""

from __future__ import annotations

import os
import shutil
import traceback
import uuid

from flask import Flask, abort, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from . import githistory
from . import ingest
from .ingest import URL_RE
from .jobs import JobRunner
from .metrics import MAX_HASHES, Metrics

MAX_ZIP_BYTES = 2 * 1024 ** 3


def _parse_filters(req) -> dict:
    f: dict = {}
    for arg, key in (("ts_from", "ts_from"), ("ts_to", "ts_to")):
        v = req.args.get(arg)
        if v not in (None, ""):
            try:
                f[key] = int(v)
            except ValueError:
                abort(400, description=f"invalid {arg}")
    v = req.args.get("author")
    if v not in (None, "", "all"):
        try:
            f["author_id"] = int(v)
        except ValueError:
            abort(400, description="invalid author")
    v = req.args.get("hashes")
    if v is not None and v != "":
        f["hashes"] = [h for h in v.split(",") if h][:MAX_HASHES]
    elif v == "":
        f["hashes"] = []  # explicit empty manual selection → empty H
    return f


def create_app(store) -> Flask:
    here = os.path.dirname(os.path.abspath(__file__))
    static_dir = os.path.join(os.path.dirname(here), "static")
    app = Flask(__name__, static_folder=static_dir, static_url_path="/static")
    app.config["MAX_CONTENT_LENGTH"] = MAX_ZIP_BYTES
    app.json.sort_keys = False

    runner = JobRunner()
    metrics = Metrics(store)
    uploads_dir = os.path.join(store.data_dir, "uploads")
    os.makedirs(uploads_dir, exist_ok=True)

    # Ingestion jobs live in this process's memory only: after a restart no
    # worker can be progressing a queued/cloning/analyzing repository, so
    # surface those as errors instead of a permanently "busy" sidebar.
    for r in store.list_repos():
        if r["status"] in ("queued", "cloning", "analyzing"):
            store.update_repo(
                r["id"], status="error",
                error="ingestion was interrupted by a server restart"
                      " — remove the repository and add it again")

    # ----------------------------------------------------------- utilities

    def get_ready(repo_id: int) -> dict:
        repo = store.get_repo(repo_id)
        if repo is None:
            abort(404, description="repository not found")
        if repo["status"] != "ready":
            abort(409, description=(
                f"repository not ready (status: {repo['status']})"))
        return repo

    def repo_root(repo: dict) -> str:
        """Filesystem location of the git repository (zip uploads may nest)."""
        primary = repo.get("root") or repo.get("dir") or ""
        if primary and githistory.is_git_repo(primary):
            return primary
        found = ingest.find_repo_root(repo.get("dir") or primary)
        if found and githistory.is_git_repo(found):
            return found
        abort(409, description="git repository files are not available")

    def progress_cb(job):
        def cb(frac=None, note=None):
            if frac is not None:
                job.progress = max(0.0, min(float(frac), 1.0))
            if note:
                job.note = note
        return cb

    def submit_ingest(repo_id: int, fn):
        def task(job):
            try:
                fn(job)
            except Exception as exc:  # surfaced to UI via repo.error
                store.update_repo(repo_id, status="error", error=str(exc))
                raise
        return runner.submit("ingest", repo_id, task)

    # ------------------------------------------------------------- static

    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    # -------------------------------------------------------------- repos

    @app.get("/api/repos")
    def list_repos():
        out = []
        for r in store.list_repos():
            stats = (metrics.repo_stats(r["id"]) if r["status"] == "ready"
                     else {"authors": 0, "files": 0})
            out.append({
                "id": r["id"], "name": r["name"], "source": r["source"],
                "source_url": r["source_url"], "status": r["status"],
                "error": r["error"], "created_at": r["created_at"],
                "head_hash": r["head_hash"], "head_ts": r["head_ts"],
                "commit_count": r["commit_count"], "ref": r["ref"],
                "authors": stats["authors"], "files": stats["files"],
            })
        return jsonify(out)

    @app.get("/api/repos/<int:repo_id>")
    def get_repo(repo_id):
        repo = store.get_repo(repo_id)
        if repo is None:
            abort(404, description="repository not found")
        stats = (metrics.repo_stats(repo_id) if repo["status"] == "ready"
                 else {"authors": 0, "files": 0})
        return jsonify({**repo, **stats})

    @app.post("/api/repos")
    def add_repo():
        payload = request.get_json(force=True, silent=True) or {}
        url = (payload.get("url") or "").strip()
        if not url or not URL_RE.match(url):
            abort(400, description="invalid repository URL")
        name = (payload.get("name") or "").strip()
        if not name:
            name = url.rstrip("/").split("/")[-1].removesuffix(".git") or "repo"
        repo_id = store.create_repo(name, "url", url, "")
        dest = os.path.join(store.repos_dir, str(repo_id))
        store.update_repo(repo_id, dir=dest)
        job = submit_ingest(repo_id, lambda j: ingest.ingest_url(
            store, repo_id, url, progress_cb(j)))
        return jsonify(job_id=job.id, repo_id=repo_id), 202

    @app.post("/api/repos/zip")
    def add_repo_zip():
        name = (request.args.get("name") or "").strip() or "uploaded-repo"
        repo_id = store.create_repo(name, "zip", None, "")
        dest = os.path.join(store.repos_dir, str(repo_id))
        store.update_repo(repo_id, dir=dest)
        zip_path = os.path.join(uploads_dir, f"{repo_id}-{uuid.uuid4().hex}.zip")
        size = 0
        try:
            with open(zip_path, "wb") as fh:
                while True:
                    chunk = request.stream.read(1 << 20)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_ZIP_BYTES:
                        abort(413, description="upload too large")
                    fh.write(chunk)
        except Exception:
            if os.path.exists(zip_path):
                os.remove(zip_path)
            raise
        if size == 0:
            os.remove(zip_path)
            abort(400, description="empty upload (expected a zip body)")
        job = submit_ingest(repo_id, lambda j: ingest.ingest_zip(
            store, repo_id, zip_path, progress_cb(j)))
        return jsonify(job_id=job.id, repo_id=repo_id), 202

    @app.delete("/api/repos/<int:repo_id>")
    def delete_repo(repo_id):
        repo = store.get_repo(repo_id)
        if repo is None:
            abort(404, description="repository not found")
        if repo["status"] in ("queued", "cloning", "analyzing"):
            abort(409, description="ingestion in progress — try again later")
        store.delete_repo(repo_id)
        d = os.path.abspath(repo["dir"] or "")
        if d.startswith(store.repos_dir + os.sep) and os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
        return jsonify(ok=True)

    # --------------------------------------------------------------- jobs

    @app.get("/api/jobs/<int:job_id>")
    def get_job(job_id):
        job = runner.get(job_id)
        if job is None:
            abort(404, description="job not found")
        return jsonify(job.to_dict())

    # ------------------------------------------------------------ metrics

    @app.get("/api/repos/<int:repo_id>/summary")
    def api_summary(repo_id):
        get_ready(repo_id)
        return jsonify(metrics.summary(repo_id, _parse_filters(request)))

    @app.get("/api/repos/<int:repo_id>/timeline")
    def api_timeline(repo_id):
        get_ready(repo_id)
        bucket = request.args.get("bucket")
        return jsonify(metrics.timeline(
            repo_id, _parse_filters(request),
            int(bucket) if bucket else None))

    @app.get("/api/repos/<int:repo_id>/paths")
    def api_paths(repo_id):
        get_ready(repo_id)
        return jsonify({
            "root": metrics.root_dir_id(repo_id),
            "paths": metrics._path_rows(repo_id),
        })

    @app.get("/api/repos/<int:repo_id>/tree")
    def api_tree(repo_id):
        get_ready(repo_id)
        dir_arg = request.args.get("dir")
        dir_id = (metrics.root_dir_id(repo_id)
                  if dir_arg in (None, "", "root") else int(dir_arg))
        info = metrics.path_by_id(repo_id, dir_id)
        if info is None or info["kind"] != 1:
            abort(404, description="directory not found")
        return jsonify({
            "dir": info,
            "children": metrics.children(repo_id, _parse_filters(request),
                                         dir_id),
        })

    @app.get("/api/repos/<int:repo_id>/objects")
    def api_object(repo_id):
        get_ready(repo_id)
        try:
            kind = int(request.args.get("kind", "0"))
            path_id = int(request.args.get("id", "0"))
        except ValueError:
            abort(400, description="invalid kind/id")
        info = metrics.path_by_id(repo_id, path_id)
        if info is None:
            abort(404, description="object not found")
        return jsonify({
            "object": info,
            "metrics": metrics.object_metrics(
                repo_id, _parse_filters(request), kind, path_id),
        })

    @app.get("/api/repos/<int:repo_id>/files")
    def api_files(repo_id):
        get_ready(repo_id)
        args = request.args
        dir_arg = args.get("dir")
        dir_path = ""
        if dir_arg not in (None, "", "root"):
            info = metrics.path_by_id(repo_id, int(dir_arg))
            if info is not None:
                dir_path = info["path"]
        return jsonify(metrics.files(
            repo_id, _parse_filters(request), dir_path=dir_path,
            q=args.get("q", ""), sort=args.get("sort", "churn"),
            desc=args.get("desc", "1") != "0",
            limit=max(1, min(int(args.get("limit", "100")), 500)),
            offset=max(0, int(args.get("offset", "0"))),
        ))

    @app.get("/api/repos/<int:repo_id>/authors")
    def api_authors(repo_id):
        get_ready(repo_id)
        return jsonify(metrics.authors_ranked(repo_id, _parse_filters(request)))

    @app.get("/api/repos/<int:repo_id>/authors/index")
    def api_authors_index(repo_id):
        get_ready(repo_id)
        return jsonify({
            "authors": metrics.author_list(repo_id),
            "identities": metrics.identities(repo_id),
        })

    @app.get("/api/repos/<int:repo_id>/identities")
    def api_identities(repo_id):
        get_ready(repo_id)
        return jsonify(metrics.identities(repo_id))

    @app.post("/api/repos/<int:repo_id>/authors/merge")
    def api_merge(repo_id):
        get_ready(repo_id)
        payload = request.get_json(force=True, silent=True) or {}
        name = (payload.get("name") or "").strip()
        ids = payload.get("identity_ids") or []
        if not name:
            abort(400, description="a name is required for the merged author")
        try:
            ids = [int(i) for i in ids]
        except (TypeError, ValueError):
            abort(400, description="identity_ids must be integers")
        if not ids:
            abort(400, description="select at least one identity to merge")
        author_id = store.merge_identities(repo_id, name, ids)
        return jsonify(ok=True, author_id=author_id)

    @app.post("/api/repos/<int:repo_id>/authors/unmerge")
    def api_unmerge(repo_id):
        get_ready(repo_id)
        payload = request.get_json(force=True, silent=True) or {}
        try:
            author_id = int(payload.get("author_id"))
        except (TypeError, ValueError):
            abort(400, description="author_id is required")
        store.unmerge_author(repo_id, author_id)
        return jsonify(ok=True)

    @app.post("/api/repos/<int:repo_id>/authors/rename")
    def api_rename(repo_id):
        get_ready(repo_id)
        payload = request.get_json(force=True, silent=True) or {}
        name = (payload.get("name") or "").strip()
        if not name:
            abort(400, description="a name is required")
        try:
            author_id = int(payload.get("author_id"))
        except (TypeError, ValueError):
            abort(400, description="author_id is required")
        store.rename_author(repo_id, author_id, name)
        return jsonify(ok=True)

    @app.get("/api/repos/<int:repo_id>/authors_of")
    def api_authors_of(repo_id):
        get_ready(repo_id)
        try:
            kind = int(request.args.get("kind", "0"))
            ids = [int(x) for x in request.args.get("ids", "").split(",")
                   if x.strip()]
        except ValueError:
            abort(400, description="invalid ids")
        return jsonify(metrics.multi_authors(
            repo_id, _parse_filters(request), kind, ids))

    @app.get("/api/repos/<int:repo_id>/commits")
    def api_commits(repo_id):
        get_ready(repo_id)
        args = request.args
        return jsonify(metrics.commits_page(
            repo_id, _parse_filters(request), q=args.get("q", ""),
            limit=max(1, min(int(args.get("limit", "100")), 500)),
            offset=max(0, int(args.get("offset", "0"))),
        ))

    @app.get("/api/repos/<int:repo_id>/history")
    def api_history(repo_id):
        get_ready(repo_id)
        args = request.args
        try:
            kind = int(args.get("kind", "0"))
            path_id = int(args.get("id", "0"))
        except ValueError:
            abort(400, description="invalid kind/id")
        return jsonify({"rows": metrics.history(
            repo_id, _parse_filters(request), kind, path_id,
            limit=max(1, min(int(args.get("limit", "100")), 500)))})

    @app.post("/api/repos/<int:repo_id>/reference")
    def api_reference(repo_id):
        """Point H̄ at a reference commit h_r (hash, branch or tag)."""
        repo = get_ready(repo_id)
        payload = request.get_json(force=True, silent=True) or {}
        ref = (payload.get("ref") or "HEAD").strip() or "HEAD"
        root = repo_root(repo)
        try:
            resolved = githistory.resolve_ref(root, ref)
        except githistory.GitError as exc:
            abort(400, description=f"cannot resolve ref: {exc}")
        if resolved == repo["head_hash"]:
            store.set_reference(repo_id, None, [])
            return jsonify(ok=True, ref=None, hash=resolved,
                           commits=repo["commit_count"])
        hashes = githistory.ref_hashes(root, resolved)
        store.set_reference(repo_id, resolved, hashes)
        return jsonify(ok=True, ref=resolved, hash=resolved,
                       commits=len(hashes))

    # ------------------------------------------------------------ errors

    @app.errorhandler(HTTPException)
    def http_error(e):
        return jsonify(error=e.description), e.code

    @app.errorhandler(Exception)
    def server_error(e):
        traceback.print_exc()
        return jsonify(error=f"internal error: {e}"), 500

    return app
