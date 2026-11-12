/* Flugwetter – Karte und Dashboard-Strategie für die Flugwetter-Integration.
 * Wird von der Integration automatisch geladen.
 */
const FW_VERSION = "0.1.0";
const FW_DOMAIN = "flugwetter";

const DEFAULT_LIMITS = { xw: [8, 15], wind: [15, 20], boe: [20, 25], ceil: [2500, 1500] };
const KEYS = ["flugtag_heute", "flugtag_morgen", "metar", "taf", "gafor", "seitenwind"];
const STATUS_FALLBACK = { gut: "Guter Flugtag", eingeschraenkt: "Eingeschränkt", schlecht: "Kein Flugwetter", keine_daten: "Keine Daten" };

/** Entitäten eines Flugplatz-Geräts über die Übersetzungsschlüssel finden. */
function fwEntities(hass, deviceId) {
  const out = {};
  if (!hass || !hass.entities) return out;
  for (const e of Object.values(hass.entities)) {
    if (e.device_id !== deviceId) continue;
    const key = e.translation_key || KEYS.find((k) => e.entity_id.endsWith(k));
    if (key) out[key] = e.entity_id;
  }
  return out;
}

function fwDevices(hass) {
  if (!hass || !hass.entities || !hass.devices) return [];
  const ids = new Set(Object.values(hass.entities).filter((e) => e.platform === FW_DOMAIN && e.device_id).map((e) => e.device_id));
  return [...ids].map((id) => hass.devices[id]).filter(Boolean);
}

const LVL = [
  { key: "ok", label: "Fliegbar", color: "var(--fw-green)" },
  { key: "warn", label: "Eingeschränkt", color: "var(--fw-amber)" },
  { key: "bad", label: "Kein Flugwetter", color: "var(--fw-red)" },
];

const pad = (n) => String(n).padStart(2, "0");
const rad = (d) => (d * Math.PI) / 180;
const pt = (cx, cy, r, deg) => [cx + r * Math.sin(rad(deg)), cy - r * Math.cos(rad(deg))];
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const rwyNum = (h) => pad(Math.round(((h % 360) || 360) / 10));

class FlugwetterCard extends HTMLElement {
  setConfig(config) {
    this._config = { ...config };
    this._tab = "heute";
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._sig = null;
  }

  set hass(hass) {
    this._hass = hass;
    let dev = this._config.device_id;
    if (!dev) dev = fwDevices(hass)[0]?.id;
    this._dev = dev;
    this._ids = fwEntities(hass, dev);
    const ids = Object.values(this._ids);
    const sig = ids.map((id) => hass.states[id]?.last_updated).join("|") + "|" + new Date().getMinutes();
    if (sig === this._sig) return;
    this._sig = sig;
    this._render();
  }

  getCardSize() {
    return 12;
  }

  getGridOptions() {
    return { columns: "full", rows: "auto" };
  }

  static getStubConfig(hass) {
    return { device_id: fwDevices(hass)[0]?.id || "" };
  }

  static getConfigElement() {
    return document.createElement("flugwetter-card-editor");
  }

  // Platzdaten und Grenzwerte kommen aus dem Sensor „Flugtag heute“
  get _runway() {
    return Number(this._st(this._ids.flugtag_heute)?.attributes?.bahn_kurs ?? 90);
  }

  get _limits() {
    const g = this._st(this._ids.flugtag_heute)?.attributes?.grenzwerte;
    if (!g) return DEFAULT_LIMITS;
    return { xw: [g.xw_warn, g.xw_limit], wind: [g.wind_warn, g.wind_limit], boe: [g.gust_warn, g.gust_limit], ceil: [g.ceil_warn, g.ceil_limit] };
  }

  _st(id) {
    return this._hass?.states?.[id];
  }

