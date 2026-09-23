"use strict";
// Dreame Scheduler add-on GUI. Talks to the Flask backend (ingress-relative
// paths), which proxies to the dreame_scheduler integration via the Core API.

const WEEK = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
let CFG = null;          // get_config response
let OPTS = {};           // working options (edited copy)
let ENTITIES = [];       // picker entities
let NOTIFY = [];         // notify service names

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// Clipboard copy with a fallback — the async Clipboard API is blocked in the
// add-on's ingress iframe (not a secure top-level context), so fall back to a
// hidden textarea + execCommand.
function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(text).catch(() => _copyFallback(text));
  }
  return _copyFallback(text);
}
function _copyFallback(text) {
  return new Promise((res, rej) => {
    try {
      const ta = document.createElement("textarea");
      ta.value = text; ta.setAttribute("readonly", "");
      ta.style.position = "fixed"; ta.style.top = "0"; ta.style.left = "0"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.focus(); ta.select(); ta.setSelectionRange(0, text.length);
      const ok = document.execCommand("copy");
      document.body.removeChild(ta);
      ok ? res() : rej(new Error("copy blocked"));
    } catch (e) { rej(e); }
  });
}

function opt(key) {
  if (OPTS[key] !== undefined) return OPTS[key];
  return (CFG && CFG.defaults && CFG.defaults[key] !== undefined) ? CFG.defaults[key] : undefined;
}
function setStatus(msg, cls) {
  const el = $("#status"); el.textContent = msg || ""; el.className = cls || "";
}

async function api(path, opts) {
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((data && data.detail) || (data && data.error) || ("HTTP " + r.status));
  return data;
}

async function boot() {
  try {
    const [cfg, states, notify] = await Promise.all([
      api("api/ha/config"),
      api("api/ha/states").catch(() => ({ entities: [] })),
      api("api/ha/notify_services").catch(() => ({ services: [] })),
    ]);
    CFG = cfg; ENTITIES = states.entities || []; NOTIFY = notify.services || [];
    if (!cfg.found) {
      $("#load-banner").style.display = "block";
      $("#load-banner").textContent = "No Dreame Scheduler integration found. Add it under Settings → Devices & Services first, then reload.";
      $("#subtitle").textContent = "Not configured";
      return;
    }
    OPTS = Object.assign({}, cfg.options);
    $("#subtitle").textContent = cfg.title + " · " + Object.keys(cfg.rooms || {}).length + " rooms";
    render();
    _homeLoaded = true; renderHome();   // Home is the default tab
  } catch (e) {
    $("#load-banner").style.display = "block";
    $("#load-banner").textContent = "Couldn't reach Home Assistant: " + e.message;
  }
}

function render() {
  renderToggles();
  renderOptInputs();
  renderWeekdaySelects();
  renderChoiceSelects();
  renderPickers();
  renderRooms();
}

function renderToggles() {
  $$("[data-toggle]").forEach(span => {
    const key = span.dataset.toggle;
    span.innerHTML = `<label class="switch"><input type="checkbox"><span class="slider"></span></label>`;
    const cb = $("input", span);
    cb.checked = !!opt(key);
    cb.addEventListener("change", () => {
      OPTS[key] = cb.checked;
      if (key === "studio_enabled" && typeof _applyStudioVisibility === "function") _applyStudioVisibility();
    });
  });
}

function renderOptInputs() {
  $$("[data-opt]").forEach(el => {
    // Only plain inputs here. Selects (weekday/mode/suction) and checklist divs
    // (presence/notify) are handled by their own renderers — skip them so a
    // bubbled change event can't clobber their value.
    if (el.tagName !== "INPUT" || el.type === "checkbox") return;
    const key = el.dataset.opt;
    const v = opt(key);
    if (v !== undefined && v !== null) el.value = v;
    el.addEventListener("change", () => {
      OPTS[key] = (el.type === "number") ? Number(el.value) : el.value;
    });
  });
}

function fillSelect(sel, items, selected, { blank = false } = {}) {
  sel.innerHTML = "";
  if (blank) sel.appendChild(new Option("(default)", ""));
  items.forEach(it => {
    const value = (typeof it === "object") ? it.value : it;
    const label = (typeof it === "object") ? it.label : it;
    const o = new Option(label, value);
    if (String(selected) === String(value)) o.selected = true;
    sel.appendChild(o);
  });
}

function renderWeekdaySelects() {
  $$("select[data-weekdays]").forEach(sel => {
    const key = sel.dataset.opt;
    fillSelect(sel, WEEK.map((d, i) => ({ value: i, label: d })), opt(key));
    sel.addEventListener("change", () => { OPTS[key] = Number(sel.value); });
  });
}

function renderChoiceSelects() {
  const map = { modes: (CFG.modes || []), suctions: (CFG.suctions || []) };
  Object.entries(map).forEach(([kind, items]) => {
    $$(`select[data-${kind}]`).forEach(sel => {
      const key = sel.dataset.opt;
      fillSelect(sel, items, opt(key), { blank: true });
      sel.addEventListener("change", () => { OPTS[key] = sel.value; });
    });
  });
}

const _GROUP_LABEL = { person: "People", device_tracker: "Device trackers", group: "Groups" };
const _openCombos = new Set();  // pos() fns of currently-open dropdowns

function renderPickers() {
  // presence entity pickers
  $$(".picker[data-entities]").forEach(host => {
    const domains = host.dataset.entities.split(",");
    const items = ENTITIES.filter(e => domains.includes(e.domain))
      .map(e => ({ value: e.entity_id, label: e.name, sub: e.entity_id, domain: e.domain, group: _GROUP_LABEL[e.domain] || e.domain }))
      .sort((a, b) => (domains.indexOf(a.domain) - domains.indexOf(b.domain)) || String(a.label).localeCompare(String(b.label)));
    buildCombo(host, items, opt(host.dataset.opt) || [], v => { OPTS[host.dataset.opt] = v; }, "Search people / trackers…");
  });
  // notify target pickers
  $$(".picker[data-notify]").forEach(host => {
    const items = NOTIFY.map(n => ({
      value: n,
      label: n === "persistent_notification" ? "🔔 Persistent notification"
        : (n.startsWith("mobile_app_") ? "📱 " + n.slice("mobile_app_".length).replace(/_/g, " ") : n),
      sub: n === "persistent_notification" ? "in Home Assistant" : "notify." + n,
    }));
    buildCombo(host, items, opt("notify_targets") || ["persistent_notification"], v => { OPTS.notify_targets = v; }, "Search notify targets…");
  });
  // test-notification button — sends to the currently selected targets
  const tnBtn = $("#test-notify-btn");
  if (tnBtn && !tnBtn._wired) {
    tnBtn._wired = true;
    tnBtn.addEventListener("click", async () => {
      const targets = OPTS.notify_targets || opt("notify_targets") || ["persistent_notification"];
      const old = tnBtn.textContent;
      tnBtn.disabled = true; tnBtn.textContent = "Sending…";
      try {
        const r = await api("api/ha/test_notify", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ targets }),
        });
        tnBtn.textContent = (r && r.sent) ? "Sent!" : "No target set";
      } catch (e) { tnBtn.textContent = "Failed — check targets"; }
      setTimeout(() => { tnBtn.textContent = old; tnBtn.disabled = false; }, 2500);
    });
  }
}

// Combobox multi-select (adapted from the wallbox add-on): a compact field
// showing selected items as chips, with a type-to-search dropdown parented on
// <body> so it's never clipped by a card. Click an item to add; chip × removes.
function buildCombo(host, items, selectedArr, onChange, placeholder) {
  const selected = new Set(selectedArr || []);
  const byVal = new Map(items.map(i => [i.value, i]));
  host.innerHTML = "";
  const chips = el("div", "combo-chips");
  const inp = el("input", "combo-input"); inp.type = "text"; inp.autocomplete = "off"; inp.placeholder = placeholder || "Type to search…";
  host.append(chips, inp);
  const list = el("div", "combo-list"); list.hidden = true; document.body.appendChild(list);

  const emit = () => { onChange(Array.from(selected)); drawChips(); };
  function drawChips() {
    chips.innerHTML = "";
    if (!selected.size) { chips.append(Object.assign(el("span", "combo-empty"), { textContent: "none selected" })); return; }
    selected.forEach(v => {
      const i = byVal.get(v) || { value: v, label: v };
      const c = el("span", "chip"); c.textContent = i.label;
      const x = el("button", "chip-x"); x.type = "button"; x.textContent = "×";
      x.addEventListener("mousedown", ev => ev.preventDefault());
      x.addEventListener("click", () => { selected.delete(v); emit(); });
      c.append(x); chips.append(c);
    });
  }
  function pos() {
    const r = inp.getBoundingClientRect(), maxH = 260, below = window.innerHeight - r.bottom;
    list.style.position = "fixed"; list.style.left = r.left + "px"; list.style.width = r.width + "px";
    if (below < maxH && r.top > below) {
      list.style.top = "auto"; list.style.bottom = (window.innerHeight - r.top + 4) + "px"; list.style.maxHeight = Math.min(maxH, r.top - 8) + "px";
    } else {
      list.style.bottom = "auto"; list.style.top = (r.bottom + 4) + "px"; list.style.maxHeight = Math.min(maxH, below - 8) + "px";
    }
  }
  function open() {
    const terms = inp.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    let avail = items.filter(i => !selected.has(i.value));
    if (terms.length) avail = avail.filter(i => {
      const hay = (i.label + " " + (i.sub || "") + " " + (i.group || "")).toLowerCase();
      return terms.every(t => hay.includes(t));
    });
    avail = avail.slice(0, 300);
    list.innerHTML = "";
    if (!avail.length) { list.append(Object.assign(el("div", "combo-empty2"), { textContent: "No matches" })); }
    let curGroup = null;
    avail.forEach(i => {
      if (i.group && i.group !== curGroup) { curGroup = i.group; const g = el("div", "combo-group"); g.textContent = i.group; list.append(g); }
      const row = el("div", "combo-item");
      const main = el("div", "ci-main");
      const lab = el("span", "ci-name"); lab.textContent = i.label; main.append(lab);
      if (i.sub && i.sub !== i.label) { const s = el("span", "ci-sub"); s.textContent = i.sub; main.append(s); }
      row.append(main);
      row.addEventListener("mousedown", ev => { ev.preventDefault(); selected.add(i.value); inp.value = ""; emit(); open(); inp.focus(); });
      list.append(row);
    });
    list.hidden = false; _openCombos.add(pos); pos();
  }
  function close() { list.hidden = true; _openCombos.delete(pos); }
  inp.addEventListener("focus", open);
  inp.addEventListener("input", open);
  inp.addEventListener("keydown", ev => { if (ev.key === "Escape") { close(); inp.blur(); } });
  inp.addEventListener("blur", () => setTimeout(close, 160));
  drawChips();
}
window.addEventListener("scroll", () => _openCombos.forEach(p => p()), true);
window.addEventListener("resize", () => _openCombos.forEach(p => p()));

function el(tag, cls) { const e = document.createElement(tag); if (cls) e.className = cls; return e; }

function roomCfg(seg) {
  const rooms = OPTS.rooms || (OPTS.rooms = {});
  if (!rooms[seg]) rooms[seg] = { enabled: true, days: [], mode: "", suction: "", repeats: 1, door_sensor: "" };
  return rooms[seg];
}

function renderRooms() {
  const rooms = CFG.rooms || {};
  const thead = $("#rooms-table thead");
  thead.innerHTML = "<tr><th>On</th><th>Room</th>" + WEEK.map(d => `<th>${d}</th>`).join("") + "<th></th></tr>";
  const tbody = $("#rooms-table tbody");
  tbody.innerHTML = "";
  const doorSensors = ENTITIES.filter(e => e.domain === "binary_sensor");

  Object.entries(rooms).forEach(([seg, name]) => {
    const rc = roomCfg(seg);
    const tr = document.createElement("tr");
    tr.className = "room-row" + (rc.enabled === false ? " off" : "");

    // include toggle
    const onTd = document.createElement("td");
    const onCb = Object.assign(document.createElement("input"), { type: "checkbox", checked: rc.enabled !== false });
    onCb.setAttribute("aria-label", `Include ${name} in the schedule`);
    onCb.title = `Include ${name} in the schedule`;
    onCb.addEventListener("change", () => { rc.enabled = onCb.checked; tr.classList.toggle("off", !onCb.checked); refreshWarn(); });
    onTd.appendChild(onCb); tr.appendChild(onTd);

    // name (+ a warning when the room is on but has no days -> never cleans)
    const nameTd = document.createElement("td");
    nameTd.className = "name";
    const nameSpan = document.createElement("span"); nameSpan.textContent = name;
    const warn = document.createElement("span");
    warn.className = "room-warn"; warn.textContent = "no days";
    warn.title = "This room is on but has no days selected — it will never be cleaned.";
    nameTd.appendChild(nameSpan); nameTd.appendChild(warn); tr.appendChild(nameTd);
    const refreshWarn = () => {
      const blank = rc.enabled !== false && (!rc.days || rc.days.length === 0);
      warn.style.display = blank ? "" : "none";
    };
    refreshWarn();

    // day checkboxes
    WEEK.forEach((_, i) => {
      const td = document.createElement("td");
      const cb = Object.assign(document.createElement("input"), { type: "checkbox", checked: (rc.days || []).includes(i) });
      cb.setAttribute("aria-label", `${name} — ${WEEK[i]}`);
      cb.title = `${name} — ${WEEK[i]}`;
      cb.addEventListener("change", () => {
        const s = new Set(rc.days || []);
        cb.checked ? s.add(i) : s.delete(i);
        rc.days = Array.from(s).sort((a, b) => a - b);
        refreshWarn();
      });
      td.appendChild(cb); tr.appendChild(td);
    });

    // expand
    const exTd = document.createElement("td");
    const exBtn = Object.assign(document.createElement("button"), { className: "expand-btn", type: "button", textContent: "⚙" });
    exBtn.setAttribute("aria-label", `Per-room settings for ${name}`);
    exBtn.setAttribute("aria-expanded", "false");
    exBtn.title = `Per-room settings for ${name}`;
    exTd.appendChild(exBtn); tr.appendChild(exTd);
    tbody.appendChild(tr);

    // extra row
    const extra = document.createElement("tr");
    extra.className = "room-extra";
    const td = document.createElement("td");
    td.colSpan = WEEK.length + 3;
    // Controls wrapped inside their <label> → implicit label association (a11y).
    td.innerHTML = `<div class="grid">
      <label><span>Mode</span>${optSelectHTML("mode", CFG.modes, rc.mode)}</label>
      <label><span>Suction</span>${optSelectHTML("suction", CFG.suctions, rc.suction)}</label>
      <label><span>Mop wetness <em>(blank = default)</em></span><input type="text" data-f="wetness" value="${esc(rc.wetness ?? "")}"></label>
      <label><span>Passes</span><input type="number" min="1" max="3" data-f="repeats" value="${esc(rc.repeats || 1)}"></label>
      <label><span>Door sensor <em>(skip when shut)</em></span>${doorSelectHTML(doorSensors, rc.door_sensor)}</label>
      <div class="wide times-editor" data-times-editor><span>Extra times <em>(clean this room more than once a day; blank = just the daily time)</em></span></div>
    </div>`;
    extra.appendChild(td); tbody.appendChild(extra);

    exBtn.addEventListener("click", () => {
      const shown = extra.classList.toggle("show");
      exBtn.setAttribute("aria-expanded", shown ? "true" : "false");
      exBtn.classList.toggle("open", shown);
    });
    // bind extra inputs
    const sels = $$("select[data-f]", td), inps = $$("input[data-f]", td);
    sels.forEach(s => s.addEventListener("change", () => { rc[s.dataset.f] = s.value; }));
    inps.forEach(inp => inp.addEventListener("change", () => {
      rc[inp.dataset.f] = inp.dataset.f === "repeats" ? Number(inp.value) : inp.value;
    }));
    // Extra times: a repeatable list of time rows + a per-time "vacuum only" toggle.
    const teHost = $("[data-times-editor]", td);
    if (teHost) buildTimesEditor(teHost, rc);
  });

  if (!Object.keys(rooms).length) {
    tbody.innerHTML = `<tr><td colspan="10" style="text-align:center;color:var(--muted);padding:20px">No rooms found yet — let the vacuum finish a full map, then reload.</td></tr>`;
  }
}

// Extra per-room clean times: a repeatable list of {at:"HH:MM", mop:bool} rows.
// A row with "vacuum only" ticked stores mop:false (a sweep-only pass). Empty/
// invalid rows are pruned on save (see collectRoomTimes). This is the shape the
// integration's scheduler.room_times expects.
function buildTimesEditor(host, rc) {
  const label = host.querySelector("span") ? host.querySelector("span").outerHTML
    : `<span>Extra times <em>(clean this room more than once a day)</em></span>`;
  const draw = () => {
    const times = Array.isArray(rc.times) ? rc.times : [];
    host.innerHTML = label +
      `<div class="time-rows">` + times.map((t, i) => `
        <div class="time-row" data-i="${i}">
          <input type="time" data-time value="${esc(t.at || "")}">
          <label class="vac"><input type="checkbox" data-vac${t.mop === false ? " checked" : ""}> vacuum only</label>
          <button type="button" class="time-rm" data-remove aria-label="Remove this time" title="Remove">&times;</button>
        </div>`).join("") + `</div>` +
      `<button type="button" class="time-add" data-add>+ Add time</button>`;

    host.querySelector("[data-add]").addEventListener("click", () => {
      rc.times = (Array.isArray(rc.times) ? rc.times : []).concat([{ at: "", mop: true }]);
      draw();
    });
    $$(".time-row", host).forEach(row => {
      const i = +row.dataset.i;
      row.querySelector("[data-time]").addEventListener("change", e => { rc.times[i].at = e.target.value; });
      row.querySelector("[data-vac]").addEventListener("change", e => { rc.times[i].mop = !e.target.checked; });
      row.querySelector("[data-remove]").addEventListener("click", () => {
        rc.times.splice(i, 1);
        if (!rc.times.length) delete rc.times;
        draw();
      });
    });
  };
  draw();
}

// Drop empty/invalid time rows and de-dup by time before saving; returns a clean
// list (or undefined when there are none, so the room falls back to the daily time).
function collectRoomTimes(times) {
  const seen = {};
  (Array.isArray(times) ? times : []).forEach(t => {
    if (t && /^\d{2}:\d{2}$/.test(t.at || "")) seen[t.at] = { at: t.at, mop: t.mop !== false };
  });
  const keys = Object.keys(seen).sort();
  return keys.length ? keys.map(k => seen[k]) : undefined;
}

function optSelectHTML(field, items, selected) {
  return `<select data-f="${esc(field)}"><option value="">(default)</option>` +
    (items || []).map(it => `<option value="${esc(it)}"${String(selected) === String(it) ? " selected" : ""}>${esc(it)}</option>`).join("") +
    `</select>`;
}
function doorSelectHTML(items, selected) {
  return `<select data-f="door_sensor"><option value="">(none)</option>` +
    items.map(e => `<option value="${esc(e.entity_id)}"${e.entity_id === selected ? " selected" : ""}>${esc(e.name)}</option>`).join("") +
    `</select>`;
}

async function save() {
  const btn = $("#save"); btn.disabled = true; setStatus("Saving…");
  try {
    // Prune empty/invalid extra-time rows per room (drop the key entirely when none).
    Object.values(OPTS.rooms || {}).forEach(rc => {
      if (rc && "times" in rc) {
        const t = collectRoomTimes(rc.times);
        if (t) rc.times = t; else delete rc.times;
      }
    });
    await api("api/ha/config", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ options: OPTS }),
    });
    MAP_DIRTY = false;
    if (document.getElementById("map-panel")) renderMapPanel();
    setStatus("Saved ✓ (integration reloaded)", "ok");
  } catch (e) {
    setStatus("Save failed: " + e.message, "err");
  } finally { btn.disabled = false; }
}

let _actionBusy = false;
async function doAction(action, quiet, srcBtn) {
  if (_actionBusy) return;                            // no double-dispatch on a fast double-tap
  if (action === "reset_week" && !(await uiConfirm({
    title: "Reset the week?",
    message: "Reset this week's cleaned/pending tracking and start a fresh week?",
    confirmText: "Reset week", danger: true,
  }))) return;
  _actionBusy = true;
  const btns = $$(`[data-action="${action}"]`); btns.forEach(b => b.disabled = true);
  setStatus("Running " + action + "…");
  try {
    await api("api/ha/action", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, data: quiet ? { quiet: true } : {} }),
    });
    setStatus("Done ✓", "ok");
  } catch (e) { setStatus(action + " failed: " + e.message, "err"); }
  finally { _actionBusy = false; btns.forEach(b => b.disabled = false); }
}
// ==== Map / floor-plan + walls-&-zones editor (v2; kept self-contained for a
//      clean extraction into its own add-on later) ====
let _mapLoaded = false;
const _PALETTE = ["#60a5fa", "#34d399", "#fbbf24", "#f472b6", "#a78bfa", "#22d3ee", "#fb923c", "#f87171"];
let MAP = null;                                   // {m, rooms, byId, minX,minY,maxX,maxY, W,H}
let ZONES = { walls: [], zones: [], no_mops: [] };// working set in map-mm coords
let MAP_OBSTACLES = [];                            // obstacles to plot on the plan
let MAP_FOCUS_OBS = null;                          // obstacle to highlight/zoom to
let DRAWMODE = "select";
let DRAWING = null;                               // active drag {kind,x0,y0,x1,y1}
let SELECTED = null;                              // {kind:'zone'|'nomop'|'wall', i} | {kind:'room', seg}
let DRAGSEL = null;                               // moving a selected zone/wall
const FILTER = new Set();                         // hidden room seg ids (visibility)
const _isSel = (kind, i) => SELECTED && SELECTED.kind === kind && SELECTED.i === i;
const _zArr = (kind) => kind === "wall" ? ZONES.walls : (kind === "nomop" ? ZONES.no_mops : ZONES.zones);
let VIEW = null;                                  // current viewBox {x,y,w,h} in SX/SY space
let PANNING = null;                               // active pan {cx,cy,vx,vy,moved}
function _applyView() { const s = document.querySelector(".floorplan"); if (s && VIEW) s.setAttribute("viewBox", `${VIEW.x} ${VIEW.y} ${VIEW.w} ${VIEW.h}`); }
function _zoomView(factor, cx, cy) {
  let nw = VIEW.w * factor;
  nw = Math.max(MAP.W / 20, Math.min(MAP.W * 2, nw));
  const nf = nw / VIEW.w, nh = VIEW.h * nf;
  VIEW = { x: cx - (cx - VIEW.x) * nf, y: cy - (cy - VIEW.y) * nf, w: nw, h: nh };
  _applyView();
}
function _fitView() { VIEW = { x: 0, y: 0, w: MAP.W, h: MAP.H }; _applyView(); }
function _svgPt(svg, evt) { const p = svg.createSVGPoint(); p.x = evt.clientX; p.y = evt.clientY; return p.matrixTransform(svg.getScreenCTM().inverse()); }
let SHAPE_EDIT = null;                             // room seg currently in vertex-edit mode
let VTXDRAG = null;                                // active vertex drag {seg, i}
let EDGEDRAG = null;                               // active edge (line) drag {seg, i, sx, sy, a, b}
let VTX_TRIM = false;                              // box-delete-dots sub-mode while shaping
let MARQUEE = null;                                // active selection rectangle {x0,y0,x1,y1}
let MAP_DIRTY = false;                             // unsaved room-shape edits (persist via footer Save)
let SNAP = true;                                   // snap room corners to neighbours + mapped walls
let _floorMask = null;                             // {w,h,ds,arr,sx,sy,px0,py0} from the underlay
let _cleanOutline = null;                          // de-noised rectilinear house outline (map coords)
let _houseMask = null;                             // filled house-body mask (same dims as _floorMask)
let SHOW_OUTLINE = true;                           // draw the clean outline as a guide layer
let MAPROT = 0;                                    // view rotation (0/90/180/270) — work in any orientation
let ROOMRECT_SEG = null;                           // room seg the "draw box" tool is targeting
let SPLIT_SEG = null;                              // room seg the "split line" tool will split on the robot
let MERGE_SEG = null;                              // room seg awaiting a neighbour click to merge on the robot
// Staged robot-room edits: gestures stage locally (instant, native feel) and
// "Apply to robot" sends the compiled native ops as ONE bulk change.
let SEG_OPS = [];                                  // [{op:"split"|"merge"|"carve"|"rename", …, label}]
let MOVEB = null;                                  // move-boundary gesture {donor} → {donor, line}
let CARVE = null;                                  // carve-chunk gesture {donor} → {donor, rect}
// ---- undo / redo: snapshot the editable map state before each change ----
let _undoStack = [], _redoStack = [];
function _mapSnapshot() {
  const o = OPTS.map_image || {};
  return JSON.stringify({ shapes: OPTS.shapes || {}, cuts: o.cuts || [], adds: o.adds || [], zones: ZONES,
                          edgewalls: OPTS.edgewalls || {} });
}
function _pushUndo() { _undoStack.push(_mapSnapshot()); if (_undoStack.length > 100) _undoStack.shift(); _redoStack = []; _syncUndoBtn(); }
function _syncUndoBtn() {
  const u = document.getElementById("map-undo"), r = document.getElementById("map-redo");
  if (u) { u.disabled = !_undoStack.length; u.title = `Undo${_undoStack.length ? " (" + _undoStack.length + ")" : ""} — Ctrl+Z`; }
  if (r) { r.disabled = !_redoStack.length; r.title = `Redo${_redoStack.length ? " (" + _redoStack.length + ")" : ""} — Ctrl+Y`; }
}
function _applySnapshot(s) {
  OPTS.shapes = s.shapes;
  if (s.edgewalls) OPTS.edgewalls = s.edgewalls;
  const o = _mapImgCfg(); o.cuts = s.cuts; o.adds = s.adds;
  ZONES = s.zones;
  SHAPE_EDIT = VTXDRAG = EDGEDRAG = DRAGSEL = SELECTED = null;
  MAP_DIRTY = true;
  buildCleanOutline().then(() => drawFloorplan());
  renderZoneList(); renderMapPanel(); _syncUndoBtn();
}
function _undoMap() { if (!_undoStack.length) return; _redoStack.push(_mapSnapshot()); _applySnapshot(JSON.parse(_undoStack.pop())); }
function _redoMap() { if (!_redoStack.length) return; _undoStack.push(_mapSnapshot()); _applySnapshot(JSON.parse(_redoStack.pop())); }
const shapeOf = seg => (OPTS.shapes && OPTS.shapes[String(seg)]) || null;

// ---- per-wall overrides: each room edge can be DEFAULT height, a CUSTOM
// height, or an OPENING (h=0). Entries are [midX, midY, h] keyed by edge
// MIDPOINT (map coords) so they survive corner tweaks; a big reshape simply
// drops them (re-flag).
const _edgeWalls = () => OPTS.edgewalls || (OPTS.edgewalls = {});
function _edgeMid(shp, i) {
  const a = shp[i], b = shp[(i + 1) % shp.length];
  return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
}
function _edgeWallOverride(seg, mid) {          // -> height in mm, or null = default
  const e = (_edgeWalls()[String(seg)] || [])
    .find(p => Math.abs(p[0] - mid[0]) <= 350 && Math.abs(p[1] - mid[1]) <= 350);
  return e ? e[2] : null;
}
function _isOpenEdge(seg, mid) { return _edgeWallOverride(seg, mid) === 0; }
function _setEdgeWall(seg, i, h) {              // h = null removes the override
  const shp = shapeOf(seg); if (!shp) return;
  const mid = _edgeMid(shp, i);
  const key = String(seg);
  const list = _edgeWalls()[key] || (_edgeWalls()[key] = []);
  const at = list.findIndex(p => Math.abs(p[0] - mid[0]) <= 350 && Math.abs(p[1] - mid[1]) <= 350);
  if (h == null) { if (at >= 0) list.splice(at, 1); }
  else {
    const e = [Math.round(mid[0]), Math.round(mid[1]), Math.max(0, Math.min(6000, Math.round(h)))];
    if (at >= 0) list[at] = e; else list.push(e);
  }
  MAP_DIRTY = true; drawFloorplan();
}
function _toggleOpenEdge(seg, i) {
  const shp = shapeOf(seg); if (!shp) return;
  _pushUndo();
  const cur = _edgeWallOverride(seg, _edgeMid(shp, i));
  _setEdgeWall(seg, i, cur === 0 ? null : 0);
}
function ensureShape(seg) {                        // start a custom polygon from the room bbox (4 corners)
  seg = String(seg);
  const s = OPTS.shapes || (OPTS.shapes = {});
  if (!s[seg]) { const r = MAP.byId[seg]; if (!r) return null; s[seg] = [[r.x0, r.y0], [r.x1, r.y0], [r.x1, r.y1], [r.x0, r.y1]]; }
  return s[seg];
}
function resetShape(seg) { seg = String(seg); if (OPTS.shapes) delete OPTS.shapes[seg]; if (SHAPE_EDIT === seg) SHAPE_EDIT = null; }

