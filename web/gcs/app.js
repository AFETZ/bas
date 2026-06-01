const stateUrl = "/api/state";
const trail = [];
const rfHistory = [];
let activeHold = null;
let holdTimer = null;
let lastState = null;
let rfObstaclesRendered = false;

const el = (id) => document.getElementById(id);
const fmt = (value, digits = 1) => Number.isFinite(value) ? value.toFixed(digits) : "--";

// Toast notification: показывает ошибки командных запросов и важные events
// в углу экрана. Без неё backend ошибки уходили в console.error и оператор
// не видел почему дрон не реагирует (пример: "Takeoff required before
// velocity control" до auto-takeoff патча).
function toast(message, kind = "info", ttl_ms = 3500) {
  let host = el("toasts");
  if (!host) {
    host = document.createElement("ol");
    host.id = "toasts";
    host.style.cssText = "position:fixed;top:16px;right:16px;z-index:9999;display:flex;flex-direction:column;gap:8px;list-style:none;margin:0;padding:0;font-family:inherit;max-width:360px;";
    document.body.appendChild(host);
  }
  const li = document.createElement("li");
  const palette = {
    info: ["rgba(15,23,42,.92)", "#e5e7eb"],
    warn: ["rgba(180,83,9,.95)", "#fff7ed"],
    error: ["rgba(153,27,27,.95)", "#fef2f2"],
    ok: ["rgba(6,95,70,.95)", "#ecfdf5"],
  }[kind] || ["rgba(15,23,42,.92)", "#e5e7eb"];
  li.style.cssText = `background:${palette[0]};color:${palette[1]};padding:10px 14px;border-radius:8px;box-shadow:0 6px 18px rgba(0,0,0,.32);font-size:13px;line-height:1.35;`;
  li.textContent = message;
  host.appendChild(li);
  setTimeout(() => li.remove(), ttl_ms);
}

async function post(path, payload) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (!data.ok) {
    const msg = data.error || "command failed";
    toast(msg, "error");
    throw new Error(msg);
  }
  return data;
}

function setPill(id, text, cls = "") {
  const node = el(id);
  node.textContent = text;
  node.className = `pill ${cls}`.trim();
}

function localToSvg(north, east) {
  return { x: east, y: -north };
}

function drawRfObstacles(obstacles = []) {
  const host = el("rf-obstacles");
  host.innerHTML = obstacles.map((obstacle) => {
    const n = Number(obstacle.north);
    const e = Number(obstacle.east);
    const sn = Number(obstacle.size_north_m);
    const se = Number(obstacle.size_east_m);
    const x = e - se / 2;
    const y = -(n + sn / 2);
    return `<g class="rf-obstacle" data-id="${obstacle.id}">
      <rect x="${x}" y="${y}" width="${se}" height="${sn}" rx="1.5"/>
      <text x="${e}" y="${-n + 3}">${obstacle.name || obstacle.id}</text>
    </g>`;
  }).join("");
  rfObstaclesRendered = true;
}

function updateRfOverlay(s, p) {
  const rf = s.rf;
  if (!rf?.enabled) {
    el("rf-layer").classList.add("hidden");
    el("rf-panel").classList.add("hidden");
    return;
  }
  el("rf-layer").classList.remove("hidden");
  el("rf-panel").classList.remove("hidden");
  if (!rfObstaclesRendered) drawRfObstacles(rf.obstacles || []);

  const gcs = localToSvg(Number(rf.gcs.north), Number(rf.gcs.east));
  el("rf-gcs").setAttribute("transform", `translate(${gcs.x} ${gcs.y})`);
  const line = el("rf-los-line");
  line.setAttribute("x1", gcs.x);
  line.setAttribute("y1", gcs.y);
  line.setAttribute("x2", p.x);
  line.setAttribute("y2", p.y);
  line.classList.toggle("nlos", !rf.los);

  const status = rf.status || (rf.los ? "LOS" : "NLOS");
  el("rf-status").textContent = status;
  setPill("rf-pill", status, rf.los ? "ok" : "danger");
  el("rf-rssi").textContent = Number.isFinite(rf.rssi_dbm) ? `${fmt(rf.rssi_dbm)} dBm` : "--";
  el("rf-loss").textContent = Number.isFinite(rf.loss_ratio) ? `${fmt(rf.loss_ratio * 100, 0)} %` : "--";
  el("rf-delay").textContent = Number.isFinite(rf.extra_delay_ms) ? `${fmt(rf.extra_delay_ms, 0)} ms` : "--";
  updateRfChart(rf);
}