  // ---------- Windrose ----------
  _compass(wd, ws, wg) {
    const c = { runway: this._runway };
    const L = this._limits;
    const cx = 120, cy = 120, R = 104;
    let s = "";
    // Farbiger Außenring: zulässige Windrichtungen bei aktueller Windstärke
    if (ws > 0) {
      for (let d = 0; d < 360; d += 10) {
        const xw = Math.abs(ws * Math.sin(rad(d - c.runway)));
        const col = xw >= L.xw[1] ? "var(--fw-red)" : xw >= L.xw[0] ? "var(--fw-amber)" : "var(--fw-green)";
        const [x1, y1] = pt(cx, cy, R, d - 4.5);
        const [x2, y2] = pt(cx, cy, R, d + 4.5);
        s += `<path d="M${x1},${y1} A${R},${R} 0 0 1 ${x2},${y2}" stroke="${col}" stroke-width="7" fill="none" opacity=".85"/>`;
      }
    } else {
      s += `<circle cx="${cx}" cy="${cy}" r="${R}" stroke="var(--fw-green)" stroke-width="7" fill="none" opacity=".5"/>`;
    }
    // Skala
    for (let d = 0; d < 360; d += 10) {
      const long = d % 30 === 0;
      const [x1, y1] = pt(cx, cy, R - 8, d);
      const [x2, y2] = pt(cx, cy, R - (long ? 18 : 13), d);
      s += `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="var(--fw-muted)" stroke-width="${long ? 2 : 1}"/>`;
      if (long) {
        const lbl = { 0: "N", 90: "O", 180: "S", 270: "W" }[d] ?? String(d / 10);
        const [tx, ty] = pt(cx, cy, R - 30, d);
        const big = d % 90 === 0;
        s += `<text x="${tx}" y="${ty}" text-anchor="middle" dominant-baseline="central" class="${big ? "card" : "num"}">${lbl}</text>`;
      }
    }
    // Bahn
    const rl = 104, rw = 24;
    const yTop = cy - rl / 2 + 14, yBot = cy + rl / 2 - 14;
    s += `<g transform="rotate(${c.runway} ${cx} ${cy})">
      <rect x="${cx - rw / 2}" y="${cy - rl / 2}" width="${rw}" height="${rl}" rx="3" fill="var(--fw-runway)" stroke="rgba(255,255,255,.35)" stroke-width="1"/>
      <line x1="${cx}" y1="${yTop + 12}" x2="${cx}" y2="${yBot - 12}" stroke="#fff" stroke-width="1.5" stroke-dasharray="7 6"/>
      <text x="${cx}" y="${yTop}" text-anchor="middle" dominant-baseline="central" class="rwy">${rwyNum(c.runway + 180)}</text>
      <text x="${cx}" y="${yBot}" text-anchor="middle" dominant-baseline="central" class="rwy" transform="rotate(180 ${cx} ${yBot})">${rwyNum(c.runway)}</text>
    </g>`;
    // Windpfeil (zeigt, wohin der Wind weht)
    if (ws > 0 && wd != null) {
      const [x1, y1] = pt(cx, cy, 90, wd);
      const [x2, y2] = pt(cx, cy, 62, wd + 180);
      const [h1x, h1y] = pt(x2, y2, 14, wd + 180 + 150);
      const [h2x, h2y] = pt(x2, y2, 14, wd + 180 - 150);
      s += `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="var(--fw-arrow)" stroke-width="5" stroke-linecap="round"/>
        <polygon points="${x2 + (x2 - x1) * 0.0},${y2} ${h1x},${h1y} ${h2x},${h2y}" fill="var(--fw-arrow)"/>
        <circle cx="${x1}" cy="${y1}" r="5" fill="var(--fw-arrow)"/>`;
    } else {
      s += `<text x="${cx}" y="${cy + 34}" text-anchor="middle" class="num">Windstill</text>`;
    }
    return `<svg viewBox="0 0 240 240" class="compass" role="img" aria-label="Windrose">${s}</svg>`;
  }