// ---- map-image underlay (manual-align) ----
let MAPIMG_DATA = null;                            // the PNG as a data: URL (embedded, no refetch on redraw)
let MAPIMG_NAT = null;                             // {iw, ih} natural pixel size
const _mapImgCfg = () => OPTS.map_image || (OPTS.map_image = {});
// User-drawn boxes (map coords) that override the mapped floor for the house
// outline + auto-fit: ✂ cuts remove out-window scans connected to the house;
// ➕ adds bridge unmapped gaps (so the outline connects straight across them).
// Stored inside map_image so they persist with the footer Save (no backend change).
const _cuts = () => { const o = _mapImgCfg(); return o.cuts || (o.cuts = []); };
const _adds = () => { const o = _mapImgCfg(); return o.adds || (o.adds = []); };
function _boxPxMask(M, boxes) {
  if (!boxes || !boxes.length) return null;
  const m = new Uint8Array(M.w * M.h);
  const toPx = (mx, my) => [(M.px0 + mx * M.sx) * M.ds, (M.py0 + my * M.sy) * M.ds];
  boxes.forEach(c => {
    const a = toPx(c[0], c[1]), b = toPx(c[2], c[3]);
    const x0 = Math.max(0, Math.floor(Math.min(a[0], b[0]))), x1 = Math.min(M.w - 1, Math.ceil(Math.max(a[0], b[0])));
    const y0 = Math.max(0, Math.floor(Math.min(a[1], b[1]))), y1 = Math.min(M.h - 1, Math.ceil(Math.max(a[1], b[1])));
    for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) m[y * M.w + x] = 1;
  });
  return m;
}
function _cutPxMask(M) { return _boxPxMask(M, _mapImgCfg().cuts); }
function _addPxMask(M) { return _boxPxMask(M, _mapImgCfg().adds); }
// Floor-plan render layer: user-tagged interior walls + fixtures (bench/island/
// counter…). Display + scheduling only; never written to the robot. Persisted in
// map_image with the footer Save.
const _dwalls = () => { const o = _mapImgCfg(); return o.dwalls || (o.dwalls = []); };     // [[x0,y0,x1,y1],…]
const _fixtures = () => { const o = _mapImgCfg(); return o.fixtures || (o.fixtures = []); }; // [{box:[..], label}]
let PLAN_VIEW = false;                             // render the clean architectural floor plan
let PLAN_STYLE = "blueprint";                      // "blueprint" (print) | "themed" (dark, coloured)
let FIXTURE_LABEL = "Bench";                       // label applied to the next drawn fixture
let SHOW_FURNITURE = true;                          // draw the robot's detected furniture
// ---- Phase 3: HA devices placed on the plan (live state + tap-to-control) ----
let DEV_MODE = false;                               // device-placement layer active
let DEV_AREAS = null;                               // fetched [{area_id,name,entities:[...]}]
let DEV_PLACE = null;                               // entity pending placement {entity_id,name,domain}
let DEVDRAG = null;                                 // dragging a placed pin {eid, cx, cy, moved}
let DEV_FILTER = null;                              // null = show all placed pins; else Set of domains to show
let CLEAN_MODE = false;                             // tap rooms to send the robot to clean them
const CLEAN_SEL = new Set();                        // selected room segs for cleaning
const EDGE_STRIP_MM = 250;                          // width of each wall strip (~robot width, ~one pass)

// Thin rectangles hugging a room's four walls (from its map box), for an edge
// pass via clean_zone. Clipped to the room, and never wider than half the room.
function edgeStripsForRoom(seg) {
  const r = MAP && MAP.byId && MAP.byId[seg];
  if (!r) return [];
  const x0 = Math.min(r.x0, r.x1), x1 = Math.max(r.x0, r.x1);
  const y0 = Math.min(r.y0, r.y1), y1 = Math.max(r.y0, r.y1);
  const w = Math.max(60, Math.min(EDGE_STRIP_MM, (x1 - x0) / 2, (y1 - y0) / 2));
  return [
    [x0, y0, x0 + w, y1],     // one wall
    [x1 - w, y0, x1, y1],     // opposite wall
    [x0, y0, x1, y0 + w],     // third wall
    [x0, y1 - w, x1, y1],     // fourth wall
  ].map(z => z.map(Math.round));
}

async function edgeCleanRoom(seg, name) {
  const strips = edgeStripsForRoom(seg);
  if (!strips.length) { alert("No map geometry for " + name + " yet."); return; }
  if (!(await uiConfirm({
    title: "Edge clean " + name + "?",
    message: "Sends the robot around the walls of " + name + " — thin strips along each edge.\n\n"
      + "Beta / Phase 1: it also cleans the 'invisible' room-boundary edges (open doorways / where the "
      + "map splits rooms), not only physical walls. For an edges-only pass, set the robot to Vacuum and "
      + "turn Auto-reclean off first — otherwise it may fill the whole room after the edges.",
    confirmText: "Edge clean",
  }))) return;
  const ve = (MAP && MAP.m && MAP.m.vacuum_entity) || (CFG && CFG.prefix ? "vacuum." + CFG.prefix : "");
  try {
    await api("api/ha/clean_zone", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ vacuum_entity: ve, zones: strips }),
    });
    setStatus("Edge cleaning " + name + " ✓", "ok");
  } catch (e) {
    setStatus("Edge clean failed: " + (e && e.message ? e.message : e), "err");
  }
}
const DEV_STATE = {};                               // eid -> {name,domain,state,device_class}
const _devices = () => { const o = _mapImgCfg(); return o.devices || (o.devices = {}); };  // eid -> {x,y}
const DEV_ICONS = { light: "💡", switch: "🔌", fan: "🌀", cover: "🪟", lock: "🔒", climate: "🌡️", media_player: "🔊", sensor: "📊", binary_sensor: "🟢", vacuum: "🧹", camera: "📷", scene: "🎬", script: "📜", button: "⏺️", person: "🧑", device_tracker: "📍", input_boolean: "☑️", automation: "⚙️", number: "🔢", select: "🎚️", climate_: "🌡️" };
const _devIcon = d => DEV_ICONS[d] || "▫️";
// domain -> [domain, service] for a tap-toggle (null = read-only, just shows state)
const DEV_TOGGLE = { light: ["light", "toggle"], switch: ["switch", "toggle"], fan: ["fan", "toggle"], input_boolean: ["input_boolean", "toggle"], cover: ["cover", "toggle"], scene: ["scene", "turn_on"], script: ["script", "turn_on"], button: ["button", "press"], media_player: ["media_player", "media_play_pause"] };
const _DEV_ON = ["on", "open", "unlocked", "playing", "home", "heat", "cool", "cleaning", "active"];
// The robot classifies furniture; map its type to an icon for the plan.
const _FURN_ICONS = {
  bed: "🛏️", nightstant: "🛋️", nightstand: "🛋️", sofa: "🛋️", couch: "🛋️", table: "🍽️",
  "dining table": "🍽️", chair: "🪑", "tv cabinet": "📺", tv: "📺", wardrobe: "🚪", cabinet: "🗄️",
  fridge: "🧊", refrigerator: "🧊", toilet: "🚽", "washing machine": "🧺", desk: "🖥️", bin: "🗑️",
  "trash can": "🗑️", plant: "🪴", "litter box": "🐈", "pet bowl": "🥣", shoe: "👟", scale: "⚖️",
};
const _furnIcon = t => _FURN_ICONS[String(t || "").toLowerCase()] || "🪑";
// Simple floor-plan symbol detail per furniture type, drawn inside its box
// (svg top-left x,y + size w,h). Vectors only — scales cleanly, no images.
function _furnDetail(type, x, y, w, h, cls) {
  cls = cls == null ? "fp-furn-d" : cls;
  const a = (cls ? `class="${cls}" ` : "") + `fill="none"`;
  const t = String(type || "").toLowerCase();
  const line = (x1, y1, x2, y2) => `<line ${a} x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"/>`;
  const rr = (rx, ry, rw, rh, r) => `<rect ${a} x="${rx}" y="${ry}" width="${rw}" height="${rh}" rx="${r || 20}"/>`;
  const ell = (ex, ey, erx, ery) => `<ellipse ${a} cx="${ex}" cy="${ey}" rx="${erx}" ry="${ery}"/>`;
  if (t.includes("bed")) return rr(x + w * 0.12, y + h * 0.08, w * 0.76, h * 0.28, 20) + line(x, y + h * 0.42, x + w, y + h * 0.42);
  if (t.includes("sofa") || t.includes("couch") || t.includes("night")) return rr(x, y, w, h * 0.3, 20) + line(x + w / 3, y + h * 0.3, x + w / 3, y + h) + line(x + 2 * w / 3, y + h * 0.3, x + 2 * w / 3, y + h);
  if (t.includes("toilet")) return ell(x + w / 2, y + h * 0.58, w * 0.32, h * 0.34) + rr(x + w * 0.3, y, w * 0.4, h * 0.22, 10);
  if (t.includes("table") || t.includes("desk")) return rr(x + w * 0.12, y + h * 0.12, w * 0.76, h * 0.76, 20);
  if (t.includes("fridge") || t.includes("frig")) return line(x, y + h * 0.45, x + w, y + h * 0.45);
  if (t.includes("wardrobe") || t.includes("cabinet") || t.includes("closet")) return line(x + w / 2, y, x + w / 2, y + h);
  if (t.includes("chair")) return rr(x + w * 0.15, y, w * 0.7, h * 0.25, 15);
  return "";
}
async function loadMapImage() {
  MAPIMG_DATA = null; MAPIMG_NAT = null;
  try {
    const blob = await (await fetch(`api/ha/map_image?prefix=${encodeURIComponent(CFG.prefix)}`)).blob();
    const durl = await new Promise((res, rej) => { const fr = new FileReader(); fr.onload = () => res(fr.result); fr.onerror = rej; fr.readAsDataURL(blob); });
    const nat = await new Promise((res, rej) => { const im = new Image(); im.onload = () => res({ iw: im.naturalWidth, ih: im.naturalHeight }); im.onerror = rej; im.src = durl; });
    MAPIMG_DATA = durl; MAPIMG_NAT = nat;
  } catch (_) { /* no image available */ }
}

// ---- 📄 uploaded reference plan (the user's real architect drawing) ----
// A second underlay source: trace/adjust rooms against the true house layout.
// The robot map keeps powering the floor mask (snap, auto-fit); this is a
// visual baseline only. Transform lives in OPTS.user_plan, image in the addon.
let USERPLAN_DATA = null, USERPLAN_NAT = null;
const _userPlanCfg = () => OPTS.user_plan || (OPTS.user_plan = {});
const _ulCfg = () => (_mapImgCfg().src === "user" ? _userPlanCfg() : _mapImgCfg());
async function loadUserPlan() {
  if (USERPLAN_DATA) return true;
  try {
    const r = await fetch("api/user_plan");
    if (!r.ok) return false;
    const blob = await r.blob();
    const durl = await new Promise((res, rej) => { const fr = new FileReader(); fr.onload = () => res(fr.result); fr.onerror = rej; fr.readAsDataURL(blob); });
    const nat = await new Promise((res, rej) => { const im = new Image(); im.onload = () => res({ iw: im.naturalWidth, ih: im.naturalHeight }); im.onerror = rej; im.src = durl; });
    USERPLAN_DATA = durl; USERPLAN_NAT = nat;
    return true;
  } catch (_) { return false; }
}
function seedUserPlan() {                          // first-fit: plan width ≈ rooms bbox width
  const o = _userPlanCfg();
  if (!USERPLAN_NAT || typeof o.cx === "number") return;
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  MAP.rooms.forEach(r => { x0 = Math.min(x0, r.x0); y0 = Math.min(y0, r.y0); x1 = Math.max(x1, r.x1); y1 = Math.max(y1, r.y1); });
  o.scale = (x1 - x0) / USERPLAN_NAT.iw;
  o.cx = (x0 + x1) / 2; o.cy = (y0 + y1) / 2; o.rot = o.rot || 0;
  if (o.opacity == null) o.opacity = 0.5;
}
// Exact placement from the camera's calibration_points — the Dreame map image
// carries a linear pixel<->map-coordinate mapping (same coords as the room
// boxes), so we can position/scale the underlay precisely instead of guessing.
// Returns true if it could compute a fit.
// Linear pixel<->map-coord transform from calibration_points: px = px0 + v*s.
// Axis-aligned (Dreame maps aren't rotated vs their coordinate grid).
function _calibXform() {
  const cps = MAP && MAP.m && MAP.m.calibration_points;
  if (!Array.isArray(cps) || cps.length < 3) return null;
  const p = cps.map(c => ({ vx: c.vacuum.x, vy: c.vacuum.y, px: c.map.x, py: c.map.y }))
    .filter(q => [q.vx, q.vy, q.px, q.py].every(n => typeof n === "number"));
  if (p.length < 3) return null;
  let sx = null, sy = null;
  for (let i = 0; i < p.length; i++) for (let j = i + 1; j < p.length; j++) {
    if (sx == null && p[i].vy === p[j].vy && p[i].vx !== p[j].vx) sx = (p[j].px - p[i].px) / (p[j].vx - p[i].vx);
    if (sy == null && p[i].vx === p[j].vx && p[i].vy !== p[j].vy) sy = (p[j].py - p[i].py) / (p[j].vy - p[i].vy);
  }
  if (!sx || !sy) return null;
  return { sx, sy, px0: p[0].px - p[0].vx * sx, py0: p[0].py - p[0].vy * sy };
}
function calibFit() {
  const o = _mapImgCfg();
  const xf = _calibXform();
  if (!MAPIMG_NAT || !xf) return false;
  const { sx, sy, px0, py0 } = xf;
  const op = o.opacity;
  o.scale = 1 / Math.abs(sx);                       // map-units per image pixel
  o.cx = (MAPIMG_NAT.iw / 2 - px0) / sx;            // map-coord under the image centre
  o.cy = (MAPIMG_NAT.ih / 2 - py0) / sy;
  o.rot = 0;
  o.flip = sy > 0;                                  // image row 0 must sit at high map-y (SVG top)
  o.opacity = op != null ? op : 0.5;
  return true;
}
function seedMapImg() {                            // exact via calibration, else first-fit guess
  const o = _mapImgCfg();
  if (!MAPIMG_NAT || typeof o.cx === "number") return;
  if (calibFit()) return;
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  MAP.rooms.forEach(r => { x0 = Math.min(x0, r.x0); y0 = Math.min(y0, r.y0); x1 = Math.max(x1, r.x1); y1 = Math.max(y1, r.y1); });
  o.scale = (x1 - x0) / MAPIMG_NAT.iw;
  o.cx = (x0 + x1) / 2; o.cy = (y0 + y1) / 2; o.rot = o.rot || 0;
  if (o.opacity == null) o.opacity = 0.5;
}

// ---- snapping: to neighbour corners, own right-angles, and the mapped walls ----
// Build a coarse floor/no-floor bitmap from the underlay so we can snap a vertex
// to the nearest real wall edge. Floor = mapped & not near-black; the boundary
// of that region is where the walls are. Needs calibration to map px<->coords.
async function buildFloorMask() {
  _floorMask = null;
  const xf = _calibXform();
  if (!xf || !MAPIMG_DATA || !MAPIMG_NAT) return;
  const maxW = 520, ds = Math.min(1, maxW / MAPIMG_NAT.iw);
  const w = Math.max(1, Math.round(MAPIMG_NAT.iw * ds)), h = Math.max(1, Math.round(MAPIMG_NAT.ih * ds));
  const im = await new Promise(res => { const x = new Image(); x.onload = () => res(x); x.onerror = () => res(null); x.src = MAPIMG_DATA; });
  if (!im) return;
  const c = document.createElement("canvas"); c.width = w; c.height = h;
  const ctx = c.getContext("2d"); ctx.drawImage(im, 0, 0, w, h);
  let data; try { data = ctx.getImageData(0, 0, w, h).data; } catch (e) { return; }
  const arr = new Uint8Array(w * h);
  for (let i = 0; i < w * h; i++) {
    const a = data[i * 4 + 3], mx = Math.max(data[i * 4], data[i * 4 + 1], data[i * 4 + 2]);
    arr[i] = (a > 60 && mx > 45) ? 1 : 0;         // mapped floor pixel
  }
  _floorMask = { w, h, ds, arr, sx: xf.sx, sy: xf.sy, px0: xf.px0, py0: xf.py0 };
}
async function _ensureFloorMask() {
  if (_floorMask) return;
  if (!MAPIMG_DATA) await loadMapImage();          // wall-snap works even if underlay is hidden
  if (MAPIMG_DATA) await buildFloorMask();
}

// Snap a map-coord to the nearest mapped-wall edge (floor pixel bordering
// non-floor), searching a small radius. Returns [x,y] or null.
function snapToWall(mx, my) {
  const M = _floorMask; if (!M) return null;
  const fpx = (M.px0 + mx * M.sx) * M.ds, fpy = (M.py0 + my * M.sy) * M.ds;
  const cx = Math.round(fpx), cy = Math.round(fpy);
  const rad = Math.max(4, Math.round(300 * Math.abs(M.sx) * M.ds));
  const F = (x, y) => (x >= 0 && y >= 0 && x < M.w && y < M.h) ? M.arr[y * M.w + x] : 0;
  const edge = (x, y) => F(x, y) && (!F(x - 1, y) || !F(x + 1, y) || !F(x, y - 1) || !F(x, y + 1));
  let best = null, bd = (rad + 1) * (rad + 1);
  for (let y = cy - rad; y <= cy + rad; y++) for (let x = cx - rad; x <= cx + rad; x++) {
    if (!edge(x, y)) continue;
    const d = (x - fpx) * (x - fpx) + (y - fpy) * (y - fpy);
    if (d < bd) { bd = d; best = [x, y]; }
  }
  if (!best) return null;
  return [(best[0] / M.ds - M.px0) / M.sx, (best[1] / M.ds - M.py0) / M.sy];
}

// Snap priority while reshaping: (1) a neighbouring room's corner (join boxes),
// (2) the nearest mapped wall from the underlay, (3) orthogonal to this shape's
// own adjacent corners → clean right angles. Snap off = free placement.
function snapVertex(seg, i, mx, my) {
  if (!SNAP) return [mx, my];
  const CORNER = 240;                              // map-units
  let best = null, bd = CORNER;
  MAP.rooms.forEach(r => {
    const s2 = String(r.room_id); if (s2 === seg || FILTER.has(s2)) return;
    const pts = shapeOf(s2) || [[r.x0, r.y0], [r.x1, r.y0], [r.x1, r.y1], [r.x0, r.y1]];
    pts.forEach(p => { const d = Math.hypot(p[0] - mx, p[1] - my); if (d < bd) { bd = d; best = [p[0], p[1]]; } });
  });
  if (best) return best;                           // exact corner join wins
  const ws = snapToWall(mx, my);
  if (ws) { mx = ws[0]; my = ws[1]; }
  const shp = shapeOf(seg);
  if (shp && shp.length >= 3) {                    // right-angle to neighbours
    const ORTHO = 170;
    const prev = shp[(i - 1 + shp.length) % shp.length], next = shp[(i + 1) % shp.length];
    [prev, next].forEach(nb => {
      if (Math.abs(nb[0] - mx) < ORTHO) mx = nb[0];
      if (Math.abs(nb[1] - my) < ORTHO) my = nb[1];
    });
  }
  return [Math.round(mx), Math.round(my)];
}

function _polyArea(pts) {
  let a = 0; for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) a += pts[j][0] * pts[i][1] - pts[i][0] * pts[j][1];
  return Math.abs(a) / 2;
}
function _pointInPoly(x, y, pts) {
  let inside = false;
  for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
    const xi = pts[i][0], yi = pts[i][1], xj = pts[j][0], yj = pts[j][1];
    if (((yi > y) !== (yj > y)) && (x < (xj - xi) * (y - yi) / (yj - yi) + xi)) inside = !inside;
  }
  return inside;
}
// Which room is under a map point? Pick by geometry (shape or box), smallest
// area wins — so overlapping/reshaped rooms stay clickable regardless of draw
// order (fixes: can't re-select a room after editing its shape).
function _roomAt(mx, my) {
  let best = null, bestA = Infinity;
  MAP.rooms.forEach(r => {
    const seg = String(r.room_id); if (FILTER.has(seg)) return;
    const shp = shapeOf(seg);
    let hit, area;
    if (shp && shp.length >= 3) { hit = _pointInPoly(mx, my, shp); area = _polyArea(shp); }
    else { hit = mx >= r.x0 && mx <= r.x1 && my >= r.y0 && my <= r.y1; area = (r.x1 - r.x0) * (r.y1 - r.y0); }
    if (hit && area < bestA) { bestA = area; best = seg; }
  });
  return best;
}

// -- small binary-grid ops used by auto-fit --
function _gErode(a, w, h) { const b = new Uint8Array(w * h); for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) b[y * w + x] = (a[y * w + x] && x > 0 && a[y * w + x - 1] && x < w - 1 && a[y * w + x + 1] && y > 0 && a[(y - 1) * w + x] && y < h - 1 && a[(y + 1) * w + x]) ? 1 : 0; return b; }
function _gDilate(a, w, h) { const b = new Uint8Array(w * h); for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) b[y * w + x] = (a[y * w + x] || (x > 0 && a[y * w + x - 1]) || (x < w - 1 && a[y * w + x + 1]) || (y > 0 && a[(y - 1) * w + x]) || (y < h - 1 && a[(y + 1) * w + x])) ? 1 : 0; return b; }
// Fill interior holes: flood the background inward from the border; any
// background NOT reachable from outside is an interior hole (a wall/bench/island
// inside the house) → set it to foreground. Leaves the outer shape untouched.
function _gFillHoles(a, w, h) {
  const outside = new Uint8Array(w * h); const st = [];
  const seed = i => { if (!a[i] && !outside[i]) { outside[i] = 1; st.push(i); } };
  for (let x = 0; x < w; x++) { seed(x); seed((h - 1) * w + x); }
  for (let y = 0; y < h; y++) { seed(y * w); seed(y * w + w - 1); }
  while (st.length) { const p = st.pop(); const x = p % w, y = (p / w) | 0;
    if (x > 0) seed(p - 1); if (x < w - 1) seed(p + 1); if (y > 0) seed(p - w); if (y < h - 1) seed(p + w); }
  const b = Uint8Array.from(a); for (let i = 0; i < w * h; i++) if (!a[i] && !outside[i]) b[i] = 1; return b;
}
function _gLargest(a, w, h) {
  const lbl = new Int32Array(w * h); let cur = 0, best = 0, bestId = 0;
  for (let i = 0; i < w * h; i++) {
    if (!a[i] || lbl[i]) continue;
    cur++; let cnt = 0; const st = [i]; lbl[i] = cur;
    while (st.length) { const p = st.pop(); cnt++; const x = p % w, y = (p / w) | 0;
      if (x > 0 && a[p - 1] && !lbl[p - 1]) { lbl[p - 1] = cur; st.push(p - 1); }
      if (x < w - 1 && a[p + 1] && !lbl[p + 1]) { lbl[p + 1] = cur; st.push(p + 1); }
      if (y > 0 && a[p - w] && !lbl[p - w]) { lbl[p - w] = cur; st.push(p - w); }
      if (y < h - 1 && a[p + w] && !lbl[p + w]) { lbl[p + w] = cur; st.push(p + w); } }
    if (cnt > best) { best = cnt; bestId = cur; }
  }
  const b = new Uint8Array(w * h); for (let i = 0; i < w * h; i++) b[i] = lbl[i] === bestId ? 1 : 0; return b;
}
// Trace the outer boundary of a filled region as a rectilinear grid-corner loop.
function _gTrace(a, w, h) {
  const F = (x, y) => (x >= 0 && y >= 0 && x < w && y < h) ? a[y * w + x] : 0;
  const adj = new Map(); const key = (x, y) => x + "," + y;
  const add = (x1, y1, x2, y2) => { const k1 = key(x1, y1), k2 = key(x2, y2); (adj.get(k1) || adj.set(k1, []).get(k1)).push(k2); (adj.get(k2) || adj.set(k2, []).get(k2)).push(k1); };
  let topX = 1e9, topY = 1e9;
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    if (!F(x, y)) continue;
    if (!F(x, y - 1)) add(x, y, x + 1, y);
    if (!F(x, y + 1)) add(x, y + 1, x + 1, y + 1);
    if (!F(x - 1, y)) add(x, y, x, y + 1);
    if (!F(x + 1, y)) add(x + 1, y, x + 1, y + 1);
    if (y < topY || (y === topY && x < topX)) { topY = y; topX = x; }
  }
  if (!adj.size) return null;
  const start = key(topX, topY); const poly = []; const used = new Set();
  let cur = start, prev = null, guard = 0, max = adj.size * 2 + 16;
  do {
    const [cx, cy] = cur.split(",").map(Number); poly.push([cx, cy]);
    const nb = adj.get(cur) || []; let next = null;
    for (const k of nb) { const e = cur < k ? cur + "|" + k : k + "|" + cur; if (k !== prev && !used.has(e)) { next = k; break; } }
    if (!next) { for (const k of nb) if (k !== prev) { next = k; break; } }
    if (!next) break;
    used.add(cur < next ? cur + "|" + next : next + "|" + cur);
    prev = cur; cur = next; guard++;
  } while (cur !== start && guard < max);
  return poly.length >= 4 ? poly : null;
}
function _rmCollinear(pts) {
  const out = [], n = pts.length;
  for (let i = 0; i < n; i++) { const a = pts[(i - 1 + n) % n], b = pts[i], c = pts[(i + 1) % n];
    if ((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) !== 0) out.push(b); }
  return out.length >= 3 ? out : pts;
}
// Rectilinear simplify: snap corners to a coarse grid (collapses pixel-level
// staircases), drop duplicate + collinear points, then absorb short notch edges
// — keeps right angles only, so rooms look like rooms, not diagonal blobs.
function _rectSimplify(pts, q) {
  // Snap corners to a grid — a rectilinear trace stays rectilinear because x and
  // y round independently, so horizontal/vertical edges remain horizontal/
  // vertical (never diagonal). Then drop duplicate + collinear points. This
  // keeps right angles only; coarser grid = fewer staircase steps.
  const snapped = pts.map(pt => [Math.round(pt[0] / q) * q, Math.round(pt[1] / q) * q]);
  const dd = [];
  for (const pt of snapped) { const l = dd[dd.length - 1]; if (!l || l[0] !== pt[0] || l[1] !== pt[1]) dd.push(pt); }
  while (dd.length > 1 && dd[0][0] === dd[dd.length - 1][0] && dd[0][1] === dd[dd.length - 1][1]) dd.pop();
  const out = _rmCollinear(dd);
  return out.length >= 3 ? out : dd;
}

// Auto-fit room shapes to the mapped walls. Every mapped-floor pixel is assigned
// to its nearest room centre (a Voronoi partition → the rooms tile with no
// overlap); each room's shape is the *traced outline* of its pixel region (not
// its bounding box, which used to bleed into neighbours), cleaned up with a
// morphological open/close, largest-component and simplification. A clean,
// non-overlapping starting layout the user then refines by hand. Returns the
// number of rooms fitted, or -1 if the mask/calibration isn't available.
async function autoFitRooms(onlySeg) {
  await _ensureFloorMask();
  const M = _floorMask;
  if (!M) return -1;
  const w = M.w, h = M.h;
  const seeds = MAP.rooms.map(r => {
    const cxm = typeof r.x === "number" ? r.x : (r.x0 + r.x1) / 2;
    const cym = typeof r.y === "number" ? r.y : (r.y0 + r.y1) / 2;
    return { seg: String(r.room_id), px: Math.round((M.px0 + cxm * M.sx) * M.ds), py: Math.round((M.py0 + cym * M.sy) * M.ds) };
  });
  const cutM = _cutPxMask(M), addM = _addPxMask(M);  // ✂ off-limits, ➕ counts as floor
  const onFloor = (x, y) => x >= 0 && y >= 0 && x < w && y < h && ((M.arr[y * w + x] || (addM && addM[y * w + x])) && !(cutM && cutM[y * w + x]));
  const nearestFloor = (x, y) => {                  // snap a seed onto floor if its centre lands on a wall
    if (onFloor(x, y)) return [x, y];
    for (let r = 1; r < 40; r++) for (let dy = -r; dy <= r; dy++) for (let dx = -r; dx <= r; dx++) {
      if (Math.abs(dx) !== r && Math.abs(dy) !== r) continue;
      if (onFloor(x + dx, y + dy)) return [x + dx, y + dy];
    }
    return null;
  };
  // Geodesic (watershed) segmentation: a simultaneous breadth-first flood from
  // every room centre, expanding ONLY across floor pixels. Fronts can't cross
  // walls, so region boundaries land on the real walls (or split open
  // doorways) — not on a straight line between two room centres.
  const label = new Int16Array(w * h).fill(-1);
  const qx = [], qy = [];
  seeds.forEach((s, si) => {
    const f = nearestFloor(s.px, s.py); if (!f) return;
    const i = f[1] * w + f[0]; if (label[i] === -1) { label[i] = si; qx.push(f[0]); qy.push(f[1]); }
  });
  for (let head = 0; head < qx.length; head++) {
    const x = qx[head], y = qy[head], si = label[y * w + x];
    const step = (nx, ny) => { if (onFloor(nx, ny)) { const i = ny * w + nx; if (label[i] === -1) { label[i] = si; qx.push(nx); qy.push(ny); } } };
    step(x + 1, y); step(x - 1, y); step(x, y + 1); step(x, y - 1);
  }
  const mX = px => (px / M.ds - M.px0) / M.sx, mY = py => (py / M.ds - M.py0) / M.sy;
  const q = Math.max(3, Math.round(350 * Math.abs(M.sx) * M.ds));   // rectilinear grid (~35cm)
  const shapes = OPTS.shapes || (OPTS.shapes = {});
  let n = 0;
  seeds.forEach((s, si) => {
    if (onlySeg && s.seg !== String(onlySeg)) return;
    let g = new Uint8Array(w * h); let cnt = 0;
    for (let i = 0; i < w * h; i++) if (label[i] === si) { g[i] = 1; cnt++; }
    if (cnt < 25) return;
    // clip to the clean house body — a room region must never extend past the
    // footprint (out-window fringes attach to whichever room floods them)
    if (_houseMask) for (let i = 0; i < w * h; i++) if (!_houseMask[i]) g[i] = 0;
    g = _gDilate(_gDilate(_gErode(_gErode(g, w, h), w, h), w, h), w, h);   // open r2: drop tendrils
    g = _gErode(_gErode(_gDilate(_gDilate(g, w, h), w, h), w, h), w, h);   // close r2: fill gaps
    g = _gLargest(g, w, h);                          // keep the main body only
    let poly = _gTrace(g, w, h);
    if (!poly) return;
    poly = _rectSimplify(poly, q);
    if (poly.length < 3) return;
    shapes[s.seg] = poly.map(p => [Math.round(mX(p[0])), Math.round(mY(p[1]))]);
    n++;
  });
  // Straighten the whole layout (rectilinear-safe): collapse sub-65cm wall
  // notches per room, then snap near-coincident wall lines across rooms AND
  // the outline onto shared axes — neighbouring rooms get the SAME wall.
  if (window.Geom && !onlySeg) {
    const segs = Object.keys(shapes).filter(k => shapes[k] && shapes[k].length >= 3);
    segs.forEach(k => { shapes[k] = Geom.collapseShort(shapes[k], 650); });
    const polys = segs.map(k => shapes[k]);
    if (_cleanOutline && _cleanOutline.length >= 3) polys.push(_cleanOutline);
    Geom.snapAxes(polys, 280);
    segs.forEach(k => { shapes[k] = Geom.collapseShort(shapes[k], 500); });
  }
  MAP_DIRTY = true;
  return n;
}

