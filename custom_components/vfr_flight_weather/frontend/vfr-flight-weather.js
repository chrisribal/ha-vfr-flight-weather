/* VFR Flight Weather – Karten und Dashboard-Strategie der Integration.
 * Wird von der Integration automatisch geladen. Farben, Schriften und Abstände
 * kommen aus dem aktiven Home-Assistant-Theme.
 */
const FW_VERSION = "0.2.2";
const FW_DOMAIN = "vfr_flight_weather";
const FW_KEYS = ["jetzt", "naechstes_flugfenster", "wolkenuntergrenze", "flugtag_heute", "flugtag_morgen", "metar", "taf", "gafor", "seitenwind"];

const FW_VIEWS = {
  komplett: "Alles in einer Karte",
  jetzt: "Jetzt",
  zeitstrahl: "Zeitstrahl mit Wind und Wolken",
  wind: "Wind und Bahn",
  meldungen: "METAR, TAF und GAFOR",
  dwd: "DWD-Flugwetterübersicht",
};

const LVL_COLOR = [
  "var(--success-color, #43a047)",
  "var(--warning-color, #ffa600)",
  "var(--error-color, #db4437)",
  "var(--disabled-color, #9e9e9e)",
];
const NOW_INFO = {
  fliegbar: { text: "Fliegbar", icon: "mdi:airplane-takeoff", lvl: 0 },
  eingeschraenkt: { text: "Eingeschränkt", icon: "mdi:airplane-alert", lvl: 1 },
  nicht_fliegbar: { text: "Nicht fliegbar", icon: "mdi:airplane-off", lvl: 2 },
  nacht: { text: "Nacht", icon: "mdi:weather-night", lvl: 3 },
  keine_daten: { text: "Keine Daten", icon: "mdi:help-circle-outline", lvl: 3 },
};
const DAY_TEXT = { gut: "Guter Flugtag", eingeschraenkt: "Eingeschränkt", schlecht: "Kein Flugwetter", vorbei: "Tag vorbei", keine_daten: "Keine Daten" };
const DAY_LVL = { gut: 0, eingeschraenkt: 1, schlecht: 2 };

const DISCLAIMER = {
  de: {
    title: "Nur zur Information – ersetzt keine Flugvorbereitung",
    text: "Diese Anzeige ist eine Übersicht, kein Wetterbriefing. Bereite jeden Flug mit den amtlichen Quellen vor (Flugwetter, NOTAM, AIP). Modelle und automatische Bewertungen können falsch sein. Die Entscheidung trägt allein der verantwortliche Pilot.",
    short: "Nur zur Information – ersetzt keine Flugvorbereitung.",
  },
  en: {
    title: "For information only – does not replace your pre-flight preparation",
    text: "This is an overview, not a weather briefing. Prepare every flight with official sources (aviation weather, NOTAM, AIP). Models and automated ratings can be wrong. The pilot in command alone decides whether to fly.",
    short: "For information only – does not replace your pre-flight preparation.",
  },
};

// ---------------------------------------------------------------- Hilfsfunktionen

function fwEntities(hass, deviceId) {
  const out = {};
  if (!hass || !hass.entities) return out;
  for (const e of Object.values(hass.entities)) {
    if (e.device_id !== deviceId || e.platform !== FW_DOMAIN) continue;
    const key = e.translation_key || FW_KEYS.find((k) => e.entity_id.endsWith(k));
    if (key) out[key] = e.entity_id;
  }
  return out;
}

function fwDevices(hass) {
  if (!hass || !hass.entities || !hass.devices) return [];
  const ids = new Set(Object.values(hass.entities).filter((e) => e.platform === FW_DOMAIN && e.device_id).map((e) => e.device_id));
  return [...ids].map((id) => hass.devices[id]).filter(Boolean);
}

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pad = (n) => String(n).padStart(2, "0");
const rad = (d) => (d * Math.PI) / 180;
const rwyNum = (h) => pad(Math.round(((h % 360) || 360) / 10));

function coverCode(pct) {
  if (pct >= 94) return "OVC";
  if (pct >= 57) return "BKN";
  if (pct >= 32) return "SCT";
  if (pct >= 7) return "FEW";
  return "";
}

function describeLayers(wk) {
  const parts = (wk || []).map(([ft, pct]) => [coverCode(pct), ft]).filter(([c]) => c).map(([c, ft]) => `${c} ${ft.toLocaleString("de-DE")} ft`);
  return parts.length ? parts.slice(0, 3).join(", ") : "Keine tiefen Wolken";
}

function fmtDuration(ms) {
  const min = Math.max(0, Math.round(ms / 60000));
  if (min < 60) return `${min} min`;
  const h = Math.floor(min / 60);
  const m = min % 60;
  return m ? `${h} h ${m} min` : `${h} h`;
}

class FwClock {
  constructor(hass) {
    const lang = hass?.locale?.language || hass?.language || "de";
    const tz = hass?.config?.time_zone;
    this.time = new Intl.DateTimeFormat(lang, { hour: "2-digit", minute: "2-digit", timeZone: tz });
    this.hourFmt = new Intl.DateTimeFormat("en-GB", { hour: "numeric", hourCycle: "h23", timeZone: tz });
    this.wd = new Intl.DateTimeFormat(lang, { weekday: "short", timeZone: tz });
    this.day = new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit", timeZone: tz });
  }
  t(d) {
    return this.time.format(new Date(d));
  }
  /** Stunde 0–23 in der Zeitzone von Home Assistant */
  hour(d) {
    const part = this.hourFmt.formatToParts(new Date(d)).find((p) => p.type === "hour");
    return part ? Number(part.value) % 24 : 0;
  }
  dayKey(d) {
    return this.day.format(new Date(d));
  }
  /** "12:00", "morgen 09:00" oder "Mo 09:00" */
  when(d) {
    const now = new Date();
    const tomorrow = new Date(now.getTime() + 86400000);
    const k = this.dayKey(d);
    if (k === this.dayKey(now)) return this.t(d);
    if (k === this.dayKey(tomorrow)) return `morgen ${this.t(d)}`;
    return `${this.wd.format(new Date(d))} ${this.t(d)}`;
  }
}

