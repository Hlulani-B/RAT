/* RAT charts — hand-rolled SVG primitives, no dependencies. */
(function () {
  "use strict";

  const NS = "http://www.w3.org/2000/svg";
  const OWN_COLORS = ["#5b9dff", "#3ddc84", "#b48cff", "#ffb454", "#4fd6e0",
                      "#ff5c6c", "#9ae66e", "#f78fb3"];

  const esc = (s) => String(s).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;",
              "'": "&#39;" }[c]));

  function fmtInt(n) {
    if (n === null || n === undefined) return "–";
    return Number(n).toLocaleString("en-US");
  }

  function fmtCompact(n) {
    if (n === null || n === undefined) return "–";
    const a = Math.abs(n), s = n < 0 ? "-" : "";
    if (a >= 1e6) return s + (a / 1e6).toFixed(a >= 1e7 ? 0 : 1) + "M";
    if (a >= 1e3) return s + (a / 1e3).toFixed(a >= 1e4 ? 0 : 1) + "k";
    return s + a;
  }

  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  function fmtDate(ts) {
    const d = new Date(ts * 1000);
    return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
  }

  function fmtMonth(ts) {
    const d = new Date(ts * 1000);
    return `${MONTHS[d.getUTCMonth()]} ${String(d.getUTCFullYear()).slice(2)}`;
  }

  function pct(x, digits) {
    return (100 * (x || 0)).toFixed(digits === undefined ? 1 : digits) + "%";
  }

  /* ------------------------------------------------------------ tooltip */

  const tipEl = () => document.getElementById("tooltip");

  function tipShow(html, x, y) {
    const el = tipEl();
    el.innerHTML = html;
    el.classList.remove("hidden");
    const r = el.getBoundingClientRect();
    let left = x + 14, top = y + 14;
    if (left + r.width > window.innerWidth - 10) left = x - r.width - 12;
    if (top + r.height > window.innerHeight - 10) top = y - r.height - 12;
    el.style.left = Math.max(6, left) + "px";
    el.style.top = Math.max(6, top) + "px";
  }

  function tipHide() { tipEl().classList.add("hidden"); }

  function tipRow(label, value, cls) {
    return `<div class="t-row"><span>${esc(label)}</span>` +
           `<b class="${cls || ""}">${esc(value)}</b></div>`;
  }

  /* ----------------------------------------------------------- timeline */

  function timeline(container, data, opts) {
    const o = opts || {};
    const rows = (data && data.rows) || [];
    container.innerHTML = "";
    if (!rows.length) {
      container.innerHTML = '<div class="empty-hint">No commits in this range.</div>';
      return;
    }
    const W = Math.max(container.clientWidth || 640, 320);
    const H = o.height || 190;
    const P = { l: 52, r: 12, t: 12, b: 26 };
    const pw = W - P.l - P.r, ph = H - P.t - P.b;
    const mid = P.t + ph / 2;
    const maxA = Math.max(...rows.map((r) => r.added), 1);
    const maxR = Math.max(...rows.map((r) => r.removed), 1);
    const maxC = Math.max(...rows.map((r) => r.commits), 1);
    const maxSide = Math.max(maxA, maxR);
    const n = rows.length;
    const bw = Math.max(2, pw / n - Math.min(6, pw / n * 0.22));

    let svg = `<svg class="tl-chart" viewBox="0 0 ${W} ${H}" ` +
              `width="${W}" height="${H}" role="img" aria-label="commit activity timeline">`;

    // horizontal gridlines + y labels (added scale, mirrored)
    for (const frac of [1, 0.5]) {
      const yUp = mid - (ph / 2) * frac, yDn = mid + (ph / 2) * frac;
      const v = Math.round(maxSide * frac);
      svg += `<line class="gridline" x1="${P.l}" x2="${W - P.r}" y1="${yUp}" y2="${yUp}"/>`;
      svg += `<line class="gridline" x1="${P.l}" x2="${W - P.r}" y1="${yDn}" y2="${yDn}"/>`;
      svg += `<text class="axis-label" x="${P.l - 7}" y="${yUp + 3}" text-anchor="end">+${fmtCompact(v)}</text>`;
      svg += `<text class="axis-label" x="${P.l - 7}" y="${yDn + 3}" text-anchor="end">-${fmtCompact(v)}</text>`;
    }
    svg += `<line x1="${P.l}" x2="${W - P.r}" y1="${mid}" y2="${mid}" stroke="#3a4a63"/>`;

    // commit-count line (own scale)
    let line = "";
    rows.forEach((r, i) => {
      const cx = P.l + (pw / n) * (i + 0.5);
      const cy = mid - (r.commits / maxC) * (ph * 0.44);
      line += (i ? " L" : "M") + cx.toFixed(1) + " " + cy.toFixed(1);
    });
    svg += `<path d="${line}" fill="none" stroke="#4fd6e0" stroke-width="1.6" opacity="0.9"/>`;

    // bars + hit areas
    rows.forEach((r, i) => {
      const cx = P.l + (pw / n) * (i + 0.5);
      const x = cx - bw / 2;
      const hUp = (r.added / maxSide) * (ph / 2);
      const hDn = (r.removed / maxSide) * (ph / 2);
      svg += `<rect class="bucket" x="${x.toFixed(1)}" y="${(mid - hUp).toFixed(1)}" ` +
             `width="${bw.toFixed(1)}" height="${Math.max(hUp, 0.5).toFixed(1)}" fill="#3ddc84" opacity="0.85"/>`;
      svg += `<rect class="bucket" x="${x.toFixed(1)}" y="${mid.toFixed(1)}" ` +
             `width="${bw.toFixed(1)}" height="${Math.max(hDn, 0.5).toFixed(1)}" fill="#ff5c6c" opacity="0.85"/>`;
      // full-column hit area for hovering/clicking
      svg += `<rect x="${(cx - pw / n / 2).toFixed(1)}" y="${P.t}" width="${(pw / n).toFixed(1)}" ` +
             `height="${ph}" fill="transparent" data-i="${i}" class="hit"/>`;
    });

    // x labels (~5 ticks)
    const step = Math.max(1, Math.ceil(n / 5));
    for (let i = 0; i < n; i += step) {
      const cx = P.l + (pw / n) * (i + 0.5);
      const label = (data.bucket >= 30 * 86400) ? fmtMonth(rows[i].bucket)
                                                : fmtDate(rows[i].bucket);
      svg += `<text class="axis-label" x="${cx.toFixed(1)}" y="${H - 8}" text-anchor="middle">${esc(label)}</text>`;
    }
    svg += "</svg>";
    container.innerHTML = svg;

    // interaction
    const svgEl = container.querySelector("svg");
    svgEl.querySelectorAll("rect.hit").forEach((hit) => {
      hit.style.cursor = o.onPick ? "pointer" : "default";
      hit.addEventListener("mousemove", (ev) => {
        const r = rows[+hit.dataset.i];
        const end = r.bucket + data.bucket;
        const html = `<div class="t-title">${fmtDate(Math.max(r.bucket, 0))} → ${fmtDate(end)}</div>` +
          tipRow("commits", fmtInt(r.commits)) +
          tipRow("added", "+" + fmtInt(r.added), "mc-add") +
          tipRow("removed", "-" + fmtInt(r.removed), "mc-del") +
          tipRow("growth", (r.added - r.removed >= 0 ? "+" : "") + fmtInt(r.added - r.removed)) +
          (o.onPick ? '<div class="t-row"><span></span><b>click to filter →</b></div>' : "");
        tipShow(html, ev.clientX, ev.clientY);
      });
      hit.addEventListener("mouseleave", tipHide);
      hit.addEventListener("click", (ev) => {
        if (o.onPick) o.onPick(rows[+hit.dataset.i], data.bucket);
      });
    });
  }

  /* ----------------------------------------------------------- treemap */

  // Squarified treemap layout (Bruls et al.). items: [{value,...}] desc.
  function squarify(items, x, y, w, h) {
    const out = [];
    const total = items.reduce((s, it) => s + it.value, 0) || 1;
    const scale = (w * h) / total;
    const rest = items.map((it) => ({ it, area: it.value * scale }));
    let rect = { x, y, w, h };
    let i = 0;
    while (i < rest.length) {
      const short = Math.min(rect.w, rect.h);
      let row = [], rowArea = 0, best = Infinity, bestN = 1;
      for (let j = i; j < rest.length; j++) {
        rowArea += rest[j].area;
        const rowLen = rowArea / short;
        let worst = 0;
        for (const r of rest.slice(i, j + 1)) {
          const side = r.area / rowLen;
          worst = Math.max(worst, Math.max(rowLen / side, side / rowLen));
        }
        if (worst > best) break;
        best = worst; bestN = j - i + 1;
      }
      row = rest.slice(i, i + bestN);
      rowArea = row.reduce((s, r) => s + r.area, 0);
      const rowLen = rowArea / short;
      let off = 0;
      for (const r of row) {
        const side = r.area / rowLen;
        if (rect.w >= rect.h) {
          out.push({ item: r.it, x: rect.x, y: rect.y + off, w: side, h: rowLen });
        } else {
          out.push({ item: r.it, x: rect.x + off, y: rect.y, w: rowLen, h: side });
        }
        off += side;
      }
      if (rect.w >= rect.h) {
        rect = { x: rect.x + rowLen, y: rect.y, w: rect.w - rowLen, h: rect.h };
      } else {
        rect = { x: rect.x, y: rect.y + rowLen, w: rect.w, h: rect.h - rowLen };
      }
      i += bestN;
    }
    return out;
  }

  function growthColor(added, removed) {
    const churn = added + removed;
    if (!churn) return "#31405a";
    const net = (added - removed) / churn; // -1..1
    const t = Math.min(1, Math.abs(net));
    if (net >= 0) {
      const r = Math.round(38 + (1 - t) * 60);
      const g = Math.round(120 + t * 100);
      const b = Math.round(100 + (1 - t) * 40);
      return `rgb(${r},${g},${b})`;
    }
    const r = Math.round(150 + t * 105);
    const g = Math.round(70 - t * 20);
    const b = Math.round(80 - t * 10);
    return `rgb(${r},${g},${b})`;
  }

  function treemap(container, nodes, opts) {
    const o = opts || {};
    container.innerHTML = "";
    const items = nodes.filter((n) => n.churn > 0)
                       .map((n) => ({ value: n.churn, node: n }))
                       .sort((a, b) => b.value - a.value);
    if (!items.length) {
      container.innerHTML = '<div class="empty-hint">Nothing with churn in this selection.</div>';
      return;
    }
    const W = Math.max(container.clientWidth || 640, 320);
    const H = o.height || 380;
    const cells = squarify(items, 0, 0, W, H);
    let svg = `<svg class="treemap" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" ` +
              `role="img" aria-label="treemap of directory contents by churn">`;
    for (const c of cells) {
      if (c.w < 1 || c.h < 1) continue;
      const n = c.item.node;
      const fill = growthColor(n.added, n.removed);
      svg += `<rect x="${c.x.toFixed(1)}" y="${c.y.toFixed(1)}" ` +
             `width="${Math.max(c.w - 1.5, 0.5).toFixed(1)}" ` +
             `height="${Math.max(c.h - 1.5, 0.5).toFixed(1)}" rx="3" ` +
             `fill="${fill}" data-id="${n.id}" data-kind="${n.kind}"/>`;
      if (c.w > 54 && c.h > 20) {
        const label = n.name.length > Math.floor(c.w / 7)
          ? n.name.slice(0, Math.max(3, Math.floor(c.w / 7) - 1)) + "…"
          : n.name;
        svg += `<text x="${(c.x + 6).toFixed(1)}" y="${(c.y + 14).toFixed(1)}">${esc(label)}</text>`;
      }
    }
    svg += "</svg>";
    container.innerHTML = svg;

    const byId = {};
    for (const n of nodes) byId[n.id] = n;
    container.querySelectorAll("rect").forEach((rect) => {
      const n = byId[+rect.dataset.id];
      if (!n) return;
      rect.addEventListener("mousemove", (ev) => {
        const html = `<div class="t-title">${n.kind === 1 ? "🗀 " : "🗎 "}${esc(n.path || "/")}</div>` +
          tipRow("churn", fmtInt(n.churn)) +
          tipRow("added", "+" + fmtInt(n.added), "mc-add") +
          tipRow("removed", "-" + fmtInt(n.removed), "mc-del") +
          tipRow("growth", (n.growth >= 0 ? "+" : "") + fmtInt(n.growth)) +
          tipRow("modifications", fmtInt(n.modifications)) +
          tipRow("churn rate", fmtCompact(n.churn_rate) + " /commit") +
          (n.kind === 1 && o.onOpenDir ? '<div class="t-row"><span></span><b>click to open →</b></div>' : "");
        tipShow(html, ev.clientX, ev.clientY);
      });
      rect.addEventListener("mouseleave", tipHide);
      rect.addEventListener("click", () => {
        if (n.kind === 1 && o.onOpenDir) o.onOpenDir(n);
        else if (o.onPick) o.onPick(n);
      });
    });
  }

  /* --------------------------------------------------- ownership bars */

  function ownBar(byAuthor, maxSegs) {
    const segs = (byAuthor || []).slice(0, maxSegs || 6);
    if (!segs.length) return '<span class="dim">no data</span>';
    let html = '<div class="ownbar">';
    let used = 0;
    segs.forEach((a, i) => {
      const w = Math.max(1.5, a.share * 100);
      used += w;
      html += `<i style="width:${w.toFixed(2)}%;background:${OWN_COLORS[i % OWN_COLORS.length]}" ` +
              `title="${esc(a.name)} — ${pct(a.share)}"></i>`;
    });
    if (used < 100) {
      html += `<i style="width:${(100 - used).toFixed(2)}%;background:#2a3549"></i>`;
    }
    return html + "</div>";
  }

  function ownLegend(byAuthor, maxSegs) {
    const segs = (byAuthor || []).slice(0, maxSegs || 6);
    return '<div class="ownlegend">' + segs.map((a, i) =>
      `<span><i class="sw" style="background:${OWN_COLORS[i % OWN_COLORS.length]}"></i>` +
      `${esc(a.name)} · ${pct(a.share)}</span>`).join("") + "</div>";
  }

  /* ----------------------------------------------------------- sparkline */

  function sparkline(container, rows, opts) {
    const o = opts || {};
    container.innerHTML = "";
    if (!rows.length) {
      container.innerHTML = '<div class="empty-hint">No commits touch this object.</div>';
      return;
    }
    const W = Math.max(container.clientWidth || 340, 240);
    const H = o.height || 54;
    const mid = H / 2;
    const chrono = rows.slice().reverse();
    const maxA = Math.max(...chrono.map((r) => r.added), 1);
    const maxR = Math.max(...chrono.map((r) => r.removed), 1);
    const maxSide = Math.max(maxA, maxR);
    const n = chrono.length;
    const step = W / n;
    const bw = Math.max(1.5, step * 0.62);
    let svg = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" aria-label="per-commit churn">`;
    svg += `<line x1="0" x2="${W}" y1="${mid}" y2="${mid}" stroke="#2d3a50"/>`;
    chrono.forEach((r, i) => {
      const cx = i * step + step / 2;
      const hu = (r.added / maxSide) * (mid - 3);
      const hd = (r.removed / maxSide) * (mid - 3);
      svg += `<rect x="${(cx - bw / 2).toFixed(1)}" y="${(mid - hu).toFixed(1)}" width="${bw.toFixed(1)}" ` +
             `height="${Math.max(hu, 0.6).toFixed(1)}" fill="#3ddc84" opacity="0.9" data-i="${i}"/>`;
      svg += `<rect x="${(cx - bw / 2).toFixed(1)}" y="${mid}" width="${bw.toFixed(1)}" ` +
             `height="${Math.max(hd, 0.6).toFixed(1)}" fill="#ff5c6c" opacity="0.9" data-i="${i}"/>`;
    });
    svg += "</svg>";
    container.innerHTML = svg;
    container.querySelectorAll("rect").forEach((rect) => {
      rect.addEventListener("mousemove", (ev) => {
        const r = chrono[+rect.dataset.i];
        const html = `<div class="t-title">${esc(r.subject || "")}</div>` +
          tipRow("date", fmtDate(r.ts)) +
          tipRow("author", r.author || "?") +
          tipRow("added", "+" + fmtInt(r.added), "mc-add") +
          tipRow("removed", "-" + fmtInt(r.removed), "mc-del") +
          tipRow("commit", (r.hash || "").slice(0, 10));
        tipShow(html, ev.clientX, ev.clientY);
      });
      rect.addEventListener("mouseleave", tipHide);
    });
  }

  window.Charts = {
    esc, fmtInt, fmtCompact, fmtDate, fmtMonth, pct,
    tipShow, tipHide, tipRow,
    timeline, treemap, ownBar, ownLegend, sparkline, growthColor, OWN_COLORS,
  };
})();