function updateRfChart(rf) {
  if (!Number.isFinite(rf?.rssi_dbm)) return;
  rfHistory.push({
    rssi: rf.rssi_dbm,
    loss: Number(rf.loss_ratio || 0),
    los: Boolean(rf.los),
  });
  if (rfHistory.length > 180) rfHistory.shift();
  const canvas = el("rf-chart");
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#0b0d0e";
  ctx.fillRect(0, 0, w, h);

  ctx.strokeStyle = "rgba(229,231,235,.18)";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i += 1) {
    const y = 12 + i * ((h - 24) / 4);
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(w, y);
    ctx.stroke();
  }

  const yForRssi = (v) => {
    const min = -100;
    const max = -45;
    return 10 + (1 - Math.max(0, Math.min(1, (v - min) / (max - min)))) * (h - 24);
  };
  const xForIndex = (i) => (rfHistory.length <= 1 ? 0 : (i / (rfHistory.length - 1)) * (w - 1));

  ctx.beginPath();
  rfHistory.forEach((sample, i) => {
    const x = xForIndex(i);
    const y = yForRssi(sample.rssi);
    if (i) ctx.lineTo(x, y);
    else ctx.moveTo(x, y);
  });
  ctx.strokeStyle = rf.los ? "#61d394" : "#ff5c5c";
  ctx.lineWidth = 2.4;
  ctx.stroke();

  ctx.beginPath();
  rfHistory.forEach((sample, i) => {
    const x = xForIndex(i);
    const y = h - 10 - sample.loss * (h - 24);
    if (i) ctx.lineTo(x, y);
    else ctx.moveTo(x, y);
  });
  ctx.strokeStyle = "#f4b860";
  ctx.lineWidth = 1.7;
  ctx.stroke();
}

function updateMap(s) {
  drawGeofence(s);   // deep integration B: запретные зоны + точка облёта
  const local = s.local;
  if (!local) {
    el("north-value").textContent = "--";
    el("east-value").textContent = "--";
    return;
  }
  const p = localToSvg(local.north, local.east);
  const heading = Number(s.heading_deg);
  const rotation = Number.isFinite(heading) ? heading : 0;
  el("drone").setAttribute("transform", `translate(${p.x} ${p.y}) rotate(${rotation})`);
  updateRfOverlay(s, p);
  // Trail только если позиция реально поменялась — иначе при стоянии
  // на месте trail копит дублирующиеся точки и path ничего не показывает.
  const last = trail[trail.length - 1];
  if (!last || Math.hypot(p.x - last.x, p.y - last.y) > 0.2) {
    trail.push(p);
    if (trail.length > 160) trail.shift();
    el("trail").setAttribute("d", trail.map((q, i) => `${i ? "L" : "M"}${q.x.toFixed(1)} ${q.y.toFixed(1)}`).join(" "));
  }
  // Источник позиции: "ned" (LOCAL_POSITION_NED от SITL) или "derived"
  // (вычислено из lat/lon — fallback когда SITL не публикует NED).
  // Показываем оператору тэгом в N/E readout, чтобы было понятно почему
  // позиция может быть менее точной чем при реальном NED.
  const tag = s.local_source === "derived" ? " (gps)" : "";
  el("north-value").textContent = fmt(local.north) + tag;
  el("east-value").textContent = fmt(local.east);

  const target = s.target;
  const targetNode = el("target");
  if (target) {
    const t = localToSvg(target.north, target.east);
    targetNode.classList.remove("hidden");
    targetNode.setAttribute("transform", `translate(${t.x} ${t.y})`);
    const gs = s.goto_status || {};
    let tag = target.active ? " active" : "";
    if (gs.state === "rerouted") tag = " ↪ облёт";
    el("target-value").textContent = `${fmt(target.north)}, ${fmt(target.east)}${tag}`;
  } else {
    targetNode.classList.add("hidden");
    el("target-value").textContent = "--";
  }
}

