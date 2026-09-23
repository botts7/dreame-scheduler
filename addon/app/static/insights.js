/* insights.js — Run-history insights on the Report tab.
   Renders the integration's persistent run log (report.history) as a per-run
   timeline plus a weekday success trend. Degrades gracefully when the
   integration hasn't been updated to expose the log yet. */
(function () {
  "use strict";

  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  let shown = 8;                       // runs visible before "show more"
  let R = null;

  async function load() {
    try {
      const r = await fetch("api/ha/report");
      R = await r.json();
    } catch (e) { R = null; }
    render();
  }

  function _names() {
    const m = {};
    ((R && R.rooms) || []).forEach(r => { if (r.seg != null) m[String(r.seg)] = r.name; });
    return m;
  }

  function _fmtTs(iso) {
    const d = new Date(iso);
    if (isNaN(d)) return esc(iso || "");
    return d.toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  }

  function render() {
    const card = document.getElementById("report-history-card");
    const list = document.getElementById("report-history");
    const trend = document.getElementById("report-trend");
    if (!card || !list) return;
    const hist = (R && Array.isArray(R.history)) ? R.history.slice().reverse() : null;
    if (!hist || !hist.length) {
      // Integration too old to expose the log, or nothing logged yet.
      if (R && R.runs_logged > 0 && !R.history) {
        card.style.display = "";
        list.innerHTML = '<div class="hint">' + (Number(R.runs_logged) || 0) +
          " runs are logged — restart Home Assistant to load the updated integration and see them here.</div>";
        trend.innerHTML = "";
      } else {
        card.style.display = "none";
      }
      return;
    }
    card.style.display = "";
    const names = _names();

    // ---- weekday trend: cleaned vs missed per weekday over the whole log
    const agg = Array.from({ length: 7 }, () => ({ ok: 0, bad: 0 }));
    hist.forEach(h => {
      const a = agg[h.weekday] || agg[0];
      Object.values(h.per_room || {}).forEach(pr => {
        if (pr.status === "cleaned") a.ok++; else a.bad++;
      });
    });
    const peak = Math.max(1, ...agg.map(a => a.ok + a.bad));
    trend.innerHTML =
      '<div class="ih-trend">' + agg.map(a => {
        const okH = Math.round(a.ok / peak * 100), badH = Math.round(a.bad / peak * 100);
        return '<div class="ih-bar">' +
          '<div class="b-bad" style="height:' + badH + '%"></div>' +
          '<div class="b-ok" style="height:' + okH + '%"></div></div>';
      }).join("") + "</div>" +
      '<div class="ih-cols">' + DAYS.map(d => '<div class="ih-bar-l">' + d + "</div>").join("") + "</div>";

    // ---- per-run timeline
    list.innerHTML = hist.slice(0, shown).map(h => {
      const pr = h.per_room || {};
      const segs = Object.keys(pr);
      const ok = segs.filter(s => pr[s].status === "cleaned").length;
      const chips = segs.map(s => {
        const st = pr[s].status;
        const cls = st === "cleaned" ? "c" : (st === "failed" ? "f" : "s");
        const mark = st === "cleaned" ? "✓" : (st === "failed" ? "!" : "✗");
        const reason = pr[s].reason ? " — " + esc(String(pr[s].reason).replace(/_/g, " ")) : "";
        return '<span class="ih-chip ' + cls + '" title="' + esc(st) + reason + '">' +
          mark + " " + esc(names[s] || ("Room " + s)) + "</span>";
      }).join("");
      return '<div class="ih-run">' +
        '<div class="ih-top"><span class="ih-when">' + _fmtTs(h.ts) + "</span>" +
        '<span class="ih-kind' + (h.kind === "manual" ? " manual" : "") + '">' + esc(h.kind || "run") + "</span>" +
        '<span class="ih-sum">' + ok + "/" + segs.length + " rooms</span></div>" +
        '<div class="ih-rooms">' + chips + "</div></div>";
    }).join("");

    if (hist.length > shown) {
      const more = document.createElement("button");
      more.className = "btn ghost ih-more";
      more.type = "button";
      more.textContent = "Show " + Math.min(20, hist.length - shown) + " more (" + (hist.length - shown) + " older)";
      more.onclick = () => { shown += 20; render(); };
      list.appendChild(more);
    }
  }

  // Load whenever the Report tab is opened (and once at boot for deck reuse).
  document.addEventListener("click", e => {
    const b = e.target.closest && e.target.closest('nav button[data-tab="report"]');
    if (b) load();
  });
  setTimeout(load, 1200);
})();