function moreInfo(el, entityId) {
  if (!entityId) return;
  el.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId }, bubbles: true, composed: true }));
}

// ---------------------------------------------------------------- Karte

class FlugwetterCard extends HTMLElement {
  setConfig(config) {
    if (config.ansicht && !FW_VIEWS[config.ansicht]) throw new Error(`Unbekannte Ansicht: ${config.ansicht}`);
    this._config = { ansicht: "komplett", stunden: 24, ...config };
    this._sel = null;
    this._sig = null;
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this.shadowRoot.addEventListener("click", (ev) => this._onClick(ev));
    }
    if (this._hass) this._render();
  }

  set hass(hass) {
    this._hass = hass;
    const dev = this._config.device_id || fwDevices(hass)[0]?.id;
    this._dev = dev;
    this._ids = fwEntities(hass, dev);
    const sig = Object.values(this._ids).map((id) => hass.states[id]?.last_updated).join("|") + "|" + Math.floor(Date.now() / 60000);
    if (sig === this._sig) return;
    this._sig = sig;
    this._render();
  }

  getCardSize() {
    return { jetzt: 4, zeitstrahl: 7, wind: 5, meldungen: 4, dwd: 5 }[this._config?.ansicht] || 14;
  }

  getGridOptions() {
    return { columns: "full", rows: "auto", min_columns: 6 };
  }

  static getStubConfig(hass) {
    return { device_id: fwDevices(hass)[0]?.id || "", ansicht: "jetzt" };
  }

  static getConfigElement() {
    return document.createElement("vfr-flight-weather-card-editor");
  }

  _st(key) {
    return this._hass?.states[this._ids?.[key]];
  }

  _onClick(ev) {
    const hour = ev.target.closest?.("[data-i]");
    if (hour) {
      const i = Number(hour.dataset.i);
      this._sel = this._sel === i ? null : i;
      this._render();
      return;
    }
    const more = ev.target.closest?.("[data-more]");
    if (more) moreInfo(this, this._ids[more.dataset.more]);
  }

  _render() {
    if (!this.shadowRoot || !this._hass) return;
    const view = this._config.ansicht;
    const jetzt = this._st("jetzt");
    let body;
    if (!this._dev || !jetzt) {
      body = `<div class="empty">Kein Flugplatz gefunden. Richte die Integration „VFR Flight Weather“ ein und wähle den Flugplatz in den Karteneinstellungen.</div>`;
    } else {
      this._clock = new FwClock(this._hass);
      const parts = {
        jetzt: () => this._now(),
        zeitstrahl: () => this._timeline(),
        wind: () => this._wind(),
        meldungen: () => this._reports(),
        dwd: () => this._dwd(),
      };
      const order = view === "komplett" ? ["jetzt", "zeitstrahl", "wind", "meldungen", "dwd"] : [view];
      body = order.map((k) => parts[k]()).filter(Boolean).join(`<div class="divider"></div>`);
    }
    const lang = String(this._hass.locale?.language || this._hass.language || "de").startsWith("de") ? "de" : "en";
    const d = DISCLAIMER[lang];
    if (this._dev && jetzt) {
      if (view === "jetzt" || view === "komplett") {
        body = `<div class="disclaimer" role="note"><ha-icon icon="mdi:alert-outline"></ha-icon><div><b>${d.title}</b><div>${d.text}</div></div></div>` + body;
      } else {
        body += `<div class="disclaimer-short"><ha-icon icon="mdi:information-outline"></ha-icon>${d.short}</div>`;
      }
    }
    const title = this._config.title ? `<h1 class="card-header">${esc(this._config.title)}</h1>` : "";
    this.shadowRoot.innerHTML = `<style>${FW_CSS}</style><ha-card>${title}<div class="content">${body}</div></ha-card>`;
  }

  // -------------------------------------------------- Jetzt

  _now() {
    const st = this._st("jetzt");
    const a = st.attributes;
    const info = NOW_INFO[st.state] || NOW_INFO.keine_daten;
    const color = LVL_COLOR[info.lvl];
    const label = this._hass.formatEntityState ? this._hass.formatEntityState(st) : info.text;
    const rows = a.stunden || [];
    const cur = rows[0];
    const clock = this._clock;
    const now = Date.now();

    // Kernaussage: bis wann / ab wann
    let headline = "";
    if (st.state === "fliegbar" && a.fliegbar_bis) {
      headline = `Fliegbar bis ${clock.when(a.fliegbar_bis)} <span class="dim">(noch ${fmtDuration(new Date(a.fliegbar_bis) - now)})</span>`;
    } else if (st.state === "eingeschraenkt" && a.fliegbar_bis) {
      headline = `Eingeschränkt bis ${clock.when(a.fliegbar_bis)}`;
      if (a.naechstes_fenster_stufe === 0 && new Date(a.naechstes_fenster_start) > now) {
        headline += ` · gut ab ${clock.when(a.naechstes_fenster_start)}`;
      }
    } else if (a.naechstes_fenster_start) {
      const what = a.naechstes_fenster_stufe === 0 ? "Fliegbar" : "Eingeschränkt fliegbar";
      headline = `${what} ab ${clock.when(a.naechstes_fenster_start)} <span class="dim">(in ${fmtDuration(new Date(a.naechstes_fenster_start) - now)})</span> bis ${clock.when(a.naechstes_fenster_ende)}`;
    } else if (st.state !== "keine_daten") {
      headline = `In den nächsten ${rows.length} Stunden kein Flugfenster`;
    }

    const reasons = (a.gruende || []).map((g) => `<span class="chip">${esc(g)}</span>`).join("");

    // Aktuelle Werte (laufende Stunde, bei frischem METAR beobachtet)
    const metar = this._st("metar");
    const ma = metar?.attributes || {};
    const fromMetar = cur?.q === "metar";
    const values = [];
    if (cur) {
      const gust = cur.wg > cur.ws + 2 ? ` G${cur.wg}` : "";
      values.push(["Wind", cur.ws ? `${String(cur.wd).padStart(3, "0")}° / ${cur.ws} kt${gust}` : "Windstill"]);
      values.push(["Seitenwind", `${cur.xw} kt · Bahn ${cur.bahn}`]);
      let clouds;
      if (fromMetar) {
        const list = (ma.wolken || []).map((c) => `${c.cover} ${Number(c.base).toLocaleString("de-DE")} ft`);
        clouds = list.length ? list.slice(0, 3).join(", ") : "Keine Wolken gemeldet";
      } else {
        clouds = describeLayers(cur.wk);
      }
      values.push(["Wolken", clouds]);
      values.push(["Untergrenze", cur.ceil != null ? `${Number(cur.ceil).toLocaleString("de-DE")} ft` : "keine"]);
      values.push(["Sicht", cur.vis == null ? "–" : cur.vis >= 10000 ? "über 10 km" : `${(cur.vis / 1000).toLocaleString("de-DE", { maximumFractionDigits: 1 })} km`]);
    }
    if (ma.qnh) values.push(["QNH", `${ma.qnh} hPa`]);
    const today = (a.tage || [])[0];
    if (today) values.push(["Flugtag (BDA–BDE)", `${clock.t(today.bda)}–${clock.t(today.bde)}`]);

    const src = fromMetar
      ? `Jetzt laut METAR ${esc(ma.station || "")}${ma.beobachtet ? ` von ${clock.t(ma.beobachtet)}` : ""}`
      : "Jetzt laut Modellvorhersage";

    const day = (key, name) => {
      const s = this._st(key);
      if (!s) return "";
      const lvl = DAY_LVL[s.state] ?? 3;
      const txt = this._hass.formatEntityState ? this._hass.formatEntityState(s) : DAY_TEXT[s.state] || s.state;
      const win = s.attributes.fenster ? ` · ${esc(s.attributes.fenster)}` : "";
      return `<div class="day" data-more="${key}"><span class="dot" style="background:${LVL_COLOR[lvl]}"></span><span class="day-name">${name}</span><span class="day-val">${esc(txt)}${win}</span></div>`;
    };

    return `
      <div class="now">
        <div class="now-head" data-more="jetzt">
          <div class="badge" style="--c:${color}"><ha-icon icon="${info.icon}"></ha-icon></div>
          <div class="now-text">
            <div class="primary">${esc(label)}</div>
            <div class="secondary">${esc(a.icao || "")} · Bahn ${esc(a.bahnen || "")} · ${src}</div>
          </div>
        </div>
        ${headline ? `<div class="headline">${headline}</div>` : ""}
        ${reasons ? `<div class="chips">${reasons}</div>` : ""}
        <div class="values">${values.map(([l, v]) => `<div class="value"><div class="label">${l}</div><div class="val">${esc(v)}</div></div>`).join("")}</div>
        <div class="days">${day("flugtag_heute", "Heute")}${day("flugtag_morgen", "Morgen")}</div>
      </div>`;
  }

  // -------------------------------------------------- Zeitstrahl

  _timeline() {
    const st = this._st("jetzt");
    const a = st.attributes;
    const rows = (a.stunden || []).slice(0, Math.max(6, Math.min(36, Number(this._config.stunden) || 24)));
    if (!rows.length) return `<div class="empty">Keine Vorhersage verfügbar.</div>`;
    const n = rows.length;
    const lim = a.grenzwerte || {};
    const clock = this._clock;
    const sel = this._sel != null && this._sel < n ? this._sel : 0;
    const t0 = new Date(rows[0].t).getTime();
    const nowFrac = Math.min(1, Math.max(0, (Date.now() - t0) / 3600000)) / n;
    const xOf = (iso) => ((new Date(iso).getTime() - t0) / 3600000);

    // Status-Leiste
    const segs = rows
      .map((r, i) => {
        const night = !r.tag;
        const style = night ? "" : `background:${LVL_COLOR[r.lvl]}`;
        const title = `${clock.when(r.t)} – ${night ? "Nacht" : ["Fliegbar", "Eingeschränkt", "Nicht fliegbar"][r.lvl]}${r.why ? `: ${r.why}` : ""}`;
        return `<div class="seg${night ? " night" : ""}${i === sel ? " sel" : ""}" data-i="${i}" style="${style}" title="${esc(title)}"></div>`;
      })
      .join("");

    // Stundenbeschriftung, Tageswechsel
    const labels = rows
      .map((r, i) => {
        const h = clock.hour(r.t);
        let txt = "";
        if (h === 0) txt = `<b>${esc(clock.wd.format(new Date(r.t)))}</b>`;
        else if (h % 3 === 0) txt = pad(h);
        return `<div class="hl" data-i="${i}">${txt}</div>`;
      })
      .join("");
    const midnights = rows
      .map((r, i) => (i > 0 && clock.hour(r.t) === 0 ? i : null))
      .filter((i) => i != null);

    // Nachtbereiche aus BDA/BDE (genauer als die Stundenwerte)
    const nightRects = [];
    const days = a.tage || [];
    let prevEnd = -1000;
    for (const d of days) {
      const s = xOf(d.bda);
      if (s > prevEnd) nightRects.push([Math.max(0, prevEnd), Math.min(n, s)]);
      prevEnd = xOf(d.bde);
    }
    if (prevEnd < n) nightRects.push([Math.max(0, prevEnd), n]);
    const nightSvg = (h) =>
      nightRects
        .filter(([s, e]) => e > s)
        .map(([s, e]) => `<rect x="${s}" y="0" width="${e - s}" height="${h}" class="nightbg"/>`)
        .join("");
    const gridSvg = (h) => midnights.map((i) => `<line x1="${i}" x2="${i}" y1="0" y2="${h}" class="midnight"/>`).join("");
    const selSvg = (h) => `<rect x="${sel}" y="0" width="1" height="${h}" class="selbg"/>`;
    const nowSvg = (h) => `<line x1="${nowFrac * n}" x2="${nowFrac * n}" y1="0" y2="${h}" class="nowline"/>`;

    // Wind
    const WH = 100;
    const wMax = Math.max(lim.xw_limit || 15, lim.wind_limit || 20, ...rows.map((r) => r.wg)) + 5;
    const wTop = Math.ceil(wMax / 10) * 10;
    const wy = (v) => WH - (v / wTop) * WH;
    const line = (key) => rows.map((r, i) => `${i + 0.5},${wy(r[key]).toFixed(1)}`).join(" ");
    const limLine = (v, cls) => (v && v < wTop ? `<line x1="0" x2="${n}" y1="${wy(v)}" y2="${wy(v)}" class="${cls}"/>` : "");
    const windSvg = `
      <svg viewBox="0 0 ${n} ${WH}" preserveAspectRatio="none" class="chart" style="height:${WH}px">
        ${nightSvg(WH)}${selSvg(WH)}${gridSvg(WH)}
        ${limLine(lim.xw_limit, "lim bad")}${limLine(lim.xw_warn, "lim warn")}
        <polygon points="0.5,${WH} ${line("xw")} ${n - 0.5},${WH}" class="xwarea"/>
        <polyline points="${line("wg")}" class="gust"/>
        <polyline points="${line("ws")}" class="wind"/>
        ${nowSvg(WH)}
      </svg>`;
    const windAxis = [wTop / 2, wTop].map((v) => `<span style="top:${(wy(v) / WH) * 100}%">${v}</span>`).join("");

    // Wolken
    const CH = 150;
    const cTop = Number(this._config.wolken_max) || 8000;
    const cy = (ft) => CH - (Math.min(ft, cTop) / cTop) * CH;
    const cloudRects = [];
    rows.forEach((r, i) => {
      const wk = (r.wk || []).filter(([ft]) => ft <= cTop * 1.2);
      wk.forEach(([ft, pct], k) => {
        if (pct < 7) return;
        const below = k > 0 ? (wk[k - 1][0] + ft) / 2 : Math.max(0, ft - (wk[k + 1] ? (wk[k + 1][0] - ft) / 2 : 300));
        const above = k < wk.length - 1 ? (ft + wk[k + 1][0]) / 2 : ft + (k > 0 ? (ft - wk[k - 1][0]) / 2 : 300);
        const y1 = cy(above);
        const y2 = cy(below);
        if (y2 - y1 <= 0) return;
        cloudRects.push(`<rect x="${i}" y="${y1.toFixed(1)}" width="1" height="${(y2 - y1).toFixed(1)}" class="cloud" style="opacity:${(0.12 + (pct / 100) * 0.6).toFixed(2)}"/>`);
      });
    });
    const ceilMarks = rows
      .map((r, i) => (r.ceil != null && r.ceil <= cTop ? `<line x1="${i + 0.15}" x2="${i + 0.85}" y1="${cy(r.ceil)}" y2="${cy(r.ceil)}" class="ceil" style="stroke:${LVL_COLOR[r.tag ? r.lvl : 3]}"/>` : ""))
      .join("");
    const cloudSvg = `
      <svg viewBox="0 0 ${n} ${CH}" preserveAspectRatio="none" class="chart" style="height:${CH}px">
        ${nightSvg(CH)}${selSvg(CH)}${gridSvg(CH)}
        ${cloudRects.join("")}
        ${limLine2(lim.ceil_limit, "lim bad")}${limLine2(lim.ceil_warn, "lim warn")}
        ${ceilMarks}
        ${nowSvg(CH)}
      </svg>`;
    function limLine2(v, cls) {
      return v && v < cTop ? `<line x1="0" x2="${n}" y1="${cy(v)}" y2="${cy(v)}" class="${cls}"/>` : "";
    }
    const step = cTop > 6000 ? 2000 : 1000;
    const cloudAxis = [];
    for (let v = 0; v <= cTop; v += step) cloudAxis.push(`<span style="top:${(cy(v) / CH) * 100}%">${v / 1000}k</span>`);

    // Detail der gewählten Stunde
    const r = rows[sel];
    const lvlTxt = r.tag ? ["Fliegbar", "Eingeschränkt", "Nicht fliegbar"][r.lvl] : "Nacht";
    const gust = r.wg > r.ws + 2 ? ` G${r.wg}` : "";
    const detail = [
      ["Wind", r.ws ? `${String(r.wd).padStart(3, "0")}° / ${r.ws} kt${gust}` : "Windstill"],
      ["Seitenwind", `${r.xw} kt · Bahn ${r.bahn}`],
      ["Wolken", describeLayers(r.wk)],
      ["Untergrenze", r.ceil != null ? `${Number(r.ceil).toLocaleString("de-DE")} ft` : "keine"],
      ["Sicht", r.vis == null ? "–" : r.vis >= 10000 ? "über 10 km" : `${(r.vis / 1000).toLocaleString("de-DE", { maximumFractionDigits: 1 })} km`],
      ["Niederschlag", `${String(r.pr).replace(".", ",")} mm`],
    ];
    if (r.gafor) detail.push(["GAFOR", r.gafor]);

    return `
      <div class="tl" style="--n:${n}">
        <div class="tl-grid">
          <div class="axis"></div>
          <div class="bar">${segs}<div class="nowmark" style="left:${nowFrac * 100}%"></div></div>
          <div class="axis"></div>
          <div class="hours">${labels}</div>

          <div class="cap">Wind, Böen und Seitenwind (kt)</div>
          <div class="axis">${windAxis}</div>
          <div class="plot">${windSvg}</div>
          <div class="cap">Wolken und Untergrenze (ft über Platz)</div>
          <div class="axis">${cloudAxis.join("")}</div>
          <div class="plot">${cloudSvg}</div>
        </div>
        <div class="legend">
          <span><i class="sw" style="background:${LVL_COLOR[0]}"></i>Fliegbar</span>
          <span><i class="sw" style="background:${LVL_COLOR[1]}"></i>Eingeschränkt</span>
          <span><i class="sw" style="background:${LVL_COLOR[2]}"></i>Nicht fliegbar</span>
          <span><i class="sw night"></i>Nacht</span>
          <span><i class="ln wind"></i>Wind</span>
          <span><i class="ln gust"></i>Böen</span>
          <span><i class="sw xw"></i>Seitenwind</span>
          <span><i class="sw cloud"></i>Wolken (dunkler = dichter)</span>
          <span><i class="ln ceil"></i>Untergrenze</span>
        </div>
        <div class="detail">
          <div class="detail-head"><span class="dot" style="background:${LVL_COLOR[r.tag ? r.lvl : 3]}"></span><b>${esc(clock.when(r.t))}</b> · ${lvlTxt}${r.q === "metar" ? " · laut METAR" : ""}</div>
          ${r.why ? `<div class="chips">${r.why.split(", ").map((w) => `<span class="chip">${esc(w)}</span>`).join("")}</div>` : ""}
          <div class="values">${detail.map(([l, v]) => `<div class="value"><div class="label">${l}</div><div class="val">${esc(v)}</div></div>`).join("")}</div>
          <div class="hint">Tippe auf eine Stunde für Details. Wolken aus dem ICON-Modell (Druckflächen), Höhen über Platz.</div>
        </div>
      </div>`;
  }

  // -------------------------------------------------- Wind

  _wind() {
    const a = this._st("jetzt").attributes;
    const cur = (a.stunden || [])[0] || {};
    const xwSt = this._st("seitenwind");
    const runway = Number(a.bahn_kurs) || 0;
    const lim = a.grenzwerte || {};
    const wd = cur.wd;
    const ws = cur.ws || 0;
    const wg = cur.wg;
    const xw = cur.xw ?? 0;
    const lvl = xw >= (lim.xw_limit || 15) ? 2 : xw >= (lim.xw_warn || 8) ? 1 : 0;

    const C = 120;
    const R = 96;
    const pt = (r, deg) => [C + r * Math.sin(rad(deg)), C - r * Math.cos(rad(deg))];
    let s = `<circle cx="${C}" cy="${C}" r="${R}" class="ring"/>`;
    for (let d = 0; d < 360; d += 10) {
      const [x1, y1] = pt(R, d);
      const [x2, y2] = pt(R - (d % 30 === 0 ? 9 : 4), d);
      s += `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" class="tick"/>`;
    }
    for (const [d, l] of [[0, "N"], [90, "O"], [180, "S"], [270, "W"]]) {
      const [x, y] = pt(R - 20, d);
      s += `<text x="${x}" y="${y}" class="card-dir">${l}</text>`;
    }
    // Bahn
    s += `<g transform="rotate(${runway} ${C} ${C})">
      <rect x="${C - 9}" y="${C - 58}" width="18" height="116" rx="2" class="rwy"/>
      <line x1="${C}" y1="${C - 46}" x2="${C}" y2="${C + 46}" class="rwy-cl"/>
      <text x="${C}" y="${C + 52}" class="rwy-num" transform="rotate(180 ${C} ${C + 49})">${rwyNum(runway)}</text>
      <text x="${C}" y="${C - 46}" class="rwy-num" transform="rotate(180 ${C} ${C - 49})">${rwyNum(runway + 180)}</text>
    </g>`;
    // Windpfeil: kommt aus wd, zeigt zur Mitte
    if (typeof wd === "number" && ws > 0) {
      const [x1, y1] = pt(R + 2, wd);
      const [x2, y2] = pt(R - 52, wd);
      s += `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" class="arrow" style="stroke:${LVL_COLOR[lvl]}"/>`;
      const [hx, hy] = pt(R - 62, wd);
      const [lx, ly] = pt(R - 46, wd - 8);
      const [rx, ry] = pt(R - 46, wd + 8);
      s += `<polygon points="${hx},${hy} ${lx},${ly} ${rx},${ry}" style="fill:${LVL_COLOR[lvl]}"/>`;
    }
    const compass = `<svg viewBox="0 0 240 240" class="compass" role="img" aria-label="Windrose">${s}</svg>`;

    const xa = xwSt?.attributes || {};
    const vals = [
      ["Wind", typeof wd === "number" && ws ? `${String(wd).padStart(3, "0")}° / ${ws} kt` : ws ? `${ws} kt` : "Windstill"],
      ["Böen", wg && wg > ws + 2 ? `${wg} kt` : "keine"],
      ["Seitenwind", `${xw} kt`, lvl],
      ["Gegenwind", xa.gegenwind_kt != null ? `${Math.round(xa.gegenwind_kt)} kt` : "–"],
      ["Aktive Bahn", cur.bahn || xa.aktive_bahn || "–"],
    ];
    return `
      <div class="wind" data-more="seitenwind">
        ${compass}
        <div class="values wind-values">${vals
          .map(([l, v, lv]) => `<div class="value"><div class="label">${l}</div><div class="val"${lv != null ? ` style="color:${LVL_COLOR[lv]}"` : ""}>${esc(v)}</div></div>`)
          .join("")}</div>
      </div>`;
  }

  // -------------------------------------------------- Meldungen

  _reports() {
    const clock = this._clock;
    const metar = this._st("metar");
    const taf = this._st("taf");
    const gafor = this._st("gafor");
    const ma = metar?.attributes || {};
    let out = "";
    const age = ma.beobachtet ? ` · vor ${fmtDuration(Date.now() - new Date(ma.beobachtet))}` : "";
    out += `<div class="block" data-more="metar"><div class="block-head">METAR ${esc(ma.station || "")}${age}</div>
      <pre>${metar && metar.state && metar.state !== "unknown" ? esc(metar.state) : "Keine aktuelle Meldung"}</pre></div>`;
    const ta = taf?.attributes || {};
    out += `<div class="block" data-more="taf"><div class="block-head">TAF ${esc(taf?.state && taf.state !== "unknown" ? taf.state : ta.station || "")}</div>
      <pre>${ta.text ? esc(ta.text) : "Keine gültige Vorhersage"}</pre></div>`;
    const ga = gafor?.attributes || {};
    const today = clock.dayKey(new Date());
    let chips;
    if (!ga.datum) chips = `<span class="dim">Kein GAFOR-Gebiet gewählt oder nicht abrufbar.</span>`;
    else if (ga.datum !== today) chips = `<span class="dim">Für heute liegt noch kein GAFOR vor (Stand ${esc(ga.datum)}).</span>`;
    else
      chips = (ga.fenster || [])
        .map((w) => {
          const c = String(w.code || "");
          const lv = /^[XM]/.test(c) ? 2 : /^D/.test(c) ? 1 : 0;
          return `<span class="gchip"><span class="dot" style="background:${LVL_COLOR[lv]}"></span>${pad(w.von_utc)}–${pad(w.bis_utc % 24)} UTC <b>${esc(c)}</b></span>`;
        })
        .join("");
    out += `<div class="block" data-more="gafor"><div class="block-head">GAFOR ${esc(ga.gebiet || "")} ${esc(ga.gebietsname || "")}</div><div class="chips">${chips}</div></div>`;
    return `<div class="reports">${out}</div>`;
  }

  // -------------------------------------------------- DWD-Übersicht

  _dwd() {
    const ov = this._st("gafor")?.attributes?.uebersicht;
    if (!ov) return this._config.ansicht === "dwd" ? `<div class="empty">Keine DWD-Flugwetterübersicht verfügbar.</div>` : "";
    const secs = ov.abschnitte || [];
    const body = (sec) => (sec.tabelle ? `<pre>${esc(sec.text)}</pre>` : esc(sec.text).split("\n\n").map((p) => `<p>${p}</p>`).join(""));
    const first = secs[0];
    const rest = secs.slice(1);
    return `
      <div class="dwd">
        <div class="block-head">${esc(ov.titel || "Flugwetterübersicht")}</div>
        <div class="dim small">${esc(ov.gueltig || "")}</div>
        ${first ? `<div class="sec"><div class="sec-title">${esc(first.titel)}</div>${body(first)}</div>` : ""}
        ${rest.map((sec) => `<details><summary>${esc(sec.titel)}</summary>${body(sec)}</details>`).join("")}
        <div class="hint">Quelle: Deutscher Wetterdienst, unverändert übernommen. ${esc(ov.naechste || "")}</div>
      </div>`;
  }
}