// Deep integration B: запретные зоны (no-fly) + точка облёта на карте пульта.
// Зоны приходят из /api/state (geofence.zones — те же препятствия, что в ИССГР).
function drawGeofence(s) {
  const host = el("geofence");
  if (!host) return;
  const gf = s.geofence || {};
  const zones = Array.isArray(gf.zones) ? gf.zones : [];
  const margin = Number(gf.margin_m) || 8;
  const status = s.goto_status || {};
  let html = zones.map((z) => {
    const w = Number(z.size_east_m);
    const h = Number(z.size_north_m);
    const e = Number(z.east);
    const n = Number(z.north);
    if (![w, h, e, n].every(Number.isFinite)) return "";
    const fx = e - w / 2;
    const fy = -(n + h / 2);
    const iw = w + margin * 2;
    const ih = h + margin * 2;
    const ix = e - iw / 2;
    const iy = -(n + ih / 2);
    return `<g class="nofly">
      <rect class="nofly-margin" x="${ix}" y="${iy}" width="${iw}" height="${ih}" rx="2"/>
      <rect class="nofly-body" x="${fx}" y="${fy}" width="${w}" height="${h}" rx="1.5"/>
      <text x="${e}" y="${-n + 2.5}">${z.name || z.id || "no-fly"}</text>
    </g>`;
  }).join("");
  if (status.via && Number.isFinite(Number(status.via.east))) {
    const vx = Number(status.via.east);
    const vy = -Number(status.via.north);
    html += `<g class="detour" transform="translate(${vx} ${vy})">
      <circle r="5"/><path d="M-8 0H8M0-8V8"/></g>`;
  }
  host.innerHTML = html;
}

function updateEvents(events) {
  const list = el("events");
  const latest = [...events].slice(-40).reverse();
  list.innerHTML = latest.map((event) => {
    const time = event.ts ? event.ts.split("T")[1]?.replace("Z", "") : "";
    const command = event.command ? ` ${event.command}` : "";
    const label = event.command_name || event.event_type;
    return `<li><strong>${time}</strong> ${label}${command}</li>`;
  }).join("");
}

function renderState(s) {
  lastState = s;
  setPill("link-pill", s.connected && s.mavproxy_running ? "LINK OK" : "LINK WAIT", s.connected ? "ok" : "warn");
  setPill("mode-pill", s.current_mode || "MODE --", s.current_mode === "LAND" ? "warn" : "ok");
  setPill("arm-pill", s.armed ? "ARMED" : "DISARMED", s.armed ? "danger" : "");
  el("altitude").textContent = `${fmt(s.altitude_m)} m`;
  el("speed").textContent = `${fmt(s.groundspeed_mps)} m/s`;
  el("gps").textContent = s.gps_fix_ok ? "FIX" : "--";
  el("run-id").textContent = s.run_id || "--";
  updateMap(s);
  updateEvents(s.events || []);
}

async function refresh() {
  try {
    const res = await fetch(stateUrl, { cache: "no-store" });
    renderState(await res.json());
  } catch (err) {
    setPill("link-pill", "UI LOST", "danger");
  }
}

async function sendAction(action) {
  const altitude = Number(el("goto-north").dataset.altitude || 10);
  await post("/api/command", { action, altitude });
  await refresh();
}

function startHold(action, button) {
  if (activeHold === action) return;
  stopHold(false);
  activeHold = action;
  button?.classList.add("active");
  const tick = () => post("/api/command", { action }).catch(console.error);
  tick();
  holdTimer = setInterval(tick, 650);
}

function stopHold(sendStop = true) {
  if (holdTimer) clearInterval(holdTimer);
  holdTimer = null;
  document.querySelectorAll(".move.active").forEach((node) => node.classList.remove("active"));
  if (activeHold && sendStop) post("/api/command", { action: "stop" }).catch(console.error);
  activeHold = null;
}

document.querySelectorAll("[data-action]").forEach((button) => {
  button.addEventListener("click", () => sendAction(button.dataset.action).catch(console.error));
});

document.querySelectorAll("[data-hold]").forEach((button) => {
  const action = button.dataset.hold;
  button.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    button.setPointerCapture(event.pointerId);
    startHold(action, button);
  });
  button.addEventListener("pointerup", () => stopHold(true));
  button.addEventListener("pointercancel", () => stopHold(true));
  button.addEventListener("pointerleave", () => stopHold(true));
});

document.addEventListener("keydown", (event) => {
  if (event.repeat || event.target.tagName === "INPUT") return;
  // event.code — физическое расположение клавиши на клавиатуре, не зависит
  // от раскладки (Cyrillic ЦФЫВ на тех же клавишах что QWERTY WASD даёт
  // KeyW/A/S/D одинаково). Также мапим IJKL/НШОЛ как альтернативу.
  // Vertical: Space = up, Ctrl = down — как в FPV Drone Simulator (Steam).
  // STOP перенесён на Escape, чтобы не блокировать Space под подъём.
  const map = {
    KeyW: "north", ArrowUp: "north", KeyI: "north",
    KeyS: "south", ArrowDown: "south", KeyK: "south",
    KeyA: "west",  ArrowLeft: "west",  KeyJ: "west",
    KeyD: "east",  ArrowRight: "east", KeyL: "east",
    Space: "up",
    ControlLeft: "down", ControlRight: "down",
    Escape: "stop",
  };
  const action = map[event.code];
  if (!action) return;
  event.preventDefault();
  if (action === "stop") sendAction("stop").catch((e) => toast(e.message, "error"));
  else startHold(action, document.querySelector(`[data-hold="${action}"]`));
});