  // ---------- Stundenverlauf ----------
  _timeline(rows) {
    const L = this._limits;
    if (!rows || !rows.length) return `<div class="empty">Keine Tageslichtstunden mehr für diesen Tag.</div>`;
    const now = new Date();
    const nowKey = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}`;
    const H = 92, top = 10, VW = 1000;
    const maxV = Math.max(L.boe[1] + 5, ...rows.map((r) => r.wg || 0)) ;
    const y = (v) => top + (H - top) * (1 - v / maxV);
    const n = rows.length;
    const X = (i) => ((i + 0.5) / n) * VW;
    let bands = "", wind = "", gust = "";
    rows.forEach((r, i) => {
      const x = X(i);
      wind += `${i ? "L" : "M"}${x},${y(r.ws)}`;
      gust += `${i ? "L" : "M"}${x},${y(r.wg)}`;
    });
    const lim = (v, cls) => `<line x1="0" x2="${VW}" y1="${y(v)}" y2="${y(v)}" class="${cls}"/>`;
    const lbl = (v, t) => `<span class="limt" style="top:${y(v) - 13}px">${t}</span>`;
    const chart = `<div class="chartwrap" style="height:${H + 4}px">
      <svg viewBox="0 0 ${VW} ${H + 4}" preserveAspectRatio="none" class="chart">
      ${lim(L.boe[1], "lim-red")}${lim(L.wind[0], "lim-amber")}
      <path d="${gust}" class="gust"/><path d="${wind}" class="wind"/>
      </svg>${lbl(L.boe[1], `Böen ${L.boe[1]} kt`)}${lbl(L.wind[0], `Wind ${L.wind[0]} kt`)}</div>`;
    const cols = rows
      .map((r) => {
        const cur = `${r.d} ${r.h.slice(0, 2)}` === nowKey;
        const lv = LVL[r.lvl] || LVL[0];
        const ceil = r.ceil ? `${r.ceil}` : "—";
        const ceilCls = r.ceil == null ? "" : r.ceil < L.ceil[1] ? "t-red" : r.ceil < L.ceil[0] ? "t-amber" : "";
        const xwCls = r.xw >= L.xw[1] ? "t-red" : r.xw >= L.xw[0] ? "t-amber" : "";
        return `<div class="hr ${cur ? "cur" : ""}" title="${esc(r.why || "Keine Einschränkung")}">
          <div class="h">${esc(r.h)}</div>
          <div class="pill" style="background:${lv.color}"></div>
          <svg viewBox="-12 -12 24 24" class="arrow" style="transform:rotate(${r.wd + 180}deg)"><path d="M0,-9 L5,4 L0,1 L-5,4 Z"/></svg>
          <div class="v">${r.ws}${r.wg - r.ws >= 5 ? `<span class="g">G${r.wg}</span>` : ""}</div>
          <div class="s ${xwCls}">${r.xw}<small> ${esc(r.bahn)}</small></div>
          <div class="s ${ceilCls}">${ceil}</div>
        </div>`;
      })
      .join("");
    return `<div class="tl-scroll"><div class="tl" style="min-width:${n * 56}px">
        <div class="cols">${cols}</div>${chart}
      </div></div>
      <div class="legend"><span><i class="lg-w"></i>Wind</span><span><i class="lg-g"></i>Böen</span>
      <span>Zeilen: Wind kt · Seitenwind kt (Bahn) · Wolken ft AGL</span></div>`;
  }

  _status(e, small) {
    if (!e) return "";
    const st = Number(e.attributes.stufe ?? -1);
    const lv = LVL[st];
    const color = lv ? lv.color : "var(--fw-muted)";
    const fenster = e.attributes.fenster;
    const g0 = e.attributes.gruende || [];
    const gr = (Array.isArray(g0) ? g0 : String(g0).split(",")).map((x) => String(x).trim()).filter(Boolean);
    return `<div class="status ${small ? "small" : ""}" style="--c:${color}">
      <div class="dotbig"></div>
      <div>
        <div class="st">${esc(this._hass.formatEntityState ? this._hass.formatEntityState(e) : STATUS_FALLBACK[e.state] || e.state)}</div>
        ${fenster ? `<div class="sub">Bestes Fenster ${esc(fenster)} Uhr</div>` : ""}
        ${gr.length ? `<div class="chips">${gr.map((g) => `<span>${esc(g)}</span>`).join("")}</div>` : ""}
      </div>
    </div>`;
  }

  _metarInfo(m) {
    if (!m) return null;
    const a = m.attributes;
    let vis = "—";
    if (a.sicht != null) {
      const v = String(a.sicht);
      vis = v.includes("+") ? "≥ 10 km" : `${Math.min(10, Math.round(parseFloat(v) * 1.609))} km`;
    }
    let ceil = "Keine Decke";
    const cl = Array.isArray(a.wolken) ? a.wolken : [];
    const dec = cl.filter((x) => ["BKN", "OVC", "VV"].includes(x.cover)).sort((p, q) => p.base - q.base)[0];
    if (dec) ceil = `${dec.cover} ${dec.base} ft`;
    else if (cl.length && cl[0].cover !== "CAVOK" && cl[0].base) ceil = `${cl[0].cover} ${cl[0].base} ft`;
    const ws = Number(a.wind_kt ?? 0), wg = a.boeen_kt != null ? Number(a.boeen_kt) : null;
    const wd = a.wind_richtung === "VRB" || a.wind_richtung == null ? null : Number(a.wind_richtung);
    let age = "";
    if (a.beobachtet) {
      const t = Date.parse(a.beobachtet);
      const mins = Math.round((Date.now() - t) / 60000);
      if (!isNaN(mins)) age = mins < 120 ? `vor ${mins} min` : `vor ${Math.round(mins / 60)} h`;
    }
    return { vis, ceil, ws, wg, wd, temp: a.temperatur, dewp: a.taupunkt, qnh: a.qnh, cat: a.flugkategorie, age, raw: m.state, station: a.station };
  }

  _render() {
    const L = this._limits;
    const runway = this._runway;
    const c = { runway };
    const ids = this._ids || {};
    if (!this._dev || !ids.flugtag_heute) {
      this.shadowRoot.innerHTML = `<ha-card><div style="padding:16px">Kein Flugplatz gefunden. Richte zuerst die Integration „Flugwetter“ ein und wähle dann den Flugplatz in den Karteneinstellungen.</div></ha-card>`;
      return;
    }
    const heute = this._st(ids.flugtag_heute), morgen = this._st(ids.flugtag_morgen);
    const met = this._metarInfo(this._st(ids.metar));
    const taf = this._st(ids.taf);
    const gaforSt = this._st(ids.gafor);
    const title = this._config.title || this._hass.devices?.[this._dev]?.name_by_user || this._hass.devices?.[this._dev]?.name || "Flugwetter";
    const now = new Date();
    const utc = `${pad(now.getUTCHours())}:${pad(now.getUTCMinutes())}`;
    const lt = `${pad(now.getHours())}:${pad(now.getMinutes())}`;

    // Seitenwind aktuell
    let xwHtml = "";
    if (met) {
      const ws = met.ws, wd = met.wd;
      const ang = wd == null ? 0 : wd - c.runway;
      const head = wd == null ? 0 : ws * Math.cos(rad(ang));
      const active = head >= 0 ? c.runway : c.runway + 180;
      const xw = wd == null ? 0 : Math.abs(ws * Math.sin(rad(ang)));
      const hw = Math.abs(head);
      const xwg = met.wg && wd != null ? Math.abs(met.wg * Math.sin(rad(ang))) : null;
      const col = xw >= L.xw[1] ? "var(--fw-red)" : xw >= L.xw[0] ? "var(--fw-amber)" : "var(--fw-green)";
      const fromRight = wd != null && Math.sin(rad(wd - active)) > 0;
      xwHtml = `<div class="xw" style="--c:${col}">
        <div class="rwybadge">${rwyNum(active)}</div>
        <div>
          <div class="big">${Math.round(xw)} <small>kt</small></div>
          <div class="sub">Seitenwind ${wd == null ? "(umlaufend)" : fromRight ? "von rechts" : "von links"}${xwg ? ` · Böen ${Math.round(xwg)} kt` : ""}</div>
          <div class="sub">Gegenwind ${Math.round(hw)} kt · Bahn ${rwyNum(active)} aktiv</div>
        </div>
      </div>`;
    }

    const rowsH = heute?.attributes?.stunden || [];
    const rowsM = morgen?.attributes?.stunden || [];
    const rows = this._tab === "heute" ? rowsH : rowsM;

    const gA = gaforSt?.attributes || {};
    const todayIso = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
    const gaforFresh = gA.datum === todayIso;
    const gafor = (gaforFresh ? gA.fenster || [] : [])
      .map((w) => {
        const code = String(w.code || "").trim();
        const k = code[0];
        const col = ["C", "O"].includes(k) ? "var(--fw-green)" : k === "D" ? "var(--fw-amber)" : ["M", "X"].includes(k) ? "var(--fw-red)" : "var(--fw-muted)";
        const off = -now.getTimezoneOffset() / 60;
        const loc = `${pad((w.von_utc + off) % 24)}–${pad((w.bis_utc + off) % 24)} Uhr`;
        return `<div class="gf" style="--c:${col}"><div class="gc">${esc(code || "—")}</div><div class="gt">${esc(loc)}</div><div class="gu">${pad(w.von_utc)}–${pad(w.bis_utc)} UTC</div></div>`;
      })
      .join("");
    const gaforName = gA.gebiet ? `${gA.gebiet}${gA.gebietsname ? " · " + gA.gebietsname : ""}` : "";
    const gaforHint = gA.gebiet && !gaforFresh ? `<div class="sub">Noch kein GAFOR für heute${gA.datum ? ` (letzter vom ${esc(gA.datum)})` : ""}.</div>` : "";

    const catCol = { VFR: "var(--fw-green)", MVFR: "var(--fw-blue)", IFR: "var(--fw-red)", LIFR: "var(--fw-purple)" }[met?.cat] || "var(--fw-muted)";

    this.shadowRoot.innerHTML = `
      <style>${FlugwetterCard.styles}</style>
      <ha-card>
        <div class="wrap">
          <header>
            <div>
              <div class="title">${esc(title)}</div>
              <div class="subtitle">Bahn ${rwyNum(c.runway)}/${rwyNum(c.runway + 180)}${met ? ` · METAR ${esc(met.station || met.raw.split(" ")[1] || "")} ${esc(met.age)}` : ""}</div>
            </div>
            <div class="clock"><div><b>${lt}</b><span>LT</span></div><div><b>${utc}</b><span>UTC</span></div></div>
          </header>