// Re-render the noisy map image as a clean house outline: rasterise the mapped
// floor, morphologically open (drop out-window tendrils/reflections), close
// (fill scan gaps), keep the largest connected body (the house), trace its
// boundary and rectilinear-simplify it. A reliable single outline to draw rooms
// onto. Returns the polygon (map coords) or null.
async function buildCleanOutline(openR, gridMM) {
  await _ensureFloorMask();
  const M = _floorMask; if (!M) { _cleanOutline = null; return null; }
  const w = M.w, h = M.h;
  const rO = openR == null ? 6 : openR;
  let g = Uint8Array.from(M.arr);
  const am = _addPxMask(M); if (am) for (let i = 0; i < g.length; i++) if (am[i]) g[i] = 1;  // ➕ fill gaps
  const cm = _cutPxMask(M); if (cm) for (let i = 0; i < g.length; i++) if (cm[i]) g[i] = 0;  // ✂ remove areas (win)
  // 1) OPEN with a big element: erode to sever the narrow window/porch necks
  //    that connect out-window scans to the house, then dilate back.
  for (let k = 0; k < rO; k++) g = _gErode(g, w, h);
  g = _gLargest(g, w, h);                                // keep the house core NOW, before anything can re-bridge
  for (let k = 0; k < rO; k++) g = _gDilate(g, w, h);   // grow the core back to size (tendrils are gone)
  g = _gLargest(g, w, h);
  // 2) light close + fill interior holes (internal walls/benches are inside)
  for (let k = 0; k < 2; k++) g = _gDilate(g, w, h);
  for (let k = 0; k < 2; k++) g = _gErode(g, w, h);
  g = _gFillHoles(g, w, h);
  g = _gLargest(g, w, h);                                // the house body only
  _houseMask = g;                                        // rooms clip to this (kills out-of-house fringes)
  let poly = _gTrace(g, w, h); if (!poly) { _cleanOutline = null; return null; }
  const q = Math.max(3, Math.round((gridMM == null ? 550 : gridMM) * Math.abs(M.sx) * M.ds));
  poly = _rectSimplify(poly, q);
  // Straighten: collapse wall features below real-world size (~70cm) so lidar
  // notch noise doesn't read as a maze of tiny walls. Rectilinear-safe.
  if (window.Geom) poly = Geom.collapseShort(poly, Math.max(4, Math.round(700 * Math.abs(M.sx) * M.ds)));
  const mX = px => (px / M.ds - M.px0) / M.sx, mY = py => (py / M.ds - M.py0) / M.sy;
  _cleanOutline = poly.length >= 3 ? poly.map(p => [Math.round(mX(p[0])), Math.round(mY(p[1]))]) : null;
  return _cleanOutline;
}

const SX = x => x - MAP.minX;                     // map-mm -> svg x
const SY = y => MAP.maxY - y;                     // map-mm -> svg y (flip upright)

// A wall is a line [x0,y0,x1,y1]; a zone is a box. The robot stores zones as
// 4-corner polygons {x0,y0,x1,y1,x2,y2,x3,y3}; read them back as their bbox.
function _coerceLine(v) {
  if (Array.isArray(v) && v.length >= 4 && v.slice(0, 4).every(n => typeof n === "number")) return v.slice(0, 4);
  if (v && typeof v === "object" && [v.x0, v.y0, v.x1, v.y1].every(n => typeof n === "number")) return [v.x0, v.y0, v.x1, v.y1];
  return null;
}
function _coerceBox(v) {
  if (Array.isArray(v) && v.length >= 4 && v.slice(0, 4).every(n => typeof n === "number")) {
    const [a, b, c, d] = v; return [Math.min(a, c), Math.min(b, d), Math.max(a, c), Math.max(b, d)];
  }
  if (v && typeof v === "object") {
    const xs = ["x0", "x1", "x2", "x3"].map(k => v[k]).filter(n => typeof n === "number");
    const ys = ["y0", "y1", "y2", "y3"].map(k => v[k]).filter(n => typeof n === "number");
    if (xs.length >= 2 && ys.length >= 2) return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
  }
  return null;
}

async function renderMap() {
  // a full map re-render (e.g. after a bulk Apply) destroys #map-wrap — close
  // the 3D view first so its canvas isn't orphaned and drawFloorplan doesn't
  // stay short-circuited by the Plan3D.active guard.
  if (window.Plan3D && Plan3D.active) {
    Plan3D.stop();
    const b3 = document.getElementById("map-3d"); if (b3) b3.classList.remove("active");
    const tb0 = document.getElementById("map-toolbar"); if (tb0) tb0.classList.remove("p3d-on");
  }
  const wrap = document.getElementById("map-wrap");
  wrap.innerHTML = `<div class="hint" id="map-status">Loading map…</div>`;
  let m;
  try { m = await api("api/ha/map?prefix=" + encodeURIComponent(CFG.prefix)); }
  catch (e) {
    _mapLoaded = false;                              // let re-opening the tab retry
    wrap.innerHTML = `<div class="hint">Map unavailable: ${esc(e.message)} <a href="#" id="map-retry">Retry</a></div>`;
    const rl = document.getElementById("map-retry");
    if (rl) rl.onclick = ev => { ev.preventDefault(); _mapLoaded = true; renderMap(); };
    return;
  }
  const rooms = Object.values(m.rooms || {}).filter(r => ["x0", "y0", "x1", "y1"].every(k => typeof r[k] === "number"));
  if (!rooms.length) { wrap.innerHTML = `<div class="hint">No map rooms found — let the vacuum finish a full map first.</div>`; return; }

  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  rooms.forEach(r => { minX = Math.min(minX, r.x0); minY = Math.min(minY, r.y0); maxX = Math.max(maxX, r.x1); maxY = Math.max(maxY, r.y1); });
  [m.vacuum_position, m.charger_position].forEach(p => { if (p && typeof p.x === "number") { minX = Math.min(minX, p.x); minY = Math.min(minY, p.y); maxX = Math.max(maxX, p.x); maxY = Math.max(maxY, p.y); } });
  const pad = 500; minX -= pad; minY -= pad; maxX += pad; maxY += pad;
  MAP = { m, rooms, byId: Object.fromEntries(rooms.map(r => [String(r.room_id), r])), minX, minY, maxX, maxY, W: maxX - minX, H: maxY - minY };
  VIEW = { x: 0, y: 0, w: MAP.W, h: MAP.H };
  MAPROT = [0, 90, 180, 270].includes(OPTS.viewrot) ? OPTS.viewrot : 0;   // saved view orientation
  // migrate legacy openings (pre per-wall heights) into the override store
  if (OPTS.openings) {
    const ew = _edgeWalls();
    for (const [seg, list] of Object.entries(OPTS.openings)) {
      const dst = ew[seg] || (ew[seg] = []);
      (list || []).forEach(p => {
        if (!dst.some(q => Math.abs(q[0] - p[0]) <= 350 && Math.abs(q[1] - p[1]) <= 350))
          dst.push([p[0], p[1], 0]);
      });
    }
    delete OPTS.openings; MAP_DIRTY = true;
  }
  if (m.has_image) loadMapImage().then(() => { if (_mapImgCfg().on) seedMapImg(); buildCleanOutline().then(() => drawFloorplan()); });
  ZONES = {
    walls: (m.virtual_walls || []).map(_coerceLine).filter(Boolean),
    zones: (m.no_go_areas || []).map(_coerceBox).filter(Boolean),
    no_mops: (m.no_mopping_areas || []).map(_coerceBox).filter(Boolean),
  };
  // obstacles to plot (from the report; fetch it if the Report tab wasn't opened)
  if (!REPORT) { try { REPORT = await api("api/ha/report"); } catch (e) {} }
  MAP_OBSTACLES = (REPORT && REPORT.obstacles) || [];
  document.getElementById("map-toolbar").style.display = "flex";
  document.getElementById("map-side").style.display = "block";
  _applyStudioVisibility();
  SELECTED = null; DRAGSEL = null;
  wireMapTools();
  drawFloorplan();
  renderZoneList();
  renderRoomList();
  renderMapPanel();
}

function _zoneRect(z, cls, opts) {
  opts = opts || {};
  const x = SX(Math.min(z[0], z[2])), y = SY(Math.max(z[1], z[3]));
  const w = Math.abs(z[2] - z[0]), h = Math.abs(z[3] - z[1]);
  const attrs = opts.zt != null ? ` data-zt="${opts.zt}" data-zi="${opts.zi}"` : "";
  return `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="30" class="${cls}${opts.preview ? " preview" : ""}${opts.sel ? " sel" : ""}"${attrs}/>`;
}

// ---- 🏛 Floor plan render: a clean architectural view (two styles) ----
function _planPalette() {
  return PLAN_STYLE === "blueprint"
    ? { bg: "#f4f1e8", wall: "#111827", roomFill: "#ffffff", roomFillOp: 1, roomStroke: "#6b7280", label: "#111827", furnFill: "#ffffff", furnStroke: "#6b7280" }
    : { bg: "#0e1420", wall: "#e5e7eb", roomFill: null, roomFillOp: 0.35, roomStroke: "#94a3b8", label: "#e5e7eb", furnFill: "#1e293b", furnStroke: "#b08968" };
}
function _drawPlan() {
  const S = _planPalette();
  const fs = Math.max(240, Math.round(Math.min(MAP.W, MAP.H) / 26));
  const wallW = Math.max(70, Math.round(Math.min(MAP.W, MAP.H) / 85));
  let svg = `<svg viewBox="${VIEW.x} ${VIEW.y} ${VIEW.w} ${VIEW.h}" preserveAspectRatio="xMidYMid meet" class="floorplan plan pan">`;
  svg += `<rect x="${-MAP.W}" y="${-MAP.H}" width="${3 * MAP.W}" height="${3 * MAP.H}" fill="${S.bg}"/>`;
  svg += `<g class="fp-rot" transform="rotate(${MAPROT} ${MAP.W / 2} ${MAP.H / 2})">`;
  // rooms: filled + labelled; their shared edges read as interior walls
  MAP.rooms.forEach(r => {
    const seg = String(r.room_id); if (FILTER.has(seg)) return;
    const shp = shapeOf(seg);
    const color = _PALETTE[(r.color_index || 0) % _PALETTE.length];
    const fill = S.roomFill || color;
    const pts = (shp && shp.length >= 3)
      ? shp.map(p => `${SX(p[0])},${SY(p[1])}`).join(" ")
      : (() => { const x = SX(r.x0), y = SY(r.y1), w = r.x1 - r.x0, h = r.y1 - r.y0; return `${x},${y} ${x + w},${y} ${x + w},${y + h} ${x},${y + h}`; })();
    svg += `<polygon points="${pts}" fill="${fill}" fill-opacity="${S.roomFillOp}" stroke="${S.roomStroke}" stroke-width="${Math.round(wallW * 0.5)}" stroke-linejoin="round"/>`;
    const cx = typeof r.x === "number" ? SX(r.x) : SX((r.x0 + r.x1) / 2), cy = typeof r.y === "number" ? SY(r.y) : SY((r.y0 + r.y1) / 2);
    svg += `<text x="${cx}" y="${cy}" font-size="${fs}" fill="${S.label}" class="plan-label"${MAPROT ? ` transform="rotate(${-MAPROT} ${cx} ${cy})"` : ""}>${esc(r.custom_name || r.name || ("Room " + seg))}</text>`;
  });
  // outer walls = the clean house outline, thick
  if (_cleanOutline && _cleanOutline.length >= 3)
    svg += `<polygon points="${_cleanOutline.map(p => `${SX(p[0])},${SY(p[1])}`).join(" ")}" fill="none" stroke="${S.wall}" stroke-width="${wallW}" stroke-linejoin="miter"/>`;
  // user-tagged interior walls, thick
  _dwalls().forEach(w => svg += `<line x1="${SX(w[0])}" y1="${SY(w[1])}" x2="${SX(w[2])}" y2="${SY(w[3])}" stroke="${S.wall}" stroke-width="${wallW}" stroke-linecap="square"/>`);
  // furniture: clean symbols, styled to the view
  if (SHOW_FURNITURE && MAP.m.furnitures) MAP.m.furnitures.forEach(f => {
    if (typeof f.x0 !== "number" || typeof f.width !== "number") return;
    const w = f.width, h = f.height || f.width;
    const cx = SX(typeof f.x === "number" ? f.x : f.x0 + w / 2), cy = SY(typeof f.y === "number" ? f.y : f.y0 + h / 2);
    const x = SX(f.x0), y = SY(f.y0 + h), rot = -(f.angle || 0);
    svg += `<g transform="rotate(${rot} ${cx} ${cy})" fill="${S.furnFill}" stroke="${S.furnStroke}" stroke-width="${Math.round(wallW * 0.35)}" stroke-linejoin="round">`
      + `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="30"/>`
      + _furnDetail(f.type, x, y, w, h, "") + `</g>`;
  });
  const p = m2 => m2 && typeof m2.x === "number";
  const cp = MAP.m.charger_position, vp = MAP.m.vacuum_position;
  if (p(cp)) svg += `<circle cx="${SX(cp.x)}" cy="${SY(cp.y)}" r="150" fill="none" stroke="${S.roomStroke}" stroke-width="40"/>`;
  if (p(vp)) svg += `<circle cx="${SX(vp.x)}" cy="${SY(vp.y)}" r="150" fill="#3b82f6"/>`;
  svg += _renderDevices();                            // devices show on the plan too
  svg += `</g></svg>`;
  return svg;
}

// Exterior walls for the 3D view: the UNION of the (possibly hand-edited) room
// shapes — so after the user sculpts rooms in the editor, the 3D house follows
// their edits, not the raw scan outline. Rasterise → bridge 1px seams → trace
// → straighten. Cached by shape content (recomputed only when shapes change).
let _shapesOutlineCache = { key: "", poly: null };
function _shapesOutline() {
  if (!MAP) return null;
  const polys = [];
  const cap = MAP.W * MAP.H * 0.5;                  // a single room can't be half the map
  MAP.rooms.forEach(r => {
    const seg = String(r.room_id);
    if (FILTER.has(seg)) return;
    if (String(r.custom_name || r.name || "").toLowerCase() === "outside") return;
    const shp = shapeOf(seg);
    if (!shp || shp.length < 3) return;
    const xs = shp.map(p => p[0]), ys = shp.map(p => p[1]);
    if ((Math.max(...xs) - Math.min(...xs)) * (Math.max(...ys) - Math.min(...ys)) > cap) return; // corrupt shape — skip
    polys.push(shp.map(p => [SX(p[0]), SY(p[1])]));
  });
  if (polys.length < 3) return null;                // not enough shapes: caller falls back
  const key = JSON.stringify(polys);
  if (_shapesOutlineCache.key === key) return _shapesOutlineCache.poly;
  const k = 420 / Math.max(MAP.W, MAP.H);
  const w = Math.max(24, Math.round(MAP.W * k)), h = Math.max(24, Math.round(MAP.H * k));
  const c = document.createElement("canvas"); c.width = w; c.height = h;
  const x = c.getContext("2d"); x.fillStyle = "#fff";
  polys.forEach(p => {
    x.beginPath(); x.moveTo(p[0][0] * k, p[0][1] * k);
    for (let i = 1; i < p.length; i++) x.lineTo(p[i][0] * k, p[i][1] * k);
    x.closePath(); x.fill();
  });
  let g = null;
  try {
    const d = x.getImageData(0, 0, w, h).data;
    g = new Uint8Array(w * h);
    for (let i = 0; i < w * h; i++) g[i] = d[i * 4 + 3] > 40 ? 1 : 0;
  } catch (e) { return null; }
  g = _gDilate(g, w, h); g = _gFillHoles(g, w, h); g = _gErode(g, w, h);
  g = _gLargest(g, w, h);
  let poly = _gTrace(g, w, h);
  if (!poly) return null;
  poly = _rectSimplify(poly, Math.max(2, Math.round(300 * k)));
  if (window.Geom) poly = Geom.collapseShort(poly, Math.max(3, Math.round(600 * k)));
  poly = poly.length >= 3 ? poly.map(p => [p[0] / k, p[1] / k]) : null;
  _shapesOutlineCache = { key, poly };
  return poly;
}

// ---- 🧊 3D view: live scene data for plan3d.js (all coords in SX/SY space) ----
function _pInPoly(x, y, poly) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i], [xj, yj] = poly[j];
    if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}
function _plan3dData() {
  if (!MAP) return null;
  // walls follow the user's edited rooms; the scan outline is only the fallback
  const outline = _shapesOutline()
    || ((_cleanOutline && _cleanOutline.length >= 3) ? _cleanOutline.map(p => [SX(p[0]), SY(p[1])]) : null);
  const rooms = [];
  MAP.rooms.forEach(r => {
    const seg = String(r.room_id);
    if (FILTER.has(seg)) return;
    const name = r.custom_name || r.name || ("Room " + seg);
    if (String(name).toLowerCase() === "outside") return;   // artifact segment
    const shp = shapeOf(seg);
    let ok = shp && shp.length >= 3;
    if (ok) {                                        // corrupt (map-sized) shape → fall back to bbox
      const xs = shp.map(p => p[0]), ys = shp.map(p => p[1]);
      ok = (Math.max(...xs) - Math.min(...xs)) * (Math.max(...ys) - Math.min(...ys)) <= MAP.W * MAP.H * 0.5;
    }
    const poly = ok
      ? shp.map(p => [SX(p[0]), SY(p[1])])
      : [[SX(r.x0), SY(r.y1)], [SX(r.x1), SY(r.y1)], [SX(r.x1), SY(r.y0)], [SX(r.x0), SY(r.y0)]];
    // per-edge wall overrides (aligned with poly edge order): null = default
    // height, 0 = opening, otherwise a custom height in mm
    const wallh = ok ? shp.map((p, i) => _edgeWallOverride(seg, _edgeMid(shp, i))) : null;
    rooms.push({ seg, name, color: _PALETTE[(r.color_index || 0) % _PALETTE.length], poly, wallh });
  });
  // artifact-suppression zones live OUTSIDE the footprint — not part of the house
  const zones = ZONES.zones.map(z => [SX(Math.min(z[0], z[2])), SY(Math.max(z[1], z[3])),
                                      Math.abs(z[2] - z[0]), Math.abs(z[3] - z[1])])
    .filter(([zx, zy, zw, zh]) => !outline || _pInPoly(zx + zw / 2, zy + zh / 2, outline));
  const devices = Object.entries(_devices()).map(([eid, pos]) => {
    if (!pos || typeof pos.x !== "number") return null;
    const st = DEV_STATE[eid] || {};
    return { eid, x: SX(pos.x), y: SY(pos.y), domain: st.domain || eid.split(".")[0],
             name: st.name || eid, on: _DEV_ON.includes(String(st.state).toLowerCase()) };
  }).filter(Boolean);
  const furniture = (SHOW_FURNITURE && MAP.m.furnitures || []).map(f => {
    if (typeof f.x0 !== "number" || typeof f.width !== "number") return null;
    return { x: SX(f.x0), y: SY(f.y0 + (f.height || f.width)),
             w: f.width, h: f.height || f.width, type: f.type, angle: -(f.angle || 0) };
  }).filter(Boolean);
  const pt = q => (q && typeof q.x === "number") ? [SX(q.x), SY(q.y)] : null;
  return { rooms, outline, zones, devices, furniture,
           dock: pt(MAP.m.charger_position), robot: pt(MAP.m.vacuum_position), W: MAP.W, H: MAP.H };
}
// ---- 3D editing hooks: plan3d.js drags handles on the model; these apply
// the same snap / right-angle / clamp rules as the 2D editor, in map coords.
// Inputs arrive in SX/SY (scene) space; shapes live in map coords.
function _plan3dEnsureShape(seg) { ensureShape(seg); MAP_DIRTY = true; }
function _plan3dBeginEdit() { _pushUndo(); }
function _plan3dMoveVertex(seg, i, sx, sy) {
  const shp = shapeOf(seg); if (!shp || !shp[i]) return;
  let x = sx + MAP.minX, y = MAP.maxY - sy;
  x = Math.max(MAP.minX, Math.min(MAP.maxX, x));
  y = Math.max(MAP.minY, Math.min(MAP.maxY, y));
  const o = shp[i].slice();                          // previous position = ortho reference
  const np = snapVertex(seg, i, Math.round(x), Math.round(y));
  const n = shp.length, TOL = 30;
  [(i - 1 + n) % n, (i + 1) % n].forEach(j => {
    const q = shp[j];
    if (Math.abs(q[0] - o[0]) <= TOL && Math.abs(q[1] - o[1]) > TOL) q[0] = np[0];
    else if (Math.abs(q[1] - o[1]) <= TOL && Math.abs(q[0] - o[0]) > TOL) q[1] = np[1];
  });
  shp[i] = np;
  MAP_DIRTY = true;
}
function _plan3dMoveEdge(seg, i, dsx, dsy) {
  const shp = shapeOf(seg); if (!shp) return;
  const a = shp[i], b = shp[(i + 1) % shp.length];
  const dxm = dsx, dym = -dsy;                       // scene deltas → map deltas (y flips)
  const vert = Math.abs(a[0] - b[0]) < Math.abs(a[1] - b[1]);
  if (Math.abs(a[0] - b[0]) <= 30 || Math.abs(a[1] - b[1]) <= 30) {
    if (vert) { a[0] = Math.round(a[0] + dxm); b[0] = Math.round(b[0] + dxm); }
    else { a[1] = Math.round(a[1] + dym); b[1] = Math.round(b[1] + dym); }
  } else {
    a[0] = Math.round(a[0] + dxm); a[1] = Math.round(a[1] + dym);
    b[0] = Math.round(b[0] + dxm); b[1] = Math.round(b[1] + dym);
  }
  MAP_DIRTY = true;
}
function _plan3dToggleOpening(seg, i) { _toggleOpenEdge(seg, i); }
function _plan3dSetWallH(seg, i, h) { _setEdgeWall(seg, i, h); }          // live during a height drag
function _plan3dResetWallH(seg, i) { _pushUndo(); _setEdgeWall(seg, i, null); }

// ---- Floor Plan Studio gate: everything beyond core vacuum features (zones,
// walls, write-to-robot, robot rooms, tap-to-clean) hides behind this Labs
// toggle so shipped installs see a focused Map tab by default.
const _studioOn = () => !!OPTS.studio_enabled;
function _applyStudioVisibility() {
  const sec = document.getElementById("map");
  if (sec) sec.classList.toggle("studio-off", !_studioOn());
  if (!_studioOn()) {
    // the only exit from 3D (🧊) is now hidden — close it so the user isn't
    // stranded; also drop shape/device edit modes that the studio owns
    if (window.Plan3D && Plan3D.active) {
      Plan3D.stop();
      const b3 = document.getElementById("map-3d"); if (b3) b3.classList.remove("active");
      const tb = document.getElementById("map-toolbar"); if (tb) tb.classList.remove("p3d-on");
    }
    SHAPE_EDIT = null; VTX_TRIM = false; DEV_MODE = false; DEV_PLACE = null;
    if (document.getElementById("map-wrap") && MAP) { drawFloorplan(); renderMapPanel(); }
  }
}

// 🧲 Weld walls: one-tap tidy of the CURRENT shapes — snap near-coincident
// wall lines (across rooms AND the outline) onto shared axes, weld corners,
// and absorb the small gaps/crossovers left by hand editing. Undoable.
function _weldWalls() {
  if (!window.Geom) return;
  const shapes = OPTS.shapes || {};
  const segs = Object.keys(shapes).filter(k => shapes[k] && shapes[k].length >= 3);
  if (!segs.length) { setStatus("Nothing to weld — no room shapes yet", "err"); return; }
  _pushUndo();
  segs.forEach(k => { shapes[k] = Geom.collapseShort(shapes[k], 350); });   // drop micro-jogs first
  const polys = segs.map(k => shapes[k]);
  if (_cleanOutline && _cleanOutline.length >= 3) polys.push(_cleanOutline);
  Geom.snapAxes(polys, 300);                        // shared walls become the same line
  segs.forEach(k => { shapes[k] = Geom.collapseShort(shapes[k], 250); });   // clean snap residue
  MAP_DIRTY = true;
  drawFloorplan(); renderMapPanel();
  setStatus("Walls welded — gaps closed, shared lines aligned (Ctrl+Z to undo)", "ok");
}

// Tap-a-room in the 3D view → back to the 2D editor with that room's shape
// open for vertex editing (auto-fit is the starting point; the user sculpts).
function _plan3dEditRoom(seg) {
  const btn3d = document.getElementById("map-3d");
  const tb = document.getElementById("map-toolbar");
  if (window.Plan3D && Plan3D.active) {
    Plan3D.toggle(document.getElementById("map-wrap"));
    if (btn3d) btn3d.classList.remove("active");
    if (tb) tb.classList.remove("p3d-on");
    _syncMapImgUI();                                 // restore the underlay strip
  }
  DRAWMODE = "select";
  if (tb) tb.querySelectorAll(".mtool[data-mode]").forEach(x => x.classList.toggle("active", x.dataset.mode === "select"));
  SELECTED = { kind: "room", seg: String(seg) };
  ensureShape(seg);
  SHAPE_EDIT = String(seg);
  drawFloorplan();
  renderMapPanel();
  renderRoomList();
}

// Refresh the live bits while the 3D view is open: robot/dock position from the
// map, states of the placed devices. Called on an interval by plan3d.js.
async function _plan3dTick() {
  try {
    const m = await api("api/ha/map?prefix=" + encodeURIComponent(CFG.prefix));
    if (MAP && m) { MAP.m.vacuum_position = m.vacuum_position; MAP.m.charger_position = m.charger_position; }
  } catch (e) {}
  for (const eid of Object.keys(_devices())) {
    try {
      const s = await api("api/ha/state?entity_id=" + encodeURIComponent(eid));
      if (s && s.state != null) DEV_STATE[eid] = { ...(DEV_STATE[eid] || {}), state: s.state };
    } catch (e) {}
  }
}

