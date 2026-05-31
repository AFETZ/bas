// BAS Admin Dashboard — explanatory UI поверх ИССГР REST + OnBoardDB stats.
// This page is a monitoring/proof surface, not the flight-control console.

const ORIGIN_LAT = -35.363262;
const ORIGIN_LON = 149.165237;
let runtimeConfig = null;

function $(sel) { return document.querySelector(sel); }

// ----- Basemap layers (shared by all Leaflet maps) -----
// Все источники — free tile servers без API-ключа / биллинга.
function makeBaseLayers() {
  return {
    'OSM': L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19, attribution: '© OpenStreetMap',
    }),
    'Спутник (Esri)': L.tileLayer(
      'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      { maxZoom: 19, attribution: 'Esri World Imagery' }),
    'Тёмная (CARTO)': L.tileLayer(
      'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
      { maxZoom: 19, attribution: '© CARTO' }),
    'Светлая (CARTO)': L.tileLayer(
      'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
      { maxZoom: 19, attribution: '© CARTO' }),
  };
}

// Add base layers + layer-switcher control to a map; returns the default layer.
function addBaseLayers(map, defaultName = 'OSM') {
  const layers = makeBaseLayers();
  (layers[defaultName] || layers['OSM']).addTo(map);
  L.control.layers(layers, null, { position: 'topright', collapsed: true })
    .addTo(map);
  return layers;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

async function api(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path} -> HTTP ${r.status}`);
  return r.json();
}

function setPill(id, text, cls) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  el.className = 'pill ' + (cls || '');
}

function setKpi(id, value) {
  const card = document.getElementById(id);
  if (!card) return;
  const valueEl = card.querySelector('.kpi-value');
  if (valueEl) valueEl.textContent = String(value);
}

function fmtTime() {
  return new Date().toISOString().substring(11, 19) + 'Z';
}

function showToast(text) {
  const el = document.getElementById('toast');
  if (!el) return;
  el.textContent = text;
  el.classList.add('show');
  window.setTimeout(() => el.classList.remove('show'), 1800);
}

function labelTable(table) {
  const headers = Array.from(table.querySelectorAll('thead th')).map(th => th.textContent.trim());
  if (!headers.length) return;
  table.querySelectorAll('tbody tr').forEach(row => {
    Array.from(row.children).forEach((cell, i) => {
      cell.dataset.label = headers[i] || '';
    });
  });
}

function labelTables() {
  document.querySelectorAll('table.dense').forEach(labelTable);
}

async function getRuntimeConfig() {
  if (!runtimeConfig) {
    runtimeConfig = await api('/api/admin/config').catch(() => ({
      issgr_url: '',
      has_onboard_db: true,
      has_sync_stats: false,
      sync_stats_url: '',
    }));
  }
  return runtimeConfig;
}

// ----- Tabs -----
document.querySelectorAll('.tab').forEach(btn => {
  btn.addEventListener('click', () => {
    const tab = btn.dataset.tab;
    document.querySelectorAll('.tab').forEach(b => b.classList.toggle('active', b === btn));
    document.querySelectorAll('.tab-panel').forEach(p => {
      p.classList.toggle('active', p.id === 'tab-' + tab);
    });
    if (tab === 'issgr') loadIssgr();
    if (tab === 'multi') loadMulti();
    if (tab === 'onboard') loadOnboard();
    if (tab === 'tilemap') ensureTileMap();
    if (tab === 'sync') loadSync();
    if (tab === 'overview') loadOverview();
  });
});

document.querySelectorAll('[data-copy]').forEach(btn => {
  btn.addEventListener('click', async () => {
    const text = btn.dataset.copy || '';
    try {
      await navigator.clipboard.writeText(text);
      showToast('Команда скопирована');
    } catch (_e) {
      showToast(text);
    }
  });
});

// ----- Health/clock pulse -----
async function clockTick() {
  const now = document.getElementById('now-pill');
  if (now) now.textContent = fmtTime();
  try {
    await api('/api/admin/health');
    setPill('api-pill', 'API ok', 'ok');
  } catch (_e) {
    setPill('api-pill', 'API down', 'err');
  }
}
setInterval(clockTick, 3000);
clockTick();

// ----- Overview -----
async function loadOverview() {
  try {
    const cfg = await getRuntimeConfig();
    const url = await api('/api/admin/issgr_url');
    const issgrUrl = url.url || '';
    const issgrUrlEl = document.getElementById('issgr-url');
    if (issgrUrlEl) issgrUrlEl.textContent = issgrUrl || '-';
    const swagger = document.getElementById('swagger-link');
    if (swagger && issgrUrl) swagger.href = issgrUrl.replace(/\/$/, '') + '/docs';

    const colls = await api('/api/admin/collections');
    setKpi('kpi-collections', Object.keys(colls).length);
    setKpi('kpi-uavs', colls.uavs ?? 0);
    setKpi('kpi-obstacles', colls.obstacles ?? 0);

    const ob = cfg.has_onboard_db
      ? await api('/api/admin/onboard_stats').catch(() => null)
      : null;
    if (ob && ob.tables) {
      const obTot = Object.values(ob.tables).reduce((a, t) => a + (t.count || 0), 0);
      setKpi('kpi-onboard-rows', obTot);
    } else {
      setKpi('kpi-onboard-rows', 'N/A');
    }

    const sync = await api('/api/admin/sync_stats').catch(() => null);
    const syncOk = sync && sync.ok;
    const eptBody = document.querySelector('#endpoints-table tbody');
    const endpoints = [
      ['ИССГР REST', issgrUrl || '(unset)', 'GeoJSON digital twin, collections, API for external ASU clients', issgrUrl ? 'ok' : 'warn'],
      ['On-board DB', ob ? (ob.path || ':memory:') : '(not configured)', 'SQLite бортовой time-series storage + composite metrics', ob ? 'ok' : 'info'],
      ['Multicast sync', syncOk ? sync.endpoint : '239.10.10.10:5500', '40/80B packets: heartbeat, UAV position, sensor readings', syncOk ? 'ok' : 'info'],
      ['Admin UI', window.location.origin + '/', 'Human-readable dashboard for grant/demo proof', 'ok'],
    ];
    eptBody.innerHTML = endpoints.map(([name, value, purpose, status]) =>
      `<tr><td>${escapeHtml(name)}</td><td>${escapeHtml(value)}</td>` +
      `<td>${escapeHtml(purpose)}</td><td><span class="pill ${status === 'ok' ? 'ok' : status === 'warn' ? 'warn' : ''}">${escapeHtml(status)}</span></td></tr>`
    ).join('');
    labelTable(document.getElementById('endpoints-table'));

    const act = await api('/api/admin/activity').catch(() => ({log: []}));
    document.getElementById('activity-log').textContent =
      (act.log || []).slice(-20).map(l => `${l.ts}  ${l.event}  ${l.detail || ''}`).join('\n') || '(no events)';
  } catch (e) {
    console.error(e);
  }
}

// ----- ИССГР items -----
async function loadIssgr() {
  const sel = document.getElementById('issgr-collection-select');
  const c = sel.value;
  try {
    const url = await api('/api/admin/issgr_url').catch(() => ({url: ''}));
    const issgrUrlEl = document.getElementById('issgr-url');
    if (issgrUrlEl) issgrUrlEl.textContent = url.url || '-';
    const swagger = document.getElementById('swagger-link');
    if (swagger && url.url) swagger.href = url.url.replace(/\/$/, '') + '/docs';

    const data = await api(`/api/admin/items?c=${encodeURIComponent(c)}`);
    document.getElementById('issgr-count').textContent =
      `${data.numberReturned || 0} returned (${data.numberMatched || 0} matched)`;
    const tbody = document.querySelector('#issgr-items-table tbody');
    tbody.innerHTML = (data.features || []).slice(0, 80).map(f => {
      const p = f.properties || {};
      const g = f.geometry || {};
      const coords = JSON.stringify(g.coordinates ?? []).substring(0, 72);
      const props = JSON.stringify({
        name: p.name,
        sysid: p.sysid,
        issgr_class: p.issgr_class,
        flight_mode: p.flight_mode,
      }).substring(0, 110);
      return `<tr><td>${escapeHtml(f.id || '-')}</td><td>${escapeHtml(p.name || '-')}</td>` +
             `<td>${escapeHtml(g.type || '-')}</td><td>${escapeHtml(coords)}</td><td>${escapeHtml(props)}</td></tr>`;
    }).join('') || '<tr><td colspan="5" class="muted">(empty or backend unavailable)</td></tr>';
    labelTable(document.getElementById('issgr-items-table'));
  } catch (e) {
    document.getElementById('issgr-count').textContent = 'error: ' + e.message;
  }
}
document.getElementById('issgr-refresh').addEventListener('click', loadIssgr);
document.getElementById('issgr-collection-select').addEventListener('change', loadIssgr);

// ----- Multi-UAV -----
let multiMap = null;
let multiMarkers = {};
let detectionLayer = null;   // deep integration C: CV-детекты с борта

// Цвета классов CV — те же, что на пульте (web/gcs/app.js), чтобы оператор и
// витрина видели объекты одинаково.
const CV_DET_COLORS = {
  person: '#ff5470', car: '#f4b860', truck: '#f4b860', bus: '#f4b860',
  bicycle: '#36d6e7', motorcycle: '#36d6e7',
};

function cvDetIcon(color) {
  return L.divIcon({
    className: 'cv-det-icon',
    html: `<span class="cv-diamond" style="background:${color}"></span>`,
    iconSize: [16, 16],
    iconAnchor: [8, 8],
  });
}

// Читает sensor_readings из ИССГР, фильтрует camera_object_detection и рисует
// объекты в ground-точках на карте витрины. Дрон видит → ИССГР → витрина.
async function loadDetections() {
  ensureMultiMap();
  try {
    const data = await api('/api/admin/items?c=sensor_readings');
    const feats = (data.features || []).filter(
      f => (f.properties || {}).sensor_type === 'camera_object_detection');
    if (!detectionLayer) detectionLayer = L.layerGroup().addTo(multiMap);
    detectionLayer.clearLayers();
    let n = 0;
    feats.forEach(f => {
      const v = (f.properties || {}).value || {};
      const lat = Number(v.ground_lat);
      const lon = Number(v.ground_lon);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
      const cls = v.class_name || '?';
      const conf = Number(v.confidence);
      const color = CV_DET_COLORS[cls] || '#54e08a';
      const confTxt = Number.isFinite(conf) ? ' ' + conf.toFixed(2) : '';
      L.marker([lat, lon], { icon: cvDetIcon(color) })
        .bindTooltip(`🎯 ${escapeHtml(cls)}${confTxt}<br>${lat.toFixed(5)}, ${lon.toFixed(5)}`)
        .addTo(detectionLayer);
      n += 1;
    });
    const badge = document.getElementById('cv-det-count');
    if (badge) badge.textContent = `🎯 ${n}`;
  } catch (e) {
    console.error('loadDetections', e);
  }
}

function ensureMultiMap() {
  if (multiMap) return;
  multiMap = L.map('multi-map').setView([ORIGIN_LAT, ORIGIN_LON], 16);
  addBaseLayers(multiMap, 'Спутник (Esri)');   // satellite default for UAV view
  multiMap.on('click', onMapClickFly);          // deep integration A: клик = лететь
}

async function loadMulti() {
  ensureMultiMap();
  setTimeout(() => multiMap && multiMap.invalidateSize(), 80);
  refreshControlState();
  loadDetections();   // deep integration C: CV-детекты с борта на карте витрины
  try {
    const data = await api('/api/admin/items?c=uavs');
    const tbody = document.querySelector('#uavs-roster tbody');
    const features = data.features || [];
    const count = document.getElementById('multi-count');
    if (count) {
      count.textContent = features.length === 1
        ? '1 БАС в master demo seed'
        : `${features.length} БАС в ИССГР`;
    }
    tbody.innerHTML = features.map(f => {
      const p = f.properties || {};
      return `<tr><td>${escapeHtml(p.sysid ?? '-')}</td><td>${escapeHtml(p.name || '-')}</td>` +
             `<td>${escapeHtml(p.flight_mode || '-')}</td><td>${p.armed ? 'yes' : 'no'}</td>` +
             `<td>${Number(p.altitude_m ?? 0).toFixed(1)}</td>` +
             `<td>${Number(p.battery_v ?? 0).toFixed(2)}</td></tr>`;
    }).join('') || '<tr><td colspan="6" class="muted">(нет UAV в ИССГР)</td></tr>';
    labelTable(document.getElementById('uavs-roster'));

    Object.values(multiMarkers).forEach(m => multiMap.removeLayer(m));
    multiMarkers = {};
    const bounds = [];
    features.forEach(f => {
      const coords = (f.geometry || {}).coordinates;
      if (!coords) return;
      const [lon, lat] = coords;
      const p = f.properties || {};
      const marker = L.circleMarker([lat, lon], {
        radius: 8,
        color: p.armed ? '#8bd36f' : '#a7a393',
        fillColor: p.armed ? '#8bd36f' : '#a7a393',
        fillOpacity: 0.85,
        weight: 2,
      }).bindTooltip(`sysid=${p.sysid ?? '-'} ${p.name || ''}<br>${p.flight_mode || 'mode unknown'} ${Number(p.altitude_m ?? 0).toFixed(1)}m`)
        .addTo(multiMap);
      multiMarkers[p.sysid] = marker;
      bounds.push([lat, lon]);
    });
    if (bounds.length) multiMap.fitBounds(bounds, {padding: [40, 40], maxZoom: 17});
  } catch (e) {
    console.error(e);
  }
}

// ----- On-board metrics -----
async function loadOnboard() {
  try {
    const cfg = await getRuntimeConfig();
    if (!cfg.has_onboard_db) {
      setKpi('ob-uav', 'N/A');
      setKpi('ob-sensor', 'N/A');
      setKpi('ob-mission', 'N/A');
      setKpi('ob-composite', 'N/A');
      const tbody = document.querySelector('#composite-table tbody');
      tbody.innerHTML = '<tr><td colspan="5" class="muted">(бортовая БД не подключена к текущему admin server)</td></tr>';
      labelTable(document.getElementById('composite-table'));
      return;
    }
    const ob = await api('/api/admin/onboard_stats');
    if (!ob || !ob.tables) {
      setKpi('ob-uav', 'N/A');
      setKpi('ob-sensor', 'N/A');
      setKpi('ob-mission', 'N/A');
      setKpi('ob-composite', 'N/A');
      return;
    }
    setKpi('ob-uav', ob.tables.uav_state?.count ?? '-');
    setKpi('ob-sensor', ob.tables.sensor_readings?.count ?? '-');
    setKpi('ob-mission', ob.tables.mission_log?.count ?? '-');
    setKpi('ob-composite', ob.tables.composite_state?.count ?? '-');

    const cm = await api('/api/admin/onboard_composite').catch(() => ({metrics: []}));
    const tbody = document.querySelector('#composite-table tbody');
    const now = Date.now();
    tbody.innerHTML = (cm.metrics || []).map(m => {
      const age = ((now - m.ts_ms) / 1000).toFixed(1);
      return `<tr><td>${escapeHtml(m.sysid)}</td><td>${escapeHtml(m.metric_name)}</td>` +
             `<td>${Number(m.metric_value).toFixed(3)}</td>` +
             `<td>${escapeHtml((m.extra_json || '').substring(0, 100))}</td>` +
             `<td>${age}</td></tr>`;
    }).join('') || '<tr><td colspan="5" class="muted">(нет composite метрик)</td></tr>';
    labelTable(document.getElementById('composite-table'));
  } catch (e) {
    console.error(e);
  }
}

// ----- Tile Map -----
let tileMap = null;
let tileLayer = null;

function ensureTileMap() {
  if (tileMap) {
    setTimeout(() => tileMap.invalidateSize(), 80);
    return;
  }
  tileMap = L.map('tile-map').setView([ORIGIN_LAT, ORIGIN_LON], 12);
  addBaseLayers(tileMap, 'Тёмная (CARTO)');   // dark default matches dashboard theme
  renderTileGrid();
}

async function renderTileGrid() {
  const size = +document.getElementById('tile-size-m').value || 2000;
  const n = +document.getElementById('tile-n').value || 10;
  const e = +document.getElementById('tile-e').value || 10;
  const data = await api(`/api/admin/tile_grid?n=${n}&e=${e}&size=${size}`);
  document.getElementById('tile-stats').textContent =
    `${data.total_tiles} tiles, ${data.coverage_km_north.toFixed(1)} x ${data.coverage_km_east.toFixed(1)} km = ${data.total_area_km2.toFixed(0)} km2`;
  if (tileLayer) tileMap.removeLayer(tileLayer);
  tileLayer = L.geoJSON(data.geojson, {
    style: () => ({
      color: '#5bb7ff',
      weight: 1,
      fillColor: '#5bb7ff',
      fillOpacity: 0.06,
    }),
    onEachFeature: (f, layer) => layer.bindTooltip(f.properties.tile_id),
  }).addTo(tileMap);
  tileMap.fitBounds(tileLayer.getBounds(), {padding: [20, 20]});
}
document.getElementById('tile-render').addEventListener('click', renderTileGrid);

// ----- Sync -----
function renderSyncStats(s) {
  const summary = document.getElementById('sync-summary');
  if (!summary) return;
  if (!s || !s.ok) {
    summary.innerHTML = '<div class="metric"><span>Status</span><strong>not connected</strong></div>';
    return;
  }
  const totals = s.totals || {};
  const last = s.last_tick || {};
  summary.innerHTML = [
    ['Node', s.node_id || '-'],
    ['Endpoint', s.endpoint || '-'],
    ['Uptime', `${Number(s.uptime_s || 0).toFixed(1)}s`],
    ['Tracked objects', s.tracked_objects ?? 0],
    ['Heartbeat packets', totals.HEARTBEAT ?? 0],
    ['L1 position packets', totals.L1 ?? 0],
    ['L2 sensor packets', totals.L2 ?? 0],
    ['Last tick', `L1=${last.n_l1 ?? 0}, L2=${last.n_l2 ?? 0}`],
  ].map(([label, value]) =>
    `<div class="metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`
  ).join('');
}

