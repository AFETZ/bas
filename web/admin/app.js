// BAS Admin Dashboard — single-page admin UI поверх ИССГР REST + OnBoardDB stats.
// Каждая tab — независимая загрузка по требованию. Backend endpoints:
//   GET /api/admin/health
//   GET /api/admin/issgr_url
//   GET /api/admin/collections          → {collection: count}
//   GET /api/admin/items?c=uavs         → ИССГР items proxy
//   GET /api/admin/onboard_stats        → OnBoardDB stats (если configured)
//   GET /api/admin/onboard_composite    → latest composite metrics
//   GET /api/admin/tile_grid?n=10&e=10&size=2000 → GeoJSON FeatureCollection
//   GET /api/admin/activity             → recent activity log

const ORIGIN_LAT = -35.363262;
const ORIGIN_LON = 149.165237;

// ----- Tabs -----
document.querySelectorAll('.tab').forEach(btn => {
  btn.addEventListener('click', () => {
    const tab = btn.dataset.tab;
    document.querySelectorAll('.tab').forEach(b => b.classList.toggle('active', b === btn));
    document.querySelectorAll('.tab-panel').forEach(p => {
      p.classList.toggle('active', p.id === 'tab-' + tab);
    });
    // Trigger lazy load.
    if (tab === 'issgr') loadIssgr();
    if (tab === 'multi') loadMulti();
    if (tab === 'onboard') loadOnboard();
    if (tab === 'tilemap') ensureTileMap();
    if (tab === 'sync') loadSync();
    if (tab === 'overview') loadOverview();
  });
});

