/* geom.js — rectilinear geometry cleanup (window.Geom).
   Turns noisy lidar-traced polygons into house-like walls WITHOUT introducing
   diagonals: collapse wall features smaller than real-world size, and snap
   near-coincident wall lines (across rooms + outline) onto shared axes.
   Loaded before app.js; pure functions, no app state. */
(function () {
  "use strict";

  const elen = (a, b) => Math.abs(a[0] - b[0]) + Math.abs(a[1] - b[1]);

  function dedupe(p) {
    const out = [];
    for (const q of p) {
      const l = out[out.length - 1];
      if (!l || l[0] !== q[0] || l[1] !== q[1]) out.push(q);
    }
    while (out.length > 1 && out[0][0] === out[out.length - 1][0] && out[0][1] === out[out.length - 1][1]) out.pop();
    return out;
  }

  function rmCollinear(p) {
    const out = [], n = p.length;
    for (let i = 0; i < n; i++) {
      const a = p[(i - 1 + n) % n], b = p[i], c = p[(i + 1) % n];
      if ((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) !== 0) out.push(b);
    }
    return out.length >= 3 ? out : p;
  }

  /* Remove rectilinear features (notches/steps) whose wall segment is shorter
     than minLen: delete the short edge and slide the SHORTER of its two
     neighbouring walls onto the longer one's line. Stays axis-aligned by
     construction; iterates shortest-first until nothing is below minLen. */
  function collapseShort(pts, minLen) {
    let p = dedupe(rmCollinear(pts.map(q => [q[0], q[1]])));
    let guard = 0;
    while (p.length > 4 && guard++ < 400) {
      const n = p.length;
      let bi = -1, bl = Infinity;
      for (let i = 0; i < n; i++) {
        const l = elen(p[i], p[(i + 1) % n]);
        if (l < minLen && l < bl) { bl = l; bi = i; }
      }
      if (bi < 0) break;
      const A = p[bi], B = p[(bi + 1) % n];
      const P = p[(bi - 1 + n) % n], Q = p[(bi + 2) % n];
      const vert = A[0] === B[0];                    // short edge vertical → neighbours horizontal
      const lenPA = elen(P, A), lenBQ = elen(B, Q);
      const keep = [];
      for (let i = 0; i < n; i++) if (i !== bi && i !== (bi + 1) % n) keep.push(p[i]);
      // slide the shorter neighbour onto the longer one's line
      if (vert) {
        const y = lenPA >= lenBQ ? A[1] : B[1];
        for (const q of keep) {
          if (lenPA >= lenBQ && q === Q) q[1] = y;
          if (lenPA < lenBQ && q === P) q[1] = y;
        }
      } else {
        const x = lenPA >= lenBQ ? A[0] : B[0];
        for (const q of keep) {
          if (lenPA >= lenBQ && q === Q) q[0] = x;
          if (lenPA < lenBQ && q === P) q[0] = x;
        }
      }
      const next = dedupe(rmCollinear(keep));
      if (next.length < 4) break;
      p = next;
    }
    return p;
  }

  /* Snap near-coincident wall lines onto shared axes across many polygons:
     1-D cluster all x coords (and separately all y coords) within tol, then
     move every vertex to its cluster's weighted centre. Shared walls between
     rooms (and against the outline) become exactly the same line. */
  function snapAxes(polys, tol) {
    for (const axis of [0, 1]) {
      const vals = [];
      polys.forEach(p => p.forEach(q => vals.push(q[axis])));
      vals.sort((a, b) => a - b);
      const snap = new Map();                        // value -> cluster centre
      let start = 0;
      for (let i = 1; i <= vals.length; i++) {
        if (i === vals.length || vals[i] - vals[i - 1] > tol) {
          const c = vals.slice(start, i);
          const mid = Math.round(c.reduce((s, v) => s + v, 0) / c.length);
          c.forEach(v => snap.set(v, mid));
          start = i;
        }
      }
      polys.forEach(p => p.forEach(q => { q[axis] = snap.get(q[axis]) ?? q[axis]; }));
    }
    // snapping can create zero-length edges — clean each polygon up
    for (let i = 0; i < polys.length; i++) {
      const c = dedupe(rmCollinear(polys[i]));
      if (c.length >= 3) { polys[i].length = 0; polys[i].push(...c); }
    }
    return polys;
  }

  window.Geom = { collapseShort, snapAxes };
})();