async function loadSync() {
  try {
    const s = await api('/api/admin/sync_stats').catch(() => null);
    renderSyncStats(s);
    document.getElementById('sync-stats').textContent =
      s ? JSON.stringify(s, null, 2) : '(нет данных - sync_publisher не запущен)';
  } catch (e) {
    document.getElementById('sync-stats').textContent = 'error: ' + e.message;
  }
}

// ===== Deep integration A: управление дроном из витрины (Admin → Web GCS) =====
let controlTargetMarker = null;
let controlHasGcs = false;

async function postControl(path, body) {
  try {
    const r = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok || d.ok === false) {
      showToast('Команда не прошла: ' + (d.error || ('HTTP ' + r.status)));
    } else {
      showToast('Команда отправлена ✓');
    }
    return d;
  } catch (e) {
    showToast('Ошибка связи с витриной');
    return { ok: false };
  }
}

async function onMapClickFly(ev) {
  if (!controlHasGcs) { showToast('Нет связи с Web GCS — запусти полётное демо'); return; }
  const lat = ev.latlng.lat, lon = ev.latlng.lng;
  const north = (lat - ORIGIN_LAT) * 111319.9;
  const east = (lon - ORIGIN_LON) * 111319.9 * Math.cos(ORIGIN_LAT * Math.PI / 180);
  if (controlTargetMarker) multiMap.removeLayer(controlTargetMarker);
  controlTargetMarker = L.marker([lat, lon]).addTo(multiMap)
    .bindTooltip('🎯 цель goto').openTooltip();
  showToast(`Лететь → N=${north.toFixed(0)} E=${east.toFixed(0)} м`);
  await postControl('/api/admin/control/goto', { north, east, altitude: 15 });
}

