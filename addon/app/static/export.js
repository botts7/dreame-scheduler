/* export.js — Floor-plan export: standalone SVG + Lovelace YAML.
   Generates a self-contained SVG of the current plan (styles inlined, editing
   handles stripped) plus ready-to-paste picture-elements / ha-floorplan YAML,
   so the plan works on any HA dashboard without this add-on.
   Loaded after app.js; uses its global helpers (openContentModal, copyText). */
(function () {
  "use strict";

  // SVG presentation properties worth freezing into the export.
  const STYLE_PROPS = [
    "fill", "fill-opacity", "stroke", "stroke-width", "stroke-opacity",
    "stroke-linecap", "stroke-linejoin", "stroke-dasharray", "opacity",
    "font-family", "font-size", "font-weight", "letter-spacing",
    "text-anchor", "dominant-baseline", "paint-order",
  ];
  // Editing-only artifacts that must not ship in an export.
  const STRIP = ".fp-vtx,.fp-mid,.fp-edge,.fp-marquee,.preview,.fp-split.pending,.fp-carve";

  function _liveSvg() {
    return document.querySelector("#map-wrap svg.floorplan");
  }

  function _inlineStyles(liveEl, cloneEl) {
    const cs = getComputedStyle(liveEl);
    let css = "";
    for (const p of STYLE_PROPS) {
      const v = cs.getPropertyValue(p);
      if (v && v !== "none" || (p === "fill" || p === "stroke")) css += `${p}:${v};`;
    }
    cloneEl.setAttribute("style", css);
    cloneEl.removeAttribute("class");
    const lk = liveEl.children, ck = cloneEl.children;
    for (let i = 0; i < lk.length && i < ck.length; i++) _inlineStyles(lk[i], ck[i]);
  }

  function buildStandaloneSvg() {
    const live = _liveSvg();
    if (!live) return null;
    let bb;
    try { bb = live.getBBox(); } catch (e) { return null; }
    // A hidden tab yields a zero bbox → NaN geometry in the export.
    if (!bb || !isFinite(bb.width) || bb.width <= 0 || bb.height <= 0) return null;
    const pad = Math.max(bb.width, bb.height) * 0.02;
    // Order matters: class-based selectors (strip list, device groups) must run
    // while classes still exist — _inlineStyles removes them. So mark first,
    // inline second (walking live + clone in identical child order), strip last.
    const clone2 = live.cloneNode(true);
    clone2.querySelectorAll(STRIP).forEach(n => n.setAttribute("data-x-strip", "1"));
    clone2.querySelectorAll("g.fp-dev[data-dev]").forEach(g => g.setAttribute("id", g.dataset.dev));
    _inlineStyles(live, clone2);
    clone2.querySelectorAll("[data-x-strip]").forEach(n => n.remove());
    const vb = [bb.x - pad, bb.y - pad, bb.width + pad * 2, bb.height + pad * 2];
    clone2.setAttribute("viewBox", vb.join(" "));
    clone2.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    clone2.removeAttribute("style"); clone2.removeAttribute("class");
    clone2.setAttribute("width", "1000");
    clone2.setAttribute("height", String(Math.round(1000 * vb[3] / vb[2])));
    // Opaque sheet behind everything (the app supplies this via page CSS).
    const bg = getComputedStyle(document.documentElement).getPropertyValue("--bg").trim() || "#0a0e17";
    const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    rect.setAttribute("x", vb[0]); rect.setAttribute("y", vb[1]);
    rect.setAttribute("width", vb[2]); rect.setAttribute("height", vb[3]);
    rect.setAttribute("fill", bg);
    clone2.insertBefore(rect, clone2.firstChild);
    return { svg: clone2, vb };
  }

  function _pct(v, off, span) { return ((v - off) / span * 100).toFixed(1) + "%"; }

  function collectPlacements(vb) {
    const live = _liveSvg();
    const devs = [], rooms = [];
    live.querySelectorAll("g.fp-dev[data-dev]").forEach(g => {
      const c = g.querySelector("circle");
      if (!c) return;
      devs.push({
        entity: g.dataset.dev,
        left: _pct(+c.getAttribute("cx"), vb[0], vb[2]),
        top: _pct(+c.getAttribute("cy"), vb[1], vb[3]),
      });
    });
    live.querySelectorAll("g.fp-room[data-seg]").forEach(g => {
      try {
        const bb = g.getBBox();
        const lbl = g.querySelector("text.fp-label");
        rooms.push({
          seg: parseInt(g.dataset.seg, 10),
          // strip newlines/control chars so a room name can't inject YAML lines
          name: ((lbl && lbl.textContent.trim()) || ("Room " + g.dataset.seg)).replace(/[\r\n:#]+/g, " ").trim(),
          left: _pct(bb.x + bb.width / 2, vb[0], vb[2]),
          top: _pct(bb.y + bb.height / 2, vb[1], vb[3]),
        });
      } catch (e) { /* zero-size group */ }
    });
    return { devs, rooms };
  }

  function yamlPictureElements(p) {
    const L = [
      "type: picture-elements",
      "image: /local/floorplan.svg",
      "elements:",
    ];
    p.devs.forEach(d => {
      L.push(
        "  - type: state-icon",
        "    entity: " + d.entity,
        "    tap_action:",
        "      action: toggle",
        "    style:",
        "      left: " + d.left,
        "      top: " + d.top,
        "      transform: translate(-50%, -50%)",
      );
    });
    p.rooms.forEach(r => {
      L.push(
        "  - type: icon",
        "    icon: mdi:robot-vacuum",
        "    title: Clean " + r.name,
        "    tap_action:",
        "      action: call-service",
        "      service: dreame_scheduler.clean_rooms",
        "      data:",
        "        segments: [" + r.seg + "]",
        "      confirmation:",
        "        text: Clean " + r.name + "?",
        "    style:",
        "      left: " + r.left,
        "      top: " + r.top,
        "      transform: translate(-50%, -50%)",
        "      opacity: '0.55'",
        "      '--mdc-icon-size': 20px",
      );
    });
    if (!p.devs.length && !p.rooms.length) L.push("  []");
    return L.join("\n");
  }

  function yamlHaFloorplan(p) {
    const L = [
      "# Requires the ha-floorplan card (HACS: ExperienceLovelace/ha-floorplan)",
      "type: custom:floorplan-card",
      "config:",
      "  image: /local/floorplan.svg",
      "  stylesheet: /local/floorplan.css",
      "  rules:",
    ];
    p.devs.forEach(d => {
      L.push(
        "    - entity: " + d.entity,
        "      element: " + d.entity,
        "      tap_action: toggle",
        "      state_action:",
        "        action: call-service",
        "        service: floorplan.class_set",
        "        service_data: '${entity.state}'",
      );
    });
    if (!p.devs.length) L.push("    []");
    return L.join("\n");
  }

  function download(name, text, mime) {
    const blob = new Blob([text], { type: mime || "text/plain" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 400);
  }

  function openExport() {
    const built = buildStandaloneSvg();
    if (!built) { alert("Open the Map tab and wait for the plan to render first."); return; }
    const svgText = '<?xml version="1.0" encoding="UTF-8"?>\n' + new XMLSerializer().serializeToString(built.svg);
    const p = collectPlacements(built.vb);
    const peYaml = yamlPictureElements(p);
    const hfYaml = yamlHaFloorplan(p);

    const box = document.createElement("div");
    box.innerHTML =
      '<div class="ex-opt"><div class="ex-ico">🗺️</div><div class="ex-main">' +
      '<div class="ex-t">Floor-plan image (SVG)</div>' +
      '<div class="ex-d">A self-contained render of the plan exactly as shown — rooms, walls, zones, furniture and device pins. Save it to <b>/config/www/</b> as <b>floorplan.svg</b>.</div>' +
      '<div class="ex-btns"><button class="btn primary" id="ex-dl-svg" type="button">⬇ Download floorplan.svg</button></div></div></div>' +

      '<div class="ex-opt"><div class="ex-ico">🧩</div><div class="ex-main">' +
      '<div class="ex-t">Picture-elements card <span class="pill">works out of the box</span></div>' +
      '<div class="ex-d">Standard Lovelace card: your ' + p.devs.length + ' placed device(s) become live tappable icons, and each of the ' + p.rooms.length + ' room(s) gets a tap-to-clean button — no custom cards needed.</div>' +
      '<div class="ex-btns"><button class="btn ghost" id="ex-cp-pe" type="button">📋 Copy YAML</button>' +
      '<button class="btn ghost" id="ex-dl-pe" type="button">⬇ Download</button></div></div></div>' +

      '<div class="ex-opt"><div class="ex-ico">📐</div><div class="ex-main">' +
      '<div class="ex-t">ha-floorplan starter <span class="pill">advanced</span></div>' +
      '<div class="ex-d">For the ha-floorplan custom card: device pins in the SVG carry their entity IDs, and this starter config binds state classes to them for CSS styling.</div>' +
      '<div class="ex-btns"><button class="btn ghost" id="ex-cp-hf" type="button">📋 Copy YAML</button>' +
      '<button class="btn ghost" id="ex-dl-hf" type="button">⬇ Download</button></div></div></div>' +

      '<div class="ex-note">Steps: <b>1</b> download the SVG → put it in <code>/config/www/</code> · ' +
      '<b>2</b> dashboard → Edit → Add card → Manual → paste the YAML · ' +
      '<b>3</b> re-export any time you move devices or reshape rooms.</div>';

    box.querySelector("#ex-dl-svg").onclick = () => download("floorplan.svg", svgText, "image/svg+xml");
    box.querySelector("#ex-cp-pe").onclick = e => { copyText(peYaml); e.target.textContent = "✓ Copied"; };
    box.querySelector("#ex-dl-pe").onclick = () => download("picture-elements.yaml", peYaml, "text/yaml");
    box.querySelector("#ex-cp-hf").onclick = e => { copyText(hfYaml); e.target.textContent = "✓ Copied"; };
    box.querySelector("#ex-dl-hf").onclick = () => download("ha-floorplan.yaml", hfYaml, "text/yaml");

    if (typeof openContentModal === "function") openContentModal("⬇ Export floor plan", box);
    else { document.body.appendChild(box); }
  }

  // The toolbar renders after boot — delegate instead of wiring at load.
  document.addEventListener("click", e => {
    const b = e.target.closest && e.target.closest("#map-export");
    if (b) openExport();
  });
})();
