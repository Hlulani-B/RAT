/* RAT views — overview / browser / authors / commits renderers.
   Depends on the App object (app.js) and Charts (charts.js). */
(function () {
  "use strict";

  const C = window.Charts;
  const esc = C.esc;

  /* ---------------------------------------------------------- helpers */

  function icon(kind) {
    return kind === 1
      ? '<span class="p-icon" title="directory">🗀</span>'
      : '<span class="p-icon" title="file">🗎</span>';
  }

  function baseName(path) { return path.split("/").pop(); }
  function dirName(path) { const i = path.lastIndexOf("/"); return i < 0 ? "" : path.slice(0, i); }

  function pathHtml(p) {
    const d = dirName(p);
    return `<span class="dim">${esc(d ? d + "/" : "")}</span><b>${esc(baseName(p))}</b>`;
  }

  function bar(value, max, cls) {
    const w = max > 0 ? Math.max(2, (value / max) * 100) : 0;
    return `<div class="bar-cell"><div class="bar-track">` +
           `<div class="bar-fill ${cls || ""}" style="width:${w.toFixed(1)}%"></div>` +
           `</div></div>`;
  }

  function num(n) { return C.fmtInt(Math.round(n || 0)); }
  function cnum(n) { return C.fmtCompact(n || 0); }

  function signed(n) {
    const v = Math.round(n || 0);
    return (v > 0 ? "+" : "") + C.fmtInt(v);
  }

  function growthCls(n) { return n > 0 ? "mc-add" : (n < 0 ? "mc-del" : "dim"); }

  function panel(title, body, hint) {
    return `<div class="panel"><h3>${esc(title)}` +
           (hint ? `<span class="hint">${hint}</span>` : "") +
           `</h3>${body}</div>`;
  }

  function loading(el, note) {
    el.innerHTML = `<div class="spinner"></div>` +
                   (note ? `<div class="load-note">${esc(note)}</div>` : "");
  }

  function emptyNotice() {
    return `<div class="err-note">The current commit filter matches no commits — ` +
           `metrics are empty. Widen the time range, clear the author, or reset ` +
           `the manual commit selection.</div>`;
  }

  /* -------------------------------------------------------- OVERVIEW */

  async function overview(el) {
    loading(el, "computing metrics…");
    const [s, tl] = await Promise.all([
      App.api(`/summary`, App.filterParams()),
      App.api(`/timeline`, App.filterParams()),
    ]);
    if (s.commits === 0 && App.filtersActive()) { el.innerHTML = emptyNotice(); return; }

    const topFiles = await App.api(`/files`, { ...App.filterParams(), sort: "churn", limit: 12 });
    const authors = await App.api(`/authors`, App.filterParams());
    const fileIds = topFiles.rows.map((r) => r.id);
    let fileOwners = {};
    if (fileIds.length) {
      fileOwners = await App.api(`/authors_of`,
        { ...App.filterParams(), kind: 0, ids: fileIds.join(",") });
    }

    const rangeDays = (s.ts_max && s.ts_min)
      ? Math.max(1, Math.round((s.ts_max - s.ts_min) / 86400)) : 0;
    const avg = s.commits ? s.churn / s.commits : 0;

    let html = `<div class="cards">
      <div class="card"><div class="k">Commits in H</div><div class="v">${num(s.commits)}</div>
        <div class="s">${s.ts_min ? C.fmtDate(s.ts_min) + " → " + C.fmtDate(s.ts_max) : "—"}</div></div>
      <div class="card accent"><div class="k">Authors</div><div class="v">${num(s.authors)}</div>
        <div class="s">distinct, after merging</div></div>
      <div class="card"><div class="k">Files touched</div><div class="v">${num(s.files_touched)}</div>
        <div class="s">${rangeDays ? "over " + rangeDays + " days" : "—"}</div></div>
      <div class="card green"><div class="k">Added lines</div><div class="v">+${cnum(s.added)}</div>
        <div class="s">l⁺ over H</div></div>
      <div class="card red"><div class="k">Removed lines</div><div class="v">−${cnum(s.removed)}</div>
        <div class="s">l⁻ over H</div></div>
      <div class="card ${s.growth >= 0 ? "green" : "red"}"><div class="k">Growth</div>
        <div class="v">${signed(s.growth)}</div><div class="s">δ = l⁺ − l⁻</div></div>
      <div class="card violet"><div class="k">Churn</div><div class="v">${cnum(s.churn)}</div>
        <div class="s">λ = l⁺ + l⁻ · ${cnum(avg)}/commit</div></div>
    </div>`;

    html += panel("Activity over time",
      `<div id="tl-wrap"></div>`,
      "bars: added ↑ / removed ↓ · line: commits · click a bucket to filter the whole dashboard");

    const maxChurn = Math.max(...topFiles.rows.map((r) => r.churn), 1);
    html += panel("Hottest files",
      topFiles.rows.length ? `<table class="grid"><thead><tr>
        <th>File</th><th class="num">Churn</th><th></th><th class="num">Mods</th>
        <th class="num">Rate</th><th>Ownership</th></tr></thead><tbody>` +
      topFiles.rows.map((r) => `<tr class="selectable" data-kind="0" data-id="${r.id}">
        <td><div class="path-cell">${icon(0)}${pathHtml(r.path)}</div></td>
        <td class="num">${num(r.churn)}</td><td>${bar(r.churn, maxChurn)}</td>
        <td class="num">${num(r.modifications)}</td>
        <td class="num">${cnum(r.churn_rate)}</td>
        <td style="min-width:120px">${C.ownBar(fileOwners[r.id], 4)}</td></tr>`).join("") +
      `</tbody></table>` : '<div class="empty-hint">No file changes in this selection.</div>',
      'ranked by churn — click to open in Browser ' +
      '<button class="btn sm" id="exp-files" title="export this table as CSV">CSV</button>');

    const maxA = Math.max(...authors.map((a) => a.churn), 1);
    html += panel("Author impact",
      authors.length ? `<table class="grid"><thead><tr>
        <th>Author</th><th class="num">Commits</th><th class="num">Churn</th>
        <th></th><th class="num">Ownership</th></tr></thead><tbody>` +
      authors.slice(0, 12).map((a) => `<tr class="selectable" data-author="${a.author_id}">
        <td>${esc(a.name)}</td>
        <td class="num">${num(a.commits)}</td>
        <td class="num">${num(a.churn)}</td>
        <td>${bar(a.churn, maxA)}</td>
        <td class="num">${C.pct(a.ownership)}</td></tr>`).join("") +
      `</tbody></table>` : '<div class="empty-hint">No authors in this selection.</div>',
      "click to filter by author");

    el.innerHTML = html;
    C.timeline(el.querySelector("#tl-wrap"), tl, {
      onPick: (row, bucket) => {
        App.setFilters({ ts_from: row.bucket, ts_to: row.bucket + bucket });
        App.toast(`Filtered to ${C.fmtDate(row.bucket)} → ${C.fmtDate(row.bucket + bucket)}`, "ok");
      },
    });

    el.querySelectorAll("tr[data-author]").forEach((tr) => {
      tr.addEventListener("click", () => {
        App.setFilters({ author_id: +tr.dataset.author });
        App.toast("Filtered by author");
      });
    });
    el.querySelectorAll("tr[data-kind]").forEach((tr) => {
      tr.addEventListener("click", () => {
        App.navigate({ tab: "browser", obj: { kind: +tr.dataset.kind, id: +tr.dataset.id } });
      });
    });
    const expFiles = el.querySelector("#exp-files");
    if (expFiles) expFiles.addEventListener("click", () => {
      App.exportCSV("hottest-files.csv",
        ["path", "added", "removed", "growth", "churn", "modifications",
         "frequency", "churn_rate"],
        topFiles.rows.map((r) => [r.path, r.added, r.removed, r.growth, r.churn,
          r.modifications, r.frequency.toFixed(6), r.churn_rate.toFixed(6)]));
    });
  }

  /* --------------------------------------------------------- BROWSER */

  async function browser(el) {
    loading(el, "loading tree…");
    const paths = await App.paths();
    const byId = {}; for (const p of paths) byId[p.id] = p;
    const root = paths.find((p) => p.kind === 1 && p.path === "");
    if (!root) { el.innerHTML = '<div class="err-note">Repository has no root path.</div>'; return; }

    // follow a selected file into its parent directory
    const selRow = App.ui.obj ? byId[App.ui.obj.id] : null;
    let dirId = App.ui.dirId;
    if (selRow && selRow.kind === 0 && selRow.parent_id) dirId = selRow.parent_id;
    if (!byId[dirId]) dirId = root.id;
    App.ui.dirId = dirId;
    const dir = byId[dirId];

    // resolve the focused object (selected file/dir or the current directory)
    const focus = selRow ? { kind: selRow.kind, id: selRow.id }
                         : { kind: 1, id: dirId };

    const [tree, focusM, hist] = await Promise.all([
      App.api(`/tree`, { ...App.filterParams(), dir: dirId }),
      App.api(`/objects`, { ...App.filterParams(), kind: focus.kind, id: focus.id }),
      App.api(`/history`, { ...App.filterParams(), kind: focus.kind, id: focus.id, limit: 100 }),
    ]);

    // breadcrumbs
    const crumbs = [];
    for (let p = dir; p; p = byId[p.parent_id]) crumbs.unshift(p);
    const crumbHtml = `<div class="crumbs">` + crumbs.map((p, i) => {
      const last = i === crumbs.length - 1;
      const label = p.path === "" ? "⏣ root" : esc(p.name);
      return (last ? `<span class="cur">${label}</span>`
                   : `<a data-dir="${p.id}">${label}</a><span class="sep">/</span>`);
    }).join("") + `<div style="flex:1"></div>
      <span class="hint dim">${tree.children.length} immediate children</span></div>`;

    const maxChurn = Math.max(...tree.children.map((r) => r.churn), 1);
    const mode = App.ui.browserMode || "table";

    let left = `<div class="panel">
      <h3>Contents of /${esc(dir.path)}
        <span class="view-toggle seg">
          <button data-mode="table" class="${mode === "table" ? "active" : ""}">Table</button>
          <button data-mode="treemap" class="${mode === "treemap" ? "active" : ""}">Treemap</button>
        </span></h3>`;

    if (!tree.children.length) {
      left += `<div class="empty-hint">No objects change inside this directory under the current filter.</div>`;
    } else if (mode === "treemap") {
      left += `<div id="tm-wrap" class="treemap-wrap"></div>
        <div class="hint dim" style="margin-top:8px">area = churn · green = net growth · red = net shrink ·
        click a directory to open it, a file to inspect it</div>`;
    } else {
      left += `<table class="grid" id="dir-table"><thead><tr>
        <th class="sortable" data-sort="name">Name</th>
        <th class="sortable num" data-sort="added">Added</th>
        <th class="sortable num" data-sort="removed">Removed</th>
        <th class="sortable num" data-sort="growth">Growth</th>
        <th class="sortable num" data-sort="churn">Churn</th><th></th>
        <th class="sortable num" data-sort="modifications">Mods</th>
        <th class="sortable num" data-sort="frequency">Freq</th>
        <th class="sortable num" data-sort="churn_rate">Rate</th>
      </tr></thead><tbody>` +
      tree.children.map((r) => `<tr class="selectable${focus.kind === r.kind && focus.id === r.id ? " active" : ""}" data-kind="${r.kind}" data-id="${r.id}">
        <td><div class="path-cell">${icon(r.kind)}<span class="p-name">${esc(r.name)}</span></div></td>
        <td class="num">${num(r.added)}</td>
        <td class="num">${num(r.removed)}</td>
        <td class="num ${growthCls(r.growth)}">${signed(r.growth)}</td>
        <td class="num">${num(r.churn)}</td>
        <td>${bar(r.churn, maxChurn)}</td>
        <td class="num">${num(r.modifications)}</td>
        <td class="num">${C.pct(r.frequency, 0)}</td>
        <td class="num">${cnum(r.churn_rate)}</td></tr>`).join("") +
      `</tbody></table>`;
    }
    left += `</div>`;

    // right: detail panel on the focused object
    const fm = focusM.metrics;
    const isDir = focus.kind === 1;
    const focusPath = byId[focus.id] ? byId[focus.id].path : "?";
    let right = `<div class="panel">
      <div class="detail-head">${icon(focus.kind)}<h2>${esc(isDir ? "/" + focusPath : baseName(focusPath))}</h2></div>
      <div class="detail-sub">${isDir ? "directory" : "file"} · ${esc(focusPath || "/")}</div>
      <div class="detail-kpis">
        <div class="dkpi"><div class="k">Added</div><div class="v green">+${cnum(fm.added)}</div></div>
        <div class="dkpi"><div class="k">Removed</div><div class="v red">−${cnum(fm.removed)}</div></div>
        <div class="dkpi"><div class="k">Growth</div><div class="v ${fm.growth >= 0 ? "green" : "red"}">${signed(fm.growth)}</div></div>
        <div class="dkpi"><div class="k">Churn</div><div class="v violet">${cnum(fm.churn)}</div></div>
        <div class="dkpi"><div class="k">Modifications</div><div class="v accent">${num(fm.modifications)}</div></div>
        <div class="dkpi"><div class="k">Churn rate</div><div class="v">${cnum(fm.churn_rate)}</div></div>
        <div class="dkpi"><div class="k">Frequency η</div><div class="v">${C.pct(fm.frequency)}</div></div>
        <div class="dkpi"><div class="k">|H|</div><div class="v">${num(fm.commits)}</div></div>
      </div>`;
    if (fm.by_author.length) {
      right += `<div class="k dim" style="font-size:11px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px">Ownership ω (by churn)</div>` +
        C.ownBar(fm.by_author, 6) + C.ownLegend(fm.by_author, 6);
    }
    right += `</div>`;

    right += panel("Churn per commit",
      `<div id="spark-wrap"></div>`,
      isDir ? "every commit touching this directory" : "every commit touching this file");
    right += panel("Commits touching this " + (isDir ? "directory" : "file"),
      hist.rows.length ? `<div class="mini-commits">` + hist.rows.map((r) =>
        `<div class="mini-commit" title="${esc(r.subject)}">
           <span class="mono dim">${esc(r.hash.slice(0, 8))}</span>
           <span class="mc-msg">${esc(r.subject)}</span>
           <span class="mc-d"><span class="mc-add">+${r.added}</span><span class="mc-del">−${r.removed}</span></span>
         </div>`).join("") + `</div>` :
      '<div class="empty-hint">No commits touch this object under the current filter.</div>');

    el.innerHTML = `<div class="browser-cols"><div>${crumbHtml}${left}</div><div>${right}</div></div>`;

    // interactions
    el.querySelectorAll("[data-dir]").forEach((a) =>
      a.addEventListener("click", () =>
        App.navigate({ dirId: +a.dataset.dir, obj: null })));
    el.querySelectorAll(".view-toggle button").forEach((b) =>
      b.addEventListener("click", () => {
        App.ui.browserMode = b.dataset.mode;
        browser(el);
      }));

    if (mode === "treemap") {
      C.treemap(el.querySelector("#tm-wrap"), tree.children, {
        onOpenDir: (n) => App.navigate({ dirId: n.id, obj: null }),
        onPick: (n) => App.navigate({ obj: { kind: n.kind, id: n.id } }),
      });
    } else {
      el.querySelectorAll("#dir-table tbody tr").forEach((tr) => {
        tr.addEventListener("click", () => {
          const kind = +tr.dataset.kind, id = +tr.dataset.id;
          if (kind === 1) App.navigate({ dirId: id, obj: null });
          else App.navigate({ obj: { kind, id } });
        });
      });
      // client-side sort of the children table
      el.querySelectorAll("#dir-table th.sortable").forEach((th) => {
        th.addEventListener("click", () => {
          const key = th.dataset.sort;
          const desc = App.ui.dirSortDesc =
            !(App.ui.dirSortKey === key && App.ui.dirSortDesc === true);
          App.ui.dirSortKey = key;
          const rows = [...tree.children].sort((a, b) => {
            const va = a[key], vb = b[key];
            const c = typeof va === "string" ? va.localeCompare(vb) : (va - vb);
            return desc ? -c : c;
          });
          renderChildrenTable(el, rows, focus, maxChurn, key, desc);
        });
      });
    }

    C.sparkline(el.querySelector("#spark-wrap"), hist.rows);
  }

  function renderChildrenTable(el, children, focus, maxChurn, sortKey, desc) {
    const table = el.querySelector("#dir-table");
    if (!table) return;
    const body = table.querySelector("tbody");
    body.innerHTML = children.map((r) => `<tr class="selectable ${focus.kind === r.kind && focus.id === r.id ? "active" : ""}"
        data-kind="${r.kind}" data-id="${r.id}">
      <td><div class="path-cell">${icon(r.kind)}<span class="p-name">${esc(r.name)}</span></div></td>
      <td class="num">${num(r.added)}</td>
      <td class="num">${num(r.removed)}</td>
      <td class="num ${growthCls(r.growth)}">${signed(r.growth)}</td>
      <td class="num">${num(r.churn)}</td>
      <td>${bar(r.churn, maxChurn)}</td>
      <td class="num">${num(r.modifications)}</td>
      <td class="num">${C.pct(r.frequency, 0)}</td>
      <td class="num">${cnum(r.churn_rate)}</td></tr>`).join("");
    table.querySelectorAll("th.sortable").forEach((th) => {
      th.classList.toggle("sorted", th.dataset.sort === sortKey);
    });
    body.querySelectorAll("tr").forEach((tr) => {
      tr.addEventListener("click", () => {
        const kind = +tr.dataset.kind, id = +tr.dataset.id;
        if (kind === 1) App.navigate({ dirId: id, obj: null });
        else App.navigate({ obj: { kind, id } });
      });
    });
  }

  /* --------------------------------------------------------- AUTHORS */

  async function authors(el) {
    loading(el, "ranking authors…");
    const [ranked, index] = await Promise.all([
      App.api(`/authors`, App.filterParams()),
      App.authorIndex(),
    ]);
    const maxChurn = Math.max(...ranked.map((a) => a.churn), 1);
    const idents = index.identities || [];
    const byAuthor = {};
    for (const i of idents) (byAuthor[i.author_id] = byAuthor[i.author_id] || []).push(i);

    let html = panel("Authors in H",
      ranked.length ? `<table class="grid"><thead><tr>
        <th>#</th><th>Author</th><th class="num">Commits</th><th class="num">Added</th>
        <th class="num">Removed</th><th class="num">Churn</th><th></th>
        <th class="num">Mods</th><th class="num">Ownership</th><th>Identities</th><th></th>
      </tr></thead><tbody>` +
      ranked.map((a, i) => `<tr>
        <td class="dim">${i + 1}</td>
        <td><b>${esc(a.name)}</b></td>
        <td class="num">${num(a.commits)}</td>
        <td class="num mc-add">+${num(a.added)}</td>
        <td class="num mc-del">−${num(a.removed)}</td>
        <td class="num">${num(a.churn)}</td>
        <td>${bar(a.churn, maxChurn)}</td>
        <td class="num">${num(a.modifications)}</td>
        <td class="num">${C.pct(a.ownership)}</td>
        <td class="num"><span class="pill">${(byAuthor[a.author_id] || []).length || 1}</span></td>
        <td class="nowrap">
          <button class="btn sm" data-filter="${a.author_id}">filter</button>
          <button class="btn sm" data-merge="${a.author_id}">merge…</button>
          <button class="btn sm" data-rename="${a.author_id}" data-name="${esc(a.name)}">rename</button>
          ${(byAuthor[a.author_id] || []).length > 1
            ? `<button class="btn sm danger" data-unmerge="${a.author_id}">unmerge</button>` : ""}
        </td></tr>`).join("") + `</tbody></table>` :
      '<div class="empty-hint">No authors in the current selection.</div>',
      'ownership ω = author churn / total churn over H ' +
      '<button class="btn sm" id="exp-authors" title="export this table as CSV">CSV</button>');

    html += panel("Identities — merge authors that are the same person",
      `<p class="hint dim" style="margin:0 0 10px">
         Identities are post-mailmap name+email pairs seen in the history.
         Tick two or more identities and merge them into a single author.
       </p>
       <div class="merge-box" id="ident-list">` +
      (idents.length ? idents.map((i) => `
        <label class="ident-row ${byAuthor[i.author_id] && byAuthor[i.author_id].length > 1 ? "merged" : ""}">
          <input type="checkbox" value="${i.id}">
          <span class="i-name">${esc(i.name)}</span>
          <span class="i-mail">${esc(i.email)}</span>
          <span class="dim" title="commits in repository">${num(i.commits)} commits</span>
        </label>`).join("") :
        '<div class="empty-hint">No identities.</div>') +
      `</div>
       <div style="display:flex;gap:8px;margin-top:12px;align-items:center">
         <input type="text" id="merge-name" placeholder="Name for the merged author…" style="flex:1">
         <button class="btn primary" id="merge-go" disabled>Merge selected</button>
       </div>`);
    el.innerHTML = html;

    const expA = el.querySelector("#exp-authors");
    if (expA) expA.addEventListener("click", () => {
      App.exportCSV("authors.csv",
        ["rank", "name", "commits", "added", "removed", "churn",
         "modifications", "ownership"],
        ranked.map((a, i) => [i + 1, a.name, a.commits, a.added, a.removed,
          a.churn, a.modifications, a.ownership.toFixed(6)]));
    });

    el.querySelectorAll("[data-filter]").forEach((b) =>
      b.addEventListener("click", () => {
        App.setFilters({ author_id: +b.dataset.filter });
        App.toast("Filtered by author");
      }));
    el.querySelectorAll("[data-merge]").forEach((b) =>
      b.addEventListener("click", () => {
        const ids = (byAuthor[+b.dataset.merge] || []).map((i) => i.id);
        App.openMergeModal(ids);
      }));
    el.querySelectorAll("[data-rename]").forEach((b) =>
      b.addEventListener("click", () => App.openRenameModal(+b.dataset.rename, b.dataset.name)));
    el.querySelectorAll("[data-unmerge]").forEach((b) =>
      b.addEventListener("click", async () => {
        if (!confirm("Split this author back into separate identities?")) return;
        await App.api(`/authors/unmerge`, null, "POST", { author_id: +b.dataset.unmerge });
        App.toast("Author split back into separate identities", "ok");
        App.invalidate(); authors(el);
      }));

    const boxes = [...el.querySelectorAll("#ident-list input[type=checkbox]")];
    const go = el.querySelector("#merge-go");
    const sync = () => { go.disabled = boxes.filter((b) => b.checked).length < 2; };
    boxes.forEach((b) => b.addEventListener("change", sync));
    go.addEventListener("click", async () => {
      const ids = boxes.filter((b) => b.checked).map((b) => +b.value);
      const name = el.querySelector("#merge-name").value.trim() ||
                   "Merged (" + ids.length + " identities)";
      await App.api(`/authors/merge`, null, "POST", { name, identity_ids: ids });
      App.toast(`Merged ${ids.length} identities into “${name}”`, "ok");
      App.invalidate(); authors(el);
    });
  }

  /* --------------------------------------------------------- COMMITS */

  async function commits(el) {
    loading(el, "loading commits…");
    const page = 50;
    const q = App.ui.commitsQ || "";
    const offset = App.ui.commitsOffset || 0;
    const data = await App.api(`/commits`,
      { ...App.filterParams(), q, limit: page, offset });
    const sel = new Set(App.filters.hashes || []);

    let banner = "";
    if (App.filters.hashes !== undefined) {
      banner = `<div class="panel" style="border-color:#3a3060;background:#161228">
        <div style="display:flex;align-items:center;gap:10px">
          <b>Manual commit set active</b>
          <span class="muted">${(App.filters.hashes || []).length} commits selected —
            every metric on the dashboard reflects this set (intersected with other filters).</span>
          <div style="flex:1"></div>
          <button class="btn sm" id="btn-clear-sel">clear selection</button>
        </div></div>`;
    }

    const maxChurn = Math.max(...data.rows.map((r) => r.added + r.removed), 1);
    el.innerHTML = banner + panel("Commits",
      `<div style="display:flex;gap:8px;align-items:center;margin-bottom:10px">
        <input type="search" id="cq" placeholder="Search subject / author / hash…" value="${esc(q)}" style="flex:1;max-width:380px">
        <span class="muted">${num(data.total)} matching commits</span>
        <div style="flex:1"></div>
        <button class="btn sm" id="exp-csv" title="export this page as CSV">CSV</button>
        <button class="btn sm" id="sel-page">select page</button>
        <button class="btn sm primary" id="sel-apply">apply selection →</button>
        <button class="btn sm" id="sel-clear">unselect all</button>
      </div>
      ` + (data.rows.length ? `<table class="grid"><thead><tr>
        <th></th><th>Commit</th><th>Date</th><th>Author</th><th>Subject</th>
        <th class="num">Added</th><th class="num">Removed</th><th class="num">Churn</th><th></th>
      </tr></thead><tbody>` +
      data.rows.map((r) => `<tr>
        <td><input type="checkbox" data-hash="${r.hash}" ${sel.has(r.hash) ? "checked" : ""}></td>
        <td class="mono dim">${esc(r.hash.slice(0, 8))}</td>
        <td class="nowrap">${C.fmtDate(r.ts)}</td>
        <td>${esc(r.author)}</td>
        <td><div class="commit-sub" title="${esc(r.subject)}">${esc(r.subject)}</div></td>
        <td class="num mc-add">+${r.added}</td>
        <td class="num mc-del">−${r.removed}</td>
        <td class="num">${r.added + r.removed}</td>
        <td>${bar(r.added + r.removed, maxChurn)}</td>
      </tr>`).join("") + `</tbody></table>` :
      '<div class="empty-hint">No commits match.</div>') +
      `<div class="pager">
        <button class="btn sm" id="pg-prev" ${offset <= 0 ? "disabled" : ""}>← prev</button>
        <span>${offset + 1}–${Math.min(offset + page, data.total)} of ${num(data.total)}</span>
        <button class="btn sm" id="pg-next" ${offset + page >= data.total ? "disabled" : ""}>next →</button>
      </div>`,
      "tick commits to build a manual commit set (H)");

    const qEl = el.querySelector("#cq");
    qEl.addEventListener("input", () => {
      clearTimeout(qEl._t);
      qEl._t = setTimeout(() => {
        App.ui.commitsQ = qEl.value;
        App.ui.commitsOffset = 0;
        commits(el);
      }, 300);
    });
    el.querySelector("#pg-prev").addEventListener("click", () => {
      App.ui.commitsOffset = Math.max(0, offset - page); commits(el);
    });
    el.querySelector("#pg-next").addEventListener("click", () => {
      App.ui.commitsOffset = offset + page; commits(el);
    });
    const boxList = () => [...el.querySelectorAll("tbody input[type=checkbox]")];
    el.querySelector("#sel-page").addEventListener("click", () =>
      boxList().forEach((b) => { b.checked = true; }));
    el.querySelector("#sel-clear").addEventListener("click", () =>
      boxList().forEach((b) => { b.checked = false; }));
    el.querySelector("#exp-csv").addEventListener("click", () => {
      App.exportCSV("commits.csv",
        ["hash", "date", "author", "subject", "added", "removed", "churn"],
        data.rows.map((r) => [r.hash,
          new Date(r.ts * 1000).toISOString().slice(0, 10),
          r.author, r.subject, r.added, r.removed, r.added + r.removed]));
    });
    el.querySelector("#sel-apply").addEventListener("click", () => {
      const hashes = boxList().filter((b) => b.checked).map((b) => b.dataset.hash);
      if (!hashes.length) { App.toast("Select at least one commit first", "error"); return; }
      App.setFilters({ hashes });
      App.toast(`Manual commit set applied: ${hashes.length} commits`, "ok");
      App.toast("Tip: the Commits tab always reflects the active filter", "ok");
    });
    const clr = el.querySelector("#btn-clear-sel");
    if (clr) clr.addEventListener("click", () => {
      App.setFilters({ hashes: undefined });
      App.toast("Manual commit selection cleared", "ok");
    });
  }

  window.Views = { overview, browser, authors, commits };
})();
