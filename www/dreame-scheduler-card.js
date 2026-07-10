/* Dreame Scheduler — custom Lovelace card.
 *
 * A self-contained web component (no build step) that shows the scheduler's
 * week status + quick controls. Registers itself in the card picker so it can
 * be added from the HA dashboard GUI ("Add card" → "Dreame Scheduler").
 *
 * Config: { type: "custom:dreame-scheduler-card", entity: sensor.<vacuum>_scheduler_status }
 * The sibling entities (buttons/switch/rooms-cleaned) are derived from that
 * status sensor's id, so a single entity is all that's needed — portable to
 * any user's vacuum.
 *
 * Deploy: copy to /config/www/ and add /local/dreame-scheduler-card.js as a
 * Lovelace resource (type: module). The add-on's Dashboard tab can do this.
 */

const VERSION = "1.1.0";

class DreameSchedulerCard extends HTMLElement {
  setConfig(config) {
    if (!config || !config.entity) {
      throw new Error("Set 'entity' to the scheduler status sensor (sensor.*_scheduler_status).");
    }
    this._config = config;
    this._built = false;
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() { return 4; }

  // Sibling entity ids derived from the status sensor's object_id prefix.
  _ids() {
    const status = this._config.entity;                 // sensor.<base>_status
    const base = status.replace(/^sensor\./, "").replace(/_status$/, "");
    return {
      status,
      rooms_cleaned: `sensor.${base}_rooms_cleaned_this_week`,
      enabled: `switch.${base}_scheduler_enabled`,
      run_today: `button.${base}_run_today_s_schedule_now`,
      clean_now: `button.${base}_clean_now`,
      clean_quiet: `button.${base}_clean_now_quiet`,
      reset_week: `button.${base}_reset_week_counters`,
    };
  }

  _press(entityId) {
    if (this._hass.states[entityId]) this._hass.callService("button", "press", { entity_id: entityId });
  }
  _toggleEnabled(entityId) {
    const st = this._hass.states[entityId];
    if (st) this._hass.callService("switch", st.state === "on" ? "turn_off" : "turn_on", { entity_id: entityId });
  }

  _render() {
    const hass = this._hass, cfg = this._config;
    if (!hass || !cfg) return;
    const ids = this._ids();
    const s = hass.states[ids.status];
    if (!s) {
      this.innerHTML = `<ha-card><div style="padding:16px;color:var(--error-color,#c00)">Unknown entity: ${ids.status}</div></ha-card>`;
      return;
    }
    const a = s.attributes || {};
    const summary = a.week_summary || "No runs yet this week.";
    const reason = a.reason || "";
    const rc = hass.states[ids.rooms_cleaned];
    const total = rc && rc.attributes ? (rc.attributes.rooms_total || 0) : 0;
    const done = rc ? parseInt(rc.state, 10) || 0 : 0;
    const pct = total ? Math.round(100 * done / total) : 0;
    const sw = hass.states[ids.enabled];
    const enabled = sw ? sw.state === "on" : true;
    const title = cfg.title || (a.friendly_name ? String(a.friendly_name).replace(/ Status$/, "") : "Vacuum scheduler");

    if (!this._built) {
      this.innerHTML = `
        <ha-card>
          <style>
            .dsc { padding: 14px 16px 16px; }
            .dsc-h { display:flex; align-items:center; justify-content:space-between; gap:10px; margin-bottom:8px; }
            .dsc-title { font-size:16px; font-weight:600; }
            .dsc-state { font-size:12px; color:var(--secondary-text-color); }
            .dsc-summary { white-space:pre-line; font-size:14px; margin:6px 0 12px; }
            .dsc-bar { height:8px; border-radius:6px; background:var(--divider-color,#3336); overflow:hidden; margin:6px 0 4px; }
            .dsc-fill { height:100%; background:var(--primary-color,#3b82f6); border-radius:6px; transition:width .3s; }
            .dsc-prog { font-size:12px; color:var(--secondary-text-color); margin-bottom:12px; }
            .dsc-actions { display:flex; flex-wrap:wrap; gap:8px; }
            .dsc-btn { flex:1 1 auto; min-width:110px; border:1px solid var(--divider-color,#3336); background:var(--secondary-background-color,#2226); color:var(--primary-text-color); border-radius:10px; padding:9px 10px; font-size:13px; cursor:pointer; }
            .dsc-btn:hover { border-color:var(--primary-color,#3b82f6); }
            .dsc-sw { display:flex; align-items:center; gap:8px; cursor:pointer; font-size:13px; }
          </style>
          <div class="dsc">
            <div class="dsc-h">
              <span class="dsc-title" data-t></span>
              <label class="dsc-sw"><input type="checkbox" data-en> <span>Enabled</span></label>
            </div>
            <div class="dsc-state" data-st></div>
            <div class="dsc-summary" data-sum></div>
            <div class="dsc-bar"><div class="dsc-fill" data-fill></div></div>
            <div class="dsc-prog" data-prog></div>
            <div class="dsc-actions">
              <button class="dsc-btn" data-a="run_today">▶ Run today</button>
              <button class="dsc-btn" data-a="clean_now">🧹 Clean pending</button>
              <button class="dsc-btn" data-a="clean_quiet">🔉 Quiet</button>
              <button class="dsc-btn" data-a="reset_week">↺ Reset week</button>
            </div>
          </div>
        </ha-card>`;
      this.querySelectorAll(".dsc-btn").forEach(b =>
        b.addEventListener("click", () => this._press(this._ids()[b.dataset.a])));
      this.querySelector("[data-en]").addEventListener("change", () => this._toggleEnabled(this._ids().enabled));
      this._built = true;
    }

    this.querySelector("[data-t]").textContent = title;
    this.querySelector("[data-st]").textContent = reason ? `${s.state} — ${reason}` : s.state;
    this.querySelector("[data-sum]").textContent = summary;
    this.querySelector("[data-fill]").style.width = pct + "%";
    this.querySelector("[data-prog]").textContent = total ? `${done}/${total} rooms cleaned this week` : "";
    this.querySelector("[data-en]").checked = enabled;
  }

  static getStubConfig(hass) {
    const found = Object.keys(hass.states || {}).find(
      id => id.startsWith("sensor.") && id.endsWith("_scheduler_status"));
    return { entity: found || "sensor.vacuum_scheduler_status" };
  }
}

customElements.define("dreame-scheduler-card", DreameSchedulerCard);

// Shared bits for the smaller companion cards (robot + presence). Both read
// everything from the one scheduler status sensor's attributes, so — like the
// main card — a single entity is all the config they need.
const _cap = (s) => (s ? String(s).replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()) : "—");
function _stubStatus(hass) {
  const found = Object.keys(hass.states || {}).find(
    (id) => id.startsWith("sensor.") && id.endsWith("_scheduler_status"));
  return { entity: found || "sensor.vacuum_scheduler_status" };
}
const _CARD_CSS = `
  .dsc2 { padding: 14px 16px 16px; }
  .dsc2-h { font-size:16px; font-weight:600; margin-bottom:10px; }
  .dsc2-top { display:flex; align-items:center; justify-content:space-between; gap:10px; margin-bottom:6px; }
  .dsc2-state { font-size:15px; font-weight:600; }
  .dsc2-bat { font-size:14px; color:var(--secondary-text-color); white-space:nowrap; }
  .dsc2-row { font-size:14px; margin:4px 0; }
  .dsc2-err { color:var(--error-color,#e53935); }
  .dsc2-chips { display:flex; flex-wrap:wrap; gap:6px; margin-top:10px; }
  .dsc2-chip { font-size:12px; padding:4px 9px; border-radius:999px; border:1px solid var(--divider-color,#3336); background:var(--secondary-background-color,#2226); }
  .dsc2-muted { color:var(--secondary-text-color); }`;