async function refreshControlState() {
  let d = {};
  try { d = await api('/api/admin/control_state'); } catch (e) { d = {}; }
  const st = (d && d.state) || {};
  controlHasGcs = !!(d && d.has_gcs) && Object.keys(st).length > 0;
  const el = document.getElementById('control-status');
  if (el) {
    if (!d || !d.has_gcs) el.textContent = 'GCS не настроен (admin без --gcs-url)';
    else if (!controlHasGcs) el.textContent = 'нет связи с Web GCS (:8765) — запусти полётное демо';
    else el.textContent =
      `${st.armed ? '🟢 ARMED' : '⚪ disarmed'} · ${st.current_mode || '?'} · ` +
      `${Number(st.altitude_m || 0).toFixed(1)} м · ${st.connected ? 'link OK' : 'no link'}`;
  }
  document.querySelectorAll('.ctl-btn').forEach(b => { b.disabled = !controlHasGcs; });
}

document.querySelectorAll('.ctl-btn').forEach(b => {
  b.addEventListener('click', async () => {
    await postControl('/api/admin/control/command', { action: b.dataset.action, altitude: 15 });
    setTimeout(refreshControlState, 700);
  });
});

// Init + soft polling.
labelTables();
loadOverview();
setInterval(() => {
  const active = document.querySelector('.tab-panel.active');
  if (!active) return;
  if (active.id === 'tab-overview') loadOverview();
  if (active.id === 'tab-multi') loadMulti();
  if (active.id === 'tab-onboard') loadOnboard();
  if (active.id === 'tab-sync') loadSync();
}, 5000);

// Faster live refresh on БАС tab — плавнее видно движение дрона + control state.
setInterval(() => {
  const active = document.querySelector('.tab-panel.active');
  if (active && active.id === 'tab-multi') loadMulti();
}, 1500);