          <section class="hero">
            <div class="col status-col">
              <div class="lbl-sm">Heute</div>
              ${this._status(heute)}
              <div class="lbl-sm">Morgen</div>
              ${this._status(morgen, true)}
            </div>
            <div class="col compass-col">
              ${this._compass(met?.wd, met?.ws || 0, met?.wg)}
              <div class="windrow">
                <div><b>${met && met.wd != null ? String(Math.round(met.wd)).padStart(3, "0") + "°" : met ? "VRB" : "—"}</b><span>Richtung</span></div>
                <div><b>${met ? Math.round(met.ws) : "—"}</b><span>kt Wind</span></div>
                <div class="gustv"><b>${met?.wg ? Math.round(met.wg) : "—"}</b><span>kt Böen</span></div>
              </div>
            </div>
            <div class="col obs-col">
              ${met ? `<div class="cat" style="--c:${catCol}">${esc(met.cat || "—")}</div>` : ""}
              <ul class="obs">
                <li><span>Sicht</span><b>${met ? esc(met.vis) : "—"}</b></li>
                <li><span>Wolken</span><b>${met ? esc(met.ceil) : "—"}</b></li>
                <li><span>Temp / Taupunkt</span><b>${met && met.temp != null ? `${Math.round(met.temp)}° / ${Math.round(met.dewp)}°` : "—"}</b></li>
                <li><span>QNH</span><b>${met && met.qnh != null ? `${Math.round(met.qnh)} hPa` : "—"}</b></li>
              </ul>
              ${xwHtml}
            </div>
          </section>