function drawFloorplan(preview) {
  if (window.Plan3D && Plan3D.active) return;       // 3D owns #map-wrap while open
  const m = MAP.m;
  const fs = Math.max(240, Math.round(Math.min(MAP.W, MAP.H) / 26));
  if (!VIEW) VIEW = { x: 0, y: 0, w: MAP.W, h: MAP.H };
  if (PLAN_VIEW) {
    document.getElementById("map-wrap").innerHTML = _drawPlan();
    const svgEl = document.querySelector(".floorplan");
    if (svgEl) {
      _attachZoomPan(svgEl); _attachPan(svgEl);
      svgEl.addEventListener("pointerdown", e => { const dv = e.target.closest && e.target.closest(".fp-dev"); if (dv && dv.getAttribute("data-dev")) _toggleDevice(dv.getAttribute("data-dev")); });
    }
    return;
  }
  const cls = DRAWMODE === "pan" ? " pan" : (DRAWMODE === "imgalign" ? " pan" : (DRAWMODE !== "select" ? " drawing" : ""));
  let svg = `<svg viewBox="${VIEW.x} ${VIEW.y} ${VIEW.w} ${VIEW.h}" preserveAspectRatio="xMidYMid meet" class="floorplan${cls}">`;
  // view rotation: all content lives in this group; pointer math inverts
  // through its CTM (_svgToMap), so editing works in any orientation
  svg += `<g class="fp-rot" transform="rotate(${MAPROT} ${MAP.W / 2} ${MAP.H / 2})">`;
  // underlay (drawn first): the robot map, or the uploaded reference plan
  const mi = _mapImgCfg();
  const useUser = mi.src === "user" && USERPLAN_DATA && USERPLAN_NAT;
  const ulc = useUser ? _userPlanCfg() : mi;
  const ulData = useUser ? USERPLAN_DATA : MAPIMG_DATA;
  const ulNat = useUser ? USERPLAN_NAT : MAPIMG_NAT;
  if (mi.on && ulData && ulNat && typeof ulc.cx === "number") {
    const w = ulNat.iw * ulc.scale, h = ulNat.ih * ulc.scale;
    const csx = SX(ulc.cx), csy = SY(ulc.cy);
    const pe = DRAWMODE === "imgalign" ? "auto" : "none";
    let tf = `rotate(${ulc.rot || 0} ${csx} ${csy})`;
    if (ulc.flip) tf += ` matrix(1 0 0 -1 0 ${2 * csy})`;
    svg += `<image href="${ulData}" x="${csx - w / 2}" y="${csy - h / 2}" width="${w}" height="${h}"`
      + ` opacity="${ulc.opacity != null ? ulc.opacity : 0.5}" transform="${tf}"`
      + ` preserveAspectRatio="none" class="fp-underlay" style="pointer-events:${pe}"/>`;
  }
  // clean house outline (guide layer) — the map re-rendered as a tidy footprint
  if (_studioOn() && SHOW_OUTLINE && _cleanOutline && _cleanOutline.length >= 3) {
    svg += `<polygon points="${_cleanOutline.map(p => `${SX(p[0])},${SY(p[1])}`).join(" ")}" class="fp-outline"/>`;
  }
  // user exclude (✂) + include (➕) boxes — click one in Select mode to remove it
  (_mapImgCfg().adds || []).forEach((c, i) => svg += `<rect x="${SX(Math.min(c[0], c[2]))}" y="${SY(Math.max(c[1], c[3]))}" width="${Math.abs(c[2] - c[0])}" height="${Math.abs(c[3] - c[1])}" rx="20" class="fp-add" data-box="add" data-bi="${i}"/>`);
  (_mapImgCfg().cuts || []).forEach((c, i) => svg += `<rect x="${SX(Math.min(c[0], c[2]))}" y="${SY(Math.max(c[1], c[3]))}" width="${Math.abs(c[2] - c[0])}" height="${Math.abs(c[3] - c[1])}" rx="20" class="fp-cut" data-box="cut" data-bi="${i}"/>`);
  MAP.rooms.forEach(r => {
    if (FILTER.has(String(r.room_id))) return;
    const x = SX(r.x0), y = SY(r.y1), w = r.x1 - r.x0, h = r.y1 - r.y0;
    const color = _PALETTE[(r.color_index || 0) % _PALETTE.length];
    const name = r.custom_name || r.name || ("Room " + r.room_id);
    const cx = typeof r.x === "number" ? SX(r.x) : x + w / 2;
    const cy = typeof r.y === "number" ? SY(r.y) : y + h / 2;
    const seg = String(r.room_id);
    const rsel = SELECTED && SELECTED.kind === "room" && String(SELECTED.seg) === seg;
    const shp = shapeOf(seg);
    const body = (shp && shp.length >= 3)
      ? `<polygon points="${shp.map(pt => `${SX(pt[0])},${SY(pt[1])}`).join(" ")}" fill="${color}" fill-opacity="0.13" stroke="${color}" stroke-width="45"/>`
      : `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="80" fill="${color}" fill-opacity="0.13" stroke="${color}" stroke-width="45"/>`;
    // edges flagged as openings (no wall) — drawn as a dashed gap marker
    let openMarks = "";
    if (shp && shp.length >= 3 && (_edgeWalls()[seg] || []).length) {
      for (let oi = 0; oi < shp.length; oi++) {
        if (!_isOpenEdge(seg, _edgeMid(shp, oi))) continue;
        const a = shp[oi], b = shp[(oi + 1) % shp.length];
        openMarks += `<line x1="${SX(a[0])}" y1="${SY(a[1])}" x2="${SX(b[0])}" y2="${SY(b[1])}" class="fp-openedge"/>`;
      }
    }
    svg += `<g class="fp-room${rsel ? " sel" : ""}${CLEAN_SEL.has(seg) ? " cleansel" : ""}" data-seg="${esc(seg)}">${body}${openMarks}`
      + `<text x="${cx}" y="${cy}" font-size="${fs}" class="fp-label"${MAPROT ? ` transform="rotate(${-MAPROT} ${cx} ${cy})"` : ""}>${esc(name)}</text></g>`;
  });
  ZONES.zones.forEach((z, i) => svg += _zoneRect(z, "fp-nogo", { zt: "zone", zi: i, sel: _isSel("zone", i) }));
  ZONES.no_mops.forEach((z, i) => svg += _zoneRect(z, "fp-nomop", { zt: "nomop", zi: i, sel: _isSel("nomop", i) }));
  ZONES.walls.forEach((w, i) => svg += `<line x1="${SX(w[0])}" y1="${SY(w[1])}" x2="${SX(w[2])}" y2="${SY(w[3])}" class="fp-wall${_isSel("wall", i) ? " sel" : ""}" data-zt="wall" data-zi="${i}"/>`);
  // staged (not yet applied) robot-room boundary changes
  SEG_OPS.forEach(o => {
    if (o.op === "split" && o.line) svg += `<line x1="${SX(o.line[0])}" y1="${SY(o.line[1])}" x2="${SX(o.line[2])}" y2="${SY(o.line[3])}" class="fp-split pending"/>`;
    if (o.op === "carve" && o.rect) svg += `<rect x="${SX(o.rect[0])}" y="${SY(o.rect[3])}" width="${o.rect[2] - o.rect[0]}" height="${o.rect[3] - o.rect[1]}" rx="30" class="fp-carve pending"/>`;
  });
  if (MOVEB && MOVEB.line) svg += `<line x1="${SX(MOVEB.line[0])}" y1="${SY(MOVEB.line[1])}" x2="${SX(MOVEB.line[2])}" y2="${SY(MOVEB.line[3])}" class="fp-split"/>`;
  if (CARVE && CARVE.rect) svg += `<rect x="${SX(CARVE.rect[0])}" y="${SY(CARVE.rect[3])}" width="${CARVE.rect[2] - CARVE.rect[0]}" height="${CARVE.rect[3] - CARVE.rect[1]}" rx="30" class="fp-carve"/>`;
  if (preview) {
    if (preview.kind === "wall") svg += `<line x1="${SX(preview.x0)}" y1="${SY(preview.y0)}" x2="${SX(preview.x1)}" y2="${SY(preview.y1)}" class="fp-wall preview"/>`;
    else if (preview.kind === "splitline" || preview.kind === "movebound") svg += `<line x1="${SX(preview.x0)}" y1="${SY(preview.y0)}" x2="${SX(preview.x1)}" y2="${SY(preview.y1)}" class="fp-split"/>`;
    else { const pc = preview.kind === "nomop" ? "fp-nomop" : preview.kind === "cut" ? "fp-cut" : preview.kind === "add" ? "fp-add" : preview.kind === "carverect" ? "fp-carve" : "fp-nogo"; svg += _zoneRect([preview.x0, preview.y0, preview.x1, preview.y1], pc, { preview: true }); }
  }
  const p = m.charger_position;
  if (p && typeof p.x === "number") svg += `<rect x="${SX(p.x) - 170}" y="${SY(p.y) - 170}" width="340" height="340" rx="60" fill="#94a3b8"/><text x="${SX(p.x)}" y="${SY(p.y)}" font-size="${fs}" class="fp-icon">⌂</text>`;
  const v = m.vacuum_position;
  if (v && typeof v.x === "number") svg += `<circle cx="${SX(v.x)}" cy="${SY(v.y)}" r="230" fill="#3b82f6" stroke="#fff" stroke-width="60"/>`;
  // obstacle markers (from the Report tab's data): red = blocked room, amber = object
  MAP_OBSTACLES.forEach(o => {
    if (o.x == null || o.y == null) return;
    const cx = SX(o.x), cy = SY(o.y);
    const foc = MAP_FOCUS_OBS && MAP_FOCUS_OBS.x === o.x && MAP_FOCUS_OBS.y === o.y;
    const col = o.type === "Blocked Room" ? "#ef4444" : "#f59e0b";
    const r = foc ? 300 : 200;
    svg += `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${col}" fill-opacity="0.9" stroke="#fff" stroke-width="${foc ? 90 : 60}" class="fp-obstacle${foc ? " foc" : ""}"/>`
      + `<text x="${cx}" y="${cy}" font-size="${fs}" class="fp-icon">!</text>`;
  });
  // robot-detected furniture (position/size/angle/type from the map camera),
  // drawn as a floor-plan symbol filling its footprint (vector, no images).
  if (SHOW_FURNITURE && MAP.m.furnitures) {
    MAP.m.furnitures.forEach(f => {
      if (typeof f.x0 !== "number" || typeof f.width !== "number") return;
      const w = f.width, h = f.height || f.width;
      const cx = SX(typeof f.x === "number" ? f.x : f.x0 + w / 2), cy = SY(typeof f.y === "number" ? f.y : f.y0 + h / 2);
      const x = SX(f.x0), y = SY(f.y0 + h);
      const rot = -(f.angle || 0);                 // map angle → svg (y is flipped)
      svg += `<g class="fp-furn" transform="rotate(${rot} ${cx} ${cy})">`
        + `<rect class="fp-furn-box" x="${x}" y="${y}" width="${w}" height="${h}" rx="40"/>`
        + _furnDetail(f.type, x, y, w, h) + `</g>`;
    });
  }
  svg += _renderDevices();                           // placed HA devices (live state + tap-to-control)
  // vertex-edit handles for the room being shaped (drawn last so they sit on top)
  if (SHAPE_EDIT && shapeOf(SHAPE_EDIT) && !FILTER.has(SHAPE_EDIT)) {
    const shp = shapeOf(SHAPE_EDIT);
    const mq = MARQUEE ? { x0: Math.min(MARQUEE.x0, MARQUEE.x1), x1: Math.max(MARQUEE.x0, MARQUEE.x1), y0: Math.min(MARQUEE.y0, MARQUEE.y1), y1: Math.max(MARQUEE.y0, MARQUEE.y1) } : null;
    const inMq = pt => mq && pt[0] >= mq.x0 && pt[0] <= mq.x1 && pt[1] >= mq.y0 && pt[1] <= mq.y1;
    if (!VTX_TRIM) {                   // hide edge/break handles in box-delete mode (cleaner)
      shp.forEach((pt, i) => {         // draggable edges (whole line moves)
        const q = shp[(i + 1) % shp.length];
        svg += `<line x1="${SX(pt[0])}" y1="${SY(pt[1])}" x2="${SX(q[0])}" y2="${SY(q[1])}" class="fp-edge" data-edge="${i}"/>`;
      });
      shp.forEach((pt, i) => {         // edge midpoints = "add a break here"
        const q = shp[(i + 1) % shp.length];
        svg += `<circle cx="${SX((pt[0] + q[0]) / 2)}" cy="${SY((pt[1] + q[1]) / 2)}" r="75" class="fp-mid" data-mid="${i}"/>`;
      });
    }
    shp.forEach((pt, i) => svg += `<circle cx="${SX(pt[0])}" cy="${SY(pt[1])}" r="100" class="fp-vtx${inMq(pt) ? " sel" : ""}" data-vi="${i}"/>`);
    if (mq) svg += `<rect x="${SX(mq.x0)}" y="${SY(mq.y1)}" width="${mq.x1 - mq.x0}" height="${mq.y1 - mq.y0}" class="fp-marquee"/>`;
  }
  svg += `</g></svg>`;
  document.getElementById("map-wrap").innerHTML = svg;
  attachDrawHandlers();
}

function _svgToMap(svg, evt) {
  const pt = svg.createSVGPoint(); pt.x = evt.clientX; pt.y = evt.clientY;
  // map through the rotated content group so every interaction (draw, drag,
  // snap) works identically at any view rotation
  const g = svg.querySelector(".fp-rot");
  const p = pt.matrixTransform(((g && g.getScreenCTM()) || svg.getScreenCTM()).inverse());
  return { x: Math.round(p.x + MAP.minX), y: Math.round(MAP.maxY - p.y) };
}

function _attachZoomPan(svg) {
  svg.addEventListener("wheel", e => {
    e.preventDefault();
    const p = _svgPt(svg, e);
    _zoomView(e.deltaY > 0 ? 1.15 : 0.87, p.x, p.y);
  }, { passive: false });
  // double-tap to zoom in on that spot (Shift+double-tap zooms out) — the
  // maps-app pattern; trackpad pinch stays available but is no longer needed.
  svg.addEventListener("dblclick", e => {
    if (e.target.getAttribute && e.target.getAttribute("data-vi") != null) return; // vertex dbl-click = delete dot
    e.preventDefault();
    const p = _svgPt(svg, e);
    _zoomView(e.shiftKey ? 1.8 : 0.55, p.x, p.y);
  });
  // right-click: tools + context actions, like any production editor
  svg.addEventListener("contextmenu", e => {
    if (!window.CtxMenu) return;
    e.preventDefault();
    CtxMenu.open(e.clientX, e.clientY, _mapCtxItems(e, svg));
  });
}

// Build the right-click menu for the map: contextual actions for what's under
// the cursor first, then the tool modes, then view actions.
function _mapCtxItems(e, svg) {
  const items = [];
  const t = e.target;
  const vi = t.getAttribute && t.getAttribute("data-vi");
  if (vi != null && SHAPE_EDIT) {
    const shp = shapeOf(SHAPE_EDIT);
    items.push({ icon: "✕", label: "Delete this corner", danger: true, disabled: !shp || shp.length <= 3, on: () => {
      const s = shapeOf(SHAPE_EDIT); if (!s || s.length <= 3) return;
      _pushUndo(); s.splice(+vi, 1); MAP_DIRTY = true; drawFloorplan(); renderMapPanel();
    } });
  }
  const zt = t.getAttribute && t.getAttribute("data-zt");
  if (zt != null && t.getAttribute("data-zi") != null) {
    const zi = +t.getAttribute("data-zi");
    const nm = zt === "wall" ? "wall" : (zt === "nomop" ? "no-mop zone" : "no-go zone");
    items.push({ icon: "🗑", label: "Delete this " + nm, danger: true, on: () => {
      _pushUndo(); _zArr(zt).splice(zi, 1); MAP_DIRTY = true; SELECTED = null;
      drawFloorplan(); renderZoneList(); renderMapPanel();
    } });
  }
  // room edge (visible while shaping): toggle wall <-> opening for 3D/plan
  const ei = t.getAttribute && t.getAttribute("data-edge");
  if (ei != null && SHAPE_EDIT) {
    const shp = shapeOf(SHAPE_EDIT);
    if (shp) {
      const mid = _edgeMid(shp, +ei);
      const cur = _edgeWallOverride(SHAPE_EDIT, mid);
      items.push({
        icon: cur === 0 ? "▬" : "◌",
        label: cur === 0 ? "Make this line a wall" : "Make this line an opening (no wall)",
        on: () => _toggleOpenEdge(SHAPE_EDIT, +ei),
      });
      items.push({
        icon: "▮",
        label: "Set this wall's height…" + (cur != null && cur > 0 ? ` (now ${(cur / 1000).toFixed(1)} m)` : ""),
        on: () => {
          const v = prompt("Wall height in metres (0 = opening, blank = default):",
            cur != null ? String(cur / 1000) : "");
          if (v === null) return;
          _pushUndo();
          _setEdgeWall(SHAPE_EDIT, +ei, v.trim() === "" ? null : (parseFloat(v) || 0) * 1000);
        },
      });
      if (cur != null) items.push({
        icon: "↺", label: "Reset this wall to default",
        on: () => { _pushUndo(); _setEdgeWall(SHAPE_EDIT, +ei, null); },
      });
    }
  }
  const roomG = t.closest && t.closest(".fp-room");
  if (roomG && roomG.dataset.seg) {
    const seg = roomG.dataset.seg;
    const name = (MAP.byId[seg] && (MAP.byId[seg].custom_name || MAP.byId[seg].name)) || ("Room " + seg);
    if (_studioOn()) {
      items.push({ icon: "✏️", label: "Edit " + name + " shape", on: () => {
        SELECTED = { kind: "room", seg }; ensureShape(seg); SHAPE_EDIT = seg;
        drawFloorplan(); renderMapPanel(); renderRoomList();
      } });
      items.push({ icon: "↺", label: "Reset " + name + " shape", on: () => {
        if (!confirm("Reset " + name + " to a plain box? Its hand-drawn shape is discarded.")) return;
        _pushUndo(); resetShape(seg); MAP_DIRTY = true; drawFloorplan(); renderMapPanel();
      } });
    }
    items.push({ icon: "🧹", label: "Clean " + name + " now", on: () => {
      if (confirm("Send the robot to clean " + name + "?"))
        api("api/ha/clean_segments", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ segments: [parseInt(seg, 10)] }) }).catch(err => alert("Failed: " + err.message));
    } });
    items.push({ icon: "🧭", label: "Edge clean " + name + " (beta)", on: () => edgeCleanRoom(seg, name) });
  }
  if (items.length) items.push({ sep: true });
  // tool modes (mirrors the toolbar; clicking keeps toolbar state in sync)
  const tools = [["select", "⊹", "Select"], ["pan", "✋", "Pan"], ["wall", "▬", "Wall"],
                 ["nogo", "⛔", "No-go"], ["nomop", "💧", "No-mop"]];
  if (_studioOn()) tools.push(["cut", "✂", "Exclude"], ["add", "➕", "Include"]);
  for (const [mode, icon, label] of tools) {
    items.push({ icon, label, active: DRAWMODE === mode, on: () => {
      const b = document.querySelector(`#map-toolbar .mtool[data-mode="${mode}"]`);
      if (b) b.click();
    } });
  }
  items.push({ sep: true });
  items.push({ icon: "⤢", label: "Zoom to fit", on: () => _fitView() });
  if (_studioOn()) items.push({ icon: "⟳", label: "Rotate view 90°" + (MAPROT ? ` (now ${MAPROT}°)` : ""), on: () => {
    const b = document.getElementById("map-rotate"); if (b) b.click();
  } });
  if (_studioOn()) items.push({ icon: "🪢", label: "Weld walls (close gaps, align lines)", on: () => _weldWalls() });
  items.push({ icon: "↶", label: "Undo", disabled: !_undoStack.length, on: () => _undoMap() });
  items.push({ icon: "↷", label: "Redo", disabled: !_redoStack.length, on: () => _redoMap() });
  return items;
}

function _attachPan(svg) {
  svg.addEventListener("pointerdown", e => {
    PANNING = { cx: e.clientX, cy: e.clientY, vx: VIEW.x, vy: VIEW.y, moved: false };
    try { svg.setPointerCapture(e.pointerId); } catch (_) {}
  });
  svg.addEventListener("pointermove", e => {
    if (!PANNING) return;
    const scale = VIEW.w / svg.getBoundingClientRect().width;
    VIEW.x = PANNING.vx - (e.clientX - PANNING.cx) * scale;
    VIEW.y = PANNING.vy - (e.clientY - PANNING.cy) * scale;
    _applyView();
  });
  svg.addEventListener("pointerup", () => { PANNING = null; });
}

function _attachImgAlign(svg) {
  let d = null;
  svg.addEventListener("pointerdown", e => {
    const m = _svgToMap(svg, e), o = _ulCfg();
    d = { sx: m.x, sy: m.y, cx: o.cx, cy: o.cy };
    try { svg.setPointerCapture(e.pointerId); } catch (_) {}
  });
  svg.addEventListener("pointermove", e => {
    if (!d) return;
    const m = _svgToMap(svg, e), o = _ulCfg();
    o.cx = d.cx + (m.x - d.sx); o.cy = d.cy + (m.y - d.sy); MAP_DIRTY = true;
    // update the <image> attributes in place (don't re-embed the data URL each move)
    const im = svg.querySelector(".fp-underlay");
    if (!im) { drawFloorplan(); return; }
    const w = +im.getAttribute("width"), h = +im.getAttribute("height"), csx = SX(o.cx), csy = SY(o.cy);
    im.setAttribute("x", csx - w / 2); im.setAttribute("y", csy - h / 2);
    let tf = `rotate(${o.rot || 0} ${csx} ${csy})`; if (o.flip) tf += ` matrix(1 0 0 -1 0 ${2 * csy})`;
    im.setAttribute("transform", tf);
  });
  svg.addEventListener("pointerup", () => { d = null; });
}

function attachDrawHandlers() {
  const svg = document.querySelector(".floorplan");
  if (!svg) return;
  _attachZoomPan(svg);
  if (DRAWMODE === "imgalign") { _attachImgAlign(svg); return; }
  if (DRAWMODE === "pan") { _attachPan(svg); return; }
  if (DRAWMODE === "select") { _attachSelect(svg); return; }
  svg.addEventListener("pointerdown", e => {
    const m = _svgToMap(svg, e);
    const g = e.target.closest && e.target.closest(".fp-room");
    DRAWING = { kind: DRAWMODE, x0: m.x, y0: m.y, x1: m.x, y1: m.y, roomSeg: g ? g.dataset.seg : null };
    try { svg.setPointerCapture(e.pointerId); } catch (_) {}
  });
  svg.addEventListener("pointermove", e => { if (!DRAWING) return; const m = _svgToMap(svg, e); DRAWING.x1 = m.x; DRAWING.y1 = m.y; drawFloorplan(DRAWING); });
  svg.addEventListener("pointerup", () => {
    if (!DRAWING) return; const d = DRAWING; DRAWING = null;
    const tiny = Math.abs(d.x1 - d.x0) < 150 && Math.abs(d.y1 - d.y0) < 150;
    if (tiny) {
      // a tap on a room, in a zone mode, adds the WHOLE room as the zone
      if (d.roomSeg && (d.kind === "nogo" || d.kind === "nomop")) {
        const r = MAP.byId[d.roomSeg];
        if (r) { _pushUndo(); (d.kind === "nomop" ? ZONES.no_mops : ZONES.zones).push([r.x0, r.y0, r.x1, r.y1]); drawFloorplan(); renderZoneList(); return; }
      }
      drawFloorplan(); return;
    }
    if (d.kind === "roomrect" && ROOMRECT_SEG) {
      // snap the drawn rectangle's edges to the mapped walls, set it as the shape
      _pushUndo();
      let X0 = Math.min(d.x0, d.x1), X1 = Math.max(d.x0, d.x1), Y0 = Math.min(d.y0, d.y1), Y1 = Math.max(d.y0, d.y1);
      if (SNAP) {
        const l = snapToWall(X0, (Y0 + Y1) / 2), rt = snapToWall(X1, (Y0 + Y1) / 2);
        const t = snapToWall((X0 + X1) / 2, Y1), b = snapToWall((X0 + X1) / 2, Y0);
        if (l) X0 = l[0]; if (rt) X1 = rt[0]; if (t) Y1 = t[1]; if (b) Y0 = b[1];
      }
      (OPTS.shapes || (OPTS.shapes = {}))[ROOMRECT_SEG] = [[X0, Y0], [X1, Y0], [X1, Y1], [X0, Y1]];
      SHAPE_EDIT = ROOMRECT_SEG; SELECTED = { kind: "room", seg: ROOMRECT_SEG };
      ROOMRECT_SEG = null; DRAWMODE = "select"; MAP_DIRTY = true;
      document.querySelectorAll('.mtool[data-mode]').forEach(x => x.classList.toggle("active", x.dataset.mode === "select"));
      _ensureFloorMask(); drawFloorplan(); renderMapPanel(); renderRoomList(); return;
    }
    if (d.kind === "movebound") {
      DRAWMODE = "select";
      document.querySelectorAll('.mtool[data-mode]').forEach(x => x.classList.toggle("active", x.dataset.mode === "select"));
      if (MOVEB && Math.hypot(d.x1 - d.x0, d.y1 - d.y0) >= 300) MOVEB = { donor: MOVEB.donor, line: [d.x0, d.y0, d.x1, d.y1] };
      else MOVEB = null;
      if (MOVEB) SELECTED = { kind: "room", seg: MOVEB.donor };
      drawFloorplan(); renderMapPanel(); return;
    }
    if (d.kind === "splitline") {
      const seg = SPLIT_SEG; SPLIT_SEG = null; DRAWMODE = "select";
      document.querySelectorAll('.mtool[data-mode]').forEach(x => x.classList.toggle("active", x.dataset.mode === "select"));
      if (seg && Math.hypot(d.x1 - d.x0, d.y1 - d.y0) >= 300) {
        const r = MAP.byId[seg], rname = (r && (r.custom_name || r.name)) || ("Room " + seg);
        SEG_OPS.push({ op: "split", segment: +seg, line: [d.x0, d.y0, d.x1, d.y1], label: `✂ Split ${rname} at the drawn line` });
        SELECTED = { kind: "room", seg };
      }
      drawFloorplan(); renderMapPanel(); return;
    }
    if (d.kind === "carverect") {
      DRAWMODE = "select";
      document.querySelectorAll('.mtool[data-mode]').forEach(x => x.classList.toggle("active", x.dataset.mode === "select"));
      const rect = [Math.min(d.x0, d.x1), Math.min(d.y0, d.y1), Math.max(d.x0, d.x1), Math.max(d.y0, d.y1)];
      if (CARVE && (rect[2] - rect[0]) >= 400 && (rect[3] - rect[1]) >= 400) {
        CARVE = { donor: CARVE.donor, rect };
        SELECTED = { kind: "room", seg: CARVE.donor };
      } else CARVE = null;
      drawFloorplan(); renderMapPanel(); return;
    }
    if (d.kind === "cut" || d.kind === "add") {
      _pushUndo();
      const box = [Math.min(d.x0, d.x1), Math.min(d.y0, d.y1), Math.max(d.x0, d.x1), Math.max(d.y0, d.y1)];
      (d.kind === "cut" ? _cuts() : _adds()).push(box);
      MAP_DIRTY = true;
      buildCleanOutline().then(() => drawFloorplan());
      return;
    }
    _pushUndo();
    if (d.kind === "wall") ZONES.walls.push([d.x0, d.y0, d.x1, d.y1]);
    else { const box = [Math.min(d.x0, d.x1), Math.min(d.y0, d.y1), Math.max(d.x0, d.x1), Math.max(d.y0, d.y1)]; (d.kind === "nomop" ? ZONES.no_mops : ZONES.zones).push(box); }
    drawFloorplan(); renderZoneList();
  });
}

