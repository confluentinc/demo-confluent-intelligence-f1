"use strict";

// ---- config ----
const OUR_CAR = 88;
// One orbit of the track == one race lap. We don't hardcode the duration because
// it varies by feed (live = seconds_per_lap, default 60s; mock replays much faster)
// and the live dashboard can't read seconds_per_lap from the Kafka data. Instead we
// measure the wall-clock time between lap-number changes and orbit at that rate.
const DEFAULT_LAP_MS = 60000; // orbit period until the feed's lap cadence is observed
const LAP_MS_MIN = 800;       // clamp measured cadence to sane bounds
const LAP_MS_MAX = 120000;
const GAP_SCALE = 0.012;      // seconds of gap -> fraction of a lap on the map

const TEAM_COLORS = {
  "River Racing": "#1fc77b",
  "Titan Dynamics": "#3b7bff",
  "Apex Motorsport": "#ff7a1a",
  "Scuderia Rossa": "#ff2b2b",
  "Sterling GP": "#00c8c8",
  "Aston Verde": "#1f8f6a",
  "Alpine Force": "#2f6bd8",
  "Sauber Spirit": "#7cff5a",
  "Haas Velocity": "#c9ced6",
  "Williams Heritage": "#5aa9ff",
  "Racing Bulls": "#5b6bff",
};
const teamColor = (t) => TEAM_COLORS[t] || "#9aa7b8";

// ---- state ----
let latest = null;
const carEls = new Map();

const track = document.getElementById("track");
const trackPath = document.getElementById("track-path");
const carsGroup = document.getElementById("cars");
let pathLen = 0;
window.addEventListener("load", () => { pathLen = trackPath.getTotalLength(); });

// Visual orbit state — one lap of the track per race lap, paced by the feed.
let lapPeriodMs = DEFAULT_LAP_MS;       // measured time for one race lap
let orbitPhase = 0;                     // 0..1 position around the track
let lastFrameMs = performance.now();
let lapSeenNum = null;                  // last lap number observed
let lapSeenAt = 0;                      // when we observed it (performance.now)

// Track how fast the feed advances laps so the animation matches race speed.
function trackLapCadence(lap) {
  if (lap == null) return;
  const now = performance.now();
  if (lapSeenNum != null && lap > lapSeenNum) {
    const observed = (now - lapSeenAt) / (lap - lapSeenNum);
    lapPeriodMs = clamp(0.5 * lapPeriodMs + 0.5 * observed, LAP_MS_MIN, LAP_MS_MAX);
    lapSeenNum = lap;
    lapSeenAt = now;
  } else if (lapSeenNum == null || lap < lapSeenNum) {
    // First snapshot, or the race looped back to lap 1 — reset the baseline.
    lapSeenNum = lap;
    lapSeenAt = now;
  }
}

// ---- websocket ----
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  const conn = document.getElementById("conn");
  ws.onopen = () => { conn.textContent = "● LIVE"; conn.className = "conn online"; };
  ws.onclose = () => {
    conn.textContent = "● RECONNECTING"; conn.className = "conn offline";
    setTimeout(connect, 1500);
  };
  ws.onmessage = (e) => { latest = JSON.parse(e.data); render(latest); };
}
connect();

// ---- render on each snapshot ----
function render(s) {
  document.getElementById("lap").textContent = s.lap || "--";
  trackLapCadence(s.lap);
  renderBoard(s.standings);
  renderTelemetry(s.telemetry, s.standings);
  renderAnomaly(s);
  renderAgent(s);
}

function renderBoard(standings) {
  const body = document.getElementById("board-body");
  body.innerHTML = "";
  for (const c of standings) {
    const tr = document.createElement("tr");
    if (c.car_number === OUR_CAR) tr.className = "us";
    const pit = c.in_pit_lane ? '<span class="pit-flag">PIT</span>' : "";
    tr.innerHTML =
      `<td>${c.position ?? "-"}</td>` +
      `<td>${c.car_number}</td>` +
      `<td>${c.driver ?? ""}${pit}</td>` +
      `<td><span class="dot ${c.tire_compound}"></span>${c.tire_compound ?? ""}</td>` +
      `<td>${c.tire_age_laps ?? "-"}</td>` +
      `<td>${c.position === 1 ? "LEADER" : "+" + fmt(c.gap_to_leader_sec, 1)}</td>` +
      `<td>${c.pit_stops ?? 0}</td>`;
    body.appendChild(tr);
  }
}

function renderTelemetry(t, standings) {
  const us = (standings || []).find((c) => c.car_number === OUR_CAR);
  const posPill = document.getElementById("telem-pos");
  posPill.textContent = us && us.position ? "P" + us.position : "";
  if (!t) return;
  const temps = { fl: t.tire_temp_fl_c, fr: t.tire_temp_fr_c, rl: t.tire_temp_rl_c, rr: t.tire_temp_rr_c };
  const anomaly = !!(latest && latest.car_state && latest.car_state.anomaly_tire_temp_fl);
  for (const el of document.querySelectorAll(".tyre")) {
    const k = el.dataset.k;
    const temp = temps[k];
    const bar = el.querySelector("i");
    const val = el.querySelector(".tyre-val");
    bar.style.width = clamp((temp - 90) / 60 * 100, 6, 100) + "%";
    bar.style.background = heatColor(temp);
    val.textContent = temp != null ? Math.round(temp) + "°" : "--";
    el.classList.toggle("alert", k === "fl" && anomaly);
  }
  setBar("m-fuel", "m-fuel-v", t.fuel_remaining_kg / 44 * 100, fmt(t.fuel_remaining_kg, 1) + "kg");
  setBar("m-batt", "m-batt-v", t.battery_charge_pct, Math.round(t.battery_charge_pct) + "%");
  document.getElementById("ro-speed").textContent = Math.round(t.speed_kph ?? 0);
  document.getElementById("ro-throttle").textContent = Math.round(t.throttle_pct ?? 0) + "%";
  document.getElementById("ro-drs").textContent = t.drs_active ? "ON" : "off";
}