/* ---- Robot status card ---- */
class DreameRobotCard extends HTMLElement {
  setConfig(config) {
    if (!config || !config.entity) throw new Error("Set 'entity' to the scheduler status sensor (sensor.*_scheduler_status).");
    this._config = config;
  }
  set hass(hass) { this._hass = hass; this._render(); }
  getCardSize() { return 3; }
  static getStubConfig(hass) { return _stubStatus(hass); }
  _render() {
    const hass = this._hass, cfg = this._config;
    if (!hass || !cfg) return;
    const s = hass.states[cfg.entity];
    if (!s) { this.innerHTML = `<ha-card><div style="padding:16px;color:var(--error-color,#c00)">Unknown entity: ${cfg.entity}</div></ha-card>`; return; }
    const r = (s.attributes && s.attributes.robot) || {};
    const bat = (r.battery != null) ? Math.round(r.battery) + "%" : "—";
    const batIcon = (r.battery != null && r.battery < 20) ? "🪫" : "🔋";
    const errNorm = r.error ? String(r.error).toLowerCase().replace(/_/g, " ").trim() : "";
    const err = errNorm && !["none", "no error", "0", "ok"].includes(errNorm) ? r.error : null;
    const chip = (lbl, v) => v ? `<span class="dsc2-chip">${lbl} ${_cap(v)}</span>` : "";
    const chips = chip("🗑️", r.dust_bag) + chip("💧", r.clean_water) + chip("🪣", r.dirty_water);
    this.innerHTML = `<ha-card><style>${_CARD_CSS}</style><div class="dsc2">
      <div class="dsc2-h">🤖 ${cfg.title || "Robot status"}</div>
      <div class="dsc2-top"><span class="dsc2-state">🧭 ${_cap(r.status || r.vacuum_state)}</span><span class="dsc2-bat">${batIcon} ${bat}</span></div>
      ${r.current_room ? `<div class="dsc2-row">📍 In <b>${r.current_room}</b></div>` : ""}
      ${err ? `<div class="dsc2-row dsc2-err">⚠️ ${_cap(err)}</div>` : ""}
      ${chips ? `<div class="dsc2-chips">${chips}</div>` : ""}
    </div></ha-card>`;
  }
}
customElements.define("dreame-robot-card", DreameRobotCard);