document.addEventListener("keyup", (event) => {
  const movementKeys = [
    "KeyW", "ArrowUp", "KeyS", "ArrowDown",
    "KeyA", "ArrowLeft", "KeyD", "ArrowRight",
    "KeyI", "KeyK", "KeyJ", "KeyL",
    "Space", "ControlLeft", "ControlRight",
  ];
  if (movementKeys.includes(event.code)) stopHold(true);
});

el("goto-button").addEventListener("click", async () => {
  await post("/api/goto", {
    north: Number(el("goto-north").value),
    east: Number(el("goto-east").value),
  });
  await refresh();
});

el("map").addEventListener("click", async (event) => {
  const svg = event.currentTarget;
  const point = svg.createSVGPoint();
  point.x = event.clientX;
  point.y = event.clientY;
  const ctm = svg.getScreenCTM().inverse();
  const p = point.matrixTransform(ctm);
  const north = -p.y;
  const east = p.x;
  el("goto-north").value = Math.round(north);
  el("goto-east").value = Math.round(east);
  await post("/api/goto", { north, east });
  await refresh();
});

el("center-map").addEventListener("click", () => {
  trail.length = 0;
  refresh();
});

el("rf-clear")?.addEventListener("click", async () => {
  el("goto-north").value = -25;
  el("goto-east").value = 35;
  await post("/api/goto", { north: -25, east: 35 });
  await refresh();
});

el("rf-nlos")?.addEventListener("click", async () => {
  el("goto-north").value = 40;
  el("goto-east").value = 90;
  await post("/api/goto", { north: 40, east: 90 });
  await refresh();
});

el("rf-reset")?.addEventListener("click", () => {
  rfHistory.length = 0;
  refresh();
});

// --- FPV live stream -------------------------------------------------------
// Backend endpoint /api/fpv проверяет TCP-сокет к bas-fpv-mjpeg. Если поток
// доступен — показываем overlay с <img src="/camera.mjpg">. На разрыв
// connection (img.onerror) делаем backoff-ретрай каждые ~3с — gst при
// перезапуске Gazebo поднимется не сразу.

const FPV_RETRY_MS = 3000;
let fpvProbeTimer = null;

function setFpvPlaceholder(text) {
  const ph = el("fpv-placeholder");
  if (!ph) return;
  if (text) {
    ph.textContent = text;
    ph.classList.remove("hidden");
  } else {
    ph.classList.add("hidden");
  }
}

function attachFpvStream() {
  const img = el("fpv-stream");
  if (!img) return;
  // Cache-bust: каждый attach даёт уникальный URL, чтобы браузер не
  // переиспользовал умерший connection после перезапуска gst.
  const bust = Date.now();
  img.classList.remove("dead");
  setFpvPlaceholder("connecting…");
  img.onload = () => setFpvPlaceholder("");
  img.onerror = () => {
    img.classList.add("dead");
    setFpvPlaceholder("waiting for stream…");
    scheduleFpvProbe();
  };
  img.src = `/camera.mjpg?t=${bust}`;
}

function detachFpvStream() {
  const img = el("fpv-stream");
  if (!img) return;
  img.onload = null;
  img.onerror = null;
  img.removeAttribute("src");
}

async function probeFpv() {
  try {
    const res = await fetch("/api/fpv");
    if (!res.ok) return false;
    const data = await res.json();
    return Boolean(data.ok);
  } catch (err) {
    return false;
  }
}

function scheduleFpvProbe(delay = FPV_RETRY_MS) {
  if (fpvProbeTimer) return;
  fpvProbeTimer = setTimeout(async () => {
    fpvProbeTimer = null;
    const overlay = el("fpv-overlay");
    if (!overlay || overlay.classList.contains("hidden")) return;
    const ok = await probeFpv();
    if (ok) attachFpvStream();
    else scheduleFpvProbe();
  }, delay);
}

async function initFpv() {
  const overlay = el("fpv-overlay");
  if (!overlay) return;
  const ok = await probeFpv();
  if (!ok) {
    overlay.classList.add("hidden");
    return;
  }
  overlay.classList.remove("hidden");
  attachFpvStream();
}