const FW_CSS = `
  :host { display: block; }
  ha-card { height: 100%; overflow: hidden; }
  .card-header { margin: 0; padding: 12px 16px 0; font-size: var(--ha-card-header-font-size, 24px); font-weight: var(--ha-font-weight-normal, 400); color: var(--ha-card-header-color, var(--primary-text-color)); line-height: 1.4; }
  .content { padding: 16px; color: var(--primary-text-color); }
  .divider { height: 1px; background: var(--divider-color); margin: 16px -16px; }
  .empty, .dim { color: var(--secondary-text-color); }
  .small { font-size: 12px; }
  .hint { color: var(--secondary-text-color); font-size: 12px; margin-top: 8px; }
  [data-more] { cursor: pointer; }
  .dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; flex: none; }

  .disclaimer { display: flex; gap: 12px; align-items: flex-start; padding: 12px; margin-bottom: 16px; border-radius: 8px; font-size: 13px; line-height: 1.45;
    border-left: 4px solid var(--warning-color, #ffa600); background: color-mix(in srgb, var(--warning-color, #ffa600) 14%, transparent); }
  .disclaimer ha-icon { color: var(--warning-color, #ffa600); flex: none; --mdc-icon-size: 22px; }
  .disclaimer b { display: block; font-size: 14px; margin-bottom: 2px; }
  .disclaimer-short { display: flex; align-items: center; gap: 6px; margin-top: 12px; font-size: 12px; color: var(--secondary-text-color); }
  .disclaimer-short ha-icon { --mdc-icon-size: 16px; }
  .now-head { display: flex; align-items: center; gap: 12px; }
  .badge { width: 44px; height: 44px; border-radius: 50%; display: flex; align-items: center; justify-content: center; flex: none;
    background: color-mix(in srgb, var(--c) 20%, transparent); color: var(--c); }
  .badge ha-icon { --mdc-icon-size: 26px; }
  .primary { font-size: 20px; font-weight: 500; line-height: 1.3; }
  .secondary { color: var(--secondary-text-color); font-size: 13px; }
  .headline { margin-top: 12px; font-size: 16px; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
  .chip, .gchip { border: 1px solid var(--divider-color); border-radius: 16px; padding: 2px 10px; font-size: 12px; display: inline-flex; align-items: center; gap: 6px; }
  .values { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 10px 16px; margin-top: 14px; }
  .wind-values { flex: 1 1 200px; margin-top: 0; align-content: center; grid-template-columns: repeat(auto-fit, minmax(100px, 1fr)); }
  .label { color: var(--secondary-text-color); font-size: 12px; }
  .val { font-size: 14px; }
  .days { display: flex; flex-wrap: wrap; gap: 8px 24px; margin-top: 14px; }
  .day { display: flex; align-items: center; gap: 8px; font-size: 14px; }
  .day-name { color: var(--secondary-text-color); }

  .tl-grid { display: grid; grid-template-columns: 30px 1fr; row-gap: 4px; }
  .bar { position: relative; display: grid; grid-template-columns: repeat(var(--n), 1fr); gap: 1px; height: 26px; border-radius: 6px; overflow: hidden; }
  .seg { cursor: pointer; opacity: .85; }
  .seg:hover { opacity: 1; }
  .seg.night { background: repeating-linear-gradient(135deg, var(--secondary-background-color) 0 4px, var(--divider-color) 4px 6px); }
  .seg.sel { box-shadow: inset 0 0 0 2px var(--primary-text-color); opacity: 1; }
  .nowmark { position: absolute; top: 0; bottom: 0; width: 2px; background: var(--primary-text-color); pointer-events: none; }
  .hours { display: grid; grid-template-columns: repeat(var(--n), 1fr); font-size: 11px; color: var(--secondary-text-color); height: 16px; }
  .hl { cursor: pointer; white-space: nowrap; overflow: visible; }
  .hl b { color: var(--primary-text-color); font-weight: 500; }
  .axis { position: relative; font-size: 10px; color: var(--secondary-text-color); }
  .axis span { position: absolute; right: 4px; transform: translateY(-50%); }
  .cap { grid-column: 1 / -1; font-size: 12px; color: var(--secondary-text-color); margin-top: 12px; }
  .plot { position: relative; }
  .chart { display: block; width: 100%; overflow: visible; border-bottom: 1px solid var(--divider-color); }
  .chart * { vector-effect: non-scaling-stroke; }
  .nightbg { fill: var(--secondary-background-color); opacity: .7; }
  .selbg { fill: var(--primary-text-color); opacity: .06; }
  .midnight { stroke: var(--divider-color); stroke-width: 1; }
  .nowline { stroke: var(--primary-text-color); stroke-width: 1.5; }
  .lim { stroke-width: 1; stroke-dasharray: 4 3; }
  .lim.bad { stroke: ${LVL_COLOR[2]}; }
  .lim.warn { stroke: ${LVL_COLOR[1]}; }
  polyline.wind { fill: none; stroke: var(--primary-color); stroke-width: 2; }
  polyline.gust { fill: none; stroke: var(--primary-color); stroke-width: 1.5; stroke-dasharray: 3 3; opacity: .7; }
  .xwarea { fill: var(--secondary-text-color); opacity: .18; }
  .cloud { fill: var(--primary-text-color); }
  .ceil { stroke-width: 3; stroke-linecap: round; }
  .legend { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 11px; color: var(--secondary-text-color); margin-top: 8px; }
  .legend span { display: inline-flex; align-items: center; gap: 5px; }
  .legend .sw { display: inline-block; width: 12px; height: 10px; border-radius: 2px; }
  .legend .sw.night { background: repeating-linear-gradient(135deg, var(--secondary-background-color) 0 3px, var(--divider-color) 3px 5px); }
  .legend .sw.xw { background: var(--secondary-text-color); opacity: .35; }
  .legend .sw.cloud { background: var(--primary-text-color); opacity: .4; }
  .legend .ln { display: inline-block; width: 14px; border-top: 2px solid var(--primary-color); }
  .legend .ln.gust { border-top-style: dashed; opacity: .7; }
  .legend .ln.ceil { border-top: 3px solid var(--secondary-text-color); }
  .detail { margin-top: 12px; padding: 12px; border-radius: 12px; background: var(--secondary-background-color); }
  .detail-head { display: flex; align-items: center; gap: 8px; font-size: 14px; }
  .detail .values { margin-top: 10px; }

  .wind { display: flex; flex-wrap: wrap; gap: 16px; align-items: center; justify-content: center; }
  .compass { flex: 0 0 200px; width: 200px; display: block; }
  .ring { fill: none; stroke: var(--divider-color); stroke-width: 2; }
  .tick { stroke: var(--secondary-text-color); stroke-width: 1; opacity: .6; }
  .card-dir { fill: var(--secondary-text-color); font-size: 13px; font-weight: 500; text-anchor: middle; dominant-baseline: central; }
  .rwy { fill: var(--secondary-text-color); opacity: .35; }
  .rwy-cl { stroke: var(--card-background-color, #fff); stroke-width: 1.5; stroke-dasharray: 6 5; }
  .rwy-num { fill: var(--primary-text-color); font-size: 10px; font-weight: 600; text-anchor: middle; dominant-baseline: central; }
  .arrow { stroke-width: 4; stroke-linecap: round; }

  .reports { display: grid; gap: 12px; }
  .block-head { font-size: 14px; font-weight: 500; margin-bottom: 6px; }
  pre { margin: 0; padding: 8px 12px; border-radius: 8px; background: var(--secondary-background-color); font-family: var(--code-font-family, ui-monospace, monospace);
    font-size: 12px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; }
  .dwd p { margin: 6px 0; font-size: 14px; line-height: 1.5; }
  .dwd .sec { margin-top: 10px; }
  .sec-title { font-size: 13px; color: var(--secondary-text-color); }
  details { border-top: 1px solid var(--divider-color); padding: 8px 0; }
  details:first-of-type { margin-top: 10px; }
  summary { cursor: pointer; font-size: 14px; }
  details[open] summary { margin-bottom: 4px; }
`;

