/* RAT app — state, API client, filters, modals, polling, routing. */
(function () {
  "use strict";

  const C = window.Charts;
  const esc = C.esc;
  const $ = (id) => document.getElementById(id);

  /* ------------------------------------------------------------ state */

  const state = {
    repos: [],
    repoId: null,
    repo: null,
    filters: {},          // ts_from, ts_to, author_id, hashes
    tab: "overview",
  };

  const ui = {
    preset: "all",
    dirId: null,
    obj: null,
    browserMode: "table",
    dirSortKey: null,
    dirSortDesc: true,
    commitsQ: "",
    commitsOffset: 0,
  };

  const cache = { paths: {}, authorIndex: {} };
  const jobs = {};   // repoId → {jobId, timer, progress}
  let renderToken = 0;
  let selfHash = "";
  let welcomeCache = "";

  /* -------------------------------------------------------------- api */

  async function api(path, params, method, body) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params || {})) {
      if (v === undefined || v === null) continue;
      qs.set(k, String(v));
    }
    let url = "/api/repos/" + state.repoId + path;
    if ([...qs].length) url += "?" + qs.toString();
    const init = { method: method || "GET", headers: {} };
    if (body !== undefined && body !== null) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    const res = await fetch(url, init);
    let data = null;
    try { data = await res.json(); } catch (e) { /* no body */ }
    if (!res.ok) {
      const msg = (data && data.error) || res.statusText || ("HTTP " + res.status);
      const err = new Error(msg);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  async function rootApi(path, params, method, body) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params || {})) {
      if (v === undefined || v === null) continue;
      qs.set(k, String(v));
    }
    let url = "/api" + path;
    if ([...qs].length) url += "?" + qs.toString();
    const init = { method: method || "GET", headers: {} };
    if (body !== undefined && body !== null) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    const res = await fetch(url, init);
    let data = null;
    try { data = await res.json(); } catch (e) { /* no body */ }
    if (!res.ok) {
      const err = new Error((data && data.error) || res.statusText);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  function filterParams(skip) {
    const f = state.filters, p = {};
    if (f.ts_from !== undefined) p.ts_from = f.ts_from;
    if (f.ts_to !== undefined) p.ts_to = f.ts_to;
    if (f.author_id !== undefined) p.author = f.author_id;
    if (f.hashes !== undefined) p.hashes = f.hashes.join(",");
    for (const k of skip || []) delete p[k];
    return p;
  }

  function filtersActive() {
    const f = state.filters;
    return f.ts_from !== undefined || f.ts_to !== undefined ||
           f.author_id !== undefined || f.hashes !== undefined;
  }

  async function paths() {
    if (!cache.paths[state.repoId]) {
      const data = await api("/paths");
      cache.paths[state.repoId] = data.paths;
    }
    return cache.paths[state.repoId];
  }

  async function authorIndex() {
    if (!cache.authorIndex[state.repoId]) {
      cache.authorIndex[state.repoId] = await api("/authors/index");
    }
    return cache.authorIndex[state.repoId];
  }

  function invalidate() {
    delete cache.paths[state.repoId];
    delete cache.authorIndex[state.repoId];
    authorIndex().then(renderAuthorOptions).catch(() => {});
  }

  /* ----------------------------------------------------------- toasts */

  function toast(msg, kind) {
    const el = document.createElement("div");
    el.className = "toast " + (kind || "");
    el.textContent = msg;
    $("toasts").appendChild(el);
    setTimeout(() => { el.style.opacity = "0"; }, 4200);
    setTimeout(() => el.remove(), 4700);
  }

  /* ----------------------------------------------------------- modals */

  function openModal(html, opts) {
    opts = opts || {};
    const root = $("modal-root");
    root.innerHTML = `<div class="modal-backdrop"><div class="modal ${opts.wide ? "wide" : ""}">
        <div class="modal-head"><h2>${esc(opts.title || "")}</h2>
          <button class="x" title="close (Esc)">×</button></div>
        <div class="modal-body"></div>
        <div class="modal-foot"></div></div></div>`;
    const backdrop = root.firstElementChild;
    const body = backdrop.querySelector(".modal-body");
    const foot = backdrop.querySelector(".modal-foot");
    const close = () => { root.innerHTML = ""; };
    backdrop.querySelector(".x").addEventListener("click", close);
    backdrop.addEventListener("mousedown", (e) => { if (e.target === backdrop) close(); });
    return { body, foot, close, backdrop };
  }

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") $("modal-root").innerHTML = "";
  });

  /* ------------------------------------------------------- add repo */

  function openAddRepo() {
    const m = openModal({ title: "Add repository" });
    m.body.innerHTML = `
      <div class="tabseg">
        <button data-m="url" class="active">Clone from URL</button>
        <button data-m="zip">Upload zip</button>
      </div>
      <div id="m-url">
        <label class="field"><span>Repository URL (https / git / ssh — public repos need no credentials)</span>
          <input type="url" id="f-url" placeholder="https://github.com/redis/redis.git"></label>
        <label class="field"><span>Display name (optional)</span>
          <input type="text" id="f-name" placeholder="derived from the URL"></label>
      </div>
      <div id="m-zip" class="hidden">
        <label class="field"><span>Display name (optional)</span>
          <input type="text" id="f-zname" placeholder="derived from the file"></label>
        <div class="dropzone" id="dz">Drop a .zip of a git repository here<br>
          <span class="dim">(must contain the .git directory — or the repo itself)</span></div>
        <input type="file" id="f-file" accept=".zip" class="hidden">
        <div class="dim" id="f-file-name" style="margin-top:8px"></div>
      </div>
      <div class="progress-track hidden" id="add-prog"><i></i></div>
      <div class="load-note" id="add-note"></div>`;
    m.foot.innerHTML = `<button class="btn ghost" id="add-cancel">Cancel</button>
      <button class="btn primary" id="add-go">Clone &amp; analyse</button>`;

    const mode = { v: "url" };
    const prog = m.body.querySelector("#add-prog");
    const note = m.body.querySelector("#add-note");
    const setProgress = (frac, text) => {
      prog.classList.remove("hidden");
      prog.firstElementChild.style.width = Math.round((frac || 0) * 100) + "%";
      if (text !== undefined) note.textContent = text;
    };

    m.body.querySelectorAll(".tabseg button").forEach((b) =>
      b.addEventListener("click", () => {
        mode.v = b.dataset.m;
        m.body.querySelectorAll(".tabseg button").forEach((x) =>
          x.classList.toggle("active", x === b));
        m.body.querySelector("#m-url").classList.toggle("hidden", mode.v !== "url");
        m.body.querySelector("#m-zip").classList.toggle("hidden", mode.v !== "zip");
        m.foot.querySelector("#add-go").textContent =
          mode.v === "url" ? "Clone & analyse" : "Upload & analyse";
      }));

    // zip picker
    const dz = m.body.querySelector("#dz");
    const fileInput = m.body.querySelector("#f-file");
    const fileName = m.body.querySelector("#f-file-name");
    let pickedFile = null;
    const pick = (f) => {
      if (!f) return;
      pickedFile = f;
      fileName.textContent = `${f.name} · ${(f.size / 1048576).toFixed(1)} MB`;
    };
    dz.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", () => pick(fileInput.files[0]));
    ["dragover", "dragenter"].forEach((ev) =>
      dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
    ["dragleave", "drop"].forEach((ev) =>
      dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
    dz.addEventListener("drop", (e) => pick(e.dataTransfer.files[0]));

    m.foot.querySelector("#add-cancel").addEventListener("click", m.close);

    m.foot.querySelector("#add-go").addEventListener("click", async () => {
      const go = m.foot.querySelector("#add-go");
      go.disabled = true;
      try {
        if (mode.v === "url") {
          const url = m.body.querySelector("#f-url").value.trim();
          const name = m.body.querySelector("#f-name").value.trim();
          if (!url) { toast("Enter a repository URL", "error"); go.disabled = false; return; }
          const r = await rootApi("/repos", null, "POST", { url, name });
          m.close();
          toast("Clone started in the background", "ok");
          await refreshRepos();
          selectRepo(r.repo_id, { skipRender: false });
          trackJob(r.repo_id, r.job_id);
        } else {
          if (!pickedFile) { toast("Choose a zip file first", "error"); go.disabled = false; return; }
          const name = m.body.querySelector("#f-zname").value.trim() ||
                       pickedFile.name.replace(/\.zip$/i, "");
          setProgress(0, "uploading…");
          const { jobId, repoId } = await uploadZip(pickedFile, name, setProgress);
          m.close();
          toast("Upload complete — analysing in the background", "ok");
          await refreshRepos();
          selectRepo(repoId);
          trackJob(repoId, jobId);
        }
      } catch (e) {
        toast(e.message, "error");
        note.textContent = e.message;
        go.disabled = false;
      }
    });
  }

  function uploadZip(file, name, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/repos/zip?name=" + encodeURIComponent(name));
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onProgress(0.92 * e.loaded / e.total, "uploading…");
      };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          const d = JSON.parse(xhr.responseText);
          resolve({ jobId: d.job_id, repoId: d.repo_id });
        } else {
          let msg = "upload failed";
          try { msg = JSON.parse(xhr.responseText).error || msg; } catch (e) {}
          reject(new Error(msg));
        }
      };
      xhr.onerror = () => reject(new Error("network error during upload"));
      xhr.send(file);
    });
  }

  /* ------------------------------------------------------ job tracking */

  function trackJob(repoId, jobId) {
    if (jobs[repoId]) clearInterval(jobs[repoId].timer);
    const entry = { jobId, timer: null };
    jobs[repoId] = entry;
    entry.timer = setInterval(async () => {
      let job;
      try {
        job = await rootApi("/jobs/" + jobId);
      } catch (e) {
        if (e.status !== 404) return;
        // Job records live in server memory: a server restart erases them,
        // so reconcile with the persisted repository state instead of
        // polling a dead job forever (which left the sidebar stuck busy).
        clearInterval(entry.timer);
        delete jobs[repoId];
        await refreshRepos().catch(() => {});
        const repo = state.repos.find((r) => r.id === repoId);
        if (repo && repo.status === "ready") {
          toast(`“${repo.name}” is ready`, "ok");
        } else if (repo && repo.status === "error") {
          toast(repo.error || "ingestion failed", "error");
        }
        if (state.repoId === repoId) { invalidate(); render(); }
        return;
      }
      // Dom updates must never break the polling below: a thrown error
      // here used to skip the done/error handling entirely (the sidebar
      // stayed "ingestion in progress…" until a manual refresh).
      try { updateJobUI(repoId, job); } catch (e) { /* keep polling */ }
      if (job.status === "done" || job.status === "error") {
        clearInterval(entry.timer);
        delete jobs[repoId];
        await refreshRepos();
        if (job.status === "error") toast(job.error || "ingestion failed", "error");
        else toast("Repository ready", "ok");
        if (state.repoId === repoId) { invalidate(); render(); }
      }
    }, 1200);
  }

  function updateJobUI(repoId, job) {
    if (jobs[repoId]) jobs[repoId].progress = job.progress || 0;
    const item = document.querySelector(`.repo-item[data-id="${repoId}"]`);
    if (!item) return;
    let bar = item.querySelector(".job-bar");
    let note = item.querySelector(".job-note");
    // renderRepoList() already draws .job-bar for busy repos (but no note),
    // so create each element independently — requiring both to be missing
    // before building them left `note` null and threw on every tick.
    if (!bar) {
      bar = document.createElement("div");
      bar.className = "job-bar"; bar.innerHTML = "<i></i>";
      item.appendChild(bar);
    }
    if (!note) {
      note = document.createElement("div");
      note.className = "job-note";
      item.appendChild(note);
    }
    bar.firstElementChild.style.width = Math.round((job.progress || 0) * 100) + "%";
    note.textContent = job.note || job.status;
  }

  /* --------------------------------------------------------- repo list */

  async function refreshRepos() {
    state.repos = await rootApi("/repos");
    const cur = state.repos.find((r) => r.id === state.repoId);
    if (cur) state.repo = cur;
    renderRepoList();
  }

  function statusBadge(r) {
    const label = { queued: "queued", cloning: "cloning", analyzing: "analysing",
                    ready: "ready", error: "error" }[r.status] || r.status;
    const cls = r.status === "ready" ? "ready" : (r.status === "error" ? "error" : "busy");
    return `<span class="badge ${cls}">${esc(label)}</span>`;
  }

  function renderRepoList() {
    const list = $("repo-list");
    if (!state.repos.length) {
      list.innerHTML = '<div class="empty-hint">No repositories yet.<br>Add one by clone URL or zip upload.</div>';
      return;
    }
    list.innerHTML = state.repos.map((r) => {
      let meta = "";
      if (r.status === "ready") {
        meta = `${C.fmtInt(r.commit_count)} commits · ${C.fmtInt(r.authors)} authors · ${C.fmtInt(r.files)} files`;
      } else if (r.status === "error") {
        meta = r.error || "failed";
      } else {
        meta = "ingestion in progress…";
      }
      const busy = !["ready", "error"].includes(r.status);
      const pctDone = Math.round(((jobs[r.id] && jobs[r.id].progress) || 0.02) * 100);
      return `<div class="repo-item ${r.id === state.repoId ? "active" : ""}" data-id="${r.id}">
        <button class="r-del" data-del="${r.id}" title="Remove repository">×</button>
        <div class="r-name">${esc(r.name)} ${statusBadge(r)}</div>
        <div class="r-meta">${esc(meta)}</div>
        ${busy ? `<div class="job-bar"><i style="width:${pctDone}%"></i></div>` : ""}
      </div>`;
    }).join("");
    list.querySelectorAll(".repo-item").forEach((el) =>
      el.addEventListener("click", (e) => {
        if (e.target.dataset.del) return;
        selectRepo(+el.dataset.id);
      }));
    list.querySelectorAll("[data-del]").forEach((b) =>
      b.addEventListener("click", async (e) => {
        e.stopPropagation();
        const id = +b.dataset.del;
        const repo = state.repos.find((r) => r.id === id);
        if (!confirm(`Remove “${repo ? repo.name : id}” and all analysed data?`)) return;
        try {
          await rootApi("/repos/" + id, null, "DELETE");
          toast("Repository removed", "ok");
          if (state.repoId === id) {
            state.repoId = null; state.repo = null;
            delete cache.paths[id]; delete cache.authorIndex[id];
          }
          await refreshRepos();
          render();
        } catch (err) { toast(err.message, "error"); }
      }));
  }

  async function selectRepo(id, opts) {
    opts = opts || {};
    if (state.repoId !== id) {
      state.filters = {};
      ui.preset = "all";
      ui.dirId = null; ui.obj = null;
      ui.commitsQ = ""; ui.commitsOffset = 0;
    }
    state.repoId = id;
    state.repo = state.repos.find((r) => r.id === id) || null;
    renderRepoList();
    syncFilterControls();
    if (state.repo && state.repo.status === "ready") {
      // never block the dashboard render on this request: populating the
      // author filter for huge repositories can take a moment
      authorIndex().then(renderAuthorOptions).catch(() => {});
    }
    if (!opts.skipRender) render();
  }

  /* ----------------------------------------------------------- filters */

  function setFilters(patch) {
    for (const [k, v] of Object.entries(patch)) {
      if (v === undefined || v === null) delete state.filters[k];
      else state.filters[k] = v;
    }
    if ("ts_from" in patch || "ts_to" in patch) ui.preset = "custom";
    syncFilterControls();
    render();
  }

  function clearFilters() {
    state.filters = {};
    ui.preset = "all";
    syncFilterControls();
    render();
  }

  function tsToDate(ts) { return new Date(ts * 1000).toISOString().slice(0, 10); }
  function dateToTs(s) { return Math.floor(Date.parse(s + "T00:00:00Z") / 1000); }

  function syncFilterControls() {
    document.querySelectorAll("#time-presets button").forEach((b) =>
      b.classList.toggle("active", b.dataset.preset === ui.preset));
    const f = state.filters;
    $("from-date").value = f.ts_from !== undefined ? tsToDate(f.ts_from) : "";
    $("to-date").value = f.ts_to !== undefined ? tsToDate(f.ts_to - 1) : "";
    $("author-filter").value = f.author_id !== undefined ? String(f.author_id) : "";
    const chip = $("btn-commits-filter");
    if (f.hashes !== undefined) {
      chip.textContent = `Commits: ${f.hashes.length ? f.hashes.length + " selected" : "empty set"}`;
      chip.classList.add("active");
    } else {
      chip.textContent = "Commits: all";
      chip.classList.remove("active");
    }
    const parts = [];
    if (f.ts_from !== undefined || f.ts_to !== undefined) {
      parts.push(`H = [${f.ts_from !== undefined ? C.fmtDate(f.ts_from) : "−∞"}, ` +
                 `${f.ts_to !== undefined ? C.fmtDate(f.ts_to) : "now"})`);
    }
    if (f.author_id !== undefined) {
      const opt = $("author-filter").selectedOptions[0];
      const label = opt ? opt.textContent.split(" (")[0] : f.author_id;
      parts.push(`author = ${label}`);
    }
    if (f.hashes !== undefined) parts.push(`manual set (${f.hashes.length})`);
    if (state.repo && state.repo.ref) parts.push(`H̄ @ ${state.repo.ref.slice(0, 8)}`);
    $("filter-summary").textContent = parts.length ? "● " + parts.join("  ·  ") : "";

    const chipRef = $("btn-ref");
    const ref = state.repo && state.repo.ref;
    chipRef.textContent = ref ? "ref: " + ref.slice(0, 8) : "ref: HEAD";
    chipRef.classList.toggle("active", !!ref);
  }

  function renderAuthorOptions() {
    const idx = cache.authorIndex[state.repoId];
    if (!idx) return;
    const sel = $("author-filter");
    const keep = sel.value;
    sel.innerHTML = '<option value="">All authors</option>' +
      idx.authors.map((a) =>
        `<option value="${a.id}">${esc(a.name)} (${C.fmtInt(a.commits)})</option>`).join("");
    sel.value = keep && [...sel.options].some((o) => o.value === keep) ? keep : "";
  }

  /* --------------------------------------------------------- rendering */

  async function render() {
    const repo = state.repo;
    const hasRepo = repo && repo.status === "ready";
    $("filters").classList.toggle("hidden", !hasRepo);
    $("tabs").classList.toggle("hidden", !hasRepo);

    if (repo) {
      $("repo-title").textContent = repo.name;
      const bits = [];
      if (repo.status === "ready") {
        bits.push(`${C.fmtInt(repo.commit_count)} non-merge commits`);
        bits.push(`head ${esc((repo.head_hash || "").slice(0, 10))}`);
        if (repo.head_ts) bits.push("last commit " + C.fmtDate(repo.head_ts));
        bits.push(repo.source === "zip" ? "uploaded zip" : "cloned from URL");
        if (repo.ref) bits.push(`H̄ from ${esc(repo.ref.slice(0, 10))}`);
      } else {
        bits.push("status: " + repo.status + (repo.error ? " — " + repo.error : ""));
      }
      $("repo-sub").innerHTML = bits.join("  ·  ");
    } else {
      $("repo-title").textContent = "No repository selected";
      $("repo-sub").textContent = state.repos.length
        ? "Pick a repository from the sidebar." : "";
    }

    document.querySelectorAll("#tabs button[data-tab]").forEach((b) =>
      b.classList.toggle("active", b.dataset.tab === state.tab));
    syncFilterControls();

    if (!hasRepo) {
      $("view").innerHTML = welcomeHtml();
      return;
    }

    const view = $("view");
    view.innerHTML = "";
    const box = document.createElement("div");
    view.appendChild(box);
    const token = ++renderToken;
    writeHash();
    try {
      await window.Views[state.tab](box);
    } catch (e) {
      if (token === renderToken) {
        box.innerHTML = `<div class="err-note">Failed to render this view: ${esc(e.message)}</div>`;
      }
    }
  }

  function welcomeHtml() {
    if (state.repos.length) {
      return `<div class="welcome"><h2>Select a repository</h2>
        <p class="muted">Choose one from the sidebar, or add another with the button above.</p></div>`;
    }
    return welcomeCache || `<div class="welcome"><h2>Add a repository to begin</h2></div>`;
  }

  function navigate(patch) {
    if (patch.tab !== undefined) state.tab = patch.tab;
    if (patch.dirId !== undefined) { ui.dirId = patch.dirId; ui.obj = null; }
    if (patch.obj !== undefined) { ui.obj = patch.obj; }
    render();
  }

  /* ------------------------------------------------------------ routing */

  function writeHash() {
    const h = `#/r/${state.repoId || ""}/${state.tab}` +
      (ui.dirId ? `?dir=${ui.dirId}` : "") +
      (ui.obj ? (ui.dirId ? "&" : "?") + `obj=${ui.obj.kind}:${ui.obj.id}` : "");
    if (h !== selfHash) { selfHash = h; location.hash = h; }
  }

  function readHash() {
    const m = location.hash.match(/^#\/r\/(\d+)\/(\w+)/);
    if (!m) return null;
    const q = new URLSearchParams(location.hash.split("?")[1] || "");
    const obj = q.get("obj");
    return {
      repoId: +m[1], tab: m[2],
      dirId: q.get("dir") ? +q.get("dir") : null,
      obj: obj ? { kind: +obj.split(":")[0], id: +obj.split(":")[1] } : null,
    };
  }

  window.addEventListener("hashchange", async () => {
    if (location.hash === selfHash) return;
    const r = readHash();
    if (!r) return;
    state.tab = r.tab;
    ui.dirId = r.dirId;
    ui.obj = r.obj;
    if (r.repoId !== state.repoId) await selectRepo(r.repoId, { skipRender: true });
    render();
  });

  /* ------------------------------------------------------------- wiring */

  function init() {
    $("engine-version").textContent = "RAT v1.0.0 · COMS3011A";
    $("btn-add-repo").addEventListener("click", openAddRepo);

    $("tabs").querySelectorAll("button[data-tab]").forEach((b) =>
      b.addEventListener("click", () => navigate({ tab: b.dataset.tab })));

    $("time-presets").querySelectorAll("button").forEach((b) =>
      b.addEventListener("click", () => {
        const p = b.dataset.preset;
        ui.preset = p;
        const now = Math.floor(Date.now() / 1000);
        const days = { "1y": 365, "6m": 182, "3m": 91, "1m": 31 }[p] || 0;
        if (p === "all") setFilters({ ts_from: undefined, ts_to: undefined });
        else { state.filters.ts_from = now - days * 86400; delete state.filters.ts_to;
               syncFilterControls(); render(); }
      }));
    $("from-date").addEventListener("change", (e) => {
      const v = e.target.value;
      setFilters(v ? { ts_from: dateToTs(v) } : { ts_from: undefined });
    });
    $("to-date").addEventListener("change", (e) => {
      const v = e.target.value;
      setFilters(v ? { ts_to: dateToTs(v) + 86400 } : { ts_to: undefined });
    });
    $("author-filter").addEventListener("change", (e) =>
      setFilters({ author_id: e.target.value ? +e.target.value : undefined }));
    $("btn-commits-filter").addEventListener("click", openCommitPicker);
    $("btn-ref").addEventListener("click", openRefModal);
    $("btn-clear-filters").addEventListener("click", clearFilters);
    if (state.repos.length) renderRepoList();
  }

  /* ----------------------------------------------------- commit picker */

  async function openCommitPicker() {
    const m = openModal({ title: "Select commits for the commit set H", wide: true });
    m.body.innerHTML = `<div style="display:flex;gap:8px;margin-bottom:10px">
        <input type="search" id="pk-q" placeholder="Search subject / author / hash…" style="flex:1">
        <span class="muted" id="pk-count"></span></div>
      <div id="pk-list"><div class="spinner"></div></div>`;
    m.foot.innerHTML = `<button class="btn ghost" id="pk-clear">Clear selection</button>
      <button class="btn primary" id="pk-apply">Apply commit set</button>`;

    const selected = new Set(state.filters.hashes || []);
    const listEl = m.body.querySelector("#pk-list");
    const countEl = m.body.querySelector("#pk-count");

    async function load(q) {
      listEl.innerHTML = '<div class="spinner"></div>';
      const data = await api("/commits",
        { ...filterParams(["hashes"]), q: q || "", limit: 500, offset: 0 });
      countEl.textContent = `${C.fmtInt(data.total)} commits`;
      listEl.innerHTML = `<div class="merge-box">` + data.rows.map((r) => `
        <label class="ident-row ${selected.has(r.hash) ? "merged" : ""}">
          <input type="checkbox" value="${r.hash}" ${selected.has(r.hash) ? "checked" : ""}>
          <span class="i-name">${esc(r.subject)}</span>
          <span class="i-mail">${esc(r.hash.slice(0, 8))} · ${esc(r.author)}</span>
          <span class="dim">${C.fmtDate(r.ts)}</span>
        </label>`).join("") + "</div>" +
        (data.total > 500 ? `<div class="load-note">showing first 500 — refine the search to reach more</div>` : "");
      listEl.querySelectorAll("input[type=checkbox]").forEach((cb) =>
        cb.addEventListener("change", () => {
          if (cb.checked) selected.add(cb.value); else selected.delete(cb.value);
          cb.closest(".ident-row").classList.toggle("merged", cb.checked);
        }));
    }
    const qEl = m.body.querySelector("#pk-q");
    qEl.addEventListener("input", () => {
      clearTimeout(qEl._t);
      qEl._t = setTimeout(() => load(qEl.value.trim()), 300);
    });
    m.foot.querySelector("#pk-clear").addEventListener("click", () => {
      selected.clear();
      listEl.querySelectorAll("input[type=checkbox]").forEach((cb) => {
        cb.checked = false; cb.closest(".ident-row").classList.remove("merged");
      });
    });
    m.foot.querySelector("#pk-apply").addEventListener("click", () => {
      m.close();
      setFilters({ hashes: [...selected] });
      toast(selected.size
        ? `Commit set applied: ${selected.size} commits`
        : "Empty commit set applied (H = ∅)", "ok");
    });
    await load("");
  }

  /* ---------------------------------------------------- reference ref */

  function openRefModal() {
    if (!state.repo) return;
    const m = openModal({ title: "Reference commit h\u1d63" });
    m.body.innerHTML = `
      <p class="hint dim" style="margin:0 0 12px">
        H̄ is the set of non-merge commits reachable from a reference commit.
        Metrics normally use HEAD; point the dashboard at any commit, branch
        or tag to reproduce the numbers as of that point in history.
      </p>
      <label class="field"><span>Commit hash / branch / tag</span>
        <input type="text" id="rf-ref" placeholder="e.g. 6d9f2443 or v1.7.18"
               value="${esc(state.repo.ref || "HEAD")}"></label>
      <div class="dim" id="rf-note">${state.repo.ref
        ? "currently scoped to " + esc(state.repo.ref.slice(0, 12))
        : "currently using HEAD"}</div>`;
    m.foot.innerHTML = `<button class="btn ghost" id="rf-head">Reset to HEAD</button>
      <button class="btn primary" id="rf-go">Apply reference</button>`;
    const apply = async (ref) => {
      try {
        const res = await api("/reference", null, "POST", { ref });
        m.close();
        toast(res.ref
          ? `H̄ scoped to ${res.hash.slice(0, 10)} — ${C.fmtInt(res.commits)} commits`
          : "Reference reset to HEAD", "ok");
        await refreshRepos();
        invalidate();
        render();
      } catch (e) { toast(e.message, "error"); }
    };
    m.foot.querySelector("#rf-head").addEventListener("click", () => apply("HEAD"));
    m.foot.querySelector("#rf-go").addEventListener("click", () => {
      const v = m.body.querySelector("#rf-ref").value.trim() || "HEAD";
      apply(v);
    });
    const input = m.body.querySelector("#rf-ref");
    input.focus();
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") m.foot.querySelector("#rf-go").click();
    });
  }

  /* ---------------------------------------------------- merge / rename */

  function openMergeModal(identityIds) {
    if (!identityIds || identityIds.length < 2) {
      toast("Need at least two identities to merge", "error");
      return;
    }
    const m = openModal({ title: "Merge identities into one author" });
    m.body.innerHTML = `<label class="field"><span>Merged author name</span>
        <input type="text" id="mg-name" placeholder="e.g. Jane Doe"></label>
      <div class="dim" style="margin-bottom:6px">${identityIds.length} identities selected:</div>
      <div class="merge-box" id="mg-list"><div class="spinner"></div></div>`;
    m.foot.innerHTML = `<button class="btn ghost" id="mg-cancel">Cancel</button>
      <button class="btn primary" id="mg-go">Merge</button>`;
    m.foot.querySelector("#mg-cancel").addEventListener("click", m.close);
    authorIndex().then((idx) => {
      const rows = idx.identities.filter((i) => identityIds.includes(i.id));
      m.body.querySelector("#mg-list").innerHTML = rows.map((i) => `
        <div class="ident-row merged"><span></span>
          <span class="i-name">${esc(i.name)}</span>
          <span class="i-mail">${esc(i.email)}</span>
          <span class="dim">${C.fmtInt(i.commits)} commits</span></div>`).join("");
      const nameEl = m.body.querySelector("#mg-name");
      const joined = [...new Set(rows.map((i) => i.name.trim()))].join(" + ");
      nameEl.value = joined.slice(0, 80) || "Merged author";
    });
    m.foot.querySelector("#mg-go").addEventListener("click", async () => {
      const name = m.body.querySelector("#mg-name").value.trim();
      if (!name) { toast("A name is required", "error"); return; }
      try {
        await api("/authors/merge", null, "POST", { name, identity_ids: identityIds });
        m.close();
        toast(`Merged ${identityIds.length} identities into “${name}”`, "ok");
        invalidate();
        render();
      } catch (e) { toast(e.message, "error"); }
    });
  }

  function openRenameModal(authorId, currentName) {
    const m = openModal({ title: "Rename author" });
    m.body.innerHTML = `<label class="field"><span>New name</span>
      <input type="text" id="rn-name" value="${esc(currentName)}"></label>`;
    m.foot.innerHTML = `<button class="btn ghost" id="rn-cancel">Cancel</button>
      <button class="btn primary" id="rn-go">Rename</button>`;
    m.foot.querySelector("#rn-cancel").addEventListener("click", m.close);
    m.foot.querySelector("#rn-go").addEventListener("click", async () => {
      const name = m.body.querySelector("#rn-name").value.trim();
      if (!name) { toast("A name is required", "error"); return; }
      try {
        await api("/authors/rename", null, "POST", { author_id: authorId, name });
        m.close();
        toast(`Renamed to “${name}”`, "ok");
        invalidate();
        render();
      } catch (e) { toast(e.message, "error"); }
    });
  }

  /* ------------------------------------------------------------ exports */

  function exportCSV(filename, headers, rows) {
    const cell = (v) => {
      const s = v === null || v === undefined ? "" : String(v);
      return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    };
    const csv = [headers.join(",")].concat(rows.map((r) => r.map(cell).join(","))).join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
    URL.revokeObjectURL(a.href);
    toast("CSV exported: " + filename, "ok");
  }

  /* --------------------------------------------------------------- boot */

  const App = {
    get state() { return state; },
    get filters() { return state.filters; },
    ui,
    api, rootApi, filterParams, filtersActive, paths, authorIndex, invalidate,
    toast, navigate, setFilters, exportCSV,
    openMergeModal, openRenameModal, openCommitPicker, render, refreshRepos,
  };
  window.App = App;

  (async function boot() {
    welcomeCache = $("view").innerHTML;
    init();
    try { await refreshRepos(); } catch (e) { toast("Failed to load repositories: " + e.message, "error"); }
    const h = readHash();
    if (h && state.repos.some((r) => r.id === h.repoId)) {
      state.tab = h.tab;
      ui.dirId = h.dirId; ui.obj = h.obj;
      await selectRepo(h.repoId);
    } else if (state.repos.length) {
      const ready = state.repos.filter((r) => r.status === "ready");
      await selectRepo((ready[ready.length - 1] || state.repos[state.repos.length - 1]).id);
    } else {
      render();
    }
    // resume progress for repos that are mid-ingestion (e.g. after a reload)
    if (state.repos.some((r) => !["ready", "error"].includes(r.status))) {
      let lastStatus = state.repo ? state.repo.status : null;
      const poll = setInterval(async () => {
        try { await refreshRepos(); } catch (e) { return; }
        const cur = state.repo ? state.repo.status : null;
        if (cur !== lastStatus) { lastStatus = cur; invalidate(); render(); }
        if (!state.repos.some((r) => !["ready", "error"].includes(r.status))) {
          clearInterval(poll);
        }
      }, 2500);
    }
  })();
})();