function renderAnomaly(s) {
  const locked = document.getElementById("anomaly-locked");
  const live = document.getElementById("anomaly-live");
  if (!s.reveal.car_state || !s.car_state) {
    locked.classList.remove("hidden"); live.classList.add("hidden"); return;
  }
  locked.classList.add("hidden"); live.classList.remove("hidden");
  const cs = s.car_state;
  const status = document.getElementById("anom-status");
  const alert = !!cs.anomaly_tire_temp_fl;
  status.textContent = alert ? "⚠ ANOMALY" : "NOMINAL";
  status.classList.toggle("alert", alert);
  document.getElementById("anom-fl").textContent = fmt(cs.tire_temp_fl_c, 0) + "°C";
  document.getElementById("anom-compound").textContent = cs.tire_compound ?? "--";
  document.getElementById("anom-age").textContent = (cs.tire_age_laps ?? "--") + " laps";
}

function renderAgent(s) {
  const locked = document.getElementById("agent-locked");
  const live = document.getElementById("agent-live");
  if (!s.reveal.pit_decisions || !s.decisions.length) {
    locked.classList.remove("hidden"); live.classList.add("hidden"); return;
  }
  locked.classList.add("hidden"); live.classList.remove("hidden");
  const d = s.decisions[s.decisions.length - 1];
  const banner = document.getElementById("agent-banner");
  banner.textContent = d.suggestion || "--";
  banner.className = "agent-banner " + sugClass(d.suggestion);
  const feed = document.getElementById("agent-feed");
  feed.innerHTML = "";
  for (const x of [...s.decisions].reverse()) {
    const li = document.createElement("li");
    li.innerHTML = `<span class="f-lap">L${x.lap}</span>` +
      `<span class="f-sug ${(x.suggestion || "").replace(/ /g, "-")}">${x.suggestion}</span>` +
      `<span class="f-ctx">${x.condition_summary || ""}</span>`;
    feed.appendChild(li);
  }
}

// ---- track animation loop ----
function animate() {
  const now = performance.now();
  const dt = now - lastFrameMs;
  lastFrameMs = now;
  if (latest && pathLen) {
    // Advance the orbit by the fraction of a lap elapsed since the last frame.
    orbitPhase = (orbitPhase + dt / lapPeriodMs) % 1;
    for (const c of latest.standings) {
      let frac = orbitPhase - (c.gap_to_leader_sec || 0) * GAP_SCALE;
      frac = ((frac % 1) + 1) % 1;
      const pt = trackPath.getPointAtLength(frac * pathLen);
      placeCar(c, pt);
    }
  }
  requestAnimationFrame(animate);
}
requestAnimationFrame(animate);

function placeCar(c, pt) {
  let el = carEls.get(c.car_number);
  if (!el) {
    el = document.createElementNS("http://www.w3.org/2000/svg", "g");
    const ours = c.car_number === OUR_CAR;
    const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    circle.setAttribute("r", ours ? 11 : 6);
    circle.setAttribute("fill", teamColor(c.team));
    circle.setAttribute("class", ours ? "car-dot car-dot-ours" : "car-dot");
    if (ours) { circle.setAttribute("stroke", "#ffffff"); circle.setAttribute("stroke-width", "2"); }
    el.appendChild(circle);
    if (ours) {
      const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
      label.setAttribute("text-anchor", "middle");
      label.setAttribute("dy", "4");
      label.setAttribute("class", "car-label");
      label.textContent = "88";
      el.appendChild(label);
    }
    carsGroup.appendChild(el);
    carEls.set(c.car_number, el);
  }
  el.setAttribute("transform", `translate(${pt.x.toFixed(1)},${pt.y.toFixed(1)})`);
  // keep our car drawn on top
  if (c.car_number === OUR_CAR) carsGroup.appendChild(el);
}

// ---- helpers ----
function setBar(barId, valId, pct, text) {
  document.getElementById(barId).style.width = clamp(pct, 0, 100) + "%";
  document.getElementById(valId).textContent = text;
}
function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
function fmt(v, d) { return v == null ? "--" : Number(v).toFixed(d); }
function heatColor(t) {
  if (t == null) return "#2a3a4e";
  const p = clamp((t - 95) / 50, 0, 1);   // 95°C green -> 145°C red
  const hue = 120 * (1 - p);
  return `hsl(${hue}, 80%, 48%)`;
}
function sugClass(s) {
  if (s === "PIT NOW") return "pit-now";
  if (s === "PIT SOON") return "pit-soon";
  return "stay-out";
}