// Select mode: click a room or a zone/wall to select; drag a selected zone/wall
// to move it. Reaching a covered room is done via the room list (below).
function _attachSelect(svg) {
  svg.addEventListener("pointerdown", e => {
    // device pins: in Devices mode drag to move / tap to control; else tap to control
    const devTap = e.target.closest && e.target.closest(".fp-dev");
    if (devTap && devTap.getAttribute("data-dev")) {
      const eid = devTap.getAttribute("data-dev");
      if (DEV_MODE && _devices()[eid]) {
        DEVDRAG = { eid, cx: e.clientX, cy: e.clientY, moved: false };
        try { svg.setPointerCapture(e.pointerId); } catch (_) {}
      } else _toggleDevice(eid);
      return;
    }
    if (DEV_MODE && DEV_PLACE) {
      const m = _svgToMap(svg, e);
      _pushUndo(); _devices()[DEV_PLACE.entity_id] = { x: m.x, y: m.y }; MAP_DIRTY = true;
      const nm = DEV_PLACE.name; DEV_PLACE = null;
      const h = document.getElementById("device-hint"); if (h) h.innerHTML = `Placed <b>${esc(nm)}</b>. Pick another, or tap a placed device to control it.`;
      drawFloorplan(); renderDeviceFilter(); renderDeviceList(); return;
    }
    // vertex-edit handles (only present when SHAPE_EDIT is set for a room)
    if (e.button === 2) return;                     // right-click belongs to the context menu
    const vi = e.target.getAttribute && e.target.getAttribute("data-vi");
    if (vi != null && SHAPE_EDIT) {
      _pushUndo();
      const shp0 = shapeOf(SHAPE_EDIT);
      VTXDRAG = { seg: SHAPE_EDIT, i: +vi, orig: shp0 && shp0[+vi] ? shp0[+vi].slice() : null };
      try { svg.setPointerCapture(e.pointerId); } catch (_) {}
      return;
    }
    const mid = e.target.getAttribute && e.target.getAttribute("data-mid");
    if (mid != null && SHAPE_EDIT) {
      // insert a new break point at this edge midpoint, then start dragging it
      _pushUndo();
      const shp = shapeOf(SHAPE_EDIT), i = +mid, a = shp[i], b = shp[(i + 1) % shp.length];
      shp.splice(i + 1, 0, [Math.round((a[0] + b[0]) / 2), Math.round((a[1] + b[1]) / 2)]);
      VTXDRAG = { seg: SHAPE_EDIT, i: i + 1, orig: shp[i + 1].slice(), fresh: true };
      try { svg.setPointerCapture(e.pointerId); } catch (_) {}
      drawFloorplan(); return;
    }
    if (SHAPE_EDIT && VTX_TRIM) {
      // box-delete-dots mode: drag a rectangle to select many corners at once
      const m = _svgToMap(svg, e);
      MARQUEE = { x0: m.x, y0: m.y, x1: m.x, y1: m.y };
      try { svg.setPointerCapture(e.pointerId); } catch (_) {}
      return;
    }
    const edge = e.target.getAttribute && e.target.getAttribute("data-edge");
    if (edge != null && SHAPE_EDIT) {
      // drag a whole edge (line): moves both its endpoints together
      _pushUndo();
      const shp = shapeOf(SHAPE_EDIT), i = +edge, m = _svgToMap(svg, e);
      EDGEDRAG = { seg: SHAPE_EDIT, i, sx: m.x, sy: m.y, a: shp[i].slice(), b: shp[(i + 1) % shp.length].slice() };
      try { svg.setPointerCapture(e.pointerId); } catch (_) {}
      return;
    }
    const bt = e.target.getAttribute && e.target.getAttribute("data-box");
    if (bt != null && e.target.getAttribute("data-bi") != null) {
      _pushUndo();
      (bt === "cut" ? _cuts() : _adds()).splice(+e.target.getAttribute("data-bi"), 1);
      MAP_DIRTY = true; buildCleanOutline().then(() => drawFloorplan());
      return;
    }
    const zt = e.target.getAttribute && e.target.getAttribute("data-zt");
    if (zt != null && e.target.getAttribute("data-zi") != null) {
      const i = +e.target.getAttribute("data-zi");
      SELECTED = { kind: zt, i };
      const m = _svgToMap(svg, e);
      _pushUndo();
      DRAGSEL = { zt, i, sx: m.x, sy: m.y, orig: _zArr(zt)[i].slice() };
      try { svg.setPointerCapture(e.pointerId); } catch (_) {}
      drawFloorplan(); renderMapPanel(); return;
    }
    const m0 = _svgToMap(svg, e), segAt = _roomAt(m0.x, m0.y);
    if (CLEAN_MODE) {
      if (segAt) { CLEAN_SEL.has(segAt) ? CLEAN_SEL.delete(segAt) : CLEAN_SEL.add(segAt); drawFloorplan(); renderMapPanel(); }
      return;
    }
    if (MOVEB && MOVEB.line && segAt) {
      const nm = s => (MAP.byId[s] && (MAP.byId[s].custom_name || MAP.byId[s].name)) || ("Room " + s);
      if (segAt !== MOVEB.donor) {
        // stage locally — nothing written until Apply
        SEG_OPS.push({ op: "split", segment: +MOVEB.donor, line: MOVEB.line, label: `✂ Split ${nm(MOVEB.donor)} at the drawn line` });
        SEG_OPS.push({ op: "merge", keep: +segAt, absorb: "$new", label: `⇄ Give the strip to ${nm(segAt)}` });
        MOVEB = null;
      }
      drawFloorplan(); renderMapPanel(); return;
    }
    if (MERGE_SEG && segAt) {
      const a = MERGE_SEG; MERGE_SEG = null;
      if (segAt !== a) {
        const nm = s => (MAP.byId[s] && (MAP.byId[s].custom_name || MAP.byId[s].name)) || ("Room " + s);
        SEG_OPS.push({ op: "merge", keep: +a, absorb: +segAt, label: `⧉ Merge ${nm(segAt)} into ${nm(a)}` });
        SELECTED = { kind: "room", seg: a };
      }
      drawFloorplan(); renderMapPanel(); return;
    }
    if (CARVE && CARVE.rect && segAt) {
      const nm = s => (MAP.byId[s] && (MAP.byId[s].custom_name || MAP.byId[s].name)) || ("Room " + s);
      if (segAt !== CARVE.donor) {
        SEG_OPS.push({ op: "carve", donor: +CARVE.donor, receiver: +segAt, rect: CARVE.rect,
                       label: `◳ Carve a chunk of ${nm(CARVE.donor)} → ${nm(segAt)}` });
        CARVE = null;
      }
      drawFloorplan(); renderMapPanel(); return;
    }
    if (segAt) { if (SHAPE_EDIT && SHAPE_EDIT !== segAt) SHAPE_EDIT = null; SELECTED = { kind: "room", seg: segAt }; DRAGSEL = null; drawFloorplan(); renderMapPanel(); renderRoomList(); return; }
    // empty space: begin a pan; a click with no drag deselects (on pointerup)
    PANNING = { cx: e.clientX, cy: e.clientY, vx: VIEW.x, vy: VIEW.y, moved: false };
    try { svg.setPointerCapture(e.pointerId); } catch (_) {}
  });
  svg.addEventListener("pointermove", e => {
    if (VTXDRAG) {
      const shp = shapeOf(VTXDRAG.seg); if (!shp) { VTXDRAG = null; return; }
      const m = _svgToMap(svg, e);
      // clamp to the map so a drag that strays over the toolbar can't fling
      // the corner off-plan
      const cx = Math.max(MAP.minX, Math.min(MAP.maxX, m.x));
      const cy = Math.max(MAP.minY, Math.min(MAP.maxY, m.y));
      const np = snapVertex(VTXDRAG.seg, VTXDRAG.i, cx, cy);
      // keep right angles (CAD-style): a neighbour that shared this corner's x
      // or y follows it, so dragging a corner of a rectangle keeps it square.
      // (Skip for a freshly inserted midpoint — it's meant to break the line.)
      const o = VTXDRAG.orig;
      if (o && !VTXDRAG.fresh) {
        const n = shp.length, TOL = 30;
        [(VTXDRAG.i - 1 + n) % n, (VTXDRAG.i + 1) % n].forEach(j => {
          const q = shp[j];
          if (Math.abs(q[0] - o[0]) <= TOL && Math.abs(q[1] - o[1]) > TOL) q[0] = np[0];      // shared vertical wall
          else if (Math.abs(q[1] - o[1]) <= TOL && Math.abs(q[0] - o[0]) > TOL) q[1] = np[1]; // shared horizontal wall
        });
        VTXDRAG.orig = np.slice();
      }
      shp[VTXDRAG.i] = np;
      drawFloorplan(); return;
    }
    if (DEVDRAG) {
      if (!DEVDRAG.moved && Math.abs(e.clientX - DEVDRAG.cx) + Math.abs(e.clientY - DEVDRAG.cy) < 4) return;
      if (!DEVDRAG.moved) { _pushUndo(); DEVDRAG.moved = true; }
      const m = _svgToMap(svg, e); _devices()[DEVDRAG.eid] = { x: m.x, y: m.y }; MAP_DIRTY = true; drawFloorplan(); return;
    }
    if (MARQUEE) { const m = _svgToMap(svg, e); MARQUEE.x1 = m.x; MARQUEE.y1 = m.y; drawFloorplan(); return; }
    if (EDGEDRAG) {
      const shp = shapeOf(EDGEDRAG.seg); if (!shp) { EDGEDRAG = null; return; }
      const m = _svgToMap(svg, e);
      const mx = Math.max(MAP.minX, Math.min(MAP.maxX, m.x));
      const my = Math.max(MAP.minY, Math.min(MAP.maxY, m.y));
      let ax = EDGEDRAG.a[0] + (mx - EDGEDRAG.sx), ay = EDGEDRAG.a[1] + (my - EDGEDRAG.sy);
      let bx = EDGEDRAG.b[0] + (mx - EDGEDRAG.sx), by = EDGEDRAG.b[1] + (my - EDGEDRAG.sy);
      const vert = Math.abs(EDGEDRAG.a[0] - EDGEDRAG.b[0]) < Math.abs(EDGEDRAG.a[1] - EDGEDRAG.b[1]);
      // CAD-style: an axis-aligned wall moves only perpendicular to itself —
      // sliding it lengthways is what pulled corners off the neighbours (skew)
      if (Math.abs(EDGEDRAG.a[0] - EDGEDRAG.b[0]) <= 30 || Math.abs(EDGEDRAG.a[1] - EDGEDRAG.b[1]) <= 30) {
        if (vert) { ay = EDGEDRAG.a[1]; by = EDGEDRAG.b[1]; }
        else { ax = EDGEDRAG.a[0]; bx = EDGEDRAG.b[0]; }
      }
      if (SNAP) {                                   // snap the moved axis of an axis-aligned edge to a wall
        const ws = snapToWall((ax + bx) / 2, (ay + by) / 2);
        if (ws) { if (vert) { const off = ws[0] - (ax + bx) / 2; ax += off; bx += off; } else { const off = ws[1] - (ay + by) / 2; ay += off; by += off; } }
      }
      shp[EDGEDRAG.i] = [Math.round(ax), Math.round(ay)];
      shp[(EDGEDRAG.i + 1) % shp.length] = [Math.round(bx), Math.round(by)];
      drawFloorplan(); return;
    }
    if (PANNING) {
      const scale = VIEW.w / svg.getBoundingClientRect().width;
      const ddx = e.clientX - PANNING.cx, ddy = e.clientY - PANNING.cy;
      if (Math.abs(ddx) + Math.abs(ddy) > 3) PANNING.moved = true;
      VIEW.x = PANNING.vx - ddx * scale; VIEW.y = PANNING.vy - ddy * scale;
      _applyView(); return;
    }
    if (!DRAGSEL) return;
    const m = _svgToMap(svg, e), o = DRAGSEL.orig, dx = m.x - DRAGSEL.sx, dy = m.y - DRAGSEL.sy;
    _zArr(DRAGSEL.zt)[DRAGSEL.i] = [o[0] + dx, o[1] + dy, o[2] + dx, o[3] + dy];
    drawFloorplan();
  });
  svg.addEventListener("pointerup", () => {
    if (DEVDRAG) { const dd = DEVDRAG; DEVDRAG = null; if (!dd.moved) _toggleDevice(dd.eid); else renderMapPanel(); return; }
    if (MARQUEE) {
      const shp = shapeOf(SHAPE_EDIT), mq = MARQUEE; MARQUEE = null;
      if (shp) {
        const x0 = Math.min(mq.x0, mq.x1), x1 = Math.max(mq.x0, mq.x1), y0 = Math.min(mq.y0, mq.y1), y1 = Math.max(mq.y0, mq.y1);
        const keep = shp.filter(p => !(p[0] >= x0 && p[0] <= x1 && p[1] >= y0 && p[1] <= y1));
        if (keep.length < shp.length && keep.length >= 3) { _pushUndo(); OPTS.shapes[SHAPE_EDIT] = keep; MAP_DIRTY = true; }
        else if (keep.length < 3) alert("That would leave fewer than 3 corners — select a smaller cluster.");
      }
      drawFloorplan(); renderMapPanel(); return;
    }
    if (VTXDRAG) { VTXDRAG = null; MAP_DIRTY = true; renderMapPanel(); return; }
    if (EDGEDRAG) { EDGEDRAG = null; MAP_DIRTY = true; renderMapPanel(); return; }
    if (PANNING) { const moved = PANNING.moved; PANNING = null; if (!moved) { SELECTED = null; drawFloorplan(); renderMapPanel(); } return; }
    if (DRAGSEL) { DRAGSEL = null; renderZoneList(); renderMapPanel(); }
  });
  // delete a vertex: double-click it (right-click now opens the context menu,
  // which offers "Delete this corner" for the same action)
  const delVtx = e => {
    const vi = e.target.getAttribute && e.target.getAttribute("data-vi");
    if (vi == null || !SHAPE_EDIT) return;
    e.preventDefault();
    const shp = shapeOf(SHAPE_EDIT); if (!shp || shp.length <= 3) return;
    _pushUndo();
    shp.splice(+vi, 1); MAP_DIRTY = true; drawFloorplan(); renderMapPanel();
  };
  svg.addEventListener("dblclick", delVtx);
}

// Staged robot-room ops block: local queue + one bulk Apply.
function _segOpsHTML() {
  if (!SEG_OPS.length) return "";
  return `<div class="segops"><div class="mp-sub">Staged robot-room changes (${SEG_OPS.length})</div>`
    + SEG_OPS.map((o, i) => `<div class="so-row">${esc(o.label || o.op)}<span class="so-x" data-so="${i}" title="Remove">✕</span></div>`).join("")
    + `<div class="mp-actions"><button class="btn" id="so-apply" type="button">⬆ Apply to robot</button><button class="btn ghost" id="so-discard" type="button">Discard</button></div>
       <div class="hint">Applied in order as one bulk change — map backed up once first. Dreame app reflects it.</div></div>`;
}
function _wireSegOps(el) {
  el.querySelectorAll(".so-x").forEach(x => x.onclick = () => {
    const i = +x.dataset.so;
    // removing a split also removes a following merge that depends on its $new
    const n = (SEG_OPS[i] && SEG_OPS[i].op === "split" && SEG_OPS[i + 1] && SEG_OPS[i + 1].absorb === "$new") ? 2 : 1;
    SEG_OPS.splice(i, n); drawFloorplan(); renderMapPanel();
  });
  const ap = el.querySelector("#so-apply");
  if (ap) ap.onclick = async () => {
    const n = SEG_OPS.length;
    if (!confirm(`Apply ${n} change${n === 1 ? "" : "s"} to the robot's map?\n\nRuns as one bulk change (~10-15s per step). The map is backed up once first. The Dreame app will reflect it.`)) return;
    ap.disabled = true; ap.textContent = "⏳ Applying…";
    try {
      const res = await api("api/ha/apply_segment_ops", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ vacuum_entity: MAP.m.vacuum_entity, prefix: CFG.prefix, ops: SEG_OPS.map(({ label, ...o }) => o) }) });
      SEG_OPS = []; MOVEB = null; CARVE = null;
      alert(`✓ Applied — rooms ${res.rooms_before} → ${res.rooms_after}. The map will refresh.`);
      REPORT = null; setTimeout(() => renderMap(), 1500);
    } catch (e) {
      alert("Apply failed: " + e.message + "\n\nThe map was backed up before the batch — restore from the Dreame app if needed. Staged changes kept.");
      ap.disabled = false; ap.textContent = "⬆ Apply to robot";
    }
  };
  const dc = el.querySelector("#so-discard");
  if (dc) dc.onclick = () => { SEG_OPS = []; drawFloorplan(); renderMapPanel(); };
}

function renderMapPanel() {
  const el = document.getElementById("map-panel");
  if (!el) return;
  if (CLEAN_MODE) {
    const segs = [...CLEAN_SEL];
    const nm = s => (MAP.byId[s] && (MAP.byId[s].custom_name || MAP.byId[s].name)) || ("Room " + s);
    el.innerHTML = `<div class="mp-title">🧹 Clean rooms</div>
      <div class="hint">Tap rooms on the plan to add/remove them, then send the robot.</div>
      <div class="mp-clean-sel">${segs.length ? segs.map(s => `<span class="rsum st-ok">${esc(nm(s))}</span>`).join(" ") : '<span class="hint">No rooms selected.</span>'}</div>
      <div class="mp-actions"><button class="btn" id="mp-clean-go"${segs.length ? "" : " disabled"}>▶ Clean ${segs.length} room${segs.length === 1 ? "" : "s"}</button>${segs.length ? '<button class="btn ghost" id="mp-clean-clear">Clear</button>' : ""}</div>`;
    const go = el.querySelector("#mp-clean-go");
    if (go) go.onclick = async () => {
      if (!CLEAN_SEL.size) return;
      const names = segs.map(nm).join(", ");
      if (!confirm(`Send the robot to clean:\n\n${names}\n\nThe robot will start now.`)) return;
      go.disabled = true; go.textContent = "⏳ Sending…";
      try {
        await api("api/ha/clean_segments", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ vacuum_entity: MAP.m.vacuum_entity, segments: segs.map(Number) }) });
        go.textContent = "✓ Robot on its way";
        CLEAN_SEL.clear(); setTimeout(() => { drawFloorplan(); renderMapPanel(); }, 1500);
      } catch (e) { alert("Clean failed: " + e.message); go.disabled = false; go.textContent = `▶ Clean ${segs.length} rooms`; }
    };
    const clr = el.querySelector("#mp-clean-clear");
    if (clr) clr.onclick = () => { CLEAN_SEL.clear(); drawFloorplan(); renderMapPanel(); };
    return;
  }
  if (!SELECTED) { el.innerHTML = _segOpsHTML() + `<div class="hint">In <b>Select</b> mode, click a room or a zone/wall to edit it. Drag a selected zone/wall to move it.</div>`; _wireSegOps(el); return; }
  if (SELECTED.kind === "room") {
    const seg = String(SELECTED.seg), r = MAP.byId[seg];
    const name = (r && (r.custom_name || r.name)) || ("Room " + seg);
    const rc = roomCfg(seg);
    const editing = SHAPE_EDIT === seg, custom = !!shapeOf(seg);
    el.innerHTML = _segOpsHTML() + `<div class="mp-title">${esc(name)} <span class="mut">#${esc(seg)}</span></div>
      <div class="mp-rename"><input id="mp-name" type="text" value="${esc(name)}" spellcheck="false"><button class="btn ghost" id="mp-rename-btn" type="button" title="Rename this room on the robot (updates the Dreame app)">✎ Rename on robot</button></div>
      <div class="mp-grid">
        <label>Mode ${optSelectHTML("mode", CFG.modes, rc.mode)}</label>
        <label>Suction ${optSelectHTML("suction", CFG.suctions, rc.suction)}</label>
      </div>
      <div class="hint">Days: ${(rc.days || []).map(d => WEEK[d]).join(", ") || "none (set in Rooms tab)"} · saved with the footer <b>Save</b>.</div>
      <div class="mp-actions"><button class="btn ghost" data-mpz="zone" type="button">Zone room: No-go</button><button class="btn ghost" data-mpz="nomop" type="button">No-mop</button></div>
      <div class="mp-sub">Robot rooms <span class="mut">(writes to the robot · Dreame app)</span></div>
      <div class="mp-actions"><button class="btn ${DRAWMODE === "splitline" && SPLIT_SEG === seg ? "" : "ghost"}" id="mp-split" type="button" title="Draw a straight line across this room to split it into two on the robot">✂ Split room</button><button class="btn ${MERGE_SEG === seg ? "" : "ghost"}" id="mp-merge" type="button" title="Merge this room with an adjacent one on the robot">⧉ Merge with…</button><button class="btn ${MOVEB && MOVEB.donor === seg ? "" : "ghost"}" id="mp-moveb" type="button" title="Draw the new boundary across this room, then click the neighbour that gets the strip — staged, applied in bulk">⇄ Move boundary</button><button class="btn ${CARVE && CARVE.donor === seg ? "" : "ghost"}" id="mp-carve" type="button" title="Drag a box over part of this room, then click the neighbour that receives it — staged, applied in bulk">◳ Carve chunk</button></div>
      ${DRAWMODE === "splitline" && SPLIT_SEG === seg ? `<div class="hint"><b class="warn">Split mode</b> — drag a straight line right across the room. Staged — applied with ⬆ Apply.</div>` : ""}
      ${MERGE_SEG === seg ? `<div class="hint"><b class="warn">Merge mode</b> — click an adjacent room to merge it into <b>${esc(name)}</b> (kept). Staged — applied with ⬆ Apply.</div>` : ""}
      ${MOVEB && MOVEB.donor === seg && !MOVEB.line ? `<div class="hint"><b class="warn">Move boundary 1/2</b> — drag a line right across <b>${esc(name)}</b> where the new boundary should sit.</div>` : ""}
      ${MOVEB && MOVEB.donor === seg && MOVEB.line ? `<div class="hint"><b class="warn">Move boundary 2/2</b> — now click the neighbouring room that should receive the strip. Nothing is written until you Apply.</div>` : ""}
      ${CARVE && CARVE.donor === seg && !CARVE.rect ? `<div class="hint"><b class="warn">Carve 1/2</b> — drag a box over the part of <b>${esc(name)}</b> to give away.</div>` : ""}
      ${CARVE && CARVE.donor === seg && CARVE.rect ? `<div class="hint"><b class="warn">Carve 2/2</b> — now click the neighbouring room that should receive the chunk. Nothing is written until you Apply.</div>` : ""}
      ${!_studioOn() ? "" : `<div class="mp-shape">
        <div class="mp-sub">Room shape</div>
        <div class="mp-actions">
          <button class="btn ${editing ? "" : "ghost"}" id="mp-shape" type="button">${editing ? "✓ Done shaping" : "✎ Edit shape"}</button>
          <button class="btn ghost" id="mp-shape-draw" type="button" title="Drag a rectangle on the map to set this room's shape">▭ Draw box</button>
          <button class="btn ghost" id="mp-shape-fit" type="button" title="Trace this room from the mapped walls">🪄 Fit to walls</button>
          ${editing ? `<button class="btn ${VTX_TRIM ? "" : "ghost"}" id="mp-shape-trim" type="button" title="Drag a box over a cluster of corners to delete them all">▚ Box-delete dots</button>` : ""}
          ${custom ? `<button class="btn ghost" id="mp-shape-reset" type="button">↺ Reset to box</button>` : ""}
        </div>
        ${editing ? (VTX_TRIM ? `<div class="hint"><b class="warn">Box-delete on</b> — drag a rectangle over a cluster of corners to delete them all at once. Tap ▚ again to go back to editing.${MAP_DIRTY ? " Footer Save to keep." : ""}</div>` : `<div class="hint">Drag a corner or an edge to move it · click a dot on an edge to add a break · double-click or right-click a corner to delete it · ▚ Box-delete to remove many at once. Snapping ${SNAP ? "on" : "off"} (🧲).${MAP_DIRTY ? ` <b class="warn">Unsaved</b> — footer Save.` : ""}</div>`) : `<div class="hint">Reshape into a polygon, or ▭ Draw box a fresh rectangle. Display only — doesn't change the robot's own map.</div>`}
      </div>`}`;
    _wireSegOps(el);
    el.querySelectorAll("select[data-f]").forEach(s => s.addEventListener("change", () => { rc[s.dataset.f] = s.value; }));
    const nameInput = el.querySelector("#mp-name"), renameBtn = el.querySelector("#mp-rename-btn");
    if (renameBtn) renameBtn.onclick = () => {
      const newName = (nameInput.value || "").trim();
      if (!newName || newName === name) { alert("Enter a new name first."); return; }
      SEG_OPS.push({ op: "rename", segment: +seg, name: newName, label: `✎ Rename ${name} → ${newName}` });
      renderMapPanel();
    };
    el.querySelectorAll("[data-mpz]").forEach(b => b.onclick = () => {
      if (!r) return;
      (b.dataset.mpz === "nomop" ? ZONES.no_mops : ZONES.zones).push([r.x0, r.y0, r.x1, r.y1]);
      drawFloorplan(); renderZoneList();
    });
    const sbtn = el.querySelector("#mp-shape");
    if (sbtn) sbtn.onclick = () => {
      VTX_TRIM = false; MARQUEE = null;
      if (SHAPE_EDIT === seg) { SHAPE_EDIT = null; }
      else { ensureShape(seg); SHAPE_EDIT = seg; if (DRAWMODE !== "select") DRAWMODE = "select"; _ensureFloorMask(); }
      drawFloorplan(); renderMapPanel();
    };
    const dbtn = el.querySelector("#mp-shape-draw");
    if (dbtn) dbtn.onclick = () => {
      ROOMRECT_SEG = seg; SHAPE_EDIT = null; DRAWMODE = "roomrect"; VTX_TRIM = false;
      document.querySelectorAll('.mtool[data-mode]').forEach(x => x.classList.remove("active"));
      drawFloorplan(); renderMapPanel();
    };
    const tbtn = el.querySelector("#mp-shape-trim");
    if (tbtn) tbtn.onclick = () => { VTX_TRIM = !VTX_TRIM; drawFloorplan(); renderMapPanel(); };
    const _clearRobotModes = () => { SPLIT_SEG = null; MERGE_SEG = null; MOVEB = null; CARVE = null; SHAPE_EDIT = null; VTX_TRIM = false; };
    const spbtn = el.querySelector("#mp-split");
    if (spbtn) spbtn.onclick = () => {
      const was = DRAWMODE === "splitline" && SPLIT_SEG === seg;
      _clearRobotModes();
      if (was) DRAWMODE = "select";
      else { SPLIT_SEG = seg; DRAWMODE = "splitline"; document.querySelectorAll('.mtool[data-mode]').forEach(x => x.classList.remove("active")); }
      drawFloorplan(); renderMapPanel();
    };
    const mgbtn = el.querySelector("#mp-merge");
    if (mgbtn) mgbtn.onclick = () => {
      const was = MERGE_SEG === seg;
      _clearRobotModes(); if (DRAWMODE !== "select") DRAWMODE = "select";
      MERGE_SEG = was ? null : seg;
      drawFloorplan(); renderMapPanel();
    };
    const mbbtn = el.querySelector("#mp-moveb");
    if (mbbtn) mbbtn.onclick = () => {
      const was = MOVEB && MOVEB.donor === seg;
      _clearRobotModes();
      if (was) DRAWMODE = "select";
      else { MOVEB = { donor: seg }; DRAWMODE = "movebound"; document.querySelectorAll('.mtool[data-mode]').forEach(x => x.classList.remove("active")); }
      drawFloorplan(); renderMapPanel();
    };
    const mcbtn = el.querySelector("#mp-carve");
    if (mcbtn) mcbtn.onclick = () => {
      const was = CARVE && CARVE.donor === seg;
      _clearRobotModes();
      if (was) DRAWMODE = "select";
      else { CARVE = { donor: seg }; DRAWMODE = "carverect"; document.querySelectorAll('.mtool[data-mode]').forEach(x => x.classList.remove("active")); }
      drawFloorplan(); renderMapPanel();
    };
    const fbtn = el.querySelector("#mp-shape-fit");
    if (fbtn) fbtn.onclick = async () => {
      _pushUndo();
      fbtn.disabled = true; fbtn.textContent = "⏳…";
      const n = await autoFitRooms(seg);
      if (n === -1) { alert("Fit-to-walls needs the map underlay + calibration. Turn on 🖼 Map first."); fbtn.disabled = false; fbtn.textContent = "🪄 Fit to walls"; return; }
      if (n === 0) alert("Couldn't trace this room from the map (not enough mapped floor near it).");
      SHAPE_EDIT = seg; _ensureFloorMask(); drawFloorplan(); renderMapPanel();
    };
    const rbtn = el.querySelector("#mp-shape-reset");
    if (rbtn) rbtn.onclick = () => { _pushUndo(); resetShape(seg); MAP_DIRTY = true; drawFloorplan(); renderMapPanel(); };
  } else {
    const label = SELECTED.kind === "wall" ? "Virtual wall" : (SELECTED.kind === "nomop" ? "No-mop zone" : "No-go zone");
    const z = _zArr(SELECTED.kind)[SELECTED.i];
    el.innerHTML = `<div class="mp-title">${label} #${SELECTED.i + 1}</div>
      <div class="hint">[${(z || []).map(Math.round).join(", ")}] · drag it on the map to move.</div>
      <div class="mp-actions"><button class="btn ghost" id="mp-del" type="button">✕ Delete</button></div>`;
    el.querySelector("#mp-del").onclick = () => { _pushUndo(); _zArr(SELECTED.kind).splice(SELECTED.i, 1); SELECTED = null; drawFloorplan(); renderZoneList(); renderMapPanel(); };
  }
}

function renderRoomList() {
  const el = document.getElementById("room-list");
  if (!el) return;
  el.innerHTML = MAP.rooms.map(r => {
    const seg = String(r.room_id), name = r.custom_name || r.name || ("Room " + seg);
    const shown = !FILTER.has(seg), sel = SELECTED && SELECTED.kind === "room" && String(SELECTED.seg) === seg;
    return `<div class="rl-row${sel ? " sel" : ""}" data-seg="${esc(seg)}"><input type="checkbox" class="rl-vis"${shown ? " checked" : ""} title="show / hide"><span class="rl-name">${esc(name)}</span></div>`;
  }).join("");
  el.querySelectorAll(".rl-row").forEach(row => {
    const seg = row.dataset.seg;
    row.querySelector(".rl-vis").addEventListener("change", e => { e.target.checked ? FILTER.delete(seg) : FILTER.add(seg); drawFloorplan(); });
    row.querySelector(".rl-name").addEventListener("click", () => { SELECTED = { kind: "room", seg }; drawFloorplan(); renderMapPanel(); renderRoomList(); });
  });
}

