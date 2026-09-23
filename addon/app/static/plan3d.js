/* plan3d.js - the live 3D view of the floor plan, inside the Map tab.
   Extrudes the app's CURRENT data (window._plan3dData(): auto-fit room
   polygons, clean outline, zones, furniture, placed devices, robot + dock)
   into an orbitable canvas scene. Lights glow with their real HA state and
   the robot's position refreshes live (window._plan3dTick()).
   Dependency-free painter's-algorithm renderer; loaded after app.js. */
(function () {
  "use strict";

  const PLINTH = 260, ZONE_H = 380, FURN_H = 500;
  // user-adjustable wall geometry (persisted per browser)
  let wallH = 2600;                                  // exterior wall height (mm)
  let glassMode = "low";                             // interior room walls: "off" | "low" | "full"
  try {
    const s = JSON.parse(localStorage.getItem("p3d_walls") || "{}");
    if (s.wallH >= 400 && s.wallH <= 6000) wallH = s.wallH;
    if (["off", "low", "full"].includes(s.glassMode)) glassMode = s.glassMode;
  } catch (e) {}
  const persistWalls = () => { try { localStorage.setItem("p3d_walls", JSON.stringify({ wallH, glassMode })); } catch (e) {} };
  const glassH = () => glassMode === "off" ? 0 : (glassMode === "full" ? wallH : Math.min(1050, wallH));

  let host = null, canvas = null, ctx = null, raf = 0, pollTimer = 0;
  let yaw = -0.62, elev = 1.02, zoom = 0, spin = false, drag = null;
  let CX = 0, CY = 0;
  let hitPolys = [];                                 // [{seg, pts:[[sx,sy]..]}] rebuilt per frame for tap-to-edit
  // ---- in-3D editing: select a room, drag its corner/wall handles ----
  let editMode = false, selSeg = null, editDrag = null;
  let handleHits = [];                               // [{type:'vtx'|'edge', i, x, y}] screen-space, per frame

  // screen → floor plane (z=0) in scene coords — exact for this projection
  function unproject(mx, my, W, H) {
    const rx = (mx - W / 2) / zoom;
    const ry = (my - H / 2) / (zoom * Math.sin(elev));
    const c = Math.cos(yaw), s = Math.sin(yaw);
    return [CX + rx * c + ry * s, CY - rx * s + ry * c];
  }
  // zoom keeping the floor point under (mx,my) fixed — cursor/pinch anchored
  function zoomAt(f, mx, my) {
    if (!canvas) return;
    const W = canvas.clientWidth, H = canvas.clientHeight;
    if (mx == null) { mx = W / 2; my = H / 2; }
    const [gx, gy] = unproject(mx, my, W, H);
    const k = zoom / (zoom = Math.max(1e-5, zoom * f));
    CX = gx - (gx - CX) * k;
    CY = gy - (gy - CY) * k;
  }
  // two-finger pinch state (touch): pointerId → last position
  const touches = new Map();
  let pinchD = 0;

  const hexRgb = h => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
  const rgba = (c, a) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;
  const shade = (c, f) => [c[0] * f | 0, c[1] * f | 0, c[2] * f | 0];
  const WALL_RGB = hexRgb("#28405c"), RED = hexRgb("#f4626e"), CYANC = hexRgb("#3ec6dd");

  function project(x, y, z, W, H) {
    const dx = x - CX, dy = y - CY;
    const c = Math.cos(yaw), s = Math.sin(yaw);
    const rx = dx * c - dy * s, ry = dx * s + dy * c;
    const se = Math.sin(elev), ce = Math.cos(elev);
    return [W / 2 + rx * zoom, H / 2 + (ry * se - z * ce) * zoom, ry * ce + z * se];
  }
  const quad = (x1, y1, x2, y2, z0, z1) => [[x1, y1, z0], [x2, y2, z0], [x2, y2, z1], [x1, y1, z1]];
  function lightFor(x1, y1, x2, y2) {
    const nx = y2 - y1, ny = -(x2 - x1), l = Math.hypot(nx, ny) || 1;
    const c = Math.cos(yaw + 0.9), s = Math.sin(yaw + 0.9);
    return 0.62 + 0.38 * Math.max(0, (nx / l) * c + (ny / l) * s);
  }
  const centroid = p => {
    const xs = p.map(q => q[0]), ys = p.map(q => q[1]);
    return [(Math.min(...xs) + Math.max(...xs)) / 2, (Math.min(...ys) + Math.max(...ys)) / 2];
  };

  function buildFaces(D) {
    const F = [];
    const o = D.outline;
    if (o) {
      for (let i = 0; i < o.length; i++) {
        const [x1, y1] = o[i], [x2, y2] = o[(i + 1) % o.length];
        F.push({ pts: quad(x1, y1, x2, y2, -PLINTH, 0), fill: rgba(shade(WALL_RGB, 0.45), 1) });
      }
      F.push({ poly: o.map(p => [p[0], p[1], 0]), fill: "#0e1524", stroke: rgba(CYANC, .5), lw: 1.2 });
    }
    const gh = glassH();
    for (const r of D.rooms) {
      const rgb = hexRgb(r.color);
      F.push({ poly: r.poly.map(p => [p[0], p[1], 22]), fill: rgba(rgb, 0.20), stroke: rgba(rgb, .8), lw: 1 });
      for (let i = 0; i < r.poly.length; i++) {
        // per-wall override wins over the global room-walls mode; 0 = opening
        const ov = r.wallh ? r.wallh[i] : null;
        const h = ov != null ? ov : gh;
        if (h <= 0) continue;
        const [x1, y1] = r.poly[i], [x2, y2] = r.poly[(i + 1) % r.poly.length];
        F.push({ pts: quad(x1, y1, x2, y2, 0, h), fill: rgba(rgb, 0.05), stroke: rgba(rgb, 0.24), lw: 0.6 });
      }
    }
    for (const [zx, zy, zw, zh] of D.zones) {
      const cs = [[zx, zy], [zx + zw, zy], [zx + zw, zy + zh], [zx, zy + zh]];
      for (let i = 0; i < 4; i++) {
        const [x1, y1] = cs[i], [x2, y2] = cs[(i + 1) % 4];
        F.push({ pts: quad(x1, y1, x2, y2, 0, ZONE_H), fill: rgba(RED, 0.15), stroke: rgba(RED, 0.45), lw: 0.8 });
      }
      F.push({ poly: cs.map(p => [p[0], p[1], ZONE_H]), fill: rgba(RED, 0.13), stroke: rgba(RED, 0.55), lw: 1, dash: [6, 5] });
    }
    for (const f of D.furniture) {
      const cs = [[f.x, f.y], [f.x + f.w, f.y], [f.x + f.w, f.y + f.h], [f.x, f.y + f.h]];
      const FC = hexRgb("#b08968");
      for (let i = 0; i < 4; i++) {
        const [x1, y1] = cs[i], [x2, y2] = cs[(i + 1) % 4];
        F.push({ pts: quad(x1, y1, x2, y2, 0, FURN_H), fill: rgba(shade(FC, lightFor(x1, y1, x2, y2)), 0.55) });
      }
      F.push({ poly: cs.map(p => [p[0], p[1], FURN_H]), fill: rgba(FC, 0.5), stroke: rgba(FC, 0.8), lw: 0.8 });
    }
    if (o) {
      for (let i = 0; i < o.length; i++) {
        const [x1, y1] = o[i], [x2, y2] = o[(i + 1) % o.length];
        F.push({ pts: quad(x1, y1, x2, y2, 0, wallH), fill: rgba(shade(WALL_RGB, lightFor(x1, y1, x2, y2)), 0.94) });
      }
    }
    if (D.dock) {
      const [dx, dy] = D.dock, r = 240, hgt = 480, A = hexRgb("#f0b429");
      const cs = [[dx - r, dy - r], [dx + r, dy - r], [dx + r, dy + r], [dx - r, dy + r]];
      for (let i = 0; i < 4; i++) {
        const [x1, y1] = cs[i], [x2, y2] = cs[(i + 1) % 4];
        F.push({ pts: quad(x1, y1, x2, y2, 0, hgt), fill: rgba(A, 0.85) });
      }
      F.push({ poly: cs.map(p => [p[0], p[1], hgt]), fill: "#f7cf6b" });
    }
    return F;
  }

  function draw() {
    if (!canvas || !window._plan3dData) return;
    const D = window._plan3dData();
    if (!D) { raf = requestAnimationFrame(draw); return; }
    const DPR = Math.min(2, window.devicePixelRatio || 1);
    const W = canvas.clientWidth, H = canvas.clientHeight;
    if (canvas.width !== W * DPR) { canvas.width = W * DPR; canvas.height = H * DPR; }
    if (!zoom) {
      // fit camera to the actual geometry (outline + rooms), not the raw map
      // extents - edited/cleaned plans are much smaller than the scan bounds
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      const upd = (x, y) => { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; };
      (D.outline || []).forEach(p => upd(p[0], p[1]));
      D.rooms.forEach(r => r.poly.forEach(p => upd(p[0], p[1])));
      if (x1 > x0) {
        CX = (x0 + x1) / 2; CY = (y0 + y1) / 2;
        zoom = Math.min(W, H) / (Math.max(x1 - x0, y1 - y0) * 1.45);
      } else {
        CX = D.W / 2; CY = D.H / 2;
        zoom = Math.min(W, H) / (Math.max(D.W, D.H) * 1.55);
      }
    }
    if (spin) yaw += 0.0022;
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = "#0a0e17"; ctx.fillRect(0, 0, W, H);
    ctx.strokeStyle = "rgba(76,201,240,.045)"; ctx.lineWidth = 1;
    ctx.beginPath();
    for (let x = 0; x < W; x += 46) { ctx.moveTo(x, 0); ctx.lineTo(x, H); }
    for (let y = 0; y < H; y += 46) { ctx.moveTo(0, y); ctx.lineTo(W, y); }
    ctx.stroke();

    const P = (x, y, z) => project(x, y, z, W, H);
    hitPolys = D.rooms.map(r => ({ seg: r.seg, pts: r.poly.map(p => P(p[0], p[1], 22)) }));
    const faces = buildFaces(D);
    for (const f of faces) {
      const pts = f.pts || f.poly;
      let d = 0; for (const p of pts) d += P(p[0], p[1], p[2])[2];
      f.d = d / pts.length;
    }
    faces.sort((a, b) => a.d - b.d);
    for (const f of faces) {
      const pts = (f.pts || f.poly).map(p => P(p[0], p[1], p[2]));
      ctx.beginPath();
      ctx.moveTo(pts[0][0], pts[0][1]);
      for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
      ctx.closePath();
      if (f.fill) { ctx.fillStyle = f.fill; ctx.fill(); }
      if (f.stroke) {
        ctx.strokeStyle = f.stroke; ctx.lineWidth = f.lw || 1;
        ctx.setLineDash(f.dash || []); ctx.stroke(); ctx.setLineDash([]);
      }
    }
    // glowing wall-cap line
    if (D.outline) {
      const o = D.outline;
      ctx.beginPath();
      for (let i = 0; i <= o.length; i++) {
        const [x, y] = o[i % o.length];
        const p = P(x, y, wallH);
        i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]);
      }
      ctx.strokeStyle = "rgba(103,232,249,.9)"; ctx.lineWidth = 1.5;
      ctx.shadowColor = "#3ec6dd"; ctx.shadowBlur = 7;
      ctx.stroke(); ctx.shadowBlur = 0;
    }
    // robot - live position puck
    if (D.robot) {
      const p = P(D.robot[0], D.robot[1], 120);
      ctx.fillStyle = "#3ec6dd";
      ctx.shadowColor = "#3ec6dd"; ctx.shadowBlur = 12;
      ctx.beginPath(); ctx.arc(p[0], p[1], 7, 0, 7); ctx.fill();
      ctx.shadowBlur = 0;
      ctx.fillStyle = "#04222b";
      ctx.beginPath(); ctx.arc(p[0], p[1], 2.6, 0, 7); ctx.fill();
    }
    // devices - lights float at ceiling height and glow with real state
    for (const dv of D.devices) {
      const ceiling = dv.domain === "light";
      const p = P(dv.x, dv.y, ceiling ? wallH - 260 : 260);
      if (dv.on) {
        const rad = 30;
        const gr = ctx.createRadialGradient(p[0], p[1], 0, p[0], p[1], rad);
        gr.addColorStop(0, "rgba(255,236,170,.95)");
        gr.addColorStop(0.35, "rgba(240,180,41,.5)");
        gr.addColorStop(1, "rgba(240,180,41,0)");
        ctx.fillStyle = gr;
        ctx.beginPath(); ctx.arc(p[0], p[1], rad, 0, 7); ctx.fill();
      }
      ctx.fillStyle = dv.on ? "#fff7df" : "#5a6478";
      ctx.beginPath(); ctx.arc(p[0], p[1], 3.5, 0, 7); ctx.fill();
    }
    // in-3D edit handles for the selected room
    handleHits = [];
    if (editMode && selSeg != null) {
      const r = D.rooms.find(q => q.seg === selSeg);
      if (r) {
        // highlight the selected floor
        const fl = r.poly.map(p => P(p[0], p[1], 26));
        ctx.beginPath();
        ctx.moveTo(fl[0][0], fl[0][1]);
        for (let i = 1; i < fl.length; i++) ctx.lineTo(fl[i][0], fl[i][1]);
        ctx.closePath();
        ctx.strokeStyle = "#67e8f9"; ctx.lineWidth = 2;
        ctx.shadowColor = "#3ec6dd"; ctx.shadowBlur = 8; ctx.stroke(); ctx.shadowBlur = 0;
        const ghSel = glassH();
        for (let i = 0; i < r.poly.length; i++) {   // wall (edge) handles — squares
          const a = r.poly[i], b = r.poly[(i + 1) % r.poly.length];
          const mx2 = (a[0] + b[0]) / 2, my2 = (a[1] + b[1]) / 2;
          const ov = r.wallh ? r.wallh[i] : null;
          const hEff = ov != null ? ov : ghSel;
          const open = hEff <= 0;
          const p = P(mx2, my2, 26);
          ctx.fillStyle = open ? "rgba(103,232,249,.25)" : (ov != null ? "#a78bfa" : "#f0b429");
          ctx.strokeStyle = open ? "#67e8f9" : "#fff";
          ctx.lineWidth = 1.2;
          ctx.fillRect(p[0] - 5, p[1] - 5, 10, 10); ctx.strokeRect(p[0] - 5, p[1] - 5, 10, 10);
          handleHits.push({ type: "edge", i, x: p[0], y: p[1] });
          // top-of-wall handle (diamond): drag vertically = this wall's height,
          // double-tap = reset to default. Sits low when the wall is open so
          // it can still be grabbed and pulled up.
          const pt = P(mx2, my2, Math.max(hEff, 180));
          ctx.save();
          ctx.translate(pt[0], pt[1]); ctx.rotate(Math.PI / 4);
          ctx.fillStyle = ov != null ? "#a78bfa" : "#3ec6dd";
          ctx.strokeStyle = "#fff"; ctx.lineWidth = 1.2;
          ctx.fillRect(-4.5, -4.5, 9, 9); ctx.strokeRect(-4.5, -4.5, 9, 9);
          ctx.restore();
          handleHits.push({ type: "wtop", i, x: pt[0], y: pt[1], h: hEff });
        }
        for (let i = 0; i < r.poly.length; i++) {   // corner handles — circles (on top)
          const p = P(r.poly[i][0], r.poly[i][1], 26);
          ctx.fillStyle = "#3ec6dd"; ctx.strokeStyle = "#fff"; ctx.lineWidth = 1.5;
          ctx.beginPath(); ctx.arc(p[0], p[1], 6, 0, 7); ctx.fill(); ctx.stroke();
          handleHits.push({ type: "vtx", i, x: p[0], y: p[1] });
        }
      }
    }
    // labels
    ctx.font = "600 11px ui-monospace, Consolas, monospace";
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    for (const r of D.rooms) {
      const [cx, cy] = centroid(r.poly);
      const p = P(cx, cy, 60);
      const label = r.name.toUpperCase();
      const w2 = ctx.measureText(label).width;
      ctx.fillStyle = "rgba(10,14,23,.72)";
      ctx.fillRect(p[0] - w2 / 2 - 5, p[1] - 9, w2 + 10, 18);
      ctx.fillStyle = "#dae3f3";
      ctx.fillText(label, p[0], p[1]);
    }
    raf = requestAnimationFrame(draw);
  }

  function _roomAtScreen(mx, my) {
    for (const hp of hitPolys) {
      let inside = false;
      for (let i = 0, j = hp.pts.length - 1; i < hp.pts.length; j = i++) {
        const [xi, yi] = hp.pts[i], [xj, yj] = hp.pts[j];
        if ((yi > my) !== (yj > my) && mx < (xj - xi) * (my - yi) / (yj - yi) + xi) inside = !inside;
      }
      if (inside) return hp.seg;
    }
    return null;
  }

  function attach() {
    canvas.addEventListener("pointerdown", e => {
      const rect = canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left, my = e.clientY - rect.top;
      if (e.pointerType === "touch") {
        touches.set(e.pointerId, { x: mx, y: my });
        if (touches.size === 2) {                    // second finger → pinch, cancel drags
          const [a, b] = [...touches.values()];
          pinchD = Math.hypot(a.x - b.x, a.y - b.y);
          drag = null; editDrag = null;
          canvas.setPointerCapture(e.pointerId);
          return;
        }
      }
      // edit mode: grabbing a handle beats orbiting
      if (editMode && selSeg != null && window._plan3dMoveVertex) {
        const hit = handleHits.slice().reverse()     // corners drawn last → checked first
          .find(h => Math.hypot(h.x - mx, h.y - my) < 12);
        if (hit) {
          const [gx, gy] = unproject(mx, my, canvas.clientWidth, canvas.clientHeight);
          editDrag = { ...hit, gx, gy, moved: false };
          window._plan3dBeginEdit();
          canvas.setPointerCapture(e.pointerId);
          return;
        }
      }
      drag = { x: e.clientX, y: e.clientY, x0: e.clientX, y0: e.clientY };
      canvas.setPointerCapture(e.pointerId);
      canvas.style.cursor = "grabbing";
    });
    canvas.addEventListener("pointermove", e => {
      const rect = canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left, my = e.clientY - rect.top;
      if (e.pointerType === "touch" && touches.has(e.pointerId)) {
        touches.set(e.pointerId, { x: mx, y: my });
        if (touches.size === 2) {                    // pinch: zoom about the finger midpoint
          const [a, b] = [...touches.values()];
          const d = Math.hypot(a.x - b.x, a.y - b.y);
          if (pinchD > 0 && d > 0) zoomAt(d / pinchD, (a.x + b.x) / 2, (a.y + b.y) / 2);
          pinchD = d;
          return;
        }
      }
      if (editDrag) {
        if (editDrag.type === "wtop") {             // vertical drag = wall height
          if (editDrag.sy == null) { editDrag.sy = my; editDrag.h0 = editDrag.h; }
          const dz = -(my - editDrag.sy) / (zoom * Math.max(0.15, Math.cos(elev)));
          const h = Math.max(0, Math.min(6000, Math.round((editDrag.h0 + dz) / 50) * 50));
          window._plan3dSetWallH(selSeg, editDrag.i, h);
          editDrag.moved = true;
          return;
        }
        const [gx, gy] = unproject(mx, my, canvas.clientWidth, canvas.clientHeight);
        if (editDrag.type === "vtx") window._plan3dMoveVertex(selSeg, editDrag.i, gx, gy);
        else window._plan3dMoveEdge(selSeg, editDrag.i, gx - editDrag.gx, gy - editDrag.gy);
        editDrag.gx = gx; editDrag.gy = gy; editDrag.moved = true;
        return;
      }
      if (!drag) return;
      yaw += (e.clientX - drag.x) * 0.0055;
      elev = Math.min(1.45, Math.max(0.35, elev + (e.clientY - drag.y) * 0.004));
      drag = { ...drag, x: e.clientX, y: e.clientY };
    });
    canvas.addEventListener("pointerup", e => {
      if (e.pointerType === "touch") { touches.delete(e.pointerId); if (touches.size < 2) pinchD = 0; }
      if (editDrag) { editDrag = null; return; }
      const wasTap = drag && Math.hypot(e.clientX - drag.x0, e.clientY - drag.y0) < 6;
      drag = null; canvas.style.cursor = "grab";
      if (!wasTap) return;
      const rect = canvas.getBoundingClientRect();
      const seg = _roomAtScreen(e.clientX - rect.left, e.clientY - rect.top);
      if (seg == null) { if (editMode) selSeg = null; return; }
      if (editMode) {                                // 3D edit: tap selects the room
        selSeg = seg;
        if (window._plan3dEnsureShape) window._plan3dEnsureShape(seg);
      } else if (window._plan3dEditRoom) {           // classic: tap jumps to the 2D editor
        window._plan3dEditRoom(seg);
      }
    });
    canvas.addEventListener("wheel", e => {
      e.preventDefault();
      const rect = canvas.getBoundingClientRect();
      zoomAt(e.deltaY > 0 ? 0.9 : 1.11, e.clientX - rect.left, e.clientY - rect.top);
    }, { passive: false });
    // double-tap: on a wall handle (edit mode) toggles wall <-> opening;
    // anywhere else zooms in (Shift = out) — maps-app pattern
    canvas.addEventListener("dblclick", e => {
      e.preventDefault();
      if (editMode && selSeg != null && window._plan3dToggleOpening) {
        const rect = canvas.getBoundingClientRect();
        const mx = e.clientX - rect.left, my = e.clientY - rect.top;
        const top = handleHits.find(h => h.type === "wtop" && Math.hypot(h.x - mx, h.y - my) < 12);
        if (top) { window._plan3dResetWallH(selSeg, top.i); return; }   // back to default height
        const hit = handleHits.find(h => h.type === "edge" && Math.hypot(h.x - mx, h.y - my) < 12);
        if (hit) { window._plan3dToggleOpening(selSeg, hit.i); return; }
      }
      const rect2 = canvas.getBoundingClientRect();
      zoomAt(e.shiftKey ? 0.6 : 1.6, e.clientX - rect2.left, e.clientY - rect2.top);
    });
  }

  window.Plan3D = {
    active: false,
    zoomBy(f) { zoomAt(f); },                        // toolbar ± while 3D owns the view
    refit() { zoom = 0; },
    rotate90() { yaw += Math.PI / 2; },
    // Tear down unconditionally (tab switch, renderMap, studio-off). Safe to
    // call when already closed. Returns true if it was open.
    stop() {
      if (!this.active) return false;
      this.active = false;
      cancelAnimationFrame(raf); raf = 0;
      clearInterval(pollTimer); pollTimer = 0;
      editMode = false; selSeg = null; editDrag = null; drag = null;
      if (host) host.innerHTML = "";
      host = canvas = ctx = null;
      return true;
    },
    toggle(hostEl) {
      if (this.active) {                      // ---- close
        this.stop();
        return false;
      }
      host = hostEl;                          // ---- open
      host.innerHTML = '<div class="p3d-wrap"><canvas class="p3d-canvas"></canvas>' +
        '<div class="p3d-ctl">' +
        '<button type="button" class="p3d-btn" data-a="edit">✏️ Edit</button>' +
        '<button type="button" class="p3d-btn" data-a="wall-">▁ Walls</button>' +
        '<button type="button" class="p3d-btn" data-a="wall+">▔ Walls</button>' +
        '<button type="button" class="p3d-btn" data-a="glass"></button>' +
        '<button type="button" class="p3d-btn" data-a="fit">⤢ Fit</button>' +
        '</div>' +
        '<div class="p3d-hint" data-hint></div></div>';
      canvas = host.querySelector("canvas");
      ctx = canvas.getContext("2d");
      zoom = 0; yaw = -0.62; elev = 1.02;
      // wall controls: exterior height, interior room walls off/low/full
      const glassBtn = host.querySelector('[data-a="glass"]');
      const syncGlass = () => { glassBtn.textContent = "▦ Room walls: " + glassMode; };
      syncGlass();
      host.querySelector('[data-a="wall-"]').onclick = () => { wallH = Math.max(600, wallH - 400); persistWalls(); };
      host.querySelector('[data-a="wall+"]').onclick = () => { wallH = Math.min(6000, wallH + 400); persistWalls(); };
      glassBtn.onclick = () => {
        glassMode = glassMode === "low" ? "full" : (glassMode === "full" ? "off" : "low");
        persistWalls(); syncGlass();
      };
      host.querySelector('[data-a="fit"]').onclick = () => { zoom = 0; };
      // ✏️ in-3D edit mode: tap selects a room, drag its handles, dbl-tap a
      // wall handle toggles opening. Off = tap jumps to the 2D editor.
      const editBtn = host.querySelector('[data-a="edit"]');
      const hint = host.querySelector("[data-hint]");
      const syncEdit = () => {
        editBtn.style.background = editMode ? "#3ec6dd" : "";
        editBtn.style.color = editMode ? "#04222b" : "";
        hint.textContent = editMode
          ? "tap room = select · drag ● corner / ■ wall = reshape · drag ◆ top = wall height · dbl-tap ■ = opening · dbl-tap ◆ = default"
          : "drag = orbit · double-tap = zoom · wheel = zoom · tap a room = edit in 2D — live robot + device state";
      };
      editMode = false; selSeg = null;
      syncEdit();
      editBtn.onclick = () => { editMode = !editMode; if (!editMode) selSeg = null; syncEdit(); };
      attach();
      this.active = true;
      if (window._plan3dTick) {
        window._plan3dTick();
        pollTimer = setInterval(() => window._plan3dTick(), 5000);
      }
      raf = requestAnimationFrame(draw);
      return true;
    },
  };
})();