function toggleFpv() {
  const overlay = el("fpv-overlay");
  if (!overlay) return;
  if (overlay.classList.contains("hidden")) {
    overlay.classList.remove("hidden");
    attachFpvStream();
  } else {
    overlay.classList.add("hidden");
    detachFpvStream();
  }
}

el("fpv-expand")?.addEventListener("click", () => {
  el("fpv-overlay")?.classList.toggle("expanded");
});

el("fpv-close")?.addEventListener("click", () => {
  const overlay = el("fpv-overlay");
  if (!overlay) return;
  overlay.classList.add("hidden");
  detachFpvStream();
});

// 'F' на физической клавише — toggle FPV без мыши. event.code = "KeyF"
// одинаков на любой раскладке (включая А-кириллицу).
document.addEventListener("keydown", (event) => {
  if (event.target instanceof HTMLInputElement) return;
  if (event.code === "KeyF" && !event.repeat) toggleFpv();
});

initFpv();

refresh();
setInterval(refresh, 650);

// --- Deep integration C: CV-детекты с борта на тактической карте ------------
// Дрон видит объекты камерой → cv_detector кладёт их в ИССГР → пульт рисует
// метки в том же NED, где летит дрон. Оператор видит то же, что и витрина.
const CV_COLORS = {
  person: "#ff5470", car: "#f4b860", truck: "#f4b860", bus: "#f4b860",
  bicycle: "#36d6e7", motorcycle: "#36d6e7",
};

function drawDetections(list = []) {
  const host = el("detections");
  if (!host) return;
  host.innerHTML = list.map((d) => {
    const x = Number(d.east);
    const y = -Number(d.north);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return "";
    const color = CV_COLORS[d.class] || "#54e08a";
    const conf = Number(d.confidence);
    const label = `${d.class}${Number.isFinite(conf) ? " " + conf.toFixed(2) : ""}`;
    return `<g class="cv-det" transform="translate(${x.toFixed(1)} ${y.toFixed(1)})">
      <circle class="cv-ring" r="5" style="stroke:${color}"/>
      <circle class="cv-dot" r="1.8" style="fill:${color}"/>
      <text x="7.5" y="3" style="fill:${color}">${label}</text>
    </g>`;
  }).join("");
}

async function pollDetections() {
  try {
    const res = await fetch("/api/detections", { cache: "no-store" });
    if (!res.ok) return;
    const data = await res.json();
    const list = Array.isArray(data.detections) ? data.detections : [];
    drawDetections(list);
    const cnt = el("cv-count");
    if (cnt) cnt.textContent = String(data.count ?? list.length);
  } catch (_e) {
    /* пульт работает и без ИССГР — слой детектов опционален */
  }
}

pollDetections();
setInterval(pollDetections, 1500);

// --- Deep integration D: кибер-алерты от defense monitor на пульте -----------
// cyber_defense_monitor пишет алерты в общий NDJSON → /api/alerts → красный
// баннер у оператора. Та же атака видна и на витрине (Admin).
const CYBER_LABELS = {
  gps_spoof: "GPS-СПУФИНГ",
  cmd_injection: "ИНЪЕКЦИЯ КОМАНД",
  rf_jamming: "РЧ-ГЛУШЕНИЕ",
};

function escapeHtmlGcs(s) {
  return String(s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function renderCyberBanner(alerts = []) {
  const banner = el("cyber-banner");
  if (!banner) return;
  if (!alerts.length) {
    banner.classList.add("hidden");
    banner.classList.remove("warn");
    banner.innerHTML = "";
    return;
  }
  const top = alerts[0];
  const kind = CYBER_LABELS[top.kind] || String(top.kind || "АТАКА").toUpperCase();
  const more = alerts.length > 1
    ? ` <span class="cyber-more">+${alerts.length - 1}</span>` : "";
  banner.classList.toggle("warn", top.severity === "warn");
  banner.classList.remove("hidden");
  banner.innerHTML = `<span class="cyber-icon">⚠</span>` +
    `<span class="cyber-kind">КИБЕРАТАКА · ${kind}</span>` +
    `<span class="cyber-detail">${escapeHtmlGcs(top.detail || "")}</span>${more}`;
}

async function pollAlerts() {
  try {
    const res = await fetch("/api/alerts", { cache: "no-store" });
    if (!res.ok) return;
    const data = await res.json();
    renderCyberBanner(Array.isArray(data.alerts) ? data.alerts : []);
  } catch (_e) {
    /* пульт работает и без монитора — баннер опционален */
  }
}

pollAlerts();
setInterval(pollAlerts, 2000);