function wireMapTools() {
  const tb = document.getElementById("map-toolbar");
  tb.querySelectorAll(".mtool[data-mode]").forEach(b => b.onclick = () => {
    DRAWMODE = b.dataset.mode;
    tb.querySelectorAll(".mtool[data-mode]").forEach(x => x.classList.toggle("active", x === b));
    if (b.dataset.mode !== "select") { SELECTED = null; DRAGSEL = null; SHAPE_EDIT = null; renderMapPanel(); }
    drawFloorplan();
  });
  const _zc = () => [VIEW.x + VIEW.w / 2, VIEW.y + VIEW.h / 2];
  const _in3d = () => window.Plan3D && Plan3D.active;   // toolbar controls drive whichever view is up
  document.getElementById("zoom-in").onclick = () => { if (_in3d()) return Plan3D.zoomBy(1.35); const [cx, cy] = _zc(); _zoomView(0.7, cx, cy); };
  document.getElementById("zoom-out").onclick = () => { if (_in3d()) return Plan3D.zoomBy(0.74); const [cx, cy] = _zc(); _zoomView(1.4, cx, cy); };
  document.getElementById("zoom-fit").onclick = () => { if (_in3d()) return Plan3D.refit(); _fitView(); };
  document.getElementById("zones-write").onclick = writeZones;
  const snapBtn = document.getElementById("map-snap");
  if (snapBtn) {
    snapBtn.classList.toggle("active", SNAP);
    snapBtn.onclick = () => { SNAP = !SNAP; snapBtn.classList.toggle("active", SNAP); if (SNAP) _ensureFloorMask(); };
  }
  const saveBtn = document.getElementById("map-save");
  if (saveBtn) saveBtn.onclick = () => save();       // footer Save is unreachable in full-screen
  const weldBtn = document.getElementById("map-weld");
  if (weldBtn) weldBtn.onclick = () => _weldWalls();
  const rotBtn = document.getElementById("map-rotate");
  if (rotBtn) {
    rotBtn.classList.toggle("active", !!MAPROT);
    rotBtn.onclick = () => {
      if (window.Plan3D && Plan3D.active) return Plan3D.rotate90();   // 3D: quarter-turn the orbit
      MAPROT = (MAPROT + 90) % 360;
      OPTS.viewrot = MAPROT; MAP_DIRTY = true;
      rotBtn.classList.toggle("active", !!MAPROT);
      rotBtn.title = `Rotate the view 90° (now ${MAPROT}°) — edit in any orientation`;
      drawFloorplan();
    };
  }
  const btn3d = document.getElementById("map-3d");
  if (btn3d) btn3d.onclick = () => {
    if (!window.Plan3D) return;
    const on = Plan3D.toggle(document.getElementById("map-wrap"));
    btn3d.classList.toggle("active", on);
    // grey out 2D-only tools while 3D owns the view (they'd silently no-op)
    tb.classList.toggle("p3d-on", on);
    const strip = document.getElementById("map-img-strip");
    if (strip) strip.style.display = on ? "none" : "";
    if (!on) { _syncMapImgUI(); drawFloorplan(); }   // back to the 2D editor
  };
  const fsBtn = document.getElementById("map-fs-btn");
  if (fsBtn) fsBtn.onclick = async () => {
    const card = document.querySelector("#map .card"); if (!card) return;
    if (document.fullscreenElement) { try { await document.exitFullscreen(); } catch (e) {} return; }
    if (card.classList.contains("map-fs")) {        // exit CSS-overlay fallback
      card.classList.remove("map-fs"); fsBtn.classList.remove("active"); requestAnimationFrame(() => drawFloorplan()); return;
    }
    try { await card.requestFullscreen(); }          // real fullscreen escapes the ingress iframe
    catch (e) {                                      // iframe blocks it → CSS overlay fallback
      card.classList.add("map-fs"); fsBtn.classList.add("active"); requestAnimationFrame(() => drawFloorplan());
    }
  };
  if (!window._fsWired) {
    window._fsWired = true;
    document.addEventListener("fullscreenchange", () => {
      const card = document.querySelector("#map .card"), b = document.getElementById("map-fs-btn");
      const fs = !!document.fullscreenElement;
      if (card) card.classList.toggle("map-fs", fs);
      if (b) b.classList.toggle("active", fs);
      requestAnimationFrame(() => drawFloorplan());
    });
  }
  const undoBtn = document.getElementById("map-undo"), redoBtn = document.getElementById("map-redo");
  if (undoBtn) undoBtn.onclick = _undoMap;
  if (redoBtn) redoBtn.onclick = _redoMap;
  _syncUndoBtn();
  if (!window._undoKeyWired) {
    window._undoKeyWired = true;
    document.addEventListener("keydown", e => {
      if (!document.getElementById("map-wrap")) return;
      if (e.key === "Escape") {
        const card = document.querySelector("#map .card.map-fs");
        if (card) { card.classList.remove("map-fs"); const b = document.getElementById("map-fs-btn"); if (b) b.classList.remove("active"); requestAnimationFrame(() => drawFloorplan()); }
        return;
      }
      if (!(e.ctrlKey || e.metaKey)) return;
      // only when the Map tab is the active view — otherwise a habitual Ctrl+Z
      // on another tab would silently revert a map edit
      if (!document.querySelector("#map.section.active")) return;
      const tag = (e.target.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select") return;
      const k = (e.key || "").toLowerCase();
      if (k === "z" && !e.shiftKey) { e.preventDefault(); _undoMap(); }
      else if (k === "y" || (k === "z" && e.shiftKey)) { e.preventDefault(); _redoMap(); }
    });
  }
  if (!window._mapPointerWired) {
    window._mapPointerWired = true;
    // A drag that ends OUTSIDE the SVG (over the toolbar/panel) never reaches the
    // svg-scoped pointerup, and drawFloorplan rebuilds the SVG mid-drag so pointer
    // capture is already gone — clear all drag state globally so nothing sticks to
    // the cursor after the button is released.
    window.addEventListener("pointerup", () => {
      if (VTXDRAG || EDGEDRAG || MARQUEE || DRAGSEL || DRAWING || PANNING || DEVDRAG) {
        VTXDRAG = EDGEDRAG = MARQUEE = DRAGSEL = DRAWING = DEVDRAG = null;
        PANNING = null;
        requestAnimationFrame(() => { if (document.getElementById("map-wrap") && !(window.Plan3D && Plan3D.active)) drawFloorplan(); });
      }
    });
    window.addEventListener("beforeunload", e => {
      if (MAP_DIRTY) { e.preventDefault(); e.returnValue = ""; }
    });
  }
  const olBtn = document.getElementById("map-outline");
  if (olBtn) {
    olBtn.classList.toggle("active", SHOW_OUTLINE);
    olBtn.onclick = async () => {
      SHOW_OUTLINE = !SHOW_OUTLINE;
      olBtn.classList.toggle("active", SHOW_OUTLINE);
      if (SHOW_OUTLINE && !_cleanOutline) { olBtn.disabled = true; await buildCleanOutline(); olBtn.disabled = false; }
      drawFloorplan();
    };
  }
  const furnBtn = document.getElementById("map-furniture");
  if (furnBtn) {
    furnBtn.classList.toggle("active", SHOW_FURNITURE);
    furnBtn.onclick = () => { SHOW_FURNITURE = !SHOW_FURNITURE; furnBtn.classList.toggle("active", SHOW_FURNITURE); drawFloorplan(); };
  }
  const planBtn = document.getElementById("map-plan"), planStyleBtn = document.getElementById("map-plan-style");
  // Editing tools have no effect in the read-only plan view — disable them there
  // so they don't look broken. (Pan, zoom, furniture, style, full-screen stay.)
  const PLAN_NA = '[data-mode="select"],[data-mode="wall"],[data-mode="nogo"],[data-mode="nomop"],[data-mode="cut"],[data-mode="add"],[data-mode="imgalign"],#map-img-toggle,#map-img-align,#map-snap,#map-autofit,#map-outline,#zones-write';
  const syncPlan = () => {
    if (planBtn) planBtn.classList.toggle("active", PLAN_VIEW);
    if (planStyleBtn) { planStyleBtn.style.display = PLAN_VIEW ? "" : "none"; planStyleBtn.textContent = PLAN_STYLE === "blueprint" ? "◑ Blueprint" : "◑ Themed"; }
    document.querySelectorAll(PLAN_NA).forEach(b => { b.disabled = PLAN_VIEW; b.classList.toggle("na", PLAN_VIEW); });
  };
  if (planBtn) planBtn.onclick = () => { PLAN_VIEW = !PLAN_VIEW; syncPlan(); drawFloorplan(); };
  if (planStyleBtn) planStyleBtn.onclick = () => { PLAN_STYLE = PLAN_STYLE === "blueprint" ? "themed" : "blueprint"; syncPlan(); drawFloorplan(); };
  syncPlan();
  const devBtn = document.getElementById("map-devices"), devSide = document.getElementById("device-side"), mapSideEl = document.getElementById("map-side"), devSearch = document.getElementById("device-search");
  if (devBtn) devBtn.onclick = async () => {
    DEV_MODE = !DEV_MODE; devBtn.classList.toggle("active", DEV_MODE);
    if (devSide) devSide.style.display = DEV_MODE ? "" : "none";
    if (mapSideEl) mapSideEl.style.display = DEV_MODE ? "none" : "";
    if (DEV_MODE) { SHAPE_EDIT = null; SPLIT_SEG = null; MERGE_SEG = null; VTX_TRIM = false; await loadDeviceAreas(); populateZoomSelect(); renderDeviceFilter(); renderDeviceList(); }
    else DEV_PLACE = null;
    drawFloorplan();
  };
  if (devSearch) devSearch.oninput = renderDeviceList;
  const cleanBtn = document.getElementById("map-clean");
  if (cleanBtn) cleanBtn.onclick = () => {
    CLEAN_MODE = !CLEAN_MODE; cleanBtn.classList.toggle("active", CLEAN_MODE);
    if (CLEAN_MODE) { SHAPE_EDIT = null; SPLIT_SEG = null; MERGE_SEG = null; VTX_TRIM = false; if (DEV_MODE) document.getElementById("map-devices").click(); DRAWMODE = "select"; document.querySelectorAll('.mtool[data-mode]').forEach(x => x.classList.toggle("active", x.dataset.mode === "select")); }
    else CLEAN_SEL.clear();
    drawFloorplan(); renderMapPanel();
  };
  const afBtn = document.getElementById("map-autofit");
  if (afBtn) afBtn.onclick = async () => {
    if (!confirm("Auto-fit ALL rooms to the mapped walls?\n\nThis replaces every room's shape with one traced from the real map (non-overlapping, out-window bits trimmed). You can then refine each by hand. Applied on the footer Save.")) return;
    _pushUndo();
    afBtn.disabled = true; const old = afBtn.textContent; afBtn.textContent = "⏳ Fitting…";
    const n = await autoFitRooms();
    afBtn.disabled = false; afBtn.textContent = old;
    if (n === -1) { alert("Auto-fit needs the map underlay + calibration. Turn on 🖼 Map first."); return; }
    drawFloorplan(); renderMapPanel();
  };
  const fa = document.getElementById("room-filter-all"), fn = document.getElementById("room-filter-none");
  if (fa) fa.onclick = e => { e.preventDefault(); FILTER.clear(); drawFloorplan(); renderRoomList(); };
  if (fn) fn.onclick = e => { e.preventDefault(); MAP.rooms.forEach(r => FILTER.add(String(r.room_id))); drawFloorplan(); renderRoomList(); };
  wireMapImageTools();
}

function _syncMapImgUI() {
  const o = _mapImgCfg(), on = !!o.on;
  document.getElementById("map-img-toggle").classList.toggle("active", on);
  document.getElementById("map-img-align").style.display = on ? "" : "none";
  document.getElementById("map-img-strip").style.display = on ? "flex" : "none";
  const op = document.getElementById("mis-opacity"); if (op) op.value = _ulCfg().opacity != null ? _ulCfg().opacity : 0.5;
  const srcBtn = document.getElementById("mis-src");
  if (srcBtn) {
    srcBtn.style.display = USERPLAN_DATA ? "" : "none";
    srcBtn.textContent = o.src === "user" ? "📄 Your plan" : "🤖 Robot map";
    srcBtn.title = "Switch the underlay between the robot's map and your uploaded plan";
  }
}

function wireMapImageTools() {
  const o = _mapImgCfg();
  _syncMapImgUI();
  loadUserPlan().then(ok => { if (ok) _syncMapImgUI(); });
  document.getElementById("map-img-toggle").onclick = async () => {
    o.on = !o.on; MAP_DIRTY = true;
    if (o.on && !MAPIMG_DATA) await loadMapImage();
    if (o.on) seedMapImg();
    if (!o.on && DRAWMODE === "imgalign") {   // leaving underlay: drop out of align mode
      DRAWMODE = "select";
      document.querySelectorAll('.mtool[data-mode]').forEach(x => x.classList.toggle("active", x.dataset.mode === "select"));
    }
    _syncMapImgUI(); drawFloorplan();
  };
  const bump = (fn) => { fn(_ulCfg()); MAP_DIRTY = true; drawFloorplan(); };
  document.getElementById("mis-opacity").oninput = e => bump(o => { o.opacity = +e.target.value; });
  document.getElementById("mis-scale-up").onclick = () => bump(o => { o.scale *= 1.05; });
  document.getElementById("mis-scale-down").onclick = () => bump(o => { o.scale *= 0.95; });
  document.getElementById("mis-rot-cw").onclick = () => bump(o => { o.rot = (o.rot || 0) + 2; });
  document.getElementById("mis-rot-ccw").onclick = () => bump(o => { o.rot = (o.rot || 0) - 2; });
  document.getElementById("mis-flip").onclick = () => bump(o => { o.flip = !o.flip; });
  const autofit = document.getElementById("mis-autofit");
  const syncAutofit = () => {
    const hasCalib = Array.isArray(MAP && MAP.m && MAP.m.calibration_points) && MAP.m.calibration_points.length >= 3;
    const user = _mapImgCfg().src === "user";
    autofit.disabled = user || !hasCalib;
    autofit.title = user ? "Calibration applies to the robot map — align your plan by hand (✥ Align)"
      : (hasCalib ? "Position the map exactly using the vacuum's calibration" : "No calibration data on this map");
  };
  if (autofit) {
    syncAutofit();
    autofit.onclick = () => bump(o => { if (!calibFit()) alert("No calibration data available for this map."); });
  }
  document.getElementById("mis-reset").onclick = () => bump(o => {
    const op = o.opacity; delete o.cx; delete o.cy; o.rot = 0; o.flip = false; o.opacity = op;
    if (_mapImgCfg().src === "user") seedUserPlan(); else seedMapImg();
  });
  // 📄 upload the real floor plan → second underlay source (traced by hand)
  const upBtn = document.getElementById("mis-upload"), upFile = document.getElementById("mis-upload-file");
  const srcBtn = document.getElementById("mis-src");
  if (upBtn && upFile) {
    upBtn.onclick = () => upFile.click();
    upFile.onchange = async () => {
      const f = upFile.files && upFile.files[0]; upFile.value = "";
      if (!f) return;
      // downscale to a sane size, re-encode as PNG (also normalises HEIC-ish inputs the browser can decode)
      const bmp = await createImageBitmap(f).catch(() => null);
      if (!bmp) { alert("Couldn't read that image."); return; }
      const k = Math.min(1, 2000 / Math.max(bmp.width, bmp.height));
      const c = document.createElement("canvas");
      c.width = Math.round(bmp.width * k); c.height = Math.round(bmp.height * k);
      c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
      const durl = c.toDataURL("image/png");
      try {
        const r = await fetch("api/user_plan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ data_url: durl }) });
        if (!r.ok) throw new Error("HTTP " + r.status);
      } catch (e) { alert("Upload failed: " + e.message); return; }
      USERPLAN_DATA = durl; USERPLAN_NAT = { iw: c.width, ih: c.height };
      const mi2 = _mapImgCfg();
      mi2.src = "user"; mi2.on = true; MAP_DIRTY = true;
      delete _userPlanCfg().cx;                      // reseed the fit for the new image
      seedUserPlan();
      if (autofit) syncAutofit();
      _syncMapImgUI(); drawFloorplan();
      setStatus("Plan uploaded — ✥ Align, then scale/rotate it to match the map", "ok");
    };
  }
  if (srcBtn) srcBtn.onclick = async () => {
    const mi2 = _mapImgCfg();
    if (mi2.src === "user") { mi2.src = "robot"; }
    else {
      if (!(await loadUserPlan())) { alert("No plan uploaded yet — use 📄 Upload plan first."); return; }
      mi2.src = "user"; seedUserPlan();
    }
    MAP_DIRTY = true;
    if (autofit) syncAutofit();
    _syncMapImgUI(); drawFloorplan();
  };
}

function renderZoneList() {
  const el = document.getElementById("zones-list");
  const rows = [];
  const add = (arr, label, cls) => arr.forEach((z, i) => rows.push(
    `<div class="zrow"><span class="zdot ${cls}"></span>${label} ${i + 1}<span class="zc">[${z.map(Math.round).join(", ")}]</span><button class="zdel" data-k="${cls}" data-i="${i}" type="button">✕</button></div>`));
  add(ZONES.walls, "Wall", "fp-wall");
  add(ZONES.zones, "No-go", "fp-nogo");
  add(ZONES.no_mops, "No-mop", "fp-nomop");
  el.innerHTML = rows.length
    ? `<div class="zones-head">${rows.length} item(s) — draw more, or write to the robot</div>` + rows.join("")
    : `<div class="hint">No walls or zones yet — pick a tool above and drag on the map.</div>`;
  el.querySelectorAll(".zdel").forEach(b => b.onclick = () => {
    const key = b.dataset.k === "fp-wall" ? "walls" : (b.dataset.k === "fp-nomop" ? "no_mops" : "zones");
    _pushUndo(); ZONES[key].splice(+b.dataset.i, 1); drawFloorplan(); renderZoneList();
  });
}

async function writeZones() {
  const st = document.getElementById("zones-status");
  const w = ZONES.walls.length, z = ZONES.zones.length, nm = ZONES.no_mops.length;
  if (!confirm(`Write to the robot's map?\n\n• ${w} virtual wall(s)\n• ${z} no-go zone(s)\n• ${nm} no-mop zone(s)\n\nThe current map is BACKED UP first, then these REPLACE all existing walls/zones. The Dreame app + robot will reflect it.`)) return;
  const wbtn = document.getElementById("zones-write"); wbtn.disabled = true;
  st.textContent = "⏳ Backing up map + writing…"; st.className = "hint";
  try {
    const res = await api("api/ha/zones", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ vacuum_entity: MAP.m.vacuum_entity, walls: ZONES.walls, zones: ZONES.zones, no_mops: ZONES.no_mops, backup: true }) });
    st.textContent = `✅ Written to robot's map — ${w} wall(s), ${z} no-go, ${nm} no-mop`
      + (res && res.backed_up ? " · map backed up first" : "");
    st.className = "hint ok";
  } catch (e) {
    st.textContent = "❌ Write failed: " + e.message + " — nothing changed on the robot.";
    st.className = "hint err";
  } finally { wbtn.disabled = false; }
}

// ---- Home: reorderable live card deck ----
let _homeLoaded = false;
const DECK_LS = "ds-deck-layout-v1";
const DECK_REGISTRY = [
  { id: "week",        label: "This week",      icon: "📅" },
  { id: "robot",       label: "Robot status",   icon: "🤖" },
  { id: "presence",    label: "Presence & next run", icon: "🏠" },
  { id: "actions",     label: "Quick actions",  icon: "🎛️" },
  { id: "today",       label: "Today",          icon: "📆" },
  { id: "suggestions", label: "Suggestions",    icon: "💡" },
  { id: "coverage",    label: "Latest coverage",icon: "🗺️" },
  { id: "obstacles",   label: "Obstacles",      icon: "⚠️" },
];
const DECK_DEFAULT = DECK_REGISTRY.map(c => c.id);
const DECK_META = {}; DECK_REGISTRY.forEach(c => DECK_META[c.id] = c);

async function renderHome() {
  const deck = $("#card-deck");
  try { if (!REPORT) REPORT = await api("api/ha/report"); }
  catch (e) { deck.innerHTML = '<div class="hint err">Could not load: ' + esc(e.message) + "</div>"; return; }
  _syncHeaderRobot();
  $("#home-title").textContent = CFG && CFG.title ? CFG.title.trim() : "Overview";
  deck.innerHTML = "";
  DECK_REGISTRY.forEach(c => {
    const card = el("div", "deck-card");
    card.dataset.card = c.id;
    card.innerHTML = '<div class="deck-card-h">' + c.icon + " " + esc(c.label) + "</div>";
    const b = el("div", "deck-card-b");
    const body = _deckBody(c.id);
    if (typeof body === "string") b.innerHTML = body; else b.appendChild(body);
    card.appendChild(b);
    // Click the card (not an inner control) → the rich drill-down in a modal.
    card.addEventListener("click", (e) => {
      if (e.target.closest("button, a, input, label, .deck-cov-img, .deck-cov-big")) return;
      openContentModal(c.icon + " " + c.label, _deckModal(c.id));
    });
    deck.appendChild(card);
  });
  applyDeckLayout();
  // Mark the long list cards as clipped (fade + "tap for more") only when they
  // actually overflow, so short cards don't get a needless fade.
  requestAnimationFrame(() => {
    ["today", "suggestions", "obstacles"].forEach(id => {
      const c = deck.querySelector('[data-card="' + id + '"]'); if (!c) return;
      const b = c.querySelector(".deck-card-b");
      if (b && b.scrollHeight > b.clientHeight + 4) c.classList.add("clipped");
    });
  });
}

// Auto-crop a Dreame coverage render to just the cleaned area.
// The robot's map picture is a tall map that's mostly empty house; the cleaned
// rooms are drawn in saturated colours (blue/green/yellow/red) over the grey.
// We pixel-scan the image (same-origin, so the canvas isn't tainted) for the
// bounding box of coloured pixels and return a cropped data URL. Falls back to
// null (→ show the original) if the canvas is tainted or nothing colourful found.
const _cropCache = {};
function cropToCleaned(url) {
  if (url in _cropCache) return Promise.resolve(_cropCache[url]);
  return new Promise((resolve) => {
    const done = (v) => { _cropCache[url] = v; resolve(v); };
    const im = new Image();
    im.onload = () => {
      try {
        const scale = Math.min(1, 800 / (im.naturalWidth || 800));
        const w = Math.max(1, Math.round((im.naturalWidth || 1) * scale));
        const h = Math.max(1, Math.round((im.naturalHeight || 1) * scale));
        const c = document.createElement("canvas"); c.width = w; c.height = h;
        const ctx = c.getContext("2d");
        ctx.drawImage(im, 0, 0, w, h);
        const data = ctx.getImageData(0, 0, w, h).data;
        // Count coloured (cleaned-room) pixels per column and per row. Using
        // per-axis histograms — rather than a raw min/max bbox — lets us trim
        // sparse strays (legend text, the stats header, a lone dock marker)
        // that would otherwise inflate the crop, so the box hugs the area the
        // robot actually covered.
        const colCount = new Uint32Array(w), rowCount = new Uint32Array(h);
        let total = 0;
        for (let y = 0; y < h; y++) {
          for (let x = 0; x < w; x++) {
            const i = (y * w + x) * 4;
            if (data[i + 3] < 40) continue;
            const r = data[i], g = data[i + 1], b = data[i + 2];
            const mx = Math.max(r, g, b), mn = Math.min(r, g, b);
            // saturated pixel = a cleaned room; skips grey map, white header, black bg
            if (mx - mn > 28 && mx > 40) { colCount[x]++; rowCount[y]++; total++; }
          }
        }
        if (total < 40) return done(null);
        const bounds = (counts, n) => {
          let mx = 0;
          for (let i = 0; i < n; i++) if (counts[i] > mx) mx = counts[i];
          const thr = Math.max(2, mx * 0.05);   // ignore thin/scattered runs
          let lo = -1, hi = -1;
          for (let i = 0; i < n; i++) if (counts[i] >= thr) { if (lo < 0) lo = i; hi = i; }
          return [lo, hi];
        };
        let [minX, maxX] = bounds(colCount, w);
        let [minY, maxY] = bounds(rowCount, h);
        if (minX < 0 || minY < 0 || maxX <= minX || maxY <= minY) return done(null);
        const padX = (maxX - minX) * 0.06 + 4, padY = (maxY - minY) * 0.06 + 4;
        minX = Math.max(0, Math.floor(minX - padX));
        minY = Math.max(0, Math.floor(minY - padY));
        maxX = Math.min(w, Math.ceil(maxX + padX));
        maxY = Math.min(h, Math.ceil(maxY + padY));
        const cw = maxX - minX, ch = maxY - minY;
        const out = document.createElement("canvas"); out.width = cw; out.height = ch;
        out.getContext("2d").drawImage(c, minX, minY, cw, ch, 0, 0, cw, ch);
        done(out.toDataURL("image/png"));
      } catch (e) { done(null); }
    };
    im.onerror = () => done(null);
    im.src = url;
  });
}

// ---- device layer: browse HA areas/entities, place, render, control ----
async function loadDeviceAreas() {
  if (DEV_AREAS) return;
  try {
    const j = await api("api/ha/areas");
    DEV_AREAS = j.areas || [];
    DEV_AREAS.forEach(a => (a.entities || []).forEach(e => { DEV_STATE[e.entity_id] = e; }));
  } catch (e) { DEV_AREAS = []; }
}
function renderDeviceList() {
  const el = document.getElementById("device-list"); if (!el) return;
  const q = (document.getElementById("device-search").value || "").trim().toLowerCase();
  const placed = _devices();
  let html = "";
  (DEV_AREAS || []).forEach(a => {
    let ents = (a.entities || []).filter(e => DEV_TOGGLE[e.domain] || ["sensor", "binary_sensor", "climate", "lock", "vacuum", "person", "device_tracker"].includes(e.domain));
    if (q) ents = ents.filter(e => (e.name || e.entity_id).toLowerCase().includes(q) || (a.name || "").toLowerCase().includes(q));
    if (!ents.length) return;
    html += `<div class="dev-area">${esc(a.name || a.area_id)} <span class="hint">${ents.length}</span></div>`;
    ents.slice(0, q ? 60 : 25).forEach(e => {
      const pl = !!placed[e.entity_id], picking = DEV_PLACE && DEV_PLACE.entity_id === e.entity_id;
      html += `<div class="dev-ent${picking ? " picking" : ""}${pl ? " placed" : ""}" data-eid="${esc(e.entity_id)}" data-dom="${esc(e.domain)}">${_devIcon(e.domain)} <span class="dev-ent-n">${esc(e.name || e.entity_id)}</span>${pl ? ' <span class="dev-pin">📍</span>' : ""}</div>`;
    });
  });
  el.innerHTML = html || '<div class="hint">No controllable devices match.</div>';
  el.querySelectorAll(".dev-ent").forEach(row => {
    row.onclick = () => {
      const eid = row.dataset.eid;
      if (_devices()[eid]) { delete _devices()[eid]; MAP_DIRTY = true; DEV_PLACE = null; }  // click a placed one → remove
      else DEV_PLACE = { entity_id: eid, name: (DEV_STATE[eid] || {}).name || eid, domain: row.dataset.dom };
      const h = document.getElementById("device-hint");
      if (h) h.innerHTML = DEV_PLACE ? `Placing <b>${esc(DEV_PLACE.name)}</b> — click on the plan.` : "Removed. Pick a device to place, or tap a placed one to control it.";
      drawFloorplan(); renderDeviceFilter(); renderDeviceList();
    };
  });
}
function _devLabel(st) {
  if (!st || st.state == null) return "";
  if (["sensor", "climate"].includes(st.domain)) return String(st.state).length <= 8 ? st.state : "";
  return "";
}
// Filter which placed pins show on the map, by device type (so you can e.g. see
// just the lights to toggle a bunch).
function renderDeviceFilter() {
  const el = document.getElementById("device-filter"); if (!el) return;
  const doms = [...new Set(Object.keys(_devices()).map(eid => (DEV_STATE[eid] || {}).domain || eid.split(".")[0]))].sort();
  if (doms.length < 2) { el.innerHTML = ""; return; }
  el.innerHTML = `<span class="dev-chip dev-chip-all${DEV_FILTER ? "" : " on"}" data-dom="__all">All</span>` +
    doms.map(dom => `<span class="dev-chip${!DEV_FILTER || DEV_FILTER.has(dom) ? " on" : ""}" data-dom="${esc(dom)}">${_devIcon(dom)} ${esc(dom)}</span>`).join("");
  el.querySelectorAll(".dev-chip").forEach(c => c.onclick = () => {
    const dom = c.dataset.dom;
    if (dom === "__all") DEV_FILTER = null;
    else { if (!DEV_FILTER) DEV_FILTER = new Set(doms); DEV_FILTER.has(dom) ? DEV_FILTER.delete(dom) : DEV_FILTER.add(dom); if (DEV_FILTER.size >= doms.length) DEV_FILTER = null; }
    renderDeviceFilter(); drawFloorplan();
  });
}
// Zoom the map view to a single room (so you can tap its devices up close).
function _zoomToRoom(seg) {
  const r = MAP.byId[seg]; if (!r) return;
  const pad = Math.max(400, (r.x1 - r.x0) * 0.15);
  VIEW = { x: SX(r.x0) - pad, y: SY(r.y1) - pad, w: (r.x1 - r.x0) + 2 * pad, h: (r.y1 - r.y0) + 2 * pad };
  _applyView();
}
function populateZoomSelect() {
  const sel = document.getElementById("device-zoom"); if (!sel || !MAP) return;
  sel.innerHTML = `<option value="">Whole map</option>` + MAP.rooms.map(r => {
    const seg = String(r.room_id); return `<option value="${esc(seg)}">${esc(r.custom_name || r.name || ("Room " + seg))}</option>`;
  }).join("");
  sel.onchange = () => { sel.value ? _zoomToRoom(sel.value) : _fitView(); };
}
function _renderDevices() {
  if (!_studioOn()) return "";                        // device layer is a studio feature
  let s = "";
  const placed = _devices();
  const rr = Math.max(110, Math.round(Math.min(MAP.W, MAP.H) / 42));
  Object.entries(placed).forEach(([eid, pos]) => {
    if (!pos || typeof pos.x !== "number") return;
    const st = DEV_STATE[eid] || { domain: eid.split(".")[0] };
    if (DEV_FILTER && !DEV_FILTER.has(st.domain)) return;   // device-type filter
    const on = _DEV_ON.includes(String(st.state).toLowerCase());
    const cx = SX(pos.x), cy = SY(pos.y);
    s += `<g class="fp-dev ${on ? "on" : "off"}" data-dev="${esc(eid)}"><circle cx="${cx}" cy="${cy}" r="${rr}"/>`
      + `<text x="${cx}" y="${cy}" font-size="${Math.round(rr * 1.15)}" class="fp-icon">${_devIcon(st.domain)}</text></g>`;
    const lbl = _devLabel(st);
    if (lbl) s += `<text x="${cx}" y="${cy + rr * 1.95}" font-size="${Math.round(rr * 0.85)}" class="fp-dev-lbl">${esc(lbl)}</text>`;
  });
  return s;
}
async function _refreshDevice(eid) {
  try { const s = await api("api/ha/state?entity_id=" + encodeURIComponent(eid)); if (s && s.state != null) { DEV_STATE[eid] = { ...(DEV_STATE[eid] || {}), state: s.state }; drawFloorplan(); } } catch (e) {}
}
async function _toggleDevice(eid) {
  const st = DEV_STATE[eid] || {}, dom = st.domain || eid.split(".")[0];
  let call = DEV_TOGGLE[dom];
  if (dom === "lock") call = ["lock", String(st.state).toLowerCase() === "locked" ? "unlock" : "lock"];
  if (dom === "vacuum") call = ["vacuum", String(st.state).toLowerCase() === "cleaning" ? "pause" : "start"];
  if (!call) { alert(`${st.name || eid}\n\n${st.state != null ? st.state : "—"}`); return; }
  // a stray tap must not silently unlock a door or open a cover
  if ((dom === "lock" || dom === "cover") &&
      !confirm(`${call[1] === "unlock" ? "Unlock" : call[1] === "lock" ? "Lock" : "Toggle"} ${st.name || eid}?`)) return;
  try {
    await api("api/ha/service", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ domain: call[0], service: call[1], entity_id: eid }) });
    setTimeout(() => _refreshDevice(eid), 700);
  } catch (e) { alert("Control failed: " + e.message); }
}