// ----- Utility -----
async function api(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path} → HTTP ${r.status}`);
  return r.json();
}

function setPill(id, text, cls) {
  const el = document.getElementById(id);
  el.textContent = text;
  el.className = 'pill ' + (cls || '');
}

function setKpi(id, value) {
  const card = document.getElementById(id);
  if (!card) return;
  card.querySelector('.kpi-value').textContent = String(value);
}

function fmtTime() {
  const d = new Date();
  return d.toISOString().substring(11, 19) + 'Z';
}

// ----- Health/clock pulse -----
async function clockTick() {
  document.getElementById('now-pill').textContent = fmtTime();
  try {
    await api('/api/admin/health');
    setPill('api-pill', 'API ok', 'ok');
  } catch (e) {
    setPill('api-pill', 'API down', 'err');
  }
}
setInterval(clockTick, 3000);
clockTick();

// ----- Overview -----
async function loadOverview() {
  try {
    const url = await api('/api/admin/issgr_url');
    document.getElementById('issgr-url').textContent = url.url || '—';
    const colls = await api('/api/admin/collections');
    const tot = Object.values(colls).reduce((a, b) => a + (b || 0), 0);
    setKpi('kpi-collections', Object.keys(colls).length);
    setKpi('kpi-uavs', colls.uavs ?? 0);
    setKpi('kpi-obstacles', colls.obstacles ?? 0);

    const ob = await api('/api/admin/onboard_stats').catch(() => null);
    if (ob && ob.tables) {
      const obTot = Object.values(ob.tables).reduce((a, t) => a + (t.count || 0), 0);
      setKpi('kpi-onboard-rows', obTot);
    } else {
      setKpi('kpi-onboard-rows', 'N/A');
    }

    const eptBody = document.querySelector('#endpoints-table tbody');
    const endpoints = [
      ['ИССГР REST',  url.url || '(unset)', url.url ? 'ok' : 'warn'],
      ['On-board DB', ob ? (ob.path || ':memory:') : '(not connected)', ob ? 'ok' : 'warn'],
      ['Multicast',   '239.10.10.10:5500',   'info'],
    ];
    eptBody.innerHTML = endpoints.map(([n, u, s]) =>
      `<tr><td>${n}</td><td>${u}</td><td><span class="pill ${s === 'ok' ? 'ok' : s === 'warn' ? 'warn' : ''}">${s}</span></td></tr>`).join('');

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
    const data = await api(`/api/admin/items?c=${encodeURIComponent(c)}`);
    document.getElementById('issgr-count').textContent =
      `${data.numberReturned || 0} returned (${data.numberMatched || 0} matched)`;
    const tbody = document.querySelector('#issgr-items-table tbody');
    tbody.innerHTML = (data.features || []).slice(0, 50).map(f => {
      const p = f.properties || {};
      const g = f.geometry || {};
      const coords = JSON.stringify(g.coordinates).substring(0, 60);
      const props = JSON.stringify({name: p.name, sysid: p.sysid, issgr_class: p.issgr_class})
        .substring(0, 80);
      return `<tr><td>${f.id || '—'}</td><td>${p.name || '—'}</td>` +
             `<td>${g.type || '—'}</td><td>${coords}</td><td>${props}</td></tr>`;
    }).join('') || '<tr><td colspan="5" class="muted">(empty)</td></tr>';
  } catch (e) {
    document.getElementById('issgr-count').textContent = 'error: ' + e.message;
  }
}
document.getElementById('issgr-refresh').addEventListener('click', loadIssgr);
document.getElementById('issgr-collection-select').addEventListener('change', loadIssgr);

// ----- Multi-UAV -----
let multiMap = null, multiMarkers = {};
function ensureMultiMap() {
  if (multiMap) return;
  multiMap = L.map('multi-map').setView([ORIGIN_LAT, ORIGIN_LON], 16);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19, attribution: '© OSM',
  }).addTo(multiMap);
}
async function loadMulti() {
  ensureMultiMap();
  setTimeout(() => multiMap && multiMap.invalidateSize(), 80);
  try {
    const data = await api('/api/admin/items?c=uavs');
    const tbody = document.querySelector('#uavs-roster tbody');
    const features = data.features || [];
    tbody.innerHTML = features.map(f => {
      const p = f.properties || {};
      return `<tr><td>${p.sysid ?? '—'}</td><td>${p.name || '—'}</td>` +
             `<td>${p.flight_mode || '—'}</td><td>${p.armed ? '✓' : '✗'}</td>` +
             `<td>${(p.altitude_m ?? 0).toFixed(1)}</td>` +
             `<td>${(p.battery_v ?? 0).toFixed(2)}</td></tr>`;
    }).join('') || '<tr><td colspan="6" class="muted">(нет UAV)</td></tr>';

    // Markers.
    Object.values(multiMarkers).forEach(m => multiMap.removeLayer(m));
    multiMarkers = {};
    const bounds = [];
    features.forEach(f => {
      const c = (f.geometry || {}).coordinates;
      if (!c) return;
      const [lon, lat] = c;
      const p = f.properties || {};
      const marker = L.circleMarker([lat, lon], {
        radius: 7,
        color: p.armed ? '#7adf94' : '#8e96a8',
        fillColor: p.armed ? '#7adf94' : '#8e96a8',
        fillOpacity: 0.8,
      }).bindTooltip(`sysid=${p.sysid} ${p.name || ''}`).addTo(multiMap);
      multiMarkers[p.sysid] = marker;
      bounds.push([lat, lon]);
    });
    if (bounds.length) multiMap.fitBounds(bounds, {padding: [40, 40], maxZoom: 17});
  } catch (e) { console.error(e); }
}

// ----- On-board metrics -----
async function loadOnboard() {
  try {
    const ob = await api('/api/admin/onboard_stats');
    if (!ob || !ob.tables) {
      setKpi('ob-uav', 'N/A');
      setKpi('ob-sensor', 'N/A');
      setKpi('ob-mission', 'N/A');
      setKpi('ob-composite', 'N/A');
      return;
    }
    setKpi('ob-uav',       ob.tables.uav_state?.count ?? '—');
    setKpi('ob-sensor',    ob.tables.sensor_readings?.count ?? '—');
    setKpi('ob-mission',   ob.tables.mission_log?.count ?? '—');
    setKpi('ob-composite', ob.tables.composite_state?.count ?? '—');

    const cm = await api('/api/admin/onboard_composite').catch(() => ({metrics: []}));
    const tbody = document.querySelector('#composite-table tbody');
    const now = Date.now();
    tbody.innerHTML = (cm.metrics || []).map(m => {
      const age = ((now - m.ts_ms) / 1000).toFixed(1);
      return `<tr><td>${m.sysid}</td><td>${m.metric_name}</td>` +
             `<td>${(+m.metric_value).toFixed(3)}</td>` +
             `<td>${(m.extra_json || '').substring(0, 80)}</td>` +
             `<td>${age}</td></tr>`;
    }).join('') || '<tr><td colspan="5" class="muted">(нет composite метрик)</td></tr>';
  } catch (e) { console.error(e); }
}

// ----- Tile Map -----
let tileMap = null, tileLayer = null;
function ensureTileMap() {
  if (tileMap) {
    setTimeout(() => tileMap.invalidateSize(), 80);
    return;
  }
  tileMap = L.map('tile-map').setView([ORIGIN_LAT, ORIGIN_LON], 12);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19, attribution: '© OSM',
  }).addTo(tileMap);
  renderTileGrid();
}
async function renderTileGrid() {
  const size = +document.getElementById('tile-size-m').value || 2000;
  const n = +document.getElementById('tile-n').value || 10;
  const e = +document.getElementById('tile-e').value || 10;
  const data = await api(`/api/admin/tile_grid?n=${n}&e=${e}&size=${size}`);
  document.getElementById('tile-stats').textContent =
    `${data.total_tiles} tiles, ${data.coverage_km_north.toFixed(1)} × ${data.coverage_km_east.toFixed(1)} km = ${data.total_area_km2.toFixed(0)} km²`;
  if (tileLayer) tileMap.removeLayer(tileLayer);
  tileLayer = L.geoJSON(data.geojson, {
    style: f => ({
      color: '#4ea3ff', weight: 1, fillColor: '#4ea3ff', fillOpacity: 0.05,
    }),
    onEachFeature: (f, l) => l.bindTooltip(f.properties.tile_id),
  }).addTo(tileMap);
  tileMap.fitBounds(tileLayer.getBounds(), {padding: [20, 20]});
}
document.getElementById('tile-render').addEventListener('click', renderTileGrid);

// ----- Sync -----
async function loadSync() {
  try {
    const s = await api('/api/admin/sync_stats').catch(() => null);
    document.getElementById('sync-stats').textContent =
      s ? JSON.stringify(s, null, 2) : '(нет данных — sync_publisher не запущен)';
  } catch (e) {
    document.getElementById('sync-stats').textContent = 'error: ' + e.message;
  }
}

// Init.
loadOverview();