/* ---- Presence & next run card ---- */
class DreamePresenceCard extends HTMLElement {
  setConfig(config) {
    if (!config || !config.entity) throw new Error("Set 'entity' to the scheduler status sensor (sensor.*_scheduler_status).");
    this._config = config;
  }
  set hass(hass) { this._hass = hass; this._render(); }
  getCardSize() { return 2; }
  static getStubConfig(hass) { return _stubStatus(hass); }
  _render() {
    const hass = this._hass, cfg = this._config;
    if (!hass || !cfg) return;
    const s = hass.states[cfg.entity];
    if (!s) { this.innerHTML = `<ha-card><div style="padding:16px;color:var(--error-color,#c00)">Unknown entity: ${cfg.entity}</div></ha-card>`; return; }
    const a = s.attributes || {};
    let presence;
    if (!a.presence_configured) presence = `<div class="dsc2-row dsc2-muted">No presence entities set — runs aren't presence-gated.</div>`;
    else presence = `<div class="dsc2-state">${a.presence_home === true ? "🏠 Someone's home" : a.presence_home === false ? "🚶 Everyone's out" : "❓ Presence unknown"}</div>`;
    const next = a.next_run_day
      ? `<div class="dsc2-row">⏰ Next run <b>${a.next_run_day}</b> at <b>${a.next_run_time}</b></div>`
      : `<div class="dsc2-row dsc2-muted">No upcoming scheduled run — enable rooms &amp; days.</div>`;
    this.innerHTML = `<ha-card><style>${_CARD_CSS}</style><div class="dsc2">
      <div class="dsc2-h">🏠 ${cfg.title || "Presence & next run"}</div>
      ${presence}
      ${next}
    </div></ha-card>`;
  }
}
customElements.define("dreame-presence-card", DreamePresenceCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "dreame-scheduler-card",
  name: "Dreame Scheduler",
  description: "Presence-aware vacuum room scheduler — week status + controls.",
  preview: true,
  documentationURL: "https://github.com/botts7/dreame-scheduler",
}, {
  type: "dreame-robot-card",
  name: "Dreame Robot Status",
  description: "Live robot state — battery, what it's doing, error, tanks & bag.",
  preview: true,
  documentationURL: "https://github.com/botts7/dreame-scheduler",
}, {
  type: "dreame-presence-card",
  name: "Dreame Presence & Next Run",
  description: "Who's home + when the scheduler next runs.",
  preview: true,
  documentationURL: "https://github.com/botts7/dreame-scheduler",
});
console.info(`%c DREAME-SCHEDULER-CARD %c ${VERSION} `, "color:#fff;background:#3b82f6;border-radius:3px 0 0 3px", "color:#3b82f6;background:#1a1d28;border-radius:0 3px 3px 0");