if (!customElements.get("vfr-flight-weather-card")) customElements.define("vfr-flight-weather-card", FlugwetterCard);

// ---------------------------------------------------------------- Editoren

const FW_LABELS = {
  device_id: "Flugplatz",
  device_ids: "Flugplätze",
  ansicht: "Ansicht",
  title: "Titel (optional)",
  stunden: "Stunden im Zeitstrahl",
  wolken_max: "Wolkendiagramm bis (ft über Platz)",
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
      hint.textContent = "Grenzwerte, Bahnrichtung und Stationen änderst du in den Einstellungen der Integration „VFR Flight Weather“.";
      this.append(this._form, hint);
    }
    this._form.hass = this._hass;
    this._form.schema = this._schema;
    this._form.data = this._config;
  }
}

class FlugwetterCardEditor extends FlugwetterFormEditor {
  get _schema() {
    const view = this._config?.ansicht || "komplett";
    const schema = [
      { name: "device_id", required: true, selector: { device: { filter: { integration: FW_DOMAIN } } } },
      { name: "ansicht", selector: { select: { mode: "dropdown", options: Object.entries(FW_VIEWS).map(([value, label]) => ({ value, label })) } } },
      { name: "title", selector: { text: {} } },
    ];
    if (view === "zeitstrahl" || view === "komplett") {
      schema.push(
        { name: "stunden", selector: { number: { min: 6, max: 36, step: 1, mode: "slider" } } },
        { name: "wolken_max", selector: { number: { min: 3000, max: 15000, step: 1000, unit_of_measurement: "ft", mode: "box" } } },
      );
    }
    return schema;
  }
}
if (!customElements.get("vfr-flight-weather-card-editor")) customElements.define("vfr-flight-weather-card-editor", FlugwetterCardEditor);