function _deckBody(id) {
  const R = REPORT || {};
  if (id === "week") {
    const t = R.totals || {};
    return '<div class="deck-chips">' +
      '<span class="rsum st-ok">🟢 <b>' + (t.cleaned || 0) + "</b> cleaned</span>" +
      '<span class="rsum st-bad">🔴 <b>' + (t.skipped || 0) + "</b> missed</span>" +
      '<span class="rsum st-pend">🟡 <b>' + (t.pending || 0) + "</b> pending</span></div>";
  }
  if (id === "robot") {
    const r = R.robot || {};
    if (!r.vacuum_entity) return '<div class="hint">No robot connected.</div>';
    const cap = (s) => s ? String(s).replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase()) : "—";
    const bat = (r.battery != null) ? Math.round(r.battery) + "%" : "—";
    const batIcon = r.battery == null ? "🔋" : (r.battery < 20 ? "🪫" : "🔋");
    const errNorm = r.error ? String(r.error).toLowerCase().replace(/_/g, " ").trim() : "";
    const err = errNorm && !["none", "no error", "0", "ok"].includes(errNorm) ? r.error : null;
    let h = '<div class="deck-rob-top"><span class="rob-state">🧭 ' + esc(cap(r.status || r.state)) +
      '</span><span class="rob-bat">' + batIcon + " " + esc(bat) + "</span></div>";
    if (r.current_room) h += '<div class="deck-row">📍 In <b>' + esc(r.current_room) + "</b></div>";
    if (r.cleaned_area) h += '<div class="deck-row">🧹 ' + esc(r.cleaned_area) + " m² cleaned</div>";
    if (err) h += '<div class="deck-row st-bad">⚠️ ' + esc(cap(err)) + "</div>";
    const chip = (lbl, v) => v ? '<span class="rob-chip">' + lbl + " " + esc(cap(v)) + "</span>" : "";
    const chips = chip("🗑️", r.dust_bag) + chip("💧", r.clean_water) + chip("🪣", r.dirty_water);
    if (chips) h += '<div class="deck-chips rob-chips">' + chips + "</div>";
    return h;
  }
  if (id === "presence") {
    const s = R.schedule || {};
    let h = "";
    if (s.presence_configured) {
      const home = s.presence_home;
      h += '<div class="deck-rob-top"><span class="rob-state">' +
        (home === true ? "🏠 Someone's home" : home === false ? "🚶 Everyone's out" : "❓ Presence unknown") +
        "</span></div>";
    } else {
      h += '<div class="deck-row hint">No presence entities set — runs aren\'t presence-gated.</div>';
    }
    if (s.next_run_day) {
      h += '<div class="deck-row">⏰ Next run <b>' + esc(s.next_run_day) + "</b> at <b>" + esc(s.next_run_time) + "</b></div>";
    } else {
      h += '<div class="deck-row hint">No upcoming scheduled run — enable rooms &amp; days.</div>';
    }
    if (s.require_away && s.presence_home === true) {
      h += '<div class="deck-row st-pend">⏸️ Waiting until everyone\'s out before it runs.</div>';
    }
    if (s.window && s.window.enabled) {
      h += '<div class="deck-row">🕑 Only runs ' + esc(s.window.start) + "–" + esc(s.window.end) + "</div>";
    }
    if (s.catchup && s.catchup.enabled && s.catchup.day) {
      h += '<div class="deck-row">🧺 Catch-up missed rooms <b>' + esc(s.catchup.day) + "</b>" +
        (s.catchup.time ? " at " + esc(s.catchup.time) : "") + "</div>";
    }
    return h;
  }
  if (id === "actions") {
    return '<div class="deck-actions">' +
      '<button class="btn ghost" data-action="run_scheduled_now">▶ Run today</button>' +
      '<button class="btn ghost" data-action="run_catchup_now">🧹 Clean pending</button>' +
      '<button class="btn ghost" data-action="run_catchup_now" data-quiet="1">🔉 Quiet</button>' +
      '<button class="btn ghost" data-action="reset_week">↺ Reset week</button></div>';
  }
  if (id === "today") {
    const t = (R.rooms || []).filter(r => r.scheduled_today);
    if (!t.length) return '<div class="hint">Nothing scheduled today.</div>';
    return t.map(r => {
      const m = STATUS_META[r.status] || STATUS_META.pending;
      return '<div class="deck-row"><span class="rbadge ' + m.cls + '">' + m.dot + "</span> " + esc(r.name) + "</div>";
    }).join("");
  }
  if (id === "suggestions") {
    const s = R.suggestions || [];
    if (!s.length) return '<div class="hint">No suggestions yet — they build up from cleaning history.</div>';
    return s.map(x => '<div class="deck-sug">' + (x.type === "recurring_fail" ? "🔴" : "📅") + " " + esc(x.message) + "</div>").join("");
  }
  if (id === "coverage") {
    const runs = (R.coverage || {}).runs || [];
    if (!runs.length) return '<div class="hint">No coverage yet.</div>';
    const run = runs[0];
    const wrap = el("div", "deck-cov");
    const img = el("img", "deck-cov-img"); img.src = run.url; img.alt = run.label; img.loading = "lazy";
    img.title = "Click to enlarge";
    // Zoom the thumbnail onto just the cleaned rooms; click still shows full map.
    cropToCleaned(run.url).then((cropped) => { if (cropped) img.src = cropped; });
    img.addEventListener("click", () => openLightbox(run.url, run.label));
    const bar = el("div", "deck-cov-bar");
    const cap = el("span", "deck-cov-cap"); cap.textContent = run.label;
    const big = el("button", "deck-cov-big"); big.textContent = "⤢ Bigger";
    big.addEventListener("click", () => {
      wrap.classList.toggle("big");
      big.textContent = wrap.classList.contains("big") ? "⤡ Smaller" : "⤢ Bigger";
    });
    bar.appendChild(cap); bar.appendChild(big);
    wrap.appendChild(img); wrap.appendChild(bar);
    return wrap;
  }
  if (id === "obstacles") {
    const obs = R.obstacles || [];
    if (!obs.length) return '<div class="hint">None flagged. 🎉</div>';
    return obs.slice(0, 4).map(o =>
      '<div class="deck-row">' + (o.type === "Blocked Room" ? "🚪" : "⚠️") + " <b>" + esc(o.room || "somewhere") +
      '</b> <span class="hint">' + esc(o.type || "") + "</span></div>").join("");
  }
  return "";
}

// Full drill-down shown when a deck card is tapped — the rich data *behind*
// the summary card, not a copy of it.
function capWords(s) { return s ? String(s).replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase()) : "—"; }

// Live robot chip in the header — filled whenever a fresh report lands.
function _syncHeaderRobot() {
  const el = document.getElementById("hdr-robot");
  const rb = REPORT && REPORT.robot;
  if (!el || !rb) return;
  const state = String(rb.status || rb.vacuum_state || "").toLowerCase();
  const err = state === "error" || (rb.error && String(rb.error).toLowerCase() !== "no_error");
  const busy = /clean|return|paused|washing|drying|empt/.test(state);
  el.className = "hdr-robot " + (err ? "err" : (busy ? "busy" : "on"));
  el.innerHTML = `<span class="dot"></span><b>${esc(capWords(state) || "Idle")}</b>` +
    (rb.battery != null ? ` · ${esc(String(rb.battery))}%` : "");
  el.title = "Robot right now" + (rb.current_room ? ` — in ${rb.current_room}` : "");
  el.style.display = "inline-flex";
}
function fmtWhen(iso) {
  try { const d = new Date(iso); if (isNaN(d)) return ""; return d.toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" }); }
  catch (e) { return ""; }
}
function _dl(rows) {
  return '<div class="md-dl">' + rows.filter(Boolean).map(
    ([k, v]) => '<div class="md-dt">' + esc(k) + '</div><div class="md-dd">' + v + "</div>").join("") + "</div>";
}
function _roomRow(r) {
  const m = STATUS_META[r.status] || STATUS_META.pending;
  const days = (r.day_names && r.day_names.length) ? r.day_names.join(" ") : "—";
  const ms = [r.mode, r.suction].filter(v => v && v !== "(default)").map(capWords).join(" · ") || "Default";
  let extra = "";
  if (r.status === "cleaned" && r.cleaned_at) { const w = fmtWhen(r.cleaned_at); if (w) extra = ' · <span class="st-ok">✅ ' + esc(w) + "</span>"; }
  else if (r.status === "skipped") extra = ' · <span class="st-bad">missed ' + (Number(r.fail_count) || 0) + "×" + (r.blocked_reason ? " (" + esc(r.blocked_reason) + ")" : "") + "</span>";
  return '<div class="md-room"><span class="rbadge ' + m.cls + '">' + m.dot + "</span>" +
    '<span class="md-room-n">' + esc(r.name) + "</span>" +
    '<span class="md-room-meta">' + esc(days) + " · " + ms + extra + "</span></div>";
}

function _deckModal(id) {
  const R = REPORT || {};
  const box = el("div", "deck-card-b cm-full md-rich");

  if (id === "robot") {
    const r = R.robot || {};
    if (!r.vacuum_entity) { box.innerHTML = '<div class="hint">No robot connected.</div>'; return box; }
    const errNorm = r.error ? String(r.error).toLowerCase().replace(/_/g, " ").trim() : "";
    const realErr = errNorm && !["none", "no error", "0", "ok"].includes(errNorm);
    box.innerHTML = _dl([
      ["State", esc(capWords(r.state))],
      ["Status", esc(capWords(r.status))],
      ["Task", esc(capWords(r.task_status))],
      ["Battery", r.battery != null ? Math.round(r.battery) + "%" : "—"],
      ["Current room", esc(r.current_room || "—")],
      ["Cleaned area today", r.cleaned_area ? esc(r.cleaned_area) + " m²" : "—"],
      ["Error", '<span class="' + (realErr ? "st-bad" : "st-ok") + '">' + (realErr ? esc(capWords(r.error)) : "None") + "</span>"],
      ["Dust bag", esc(capWords(r.dust_bag))],
      ["Clean-water tank", esc(capWords(r.clean_water))],
      ["Dirty-water tank", esc(capWords(r.dirty_water))],
      ["Vacuum entity", "<code>" + esc(r.vacuum_entity) + "</code>"],
    ]);
    return box;
  }

  if (id === "presence") {
    const s = R.schedule || {};
    const w = s.window || {}, cu = s.catchup || {};
    const homeTxt = !s.presence_configured ? "Not configured — runs aren't presence-gated" :
      s.presence_home === true ? "🏠 Someone's home" : s.presence_home === false ? "🚶 Everyone's out" : "❓ Unknown";
    box.innerHTML = _dl([
      ["Presence", esc(homeTxt)],
      ["Presence-gated", s.require_away ? "Yes — only runs when everyone's out" : "No"],
      ["Next scheduled run", s.next_run_day ? esc(s.next_run_day + " at " + s.next_run_time) : "—"],
      ["Daily start time", esc(s.daily_time || "—")],
      ["Time window", w.enabled ? esc((w.start || "?") + " – " + (w.end || "?")) : "Off"],
      ["Weekly catch-up", cu.enabled ? esc((cu.day || "?") + (cu.time ? " at " + cu.time : "")) : "Off"],
    ]);
    return box;
  }

  if (id === "week") {
    const rooms = R.rooms || [];
    const t = R.totals || {};
    let h = '<div class="deck-chips">' +
      '<span class="rsum st-ok">🟢 <b>' + (t.cleaned || 0) + "</b> cleaned</span>" +
      '<span class="rsum st-bad">🔴 <b>' + (t.skipped || 0) + "</b> missed</span>" +
      '<span class="rsum st-pend">🟡 <b>' + (t.pending || 0) + "</b> pending</span></div>";
    const sections = [["cleaned", "Cleaned"], ["skipped", "Missed"], ["pending", "Pending"], ["not_scheduled", "Not scheduled"]];
    sections.forEach(([st, title]) => {
      const rs = rooms.filter(r => r.status === st);
      if (!rs.length) return;
      h += '<div class="md-sec">' + title + " (" + rs.length + ")</div>" + rs.map(_roomRow).join("");
    });
    box.innerHTML = h;
    return box;
  }

  if (id === "today") {
    const rs = (R.rooms || []).filter(r => r.scheduled_today);
    box.innerHTML = rs.length ? rs.map(_roomRow).join("") : '<div class="hint">Nothing scheduled today.</div>';
    return box;
  }

  if (id === "suggestions") {
    const s = R.suggestions || [];
    let h = s.length
      ? s.map(x => '<div class="deck-sug">' + (x.type === "recurring_fail" ? "🔴" : "📅") + " " + esc(x.message) + "</div>").join("")
      : '<div class="hint">No suggestions yet — they build up from cleaning history.</div>';
    const flaky = (R.rooms || []).filter(r => (r.fail_streak || 0) >= 1);
    if (flaky.length) {
      h += '<div class="md-sec">Rooms with recent misses</div>' + flaky.map(r =>
        '<div class="md-room"><span class="md-room-n">' + esc(r.name) + "</span>" +
        '<span class="md-room-meta st-bad">' + (Number(r.fail_streak) || 0) + "× in a row" +
        (r.suggested_day ? ' · <span class="st-ok">try ' + esc(r.suggested_day) + "</span>" : "") + "</span></div>").join("");
    }
    box.innerHTML = h;
    return box;
  }

  if (id === "coverage") {
    const runs = (R.coverage || {}).runs || [];
    if (!runs.length) { box.innerHTML = '<div class="hint">No coverage yet.</div>'; return box; }
    const run = runs[0];
    const fig = el("img", "md-cov-img"); fig.src = run.url; fig.alt = run.label; fig.loading = "lazy";
    fig.title = "Click to enlarge";
    fig.addEventListener("click", () => openLightbox(run.url, run.label));
    box.appendChild(fig);
    const cap = el("div", "md-cov-cap"); cap.textContent = run.label; box.appendChild(cap);
    if (runs.length > 1) {
      const h = el("div", "md-sec"); h.textContent = "Recent runs"; box.appendChild(h);
      runs.forEach(rn => {
        const row = el("div", "md-run");
        row.innerHTML = (rn.status === "completed" ? "✅" : rn.status === "interrupted" ? "🟠" : "•") + " " + esc(rn.label);
        row.addEventListener("click", () => openLightbox(rn.url, rn.label));
        box.appendChild(row);
      });
    }
    return box;
  }

  if (id === "obstacles") {
    const obs = R.obstacles || [];
    if (!obs.length) { box.innerHTML = '<div class="hint">None flagged. 🎉</div>'; return box; }
    obs.forEach(o => {
      const row = el("div", "md-obs");
      if (o.picture_url) {
        const im = el("img", "md-obs-img"); im.src = o.picture_url; im.loading = "lazy";
        im.addEventListener("click", () => openLightbox(o.picture_url, o.room || "obstacle"));
        row.appendChild(im);
      }
      const info = el("div", "md-obs-info");
      info.innerHTML = '<div class="md-obs-t">' + (o.type === "Blocked Room" ? "🚪" : "⚠️") +
        " <b>" + esc(o.room || "somewhere") + '</b> <span class="hint">' + esc(o.type || "") + "</span></div>" +
        (o.reason ? '<div class="hint">' + esc(o.reason) + "</div>" : "");
      row.appendChild(info);
      box.appendChild(row);
    });
    return box;
  }

  // actions (and any fallback) — nothing richer to show than the card itself.
  const b = _deckBody(id);
  if (typeof b === "string") box.innerHTML = b; else box.appendChild(b);
  return box;
}

// --- layout persistence (per-browser) + apply ---
function _deckLoad() {
  try { const o = JSON.parse(localStorage.getItem(DECK_LS)); if (o && typeof o === "object") return { order: o.order || [], hidden: o.hidden || [] }; } catch (e) {}
  return null;
}
function _deckSave(order, hidden) { try { localStorage.setItem(DECK_LS, JSON.stringify({ order, hidden })); } catch (e) {} }

function applyDeckLayout() {
  const deck = $("#card-deck"); if (!deck) return;
  const saved = _deckLoad();
  const present = Array.from(deck.querySelectorAll("[data-card]")).map(e => e.dataset.card);
  const order = []; const seen = {};
  const add = id => { if (present.includes(id) && !seen[id]) { order.push(id); seen[id] = 1; } };
  if (saved) (saved.order || []).forEach(add);
  DECK_DEFAULT.forEach(add);
  present.forEach(add);
  order.forEach(id => { const c = deck.querySelector('[data-card="' + id + '"]'); if (c) deck.appendChild(c); });
  const hidden = new Set((saved && saved.hidden) || []);
  present.forEach(id => { const c = deck.querySelector('[data-card="' + id + '"]'); if (c) c.style.display = hidden.has(id) ? "none" : ""; });
}

// --- customize panel (toggle + drag reorder) ---
let _deckPanel = null;
function openDeckCustomize() {
  if (!_deckPanel) _deckPanel = _buildDeckPanel();
  _deckRenderRows();
  _deckPanel.classList.add("show");
}
function _buildDeckPanel() {
  const p = el("div", "lightbox deck-panel"); p.id = "deck-panel";
  p.innerHTML = '<div class="lb-inner dp-inner"><div class="dp-head"><b>⚙️ Customize dashboard</b>' +
    '<button class="lb-close" aria-label="Close">✕</button></div>' +
    '<p class="hint">Toggle cards and drag ☰ to reorder. Saved in this browser.</p>' +
    '<div class="dp-list"></div>' +
    '<div class="dp-foot"><button class="btn ghost dp-reset">Reset</button><button class="btn primary dp-done">Done</button></div></div>';
  document.body.appendChild(p);
  p.addEventListener("click", (e) => { if (e.target === p || e.target.closest(".lb-close") || e.target.closest(".dp-done")) p.classList.remove("show"); });
  p.querySelector(".dp-reset").addEventListener("click", () => { try { localStorage.removeItem(DECK_LS); } catch (e) {} applyDeckLayout(); _deckRenderRows(); });
  const list = p.querySelector(".dp-list");
  list.addEventListener("dragover", (e) => {
    e.preventDefault();
    const dragging = list.querySelector(".dragging"); if (!dragging) return;
    const after = _deckRowAfter(list, e.clientY);
    if (after == null) list.appendChild(dragging); else list.insertBefore(dragging, after);
  });
  return p;
}
function _deckRowAfter(list, y) {
  const rows = Array.from(list.querySelectorAll(".dp-row:not(.dragging)"));
  let closest = null, off = -Infinity;
  rows.forEach(row => { const b = row.getBoundingClientRect(); const o = y - b.top - b.height / 2; if (o < 0 && o > off) { off = o; closest = row; } });
  return closest;
}
function _deckRenderRows() {
  const deck = $("#card-deck");
  const list = _deckPanel.querySelector(".dp-list");
  list.innerHTML = "";
  const order = Array.from(deck.querySelectorAll("[data-card]")).map(e => e.dataset.card);
  order.forEach(id => {
    const m = DECK_META[id] || { label: id, icon: "▫️" };
    const c = deck.querySelector('[data-card="' + id + '"]');
    const visible = c && c.style.display !== "none";
    const row = el("div", "dp-row"); row.dataset.id = id; row.draggable = true;
    row.innerHTML = '<span class="dp-grip">☰</span><span class="dp-ico">' + m.icon + '</span><span class="dp-name">' + esc(m.label) + "</span>" +
      '<label class="switch"><input type="checkbox" class="dp-toggle"' + (visible ? " checked" : "") + '><span class="slider"></span></label>';
    row.addEventListener("dragstart", () => row.classList.add("dragging"));
    row.addEventListener("dragend", () => { row.classList.remove("dragging"); _deckPersist(); });
    row.querySelector(".dp-toggle").addEventListener("change", _deckPersist);
    list.appendChild(row);
  });
}
function _deckPersist() {
  const list = _deckPanel.querySelector(".dp-list");
  const order = [], hidden = [];
  list.querySelectorAll(".dp-row").forEach(row => {
    order.push(row.dataset.id);
    if (!row.querySelector(".dp-toggle").checked) hidden.push(row.dataset.id);
  });
  _deckSave(order, hidden);
  applyDeckLayout();
}

// ---- Dashboard tab: generate copy-paste native Lovelace cards ----
let _dashboardLoaded = false;
function renderDashboard() {
  const box = $("#dash-cards");
  // Prefer the integration's reported ids; else derive them from the title the
  // way HA slugifies has_entity_name entities. Works for ANY user's vacuum.
  const ents = (CFG && CFG.scheduler_entities) || {};
  const slug = s => String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  const eid = (domain, name) => domain + "." + slug((CFG.title || "") + " " + name);
  const S = ents.status || eid("sensor", "Status");
  const RC = ents.rooms_cleaned || eid("sensor", "Rooms cleaned this week");
  const SW = ents.enabled || eid("switch", "Scheduler enabled");
  const B_TODAY = ents.run_today || eid("button", "Run today's schedule now");
  const B_NOW = ents.clean_now || eid("button", "Clean now");
  const B_QUIET = ents.clean_now_quiet || eid("button", "Clean now (quiet)");
  const B_RESET = ents.reset_week || eid("button", "Reset week counters");
  const VAC = CFG.vacuum, CAM = CFG.map_camera || ("camera." + CFG.prefix + "_map");

  const cards = [];
  cards.push({ id: "custom", title: "⭐ Dreame Scheduler card (GUI picker)", excludeFromView: true,
    desc: "A richer all-in-one card that shows in HA's “Add card” picker. Install once: copy dreame-scheduler-card.js into /config/www/, add /local/dreame-scheduler-card.js as a resource (Settings → Dashboards → ⋮ → Resources → type: JavaScript Module), then Add card → “Dreame Scheduler”. Or paste this into a Manual card:", yaml:
`type: custom:dreame-scheduler-card
entity: ${S}` });
  cards.push({ id: "robot-card", title: "🤖 Robot status card (GUI picker)", excludeFromView: true,
    desc: "Live robot state (battery / what it's doing / error / tanks). Same install as above; then Add card → “Dreame Robot Status”. Or paste into a Manual card:", yaml:
`type: custom:dreame-robot-card
entity: ${S}` });
  cards.push({ id: "presence-card", title: "🏠 Presence & next-run card (GUI picker)", excludeFromView: true,
    desc: "Who's home + when the scheduler next runs. Then Add card → “Dreame Presence & Next Run”. Or paste into a Manual card:", yaml:
`type: custom:dreame-presence-card
entity: ${S}` });
  cards.push({ id: "status", title: "Status summary", excludeFromView: false,
    desc: "One-glance markdown: this-week summary + current state + last run.", yaml:
`type: markdown
title: 🧹 Vacuum — this week
content: |
  {{ state_attr('${S}','week_summary') or 'No runs yet this week.' }}

  **Now:** {{ states('${S}') }} — {{ state_attr('${S}','reason') }}
  {%- set lr = state_attr('${S}','last_run') %}
  {%- if lr %}

  **Last run:** {{ (lr.cleaned | join(', ')) if lr.cleaned else '—' }}{% if lr.skipped %} · missed {{ lr.skipped | join(', ') }}{% endif %}
  {%- endif %}` });

  cards.push({ id: "actions", title: "Controls & actions",
    desc: "Enable toggle, run/clean/reset buttons, and rooms-cleaned count.", yaml:
`type: entities
title: Vacuum scheduler
entities:
  - entity: ${SW}
    name: Scheduler enabled
  - entity: ${B_TODAY}
    name: Run today's schedule
  - entity: ${B_NOW}
    name: Clean pending now
  - entity: ${B_QUIET}
    name: Clean now (quiet)
  - entity: ${B_RESET}
    name: Reset week
  - entity: ${RC}
    name: Rooms cleaned this week` });

  cards.push({ id: "vacuum", title: "Vacuum control",
    desc: "Native tile — start / pause / dock.", yaml:
`type: tile
entity: ${VAC}
features:
  - type: vacuum-commands
    commands:
      - start
      - pause
      - return_home` });

  cards.push({ id: "map", title: "Live map",
    desc: "The robot's map camera.", yaml:
`type: picture-entity
entity: ${CAM}
name: Vacuum map
show_state: false
camera_view: auto` });

  cards.push({ id: "panel", title: "Embed the scheduler UI", excludeFromView: true,
    desc: "Embeds this whole add-on in a card. Experimental — some setups block nested ingress iframes.", yaml:
`type: iframe
url: /local_dreame_scheduler
aspect_ratio: 80%` });

  const indent = (yaml) => yaml.split("\n").map((l, i) => (i === 0 ? "  - " : "    ") + l).join("\n");
  const viewYaml =
`title: Vacuum
path: vacuum
icon: mdi:robot-vacuum-variant
cards:
${cards.filter(c => !c.excludeFromView).map(c => indent(c.yaml)).join("\n")}`;

  box.innerHTML = "";
  cards.forEach(c => box.appendChild(_dashCardEl(c.title, c.desc, c.yaml)));
  box.appendChild(_dashCardEl("Whole dashboard view",
    "All cards as one view — paste under views: in a dashboard's Raw configuration editor (⋮ → Edit dashboard → ⋮ → Raw configuration editor).",
    viewYaml));
}

function _dashCardEl(title, desc, yaml) {
  const wrap = el("div", "dash-block");
  const head = el("div", "dash-head");
  head.innerHTML = "<div><b>" + esc(title) + '</b><div class="hint">' + esc(desc) + "</div></div>";
  const copy = el("button", "btn ghost dash-copy"); copy.textContent = "Copy";
  copy.addEventListener("click", () => {
    copyText(yaml)
      .then(() => { copy.textContent = "✓ Copied"; setTimeout(() => { copy.textContent = "Copy"; }, 1500); })
      .catch(() => setStatus("Copy failed — select the YAML manually", "err"));
  });
  head.appendChild(copy);
  const pre = el("pre", "dash-yaml"); pre.textContent = yaml;
  wrap.appendChild(head); wrap.appendChild(pre);
  return wrap;
}

// ---- Report tab ----
let _reportLoaded = false, REPORT = null, _reportScope = "week";
const STATUS_META = {
  cleaned:       { dot: "🟢", label: "Cleaned",  cls: "st-ok" },
  skipped:       { dot: "🔴", label: "Missed",   cls: "st-bad" },
  pending:       { dot: "🟡", label: "Pending",  cls: "st-pend" },
  not_scheduled: { dot: "⚪", label: "Off",      cls: "st-off" },
};

async function loadReport() {
  const box = $("#report-rooms");
  try {
    REPORT = await api("api/ha/report");
    _syncHeaderRobot();
    if (!REPORT || REPORT.found === false) {
      box.innerHTML = '<div class="hint">No report available yet.</div>';
      return;
    }
    renderReport();
  } catch (e) {
    _reportLoaded = false;                            // let re-opening the tab retry
    box.innerHTML = '<div class="hint err">Could not load report: ' + esc(e.message) +
      ' <a href="#" id="rep-retry">Retry</a></div>';
    const rl = document.getElementById("rep-retry");
    if (rl) rl.onclick = ev => { ev.preventDefault(); _reportLoaded = true; loadReport(); };
  }
}

function _isToday(iso) {
  if (!iso) return false;
  const d = new Date(iso), n = new Date();
  return d.getFullYear() === n.getFullYear() && d.getMonth() === n.getMonth() && d.getDate() === n.getDate();
}

// Effective status for the chosen scope (today narrows "cleaned" to today's date).
function _statusForScope(r) {
  if (_reportScope === "today") {
    if (r.status === "cleaned") return _isToday(r.cleaned_at) ? "cleaned" : "pending";
    if (!r.scheduled_today && r.status !== "skipped") return "not_scheduled";
  }
  return r.status;
}

function renderReport() {
  if (!REPORT) return;
  const rooms = (REPORT.rooms || []);
  const scoped = rooms
    .map(r => ({ r, s: _statusForScope(r) }))
    .filter(x => _reportScope === "week" ? true : (x.s !== "not_scheduled"));

  // summary chips
  const counts = { cleaned: 0, skipped: 0, pending: 0, not_scheduled: 0 };
  scoped.forEach(x => { counts[x.s] = (counts[x.s] || 0) + 1; });
  const sum = $("#report-summary");
  sum.innerHTML = "";
  [["cleaned", "cleaned"], ["skipped", "missed"], ["pending", "pending"]].forEach(([k, lbl]) => {
    const chip = el("span", "rsum " + STATUS_META[k].cls);
    chip.innerHTML = STATUS_META[k].dot + " <b>" + (counts[k] || 0) + "</b> " + lbl;
    sum.appendChild(chip);
  });
  if (REPORT.cleaned_area_today && _reportScope === "today") {
    const a = el("span", "rsum"); a.innerHTML = "📐 <b>" + esc(REPORT.cleaned_area_today) + "</b> m² today";
    sum.appendChild(a);
  }

  // room rows
  const box = $("#report-rooms");
  box.innerHTML = "";
  if (!scoped.length) { box.innerHTML = '<div class="hint">Nothing scheduled for this view.</div>'; return; }
  scoped.forEach(({ r, s }) => {
    const m = STATUS_META[s] || STATUS_META.not_scheduled;
    const row = el("div", "rrow");
    const meta = [];
    if (r.mode) meta.push(esc(r.mode));
    if (r.suction) meta.push(esc(r.suction));
    if (r.day_names && r.day_names.length) meta.push(r.day_names.join(" "));
    let sub = meta.join(" · ");
    if (s === "skipped") {
      sub = '<span class="rreason">✗ ' + esc(r.blocked_reason || "couldn’t reach") +
            (r.fail_count > 1 ? " · " + (Number(r.fail_count) || 0) + "× in a row" : "") + "</span>" + (sub ? " · " + sub : "");
    } else if (s === "cleaned" && r.cleaned_at) {
      sub = '<span class="rok">✓ ' + esc(new Date(r.cleaned_at).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" })) + "</span>" + (sub ? " · " + sub : "");
    }
    row.innerHTML =
      '<span class="rbadge ' + m.cls + '">' + m.dot + " " + m.label + "</span>" +
      '<span class="rname">' + esc(r.name) + "</span>" +
      '<span class="rmeta">' + sub + "</span>";
    box.appendChild(row);
  });

  renderManualClean();
  renderSuggestions();
  renderConsumables();
  renderCoverage();
  renderObstacles();
}

// Maintenance / wear-part panel: each consumable's remaining life + a one-tap
// Reset (presses the robot's own reset button once the part's been replaced).
function renderConsumables() {
  const card = $("#report-consumables-card");
  const box = $("#report-consumables");
  if (!card || !box) return;
  const items = (REPORT && REPORT.robot && REPORT.robot.consumables) || [];
  if (!items.length) { card.style.display = "none"; return; }
  card.style.display = "";
  box.innerHTML = "";
  items.forEach(c => {
    const pct = Math.max(0, Math.min(100, Number(c.percent) || 0));
    const low = !!c.low;
    const row = el("div", "crow" + (low ? " low" : ""));
    row.innerHTML =
      '<span class="cemoji">' + esc(c.emoji || "🔧") + '</span>'
      + '<div class="cmain">'
      + '<div class="cname">' + esc(c.name) + (low ? ' <span class="pill cpill-low">low</span>' : '') + '</div>'
      + '<div class="cbar"><i style="width:' + pct + '%"></i></div>'
      + '</div>'
      + '<span class="cpct">' + pct + '%</span>';
    const btn = el("button", "btn ghost creset");
    btn.type = "button";
    btn.textContent = "Reset";
    if (!c.reset_entity) { btn.disabled = true; btn.title = "No reset control for this part"; }
    btn.addEventListener("click", () => resetConsumable(c, btn));
    row.appendChild(btn);
    box.appendChild(row);
  });
}

async function resetConsumable(c, btn) {
  if (!c.reset_entity) return;
  if (!(await uiConfirm({
    title: "Reset " + c.name + "?",
    message: "Only do this once you've actually replaced the " + c.name.toLowerCase()
      + " — it resets the robot's life counter back to 100%.",
    confirmText: "Reset counter",
  }))) return;
  btn.disabled = true; const was = btn.textContent; btn.textContent = "…";
  try {
    await api("api/ha/service", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ domain: "button", service: "press", entity_id: c.reset_entity }),
    });
    setStatus(c.name + " counter reset ✓", "ok");
    REPORT = null;                 // force a fresh pull so the % updates
    await loadReport();
  } catch (e) {
    btn.disabled = false; btn.textContent = was;
    setStatus("Reset failed: " + (e && e.message ? e.message : e), "err");
  }
}