          <section class="timeline">
            <div class="tabs">
              <button class="${this._tab === "heute" ? "on" : ""}" data-tab="heute">Heute</button>
              <button class="${this._tab === "morgen" ? "on" : ""}" data-tab="morgen">Morgen</button>
              <span class="hint">Modell DWD ICON · Bahn ${esc(heute?.attributes?.bahnen || "")} · nur Tageslicht</span>
            </div>
            ${this._timeline(rows)}
          </section>

          <section class="bottom">
            ${gA.gebiet ? `<div class="gafor"><div class="lbl-sm">GAFOR ${esc(gaforName)}</div>${gafor ? `<div class="gfs">${gafor}</div>` : gaforHint}</div>` : ""}
            <div class="codes">
              ${met ? `<div class="lbl-sm">METAR</div><code>${esc(met.raw)}</code>` : ""}
              ${taf?.attributes?.text ? `<div class="lbl-sm">TAF</div><code>${esc(taf.attributes.text)}</code>` : ""}
            </div>
          </section>

          <footer>Orientierungshilfe, ersetzt keine Flugvorbereitung. Wolkenuntergrenze aus Temperatur/Taupunkt geschätzt. Flugwetter-Karte ${FW_VERSION}</footer>
        </div>
      </ha-card>`;

    this.shadowRoot.querySelectorAll(".tabs button").forEach((b) =>
      b.addEventListener("click", () => {
        this._tab = b.dataset.tab;
        this._render();
      })
    );
    const cur = this.shadowRoot.querySelector(".hr.cur");
    if (cur) {
      const sc = this.shadowRoot.querySelector(".tl-scroll");
      sc.scrollLeft = Math.max(0, cur.offsetLeft - 60);
    }
  }
}

FlugwetterCard.styles = `
  :host {
    --fw-green: #2fbf71; --fw-amber: #f2b33d; --fw-red: #e5484d; --fw-blue: #3b82f6; --fw-purple: #a855f7;
    --fw-bg1: #0b2545; --fw-bg2: #134074; --fw-panel: rgba(255,255,255,.07); --fw-line: rgba(255,255,255,.12);
    --fw-text: #f1f5fb; --fw-muted: rgba(241,245,251,.6); --fw-arrow: #ffffff; --fw-runway: #1b1f27;
  }
  ha-card { overflow: hidden; border: none; background: radial-gradient(1200px 500px at 50% -10%, var(--fw-bg2), var(--fw-bg1)); color: var(--fw-text); }
  .wrap { padding: 18px 20px 12px; display: flex; flex-direction: column; gap: 16px; }
  header { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
  .title { font-size: 26px; font-weight: 700; letter-spacing: -.3px; line-height: 1.1; }
  .subtitle { color: var(--fw-muted); font-size: 13px; margin-top: 4px; }
  .clock { display: flex; gap: 16px; text-align: right; }
  .clock b { display: block; font-size: 22px; font-variant-numeric: tabular-nums; }
  .clock span { font-size: 11px; color: var(--fw-muted); letter-spacing: .08em; }
  .lbl-sm { font-size: 11px; text-transform: uppercase; letter-spacing: .1em; color: var(--fw-muted); margin: 2px 0 6px; }

  .hero { display: grid; grid-template-columns: 1fr minmax(220px, 300px) 1fr; gap: 18px; align-items: center; }
  @media (max-width: 860px) { .hero { grid-template-columns: 1fr; } .compass-col { order: -1; } }
  .col { display: flex; flex-direction: column; gap: 8px; }
  .status { display: flex; gap: 14px; align-items: flex-start; background: var(--fw-panel); border-radius: 16px; padding: 14px 16px; border-left: 6px solid var(--c); }
  .status .dotbig { width: 18px; height: 18px; border-radius: 50%; background: var(--c); box-shadow: 0 0 18px var(--c); margin-top: 5px; flex: none; }
  .status .st { font-size: 22px; font-weight: 700; }
  .status.small { padding: 10px 14px; } .status.small .st { font-size: 16px; } .status.small .dotbig { width: 12px; height: 12px; margin-top: 4px; }
  .sub { color: var(--fw-muted); font-size: 13px; margin-top: 2px; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
  .chips span { font-size: 12px; padding: 3px 9px; border-radius: 999px; background: rgba(255,255,255,.1); }

  .compass { width: 100%; max-width: 300px; margin: 0 auto; display: block; }
  .compass .card { fill: var(--fw-text); font-size: 17px; font-weight: 700; }
  .compass .num { fill: var(--fw-muted); font-size: 11px; font-weight: 600; }
  .compass .rwy { fill: #fff; font-size: 13px; font-weight: 800; font-family: ui-monospace, monospace; }
  .compass .lbl { fill: var(--fw-bg1); font-size: 11px; font-weight: 700; }
  .windrow { display: flex; justify-content: center; gap: 8px; }
  .windrow > div { background: var(--fw-panel); border-radius: 12px; padding: 6px 16px; text-align: center; min-width: 70px; }
  .windrow b { font-size: 22px; display: block; } .windrow span { font-size: 11px; color: var(--fw-muted); }
  .windrow .gustv b { color: var(--fw-amber); }

  .cat { align-self: flex-start; font-weight: 800; font-size: 20px; letter-spacing: .05em; padding: 4px 16px; border-radius: 10px; background: var(--c); color: #0b1220; }
  .obs { list-style: none; margin: 0; padding: 0; background: var(--fw-panel); border-radius: 14px; }
  .obs li { display: flex; justify-content: space-between; padding: 9px 14px; border-bottom: 1px solid var(--fw-line); font-size: 14px; }
  .obs li:last-child { border: none; } .obs span { color: var(--fw-muted); }
  .xw { display: flex; gap: 14px; align-items: center; background: var(--fw-panel); border-radius: 14px; padding: 12px 14px; border-left: 6px solid var(--c); }
  .xw .big { font-size: 28px; font-weight: 800; color: var(--c); line-height: 1; } .xw .big small { font-size: 14px; color: var(--fw-muted); }
  .rwybadge { background: var(--fw-runway); color: #fff; font-family: monospace; font-weight: 700; font-size: 18px; padding: 14px 10px; border-radius: 6px 6px 2px 2px; border-bottom: 3px dashed #fff; }

  .timeline { background: var(--fw-panel); border-radius: 16px; padding: 10px 12px 8px; }
  .tabs { display: flex; gap: 6px; align-items: center; margin-bottom: 8px; }
  .tabs button { background: transparent; color: var(--fw-text); border: 1px solid var(--fw-line); border-radius: 999px; padding: 5px 14px; cursor: pointer; font: inherit; font-size: 13px; }
  .tabs button.on { background: var(--fw-text); color: var(--fw-bg1); font-weight: 700; }
  .tabs .hint { margin-left: auto; font-size: 11px; color: var(--fw-muted); }
  .tl-scroll { overflow-x: auto; scrollbar-width: thin; }
  .tl { position: relative; }
  .cols { display: flex; }
  .hr { flex: 1 1 0; min-width: 56px; text-align: center; padding: 6px 0; border-radius: 10px; font-size: 13px; }
  .hr.cur { background: rgba(255,255,255,.1); outline: 1px solid rgba(255,255,255,.3); }
  .hr .h { font-weight: 700; font-variant-numeric: tabular-nums; }
  .hr .pill { height: 6px; margin: 6px 8px; border-radius: 3px; }
  .hr .arrow { width: 22px; height: 22px; fill: var(--fw-text); display: block; margin: 2px auto; }
  .hr .v { font-weight: 700; } .hr .g { color: var(--fw-amber); font-size: 11px; margin-left: 2px; }
  .hr .s { color: var(--fw-muted); font-size: 12px; margin-top: 2px; } .hr .s small { font-size: 9px; }
  .t-amber { color: var(--fw-amber) !important; font-weight: 700; } .t-red { color: var(--fw-red) !important; font-weight: 700; }
  .chartwrap { position: relative; margin-top: 4px; }
  .chart { display: block; width: 100%; height: 100%; }
  .chart * { vector-effect: non-scaling-stroke; }
  .chart .wind { fill: none; stroke: var(--fw-text); stroke-width: 2.5; stroke-linejoin: round; }
  .chart .gust { fill: none; stroke: var(--fw-amber); stroke-width: 2; stroke-dasharray: 5 4; }
  .chart .dot { fill: var(--fw-text); }
  .chart .lim-red { stroke: var(--fw-red); stroke-width: 1; stroke-dasharray: 2 4; }
  .chart .lim-amber { stroke: var(--fw-amber); stroke-width: 1; stroke-dasharray: 2 4; }
  .limt { position: absolute; left: 4px; color: var(--fw-muted); font-size: 10px; }
  .legend { display: flex; gap: 14px; flex-wrap: wrap; font-size: 11px; color: var(--fw-muted); margin-top: 4px; }
  .legend i { display: inline-block; width: 16px; height: 0; vertical-align: middle; margin-right: 4px; }
  .lg-w { border-top: 2.5px solid var(--fw-text); } .lg-g { border-top: 2px dashed var(--fw-amber); }
  .empty { padding: 18px; text-align: center; color: var(--fw-muted); }

  .bottom { display: grid; grid-template-columns: auto 1fr; gap: 18px; align-items: start; }
  @media (max-width: 860px) { .bottom { grid-template-columns: 1fr; } }
  .gfs { display: flex; gap: 8px; }
  .gf { background: var(--fw-panel); border-radius: 12px; padding: 8px 12px; text-align: center; border-top: 4px solid var(--c); min-width: 74px; }
  .gf .gc { font-size: 22px; font-weight: 800; color: var(--c); } .gf .gt { font-size: 12px; } .gf .gu { font-size: 10px; color: var(--fw-muted); }
  .codes code { display: block; font-family: ui-monospace, Menlo, monospace; font-size: 12.5px; background: rgba(0,0,0,.25); border-radius: 10px; padding: 8px 10px; margin-bottom: 8px; white-space: pre-wrap; word-break: break-word; }
  footer { font-size: 11px; color: var(--fw-muted); text-align: center; border-top: 1px solid var(--fw-line); padding-top: 8px; }
`;

if (!customElements.get("flugwetter-card")) customElements.define("flugwetter-card", FlugwetterCard);

// ---------------------------------------------------------------- Editoren

const FW_LABELS = {
  device_id: "Flugplatz",
  device_ids: "Flugplätze",
  title: "Titel (optional)",
};

async function fwEnsureHaForm() {
  if (customElements.get("ha-form")) return;
  try {
    const helpers = await window.loadCardHelpers?.();
    const card = await helpers?.createCardElement({ type: "entities", entities: [] });
    await card?.constructor?.getConfigElement?.();
  } catch (e) {
    /* ha-form wird dann später geladen */
  }
}

class FlugwetterFormEditor extends HTMLElement {
  setConfig(config) {
    this._config = { ...config };
    this._render();
  }
  set hass(hass) {
    this._hass = hass;
    this._render();
  }
  get _schema() {
    return [];
  }
  async _render() {
    if (!this._hass || !this._config) return;
    await fwEnsureHaForm();
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (s) => FW_LABELS[s.name] || s.name;
      this._form.addEventListener("value-changed", (ev) => {
        ev.stopPropagation();
        this._config = { ...this._config, ...ev.detail.value };
        Object.keys(this._config).forEach((k) => (this._config[k] === "" || this._config[k] == null) && k !== "type" && delete this._config[k]);
        this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: this._config }, bubbles: true, composed: true }));
      });
      const hint = document.createElement("div");
      hint.style.cssText = "margin-top:12px;color:var(--secondary-text-color);font-size:13px";
      hint.textContent = "Grenzwerte, Bahnrichtung und Stationen änderst du in den Einstellungen der Integration „Flugwetter“.";
      this.append(this._form, hint);
    }
    this._form.hass = this._hass;
    this._form.schema = this._schema;
    this._form.data = this._config;
  }
}

class FlugwetterCardEditor extends FlugwetterFormEditor {
  get _schema() {
    return [
      { name: "device_id", required: true, selector: { device: { filter: { integration: FW_DOMAIN } } } },
      { name: "title", selector: { text: {} } },
    ];
  }
}
if (!customElements.get("flugwetter-card-editor")) customElements.define("flugwetter-card-editor", FlugwetterCardEditor);

class FlugwetterStrategyEditor extends FlugwetterFormEditor {
  get _schema() {
    return [{ name: "device_ids", required: true, selector: { device: { multiple: true, filter: { integration: FW_DOMAIN } } } }];
  }
}
if (!customElements.get("flugwetter-strategy-editor")) customElements.define("flugwetter-strategy-editor", FlugwetterStrategyEditor);

// ---------------------------------------------------------------- Dashboard-Strategie

class FlugwetterDashboardStrategy extends HTMLElement {
  static configRequired = true;
  static registryDependencies = ["entities", "devices"];

  static getConfigElement() {
    return document.createElement("flugwetter-strategy-editor");
  }

  static getCreateSuggestions() {
    return { title: "Flugwetter", icon: "mdi:airplane" };
  }

  static async generate(config, hass) {
    let ids = config.device_ids || (config.device_id ? [config.device_id] : []);
    if (!ids.length) ids = fwDevices(hass).map((d) => d.id);
    if (!ids.length) {
      return {
        views: [{
          title: "Flugwetter",
          cards: [{ type: "markdown", content: "## Noch kein Flugplatz\nRichte unter **Einstellungen → Geräte & Dienste** die Integration **Flugwetter** ein und öffne dann die Einstellungen dieses Dashboards (Zahnrad)." }],
        }],
      };
    }
    return {
      views: ids.map((id) => {
        const dev = hass.devices?.[id];
        const name = dev?.name_by_user || dev?.name || "Flugwetter";
        const icao = name.split(" ")[0];
        return {
          title: ids.length > 1 ? icao : "Flugwetter",
          path: icao.toLowerCase().replace(/[^a-z0-9]/g, "") || undefined,
          icon: "mdi:airplane",
          type: "panel",
          cards: [{ type: "custom:flugwetter-card", device_id: id }],
        };
      }),
    };
  }
}
if (!customElements.get("ll-strategy-dashboard-flugwetter")) customElements.define("ll-strategy-dashboard-flugwetter", FlugwetterDashboardStrategy);

window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "flugwetter-card")) {
  window.customCards.push({ type: "flugwetter-card", name: "Flugwetter", description: "Flugtag-Ampel, Windrose, Stundenverlauf, GAFOR und METAR", preview: false });
}
window.customStrategies = window.customStrategies || [];
if (!window.customStrategies.some((c) => c.type === "flugwetter")) {
  window.customStrategies.push({
    type: "flugwetter",
    strategyType: "dashboard",
    name: "Flugwetter",
    description: "Flugtag-Ampel mit Windrose, Stundenverlauf, GAFOR, METAR und TAF für deine Flugplätze.",
    documentationURL: "https://github.com/",
  });
}
console.info(`%c FLUGWETTER %c ${FW_VERSION} `, "background:#134074;color:#fff;font-weight:700", "background:#2fbf71;color:#0b1220");