class FlugwetterStrategyEditor extends FlugwetterFormEditor {
  get _schema() {
    return [{ name: "device_ids", required: true, selector: { device: { multiple: true, filter: { integration: FW_DOMAIN } } } }];
  }
}
if (!customElements.get("vfr-flight-weather-strategy-editor")) customElements.define("vfr-flight-weather-strategy-editor", FlugwetterStrategyEditor);

// ---------------------------------------------------------------- Dashboard-Strategie

class FlugwetterDashboardStrategy extends HTMLElement {
  static configRequired = true;
  static registryDependencies = ["entities", "devices"];

  static getConfigElement() {
    return document.createElement("vfr-flight-weather-strategy-editor");
  }

  static getCreateSuggestions() {
    return { title: "VFR-Flugwetter", icon: "mdi:airplane" };
  }

  static async generate(config, hass) {
    let ids = config.device_ids || (config.device_id ? [config.device_id] : []);
    if (!ids.length) ids = fwDevices(hass).map((d) => d.id);
    if (!ids.length) {
      return {
        views: [{
          title: "VFR-Flugwetter",
          cards: [{ type: "markdown", content: "## Noch kein Flugplatz\nRichte unter **Einstellungen → Geräte & Dienste** die Integration **VFR Flight Weather** ein und öffne dann die Einstellungen dieses Dashboards (Zahnrad)." }],
        }],
      };
    }
    return {
      views: ids.map((id) => {
        const dev = hass.devices?.[id];
        const name = dev?.name_by_user || dev?.name || "Flugwetter";
        const icao = name.split(" ")[0];
        const ent = fwEntities(hass, id);
        const card = (ansicht, extra = {}) => ({ type: "custom:vfr-flight-weather-card", device_id: id, ansicht, ...extra });
        const de = String(hass.locale?.language || hass.language || "de").startsWith("de");
        const tile = (key, label) =>
          ent[key] ? { type: "tile", entity: ent[key], name: label, state_content: ["state", "fenster"], grid_options: { columns: 6 } } : null;
        const heading = (h, icon) => ({ type: "heading", heading: h, icon, heading_style: "title" });
        return {
          title: ids.length > 1 ? icao : "VFR-Flugwetter",
          path: icao.toLowerCase().replace(/[^a-z0-9]/g, "") || undefined,
          icon: "mdi:airplane",
          type: "sections",
          max_columns: 3,
          sections: [
            { type: "grid", cards: [heading(name, "mdi:airport"), card("jetzt"), tile("flugtag_heute", de ? "Heute" : "Today"), tile("flugtag_morgen", de ? "Morgen" : "Tomorrow")].filter(Boolean) },
            { type: "grid", column_span: 2, cards: [heading("Nächste 24 Stunden", "mdi:chart-timeline-variant"), card("zeitstrahl")] },
            { type: "grid", cards: [heading("Wind", "mdi:windsock"), card("wind")] },
            { type: "grid", column_span: 2, cards: [heading("Meldungen", "mdi:text-box-outline"), card("meldungen")] },
            { type: "grid", column_span: 3, cards: [heading("DWD-Flugwetterübersicht", "mdi:file-document-outline"), card("dwd")] },
          ],
        };
      }),
    };
  }
}
if (!customElements.get("ll-strategy-dashboard-vfr-flight-weather")) customElements.define("ll-strategy-dashboard-vfr-flight-weather", FlugwetterDashboardStrategy);

window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "vfr-flight-weather-card")) {
  window.customCards.push({
    type: "vfr-flight-weather-card",
    name: "VFR-Flugwetter",
    description: "Flugwetter jetzt, Zeitstrahl mit Wind und Wolkenschichten, Windrose, METAR/TAF/GAFOR und DWD-Übersicht",
    preview: false,
  });
}
window.customStrategies = window.customStrategies || [];
if (!window.customStrategies.some((c) => c.type === "vfr-flight-weather")) {
  window.customStrategies.push({
    type: "vfr-flight-weather",
    strategyType: "dashboard",
    name: "VFR-Flugwetter",
    description: "Ist jetzt Flugwetter, und wann wird es fliegbar? Zeitstrahl, Wolken, Wind, METAR, TAF, GAFOR und DWD-Übersicht für deine Flugplätze.",
    documentationURL: "https://github.com/chrisribal/ha-vfr-flight-weather",
  });
}
console.info(`%c VFR FLIGHT WEATHER %c ${FW_VERSION} `, "background:var(--primary-color,#03a9f4);color:#fff;font-weight:700", "");