// Floor-plan data for the obstacle modal (reuse the Map tab's if it's loaded,
// else fetch just what we need).
let _mapData = null;
async function _ensureMapData() {
  if (MAP && MAP.rooms && MAP.rooms.length) {
    return { rooms: MAP.rooms, minX: MAP.minX, minY: MAP.minY, maxX: MAP.maxX, maxY: MAP.maxY };
  }
  if (_mapData) return _mapData;
  let m;
  try { m = await api("api/ha/map?prefix=" + encodeURIComponent(CFG.prefix)); } catch (e) { return null; }
  const rooms = Object.values(m.rooms || {}).filter(r => ["x0", "y0", "x1", "y1"].every(k => typeof r[k] === "number"));
  if (!rooms.length) return null;
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  rooms.forEach(r => { minX = Math.min(minX, r.x0); minY = Math.min(minY, r.y0); maxX = Math.max(maxX, r.x1); maxY = Math.max(maxY, r.y1); });
  const pad = 500;
  _mapData = { rooms, minX: minX - pad, minY: minY - pad, maxX: maxX + pad, maxY: maxY + pad };
  return _mapData;
}

// Show where an obstacle is on the floor plan, in a modal (no tab switch).
async function showObstacleModal(o) {
  const md = await _ensureMapData();
  if (!md) { setStatus("Floor plan not available yet", "err"); return; }
  const sx = x => x - md.minX, sy = y => md.maxY - y;
  const fs = Math.max(240, Math.round(Math.min(md.maxX - md.minX, md.maxY - md.minY) / 26));
  const half = 2600;
  const vb = `${sx(o.x) - half} ${sy(o.y) - half} ${half * 2} ${half * 2}`;
  let svg = `<svg viewBox="${vb}" preserveAspectRatio="xMidYMid meet" class="floorplan">`;
  md.rooms.forEach(r => {
    const x = sx(r.x0), y = sy(r.y1), w = r.x1 - r.x0, h = r.y1 - r.y0;
    const color = _PALETTE[(r.color_index || 0) % _PALETTE.length];
    const name = r.custom_name || r.name || ("Room " + r.room_id);
    const cx = typeof r.x === "number" ? sx(r.x) : x + w / 2;
    const cy = typeof r.y === "number" ? sy(r.y) : y + h / 2;
    svg += `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="80" fill="${color}" fill-opacity="0.13" stroke="${color}" stroke-width="45"/>`
      + `<text x="${cx}" y="${cy}" font-size="${fs}" class="fp-label">${esc(name)}</text>`;
  });
  (REPORT.obstacles || []).forEach(ob => {
    if (ob.x == null || ob.y == null) return;
    const foc = ob.x === o.x && ob.y === o.y;
    const col = ob.type === "Blocked Room" ? "#ef4444" : "#f59e0b";
    svg += `<circle cx="${sx(ob.x)}" cy="${sy(ob.y)}" r="${foc ? 320 : 200}" fill="${col}" fill-opacity="0.9" stroke="#fff" stroke-width="${foc ? 90 : 60}"${foc ? ' class="fp-obstacle foc"' : ""}/>`
      + `<text x="${sx(ob.x)}" y="${sy(ob.y)}" font-size="${fs}" class="fp-icon">!</text>`;
  });
  svg += "</svg>";
  openMapModal(svg, (o.room || "somewhere") + " · " + (o.type || "obstacle") + (o.reason ? " · " + o.reason : ""));
}

// Generic content modal (deck card → full content).
function openContentModal(title, contentNode) {
  let lb = $("#contentmodal");
  if (!lb) {
    lb = el("div", "lightbox"); lb.id = "contentmodal";
    lb.innerHTML = '<div class="lb-inner cm-inner"><div class="cm-head"><b class="cm-title"></b>' +
      '<button class="lb-close" aria-label="Close">✕</button></div><div class="cm-body"></div></div>';
    document.body.appendChild(lb);
    lb.addEventListener("click", (e) => { if (e.target === lb || e.target.closest(".lb-close")) lb.classList.remove("show"); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") lb.classList.remove("show"); });
  }
  lb.querySelector(".cm-title").textContent = title;
  const body = lb.querySelector(".cm-body"); body.innerHTML = "";
  if (typeof contentNode === "string") body.innerHTML = contentNode; else body.appendChild(contentNode);
  // cover-the-screen element: true fullscreen, else the CSS-overlay .map-fs card
  // (ingress path), else body — or the modal opens behind the editor
  const root = document.fullscreenElement || document.querySelector(".card.map-fs") || document.body;
  if (lb.parentNode !== root) root.appendChild(lb);
  lb.classList.add("show");
}

// Styled in-app confirm — replaces the browser-native confirm() so dialogs
// match the app (dark theme, real buttons). Promise-based:
//   if (!(await uiConfirm({title, message}))) return;
// opts: {title, message, confirmText, cancelText (null hides it), danger}
function uiConfirm(opts) {
  opts = opts || {};
  return new Promise(resolve => {
    let lb = $("#confirmmodal");
    if (!lb) {
      lb = el("div", "lightbox"); lb.id = "confirmmodal";
      lb.innerHTML =
        '<div class="lb-inner confirm-inner">' +
        '<div class="confirm-title"></div>' +
        '<div class="confirm-msg"></div>' +
        '<div class="confirm-actions">' +
        '<button class="btn ghost confirm-cancel" type="button"></button>' +
        '<button class="btn primary confirm-ok" type="button"></button>' +
        "</div></div>";
      document.body.appendChild(lb);
    }
    const ok = lb.querySelector(".confirm-ok");
    const cancel = lb.querySelector(".confirm-cancel");
    const onKey = (e) => {
      if (e.key === "Escape") done(false);
      else if (e.key === "Enter") done(true);
    };
    function done(val) {
      lb.classList.remove("show");
      document.removeEventListener("keydown", onKey);
      resolve(val);
    }
    lb.querySelector(".confirm-title").textContent = opts.title || "Are you sure?";
    const msg = lb.querySelector(".confirm-msg");
    msg.textContent = "";                    // render \n as line breaks, text-safe
    String(opts.message || "").split("\n").forEach((line, i) => {
      if (i) msg.appendChild(document.createElement("br"));
      msg.appendChild(document.createTextNode(line));
    });
    ok.textContent = opts.confirmText || "Confirm";
    ok.classList.toggle("danger", !!opts.danger);
    if (opts.cancelText === null) { cancel.style.display = "none"; }
    else { cancel.style.display = ""; cancel.textContent = opts.cancelText || "Cancel"; }
    ok.onclick = () => done(true);
    cancel.onclick = () => done(false);
    lb.onclick = (e) => { if (e.target === lb) done(false); };
    document.addEventListener("keydown", onKey);
    const root = document.fullscreenElement || document.querySelector(".card.map-fs") || document.body;
    if (lb.parentNode !== root) root.appendChild(lb);
    lb.classList.add("show");
    setTimeout(() => ok.focus(), 30);
  });
}

// Styled alert — single OK button, same look. Returns a Promise you can await.
function uiAlert(message, title) {
  return uiConfirm({ title: title || "", message, confirmText: "OK", cancelText: null });
}

function openMapModal(inner, caption) {
  let lb = $("#mapmodal");
  if (!lb) {
    lb = el("div", "lightbox"); lb.id = "mapmodal";
    lb.innerHTML = '<div class="lb-inner mm-inner"><button class="lb-close" aria-label="Close">✕</button>'
      + '<div class="mm-body"></div><div class="lb-cap"></div></div>';
    document.body.appendChild(lb);
    lb.addEventListener("click", (e) => { if (e.target === lb || e.target.closest(".lb-close")) lb.classList.remove("show"); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") lb.classList.remove("show"); });
  }
  lb.querySelector(".mm-body").innerHTML = inner;
  lb.querySelector(".lb-cap").textContent = caption || "";
  const root = document.fullscreenElement || document.querySelector(".card.map-fs") || document.body;
  if (lb.parentNode !== root) root.appendChild(lb);
  lb.classList.add("show");
}

// Jump to the Map tab and highlight/zoom to a specific obstacle.
async function showObstacleOnMap(o) {
  $$("nav button").forEach(b => b.classList.toggle("active", b.dataset.tab === "map"));
  $$(".section").forEach(s => s.classList.toggle("active", s.id === "map"));
  if (!_mapLoaded) { _mapLoaded = true; await renderMap(); }
  if (!MAP || !MAP.rooms || !MAP.rooms.length) return;
  MAP_OBSTACLES = (REPORT && REPORT.obstacles) || MAP_OBSTACLES;
  MAP_FOCUS_OBS = o;
  if (o.x != null && o.y != null) {
    const half = 2500;   // ~5m window centred on the obstacle
    VIEW = { x: SX(o.x) - half, y: SY(o.y) - half, w: half * 2, h: half * 2 };
  }
  drawFloorplan();
}

// Per-suggestion presentation: icon + severity class. Learner no-go suggestions
// carry a `key` + `apply_service` and get an Apply button that writes the zone.
const SUG_META = {
  learned_nogo:  { ico: "🧠", cls: "sug-act" },
  path_block:    { ico: "🧱", cls: "sug-warn" },
  tidy_floor:    { ico: "🧹", cls: "sug-info" },
  recurring_fail:{ ico: "🔴", cls: "sug-warn" },
  move_day:      { ico: "📅", cls: "sug-info" },
};

function renderSuggestions() {
  const card = $("#report-suggestions-card");
  const box = $("#report-suggestions");
  const sugs = (REPORT && REPORT.suggestions) || [];
  if (!sugs.length) { card.style.display = "none"; return; }
  card.style.display = "block";
  box.innerHTML = "";
  sugs.forEach(s => {
    const m = SUG_META[s.type] || { ico: "💡", cls: "sug-info" };
    const row = el("div", "sug-row " + m.cls);
    row.innerHTML =
      '<span class="sug-ico">' + m.ico + "</span>" +
      '<span class="sug-msg">' + esc(s.message) + "</span>";
    // The learner has enough evidence to place a permanent no-go here — offer it.
    if (s.type === "learned_nogo" && s.key) {
      const btn = el("button", "sug-apply");
      btn.textContent = "⛔ Apply no-go";
      btn.title = "Back up the map, then write a permanent no-go around this spot";
      btn.addEventListener("click", () => applyLearnedNogo(s.key, btn));
      row.appendChild(btn);
    }
    box.appendChild(row);
  });
}

// Apply a learner-surfaced no-go through the integration (which owns the
// evidence + the safe write): dreame_scheduler.apply_learned_nogo backs up the
// map before writing. We just fire it and refresh so the suggestion clears.
async function applyLearnedNogo(key, btn) {
  if (!(await uiConfirm({
    title: "Apply a permanent no-go?",
    message: "The robot's map is backed up first, then a no-go zone sized from where it actually " +
      "got stuck is written. You can remove it later in the Map tab.",
    confirmText: "⛔ Apply no-go", danger: true,
  }))) return;
  if (btn) btn.disabled = true;
  setStatus("Applying no-go…", "");
  try {
    await api("api/ha/action", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "apply_learned_nogo", data: _vacData({ key }) }),
    });
    setStatus("✓ No-go applied — the robot will route around it", "ok");
    if (btn) btn.textContent = "✓ Applied";
    setTimeout(loadReport, 1500);   // let the write land, then refresh the list
  } catch (e) {
    setStatus("Couldn’t apply no-go: " + e.message, "err");
    if (btn) btn.disabled = false;
  }
}

// ---- Clean by hand (rooms the robot can't reach) ----
function _vacData(extra) {
  // Target THIS scheduler's vacuum so a multi-robot home acts on the right one.
  const v = (REPORT && REPORT.robot && REPORT.robot.vacuum_entity) || null;
  return Object.assign(v ? { vacuum: v } : {}, extra || {});
}

function _sinceLabel(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  const days = Math.floor((Date.now() - d.getTime()) / 86400000);
  if (days <= 0) return "today";
  if (days === 1) return "since yesterday";
  return "for " + days + " days";
}

function renderManualClean() {
  const card = $("#report-manual-card");
  const box = $("#report-manual");
  const mapBtn = $("#manual-map-btn");
  const mc = (REPORT && REPORT.manual_clean) || {};
  const segs = Object.keys(mc);
  // Always show the card so the feature is visible — even when everything's
  // reachable, an all-clear state is reassuring (and tells you what would appear).
  card.style.display = "block";
  if (!segs.length) {
    if (mapBtn) mapBtn.style.display = "none";
    box.innerHTML = '<div class="mc-clear">✅ The robot is reaching every scheduled room — nothing needs a hand right now.</div>';
    return;
  }
  if (mapBtn) mapBtn.style.display = "";
  box.innerHTML = "";
  segs.forEach(seg => {
    const t = mc[seg] || {};
    const name = t.name || ("Room " + seg);
    const reason = t.reason || "the robot can’t reach it";
    const since = _sinceLabel(t.added);
    const row = el("div", "mc-row");
    row.innerHTML =
      '<span class="mc-ico">🧹</span>' +
      '<span class="mc-main"><b>' + esc(name) + "</b>" +
      '<span class="mc-sub">' + esc(reason) + (since ? " · " + esc(since) : "") + "</span></span>";
    const acts = el("span", "mc-acts");

    const show = el("button", "mc-btn mc-show");
    show.innerHTML = "👉 Show me";
    show.title = "The robot drives to this spot (silently, no cleaning) so you can see where it means";
    show.addEventListener("click", () => showUnreachable(seg, name, show));
    acts.appendChild(show);

    const where = el("button", "mc-btn mc-where");
    where.innerHTML = "📍 Where";
    where.title = "Show this room on your floor plan";
    where.addEventListener("click", () => showRoomModal(seg));
    acts.appendChild(where);

    const done = el("button", "mc-btn mc-done");
    done.innerHTML = "✓ Done";
    done.title = "I cleaned this by hand — credit it and drop it from the list";
    done.addEventListener("click", () => manualDone(seg, name, done));
    acts.appendChild(done);

    row.appendChild(acts);
    box.appendChild(row);
  });
}

// Send the robot to the unreachable spot to physically point it out. Routed
// through dreame_scheduler.show_unreachable (mutes volume, goes silent, no clean,
// dwells, then restores) — a device action, so we confirm first.
async function showUnreachable(seg, name, btn) {
  if (!(await uiConfirm({
    title: "Show you where " + name + " is?",
    message: "The robot drives there silently (no cleaning, no beep), waits about a minute so you can " +
      "see the spot, then carries on. Make sure its path is clear.",
    confirmText: "👉 Show me",
  }))) return;
  if (btn) btn.disabled = true;
  setStatus("Sending the robot to " + name + "…", "");
  try {
    await api("api/ha/action", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "show_unreachable", data: _vacData({ segment: String(seg) }) }),
    });
    setStatus("✓ On its way to " + name, "ok");
    if (btn) { btn.innerHTML = "✓ On its way"; setTimeout(() => { btn.disabled = false; btn.innerHTML = "👉 Show me"; }, 8000); }
  } catch (e) {
    setStatus("Couldn’t send the robot: " + e.message, "err");
    if (btn) btn.disabled = false;
  }
}

async function manualDone(seg, name, btn) {
  if (btn) btn.disabled = true;
  setStatus("Marking " + name + " cleaned…", "");
  try {
    await api("api/ha/manual_done", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prefix: CFG.prefix, segment: String(seg) }),
    });
    setStatus("✓ " + name + " cleaned by hand", "ok");
    setTimeout(loadReport, 800);
  } catch (e) {
    setStatus("Couldn’t mark done: " + e.message, "err");
    if (btn) btn.disabled = false;
  }
}

// Floor-plan modal that shades every clean-by-hand room red, with `focusSeg`
// (if given) brightened and the view zoomed to it. Reuses the obstacle modal's
// map data + coordinate transform.
async function showRoomModal(focusSeg) {
  const md = await _ensureMapData();
  if (!md) { setStatus("Floor plan not available yet", "err"); return; }
  const mc = (REPORT && REPORT.manual_clean) || {};
  const sx = x => x - md.minX, sy = y => md.maxY - y;
  const fs = Math.max(240, Math.round(Math.min(md.maxX - md.minX, md.maxY - md.minY) / 26));

  // Default view = whole plan; if focusing a room, zoom to its bbox + margin.
  let vb = `${0} ${0} ${md.maxX - md.minX} ${md.maxY - md.minY}`;
  const focus = focusSeg != null
    ? md.rooms.find(r => String(r.room_id) === String(focusSeg)) : null;
  if (focus) {
    const pad = 1400;
    const x = sx(focus.x0) - pad, y = sy(focus.y1) - pad;
    const w = (focus.x1 - focus.x0) + pad * 2, h = (focus.y1 - focus.y0) + pad * 2;
    vb = `${x} ${y} ${w} ${h}`;
  }

  let svg = `<svg viewBox="${vb}" preserveAspectRatio="xMidYMid meet" class="floorplan">`;
  md.rooms.forEach(r => {
    const x = sx(r.x0), y = sy(r.y1), w = r.x1 - r.x0, h = r.y1 - r.y0;
    const isManual = Object.prototype.hasOwnProperty.call(mc, String(r.room_id));
    const isFocus = focus && String(r.room_id) === String(focusSeg);
    const base = _PALETTE[(r.color_index || 0) % _PALETTE.length];
    const color = isManual ? "#ef4444" : base;
    const fillOp = isManual ? (isFocus ? 0.34 : 0.2) : 0.1;
    const name = r.custom_name || r.name || ("Room " + r.room_id);
    const cx = typeof r.x === "number" ? sx(r.x) : x + w / 2;
    const cy = typeof r.y === "number" ? sy(r.y) : y + h / 2;
    svg += `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="80" fill="${color}" fill-opacity="${fillOp}" stroke="${color}" stroke-width="${isFocus ? 75 : 45}"${isManual ? ' class="fp-manual"' : ""}/>`
      + `<text x="${cx}" y="${cy}" font-size="${fs}" class="fp-label">${esc(name)}</text>`
      + (isManual ? `<text x="${cx}" y="${cy + fs * 1.25}" font-size="${fs}" class="fp-icon">🧹</text>` : "");
  });
  svg += "</svg>";
  const n = Object.keys(mc).length;
  const cap = focus
    ? ((focus.custom_name || focus.name || ("Room " + focusSeg)) + " · clean by hand")
    : (n + (n === 1 ? " room" : " rooms") + " to clean by hand");
  openMapModal(svg, cap);
}

let _covObserver = null;
function renderCoverage() {
  const box = $("#report-coverage");
  box.innerHTML = "";
  const cov = REPORT.coverage || {};
  const runs = cov.runs || [];
  if (!runs.length && !cov.live_map_url) {
    box.innerHTML = '<div class="hint">No coverage renders available.</div>'; return;
  }
  // Lazy-load: cards + captions paint instantly; each full render only downloads
  // when it scrolls into the strip (rootMargin preloads the next one or two).
  if (_covObserver) _covObserver.disconnect();
  _covObserver = new IntersectionObserver((entries, obs) => {
    entries.forEach(en => {
      if (en.isIntersecting) {
        const img = en.target;
        if (img.dataset.src && !img.src) img.src = img.dataset.src;
        obs.unobserve(img);
      }
    });
  }, { root: box, rootMargin: "300px" });

  runs.slice(0, 12).forEach(run => {
    const card = el("figure", "cov-card cov-loading");
    const img = el("img", "cov-img");
    img.dataset.src = run.url; img.alt = run.label; img.decoding = "async";
    img.onload = () => card.classList.remove("cov-loading");
    img.onerror = () => { img.remove(); card.classList.remove("cov-loading"); card.classList.add("cov-noimg"); };
    const cap = el("figcaption", "cov-cap");
    const badge = run.status === "completed" ? '<span class="cov-b ok">done</span>'
                : run.status === "interrupted" ? '<span class="cov-b warn">interrupted</span>' : "";
    cap.innerHTML = esc(run.label) + " " + badge;
    card.appendChild(img); card.appendChild(cap);
    card.title = "Click to enlarge";
    card.addEventListener("click", () => openLightbox(run.url, run.label));
    box.appendChild(card);
    _covObserver.observe(img);
  });
}

// Full-screen coverage image viewer (click a thumbnail).
function openLightbox(url, label) {
  let lb = $("#lightbox");
  if (!lb) {
    lb = el("div", "lightbox"); lb.id = "lightbox";
    lb.innerHTML =
      '<div class="lb-inner">' +
      '<button class="lb-close" title="Close" aria-label="Close">✕</button>' +
      '<img class="lb-img" alt="">' +
      '<div class="lb-cap"></div>' +
      "</div>";
    document.body.appendChild(lb);
    const close = () => lb.classList.remove("show");
    lb.addEventListener("click", (e) => { if (e.target === lb || e.target.closest(".lb-close")) close(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
  }
  lb.querySelector(".lb-img").src = url;
  lb.querySelector(".lb-cap").textContent = label || "";
  lb.classList.add("show");
}

function renderObstacles() {
  const box = $("#report-obstacles");
  box.innerHTML = "";
  const obs = REPORT.obstacles || [];
  if (!obs.length) { box.innerHTML = '<div class="hint">None flagged. 🎉</div>'; return; }
  obs.forEach(o => {
    const row = el("div", "obs-row");
    const isBlock = o.type === "Blocked Room";
    const hasXY = (o.x != null && o.y != null);
    const caption = (o.room || "") + " · " + (o.type || "obstacle") + (o.reason ? " · " + o.reason : "");

    // Left: the robot's actual photo of the obstacle (click to enlarge), or an
    // icon if it didn't upload one. Coordinates are meaningless to a human, so
    // we show the picture instead — the number only lives on the No-go button.
    let left;
    if (o.picture_url) {
      left = el("img", "obs-photo");
      left.src = o.picture_url; left.alt = "obstacle photo"; left.loading = "lazy";
      left.title = "Click to enlarge";
      left.onerror = () => { const s = el("span", "obs-ico"); s.textContent = isBlock ? "🚪" : "⚠️"; left.replaceWith(s); };
      left.addEventListener("click", () => openLightbox(o.picture_url, caption));
    } else {
      left = el("span", "obs-ico"); left.textContent = isBlock ? "🚪" : "⚠️";
    }
    row.appendChild(left);

    const main = el("span", "obs-main");
    main.innerHTML = "<b>" + esc(o.room || "somewhere") + "</b>" +
      '<span class="obs-sub">' + esc(o.type || "obstacle") + (o.reason ? " · " + esc(o.reason) : "") +
      (o.possibility ? " · " + (Number(o.possibility) || 0) + "%" : "") + "</span>";
    row.appendChild(main);

    if (hasXY) {
      const mapBtn = el("button", "obs-map");
      mapBtn.textContent = "📍 Where";
      mapBtn.title = "Show where this is on your floor plan";
      mapBtn.addEventListener("click", () => showObstacleModal(o));
      row.appendChild(mapBtn);

      const b = el("button", "obs-nogo");
      b.textContent = "＋ No-go";
      b.dataset.x = o.x; b.dataset.y = o.y; b.dataset.lbl = o.room || "here";
      b.title = "Create a permanent no-go zone here";
      row.appendChild(b);
    }
    box.appendChild(row);
  });
}

// Create a permanent no-go zone around an obstacle's coordinates. Reuses the
// map editor's zone plumbing: reads the current zones, appends a box, and
// writes back (backing up the robot's map first).
async function addObstacleNoGo(x, y, label, btn) {
  const HALF = 300; // 30cm half-box
  if (!(await uiConfirm({
    title: "Create a no-go zone here?",
    message: "Around " + label + " (" + x + "," + y + "). The robot's map is backed up first, then a " +
      "~60cm no-go box is added to your existing zones. It stays until you remove it in the Map tab.",
    confirmText: "⛔ Create no-go", danger: true,
  }))) return;
  if (btn) btn.disabled = true;
  setStatus("Adding no-go zone…", "");
  try {
    const m = await api("api/ha/map?prefix=" + encodeURIComponent(CFG.prefix));
    const walls = (m.virtual_walls || []).map(_coerceLine).filter(Boolean);
    const zones = (m.no_go_areas || []).map(_coerceBox).filter(Boolean);
    const no_mops = (m.no_mopping_areas || []).map(_coerceBox).filter(Boolean);
    zones.push([x - HALF, y - HALF, x + HALF, y + HALF]);
    await api("api/ha/zones", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ vacuum_entity: m.vacuum_entity, walls, zones, no_mops, backup: true }),
    });
    setStatus("✓ No-go zone added around " + label, "ok");
    if (btn) { btn.textContent = "✓ Added"; }
  } catch (e) {
    setStatus("Failed to add no-go: " + e.message, "err");
    if (btn) btn.disabled = false;
  }
}

$("#report-scope").addEventListener("click", (e) => {
  const b = e.target.closest(".seg-btn"); if (!b) return;
  _reportScope = b.dataset.scope;
  $$("#report-scope .seg-btn").forEach(x => x.classList.toggle("active", x === b));
  renderReport();
});

$("#report-obstacles").addEventListener("click", (e) => {
  const b = e.target.closest(".obs-nogo"); if (!b) return;
  addObstacleNoGo(parseInt(b.dataset.x, 10), parseInt(b.dataset.y, 10), b.dataset.lbl, b);
});

$("#manual-map-btn").addEventListener("click", () => showRoomModal(null));

// nav (mobile: ☰ collapses the tabs into a stacked menu)
document.addEventListener("click", (e) => {
  const nav = document.getElementById("nav");
  const burger = e.target.closest("#nav-burger");
  if (burger && nav) {
    const open = nav.classList.toggle("open");
    burger.setAttribute("aria-expanded", String(open));
    return;
  }
  const tabBtn = e.target.closest("nav button[data-tab]");
  if (tabBtn) {
    // leaving the Map tab: tear the 3D view down (stops its poll loop + rAF)
    if (tabBtn.dataset.tab !== "map" && window.Plan3D && Plan3D.active) {
      Plan3D.stop();
      const b3 = document.getElementById("map-3d"); if (b3) b3.classList.remove("active");
      const tb = document.getElementById("map-toolbar"); if (tb) tb.classList.remove("p3d-on");
    }
    $$("nav button[data-tab]").forEach(b => b.classList.toggle("active", b === tabBtn));
    $$(".section").forEach(s => s.classList.toggle("active", s.id === tabBtn.dataset.tab));
    if (nav) nav.classList.remove("open");           // picking a tab closes the ☰ menu
    if (tabBtn.dataset.tab === "map" && !_mapLoaded) { _mapLoaded = true; renderMap(); }
    if (tabBtn.dataset.tab === "home" && !_homeLoaded) { _homeLoaded = true; renderHome(); }
    if (tabBtn.dataset.tab === "report" && !_reportLoaded) { _reportLoaded = true; loadReport(); }
    if (tabBtn.dataset.tab === "dashboard" && !_dashboardLoaded) { _dashboardLoaded = true; renderDashboard(); }
  } else if (nav && nav.classList.contains("open") && !e.target.closest("nav")) {
    nav.classList.remove("open");                    // tap-away closes it too
  }
  const actBtn = e.target.closest("[data-action]");
  if (actBtn && actBtn.id !== "save") doAction(actBtn.dataset.action, actBtn.dataset.quiet === "1");
});
$("#save").addEventListener("click", save);
$("#deck-customize").addEventListener("click", openDeckCustomize);

// theme toggle: Dark (default) → Light → Auto (follow OS), persisted locally.
const THEMES = ["dark", "light", "auto"];
const THEME_LABEL = { dark: "🌙 Dark", light: "☀️ Light", auto: "🌗 Auto" };
let _theme = "dark";
function applyTheme(t) {
  _theme = THEMES.includes(t) ? t : "dark";
  if (_theme === "dark") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", _theme);
  const btn = $("#theme-toggle");
  if (btn) btn.textContent = THEME_LABEL[_theme];
}
function initTheme() {
  let saved = null;
  try { saved = localStorage.getItem("ds-theme"); } catch (e) {}
  applyTheme(saved);
  const btn = $("#theme-toggle");
  if (btn) btn.addEventListener("click", () => {
    const next = THEMES[(THEMES.indexOf(_theme) + 1) % THEMES.length];
    try { localStorage.setItem("ds-theme", next); } catch (e) {}
    applyTheme(next);
  });
}
initTheme();
boot();
