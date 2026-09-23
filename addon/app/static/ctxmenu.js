/* ctxmenu.js — lightweight right-click context menu (window.CtxMenu).
   Production-app pattern: right-click anywhere on the plan for tools and
   context actions. Generic: callers pass [{label, icon?, on?, sep?, active?,
   disabled?}] items. One menu instance, repositioned per open, closed by
   click-away / Escape / scroll. No dependencies. */
(function () {
  "use strict";

  let el = null;

  function ensure() {
    if (el) return el;
    el = document.createElement("div");
    el.className = "ctx-menu";
    el.style.display = "none";
    document.body.appendChild(el);
    const close = () => { el.style.display = "none"; };
    document.addEventListener("pointerdown", e => { if (!el.contains(e.target)) close(); }, true);
    document.addEventListener("keydown", e => { if (e.key === "Escape") close(); });
    window.addEventListener("blur", close);
    document.addEventListener("scroll", close, true);
    return el;
  }

  window.CtxMenu = {
    close() { if (el) el.style.display = "none"; },
    open(x, y, items) {
      const m = ensure();
      // Reparent into whatever is covering the screen, or the menu opens behind
      // it. True fullscreen → fullscreenElement; the CSS-overlay fallback (the
      // path HA ingress always takes, since requestFullscreen is blocked in the
      // iframe) → the .map-fs card; otherwise body.
      const root = document.fullscreenElement || document.querySelector(".card.map-fs") || document.body;
      if (m.parentNode !== root) root.appendChild(m);
      m.innerHTML = "";
      for (const it of items) {
        if (it.sep) { const s = document.createElement("div"); s.className = "cm-sep"; m.appendChild(s); continue; }
        const b = document.createElement("button");
        b.type = "button";
        b.className = "cm-item" + (it.active ? " active" : "") + (it.danger ? " danger" : "");
        b.disabled = !!it.disabled;
        const ic = document.createElement("span");
        ic.className = "cm-ic"; ic.textContent = it.icon || "";
        b.appendChild(ic);
        b.appendChild(document.createTextNode(it.label));   // labels may carry room names — text only
        b.onclick = () => { m.style.display = "none"; if (it.on) it.on(); };
        m.appendChild(b);
      }
      m.style.display = "block";
      m.style.left = "0px"; m.style.top = "0px";          // measure first
      const r = m.getBoundingClientRect();
      m.style.left = Math.min(x, window.innerWidth - r.width - 8) + "px";
      m.style.top = Math.min(y, window.innerHeight - r.height - 8) + "px";
    },
  };
})();
