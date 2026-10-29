'use strict';

// ---------------------------------------------------------------- utilities
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const store = {
  get(k, d) { try { const v = localStorage.getItem('trackyard.' + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem('trackyard.' + k, JSON.stringify(v)); } catch { /* private mode */ } },
};
const fmtInt = n => n == null ? '—' : Math.round(n).toLocaleString();
const compact = n => Math.abs(n) >= 1e6 ? (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M' : Math.abs(n) >= 1e4 ? Math.round(n / 1e3) + 'k' : Math.abs(n) >= 1e3 ? (n / 1e3).toFixed(1).replace(/\.0$/, '') + 'k' : String(Math.round(n * 10) / 10);
const fmtTime = ts => new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
const fmtDay = ts => new Date(ts * 1000).toLocaleDateString([], { month: 'short', day: 'numeric' });
const fmtDT = ts => ts ? new Date(ts * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—';
const fmtDTm = ts => ts ? new Date(ts * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—';
// Local wall-clock time with its UTC offset, e.g. 2026-10-29T07:21:03+01:00 (exports stay unambiguous).
function isoLocal(ts) {
  const d = new Date(ts * 1000), p = n => String(n).padStart(2, '0'), off = -d.getTimezoneOffset();
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
    + `${off < 0 ? '-' : '+'}${p(Math.floor(Math.abs(off) / 60))}:${p(Math.abs(off) % 60)}`;
}
const RANGE_NAMES = { 1: '1 h', 6: '6 h', 24: '24 h', 168: '7 d' };
function fmtDur(s) {
  s = Math.max(0, Math.round(s));
  if (s < 60) return s + 's';
  if (s < 3600) return Math.floor(s / 60) + 'm' + (s % 60 ? ' ' + (s % 60) + 's' : '');
  if (s < 86400) return Math.floor(s / 3600) + 'h ' + Math.floor(s % 3600 / 60) + 'm';
  return Math.floor(s / 86400) + 'd ' + Math.floor(s % 86400 / 3600) + 'h';
}
function ago(ts) { return ts ? fmtDur(now() - ts).split(' ')[0] + ' ago' : 'never'; }
function hash32(str) { let h = 2166136261; for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); } return h >>> 0; }
function niceTicks(max, n = 4) {
  if (!(max > 0)) return [0, 1];
  const raw = max / n, p = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map(m => m * p).find(s => s >= raw);
  const out = [];
  for (let v = 0; v <= Math.ceil(max / step) * step + 1e-9; v += step) out.push(+v.toFixed(6));
  return out;
}
function barPath(x, y, w, h, horizontal) {
  const r = 0; // square data-ends, as in Carbon charts
  if (horizontal) return `M${x},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h - r}Q${x + w},${y + h} ${x + w - r},${y + h}H${x}Z`;
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}
function csvCell(v) {
  let s = String(v ?? '');
  if (typeof v === 'string' && /^[=+\-@\t\r]/.test(s)) s = "'" + s; // spreadsheet formula injection; numbers pass through
  return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}
// Replace a container's markup without dropping keyboard focus from the button the user is on.
function setHtml(host, html, key) {
  if (host._html === html) return;
  const a = document.activeElement, k = host.contains(a) ? a.getAttribute(key) : null;
  host.innerHTML = host._html = html;
  if (k != null) host.querySelector(`[${key}="${CSS.escape(k)}"]`)?.focus();
}
function download(name, text) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([text], { type: 'text/csv;charset=utf-8' }));
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

// ---------------------------------------------------------------- state
const S = {
  seq: 0, clockOffset: 0, gap: 60, lastUpdate: 0,
  targets: new Map(), receivers: [], mapImage: '', totalSightings: 0,
  stats: new Map(), analytics: null, analyticsV: -1,
  route: null, routeHours: 24,
  watch: new Set(store.get('watch', [])), watchState: new Map(),
  f: { status: 'all', zone: '', minS: 0 },
  sort: { col: 'last_seen', dir: -1 },
  tab: 'live', selected: null, cursor: -1, paused: false,
  detail: null, detailHours: 6, activityMode: 'tags',
  rate: [],
  replay: null, // { at, targets: Map } while the time scrubber shows a past moment
  health: null, conn: 'connecting', truth: null,
};
const liveNow = () => Date.now() / 1000 + S.clockOffset;
const now = () => S.replay ? S.replay.at : liveNow();
const T = () => S.replay ? S.replay.targets : S.targets; // badge states being displayed
const isStale = t => !t.last_seen || now() - t.last_seen > S.gap;

// ---------------------------------------------------------------- zone colours (fixed order = receiver order, never rank)
let zoneIdx = new Map();
function rebuildZones() {
  zoneIdx = new Map(S.receivers.map((r, i) => [r.name, i]));
  const sel = $('#zone-filter'), cur = S.f.zone;
  sel.replaceChildren(new Option('All zones', ''), ...S.receivers.map(r => new Option(r.location ? `${r.name} — ${r.location}` : r.name, r.name)), new Option('Not located', '__none'));
  sel.value = cur;
}
const zoneVar = z => { const i = zoneIdx.get(z); return i == null || i > 7 ? 'var(--c-other)' : `var(--c${i + 1})`; };
let cssCache = {};
function css(name) { return cssCache[name] ??= getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

// ---------------------------------------------------------------- render scheduler
const dirty = new Set();
let rafId = 0;
function invalidate(...parts) {
  parts.forEach(p => dirty.add(p));
  if (!rafId) rafId = requestAnimationFrame(flush);
}
function flush() {
  rafId = 0;
  if (S.paused || document.hidden) return; // keep parts dirty until we are visible again
  const d = new Set(dirty);
  dirty.clear();
  const all = d.has('all');
  if (all || d.has('rows')) { rowsCache = null; d.add('table'); }
  if (all || d.has('tiles')) renderTiles();
  if (S.tab === 'live') {
    if (all || d.has('table')) renderTable('full');
    else if (d.has('tick')) renderTable('tick');
    if (all || d.has('map')) renderMap();
    if (all || d.has('zones')) renderZones();
    if (all || d.has('health')) renderHealth();
  } else {
    if (all || d.has('analytics')) renderAnalytics();
    if (all || d.has('heat') || d.has('analytics')) renderHeat();
  }
  if (S.selected && (all || d.has('facts') || d.has('tick'))) renderFacts();
  if (S.detail && (all || d.has('detail'))) renderDetailCharts();
  renderLivePill();
}

// ---------------------------------------------------------------- live data
// Rows arrive as arrays in LIVE_COLS order: target_id, label (null = same as id), zone, rssi, distance_m, last_seen, stale.
const unpack = a => Array.isArray(a) ? { target_id: a[0], label: a[1] ?? a[0], zone: a[2], rssi: a[3], distance_m: a[4], last_seen: a[5], stale: a[6] } : a;
function applyFull(d) {
  S.seq = d.seq;
  S.clockOffset = d.server_time - Date.now() / 1000;
  S.gap = d.gap_seconds ?? S.gap;
  S.mapImage = d.map_image || '';
  S.totalSightings = d.total_sightings;
  const prev = S.targets;
  S.targets = new Map(d.targets.map(unpack).map(t => [t.target_id, t]));
  watchCheck(prev);
  if (JSON.stringify(d.receivers) !== JSON.stringify(S.receivers)) { S.receivers = d.receivers; rebuildZones(); }
  if (d.analytics_v !== S.analyticsV) loadAnalytics(d.analytics_v);
  liveChanged();
}
function applyDelta(d) {
  if (d.base !== S.seq) return reconnect(); // the server sends a fresh full state to every new subscriber
  S.seq = d.seq;
  S.clockOffset = d.server_time - Date.now() / 1000;
  S.totalSightings = d.total_sightings;
  const prev = new Map();
  for (const raw of d.upsert) { const t = unpack(raw); prev.set(t.target_id, S.targets.get(t.target_id)); S.targets.set(t.target_id, t); }
  for (const id of d.remove) S.targets.delete(id);
  watchCheck(prev, true);
  if (d.receivers) { S.receivers = d.receivers; rebuildZones(); }
  liveChanged();
}
function liveChanged() {
  S.lastUpdate = liveNow();
  S.rate.push([liveNow(), S.totalSightings]);
  while (S.rate.length > 2 && S.rate[0][0] < liveNow() - 120) S.rate.shift();
  invalidate('rows', 'tiles', 'map', 'zones', 'facts');
}
let resyncing = false;
async function resync() {
  if (resyncing) return;
  resyncing = true;
  try { applyFull(await (await fetch('/api/live')).json()); } catch { /* next event retries */ } finally { resyncing = false; }
}
let analyticsLoading = null, analyticsWanted = -1;
async function loadAnalytics(v) {
  analyticsWanted = Math.max(analyticsWanted, v ?? -1);
  if (analyticsLoading) return; // the running load re-checks analyticsWanted when it finishes
  analyticsLoading = (async () => {
    try {
      $('#view-analytics').classList.add('loading');
      const a = await (await fetch('/api/analytics')).json();
      S.analytics = a;
      S.analyticsV = v;
      S.stats = new Map(a.tag_stats.map(s => [s.target_id, s]));
      invalidate('rows', 'tiles', 'analytics', 'facts');
      if (S.tab === 'analytics') loadRoute();
    } catch { /* keep previous */ } finally {
      $('#view-analytics').classList.remove('loading');
      analyticsLoading = null;
      if (analyticsWanted > S.analyticsV) loadAnalytics(analyticsWanted);
    }
  })();
}
async function loadRoute() {
  try {
    S.route = await (await fetch('/api/route?hours=' + S.routeHours)).json();
    invalidate('heat');
  } catch { /* keep previous */ }
}

function setLive(state) {
  S.conn = state;
  renderLivePill();
}
// The pill reflects the scanner too: a healthy stream with no new sightings is not "live".
const QUIET_AFTER = 30;
function renderLivePill() {
  const el = $('#live');
  let state = S.conn, label = S.conn;
  const last = Math.max(0, ...[...S.targets.values()].map(t => t.last_seen || 0));
  const silent = last ? liveNow() - last : null;
  if (S.replay) { state = 'replay'; label = 'replay'; }
  else if (S.paused) { state = 'paused'; label = 'paused'; }
  else if (S.conn === 'live' && silent != null && silent > QUIET_AFTER) { state = 'quiet'; label = 'no new sightings'; }
  el.className = 'pill ' + state;
  $('.txt', el).textContent = label;
  el.title = state === 'quiet' ? 'Connected to the dashboard, but no receiver has reported a badge for ' + fmtDur(silent) : '';
  $('#updated').textContent = S.replay ? 'showing ' + fmtDT(S.replay.at)
    : last ? 'last sighting ' + fmtDur(silent).split(' ')[0] + ' ago' : '';
}
let es = null;
function reconnect() {
  es?.close();
  connect();
}
function connect() {
  if (!('EventSource' in window)) {
    setLive('polling');
    resync();
    setInterval(resync, 3000);
    return;
  }
  es = new EventSource('/api/stream');
  es.addEventListener('status', ev => { setLive(S.paused ? 'paused' : 'live'); applyFull(JSON.parse(ev.data)); });
  es.addEventListener('delta', ev => applyDelta(JSON.parse(ev.data)));
  es.addEventListener('analytics', ev => { const v = JSON.parse(ev.data).v; if (v !== S.analyticsV) loadAnalytics(v); });
  es.onopen = () => setLive(S.paused ? 'paused' : 'live');
  es.onerror = () => setLive('reconnecting');
}

// ---------------------------------------------------------------- watchlist
function saveWatch() { store.set('watch', [...S.watch]); }
function toggleWatch(id) {
  if (S.watch.has(id)) S.watch.delete(id); else S.watch.add(id);
  saveWatch();
  invalidate('rows', 'tiles', 'map');
  if (S.selected === id) renderWatchBtn();
}
function watchCheck(prev, partial) {
  for (const id of S.watch) {
    if (partial && !prev.has(id)) continue;
    const before = prev.get(id), after = S.targets.get(id);
    if (!after || !before) continue;
    if (before.zone !== after.zone && after.zone) toast(id, `moved ${before.zone || 'unlocated'} → ${after.zone}`);
  }
}
function watchStaleCheck() {
  for (const id of S.watch) {
    const t = S.targets.get(id);
    if (!t) continue;
    const st = !t.last_seen || liveNow() - t.last_seen > S.gap, was = S.watchState.get(id); // live, even during replay
    if (was !== undefined && was !== st) toast(id, st ? `went stale (not heard for ${fmtDur(liveNow() - t.last_seen)})` : `is back${t.zone ? ' in ' + t.zone : ''}`);
    S.watchState.set(id, st);
  }
}
function toast(id, msg) {
  const box = $('#toasts');
  while (box.children.length >= 4) box.firstChild.remove();
  const t = document.createElement('button');
  t.className = 'toast';
  const b = document.createElement('b');
  b.textContent = '★ ' + labelOf(id) + ' ';
  t.append(b, document.createTextNode(msg));
  t.onclick = () => { openDetail(id); t.remove(); };
  box.append(t);
  setTimeout(() => t.remove(), 8000);
}
const labelOf = id => T().get(id)?.label || S.targets.get(id)?.label || S.stats.get(id)?.label || id;

// ---------------------------------------------------------------- rows / filtering
let rowsCache = null;
function allRows() {
  if (rowsCache) return rowsCache;
  const out = [], cur = T();
  // In replay only badges present at that moment are listed, and "last seen" is as of then.
  const ids = S.replay ? new Set(cur.keys()) : new Set([...cur.keys(), ...S.stats.keys()]);
  for (const id of ids) {
    const t = cur.get(id), s = S.stats.get(id);
    const label = t?.label || s?.label || id;
    out.push({
      id, label, zone: t?.zone || null, rssi: t?.rssi ?? null, dist: t?.distance_m ?? null,
      last_seen: (S.replay ? t?.last_seen : Math.max(t?.last_seen || 0, s?.last_seen || 0)) || null,
      sightings: s?.sightings ?? null, encounters: s?.encounters ?? null, nearby: s?.nearby_tags ?? null,
      first_seen: s?.first_seen ?? null,
    });
  }
  return rowsCache = out;
}
const SORTERS = {
  watch: r => S.watch.has(r.id) ? 0 : 1,
  label: r => r.label.toLowerCase(), zone: r => r.zone ?? '￿', status: r => isStale(r) ? 1 : 0,
  rssi: r => r.rssi, dist: r => r.dist, last_seen: r => r.last_seen, sightings: r => r.sightings,
  encounters: r => r.encounters, nearby: r => r.nearby, first_seen: r => r.first_seen,
};
function viewRows(skipZone) {
  const f = S.f;
  const rows = allRows().filter(r => {
    if (!skipZone && f.zone && (f.zone === '__none' ? r.zone : r.zone !== f.zone)) return false;
    if (f.minS && (r.sightings ?? 0) < f.minS) return false;
    if (f.status === 'active' && isStale(r)) return false;
    if (f.status === 'stale' && !isStale(r)) return false;
    if (f.status === 'watched' && !S.watch.has(r.id)) return false;
    return true;
  });
  const key = SORTERS[S.sort.col], dir = S.sort.dir;
  rows.sort((a, b) => {
    const va = key(a), vb = key(b);
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;
    return (va < vb ? -1 : va > vb ? 1 : 0) * dir || (a.id < b.id ? -1 : 1);
  });
  return rows;
}

// ---------------------------------------------------------------- tiles
function renderTiles() {
  let active = 0, stale = 0, watchedActive = 0;
  const perZone = new Map();
  for (const t of T().values()) {
    if (isStale(t)) { stale++; continue; }
    active++;
    if (S.watch.has(t.target_id)) watchedActive++;
    if (t.zone) perZone.set(t.zone, (perZone.get(t.zone) || 0) + 1);
  }
  const busiest = [...perZone].sort((a, b) => b[1] - a[1])[0];
  const r = S.rate, perMin = r.length > 1 && r[r.length - 1][0] - r[0][0] > 5
    ? (r[r.length - 1][1] - r[0][1]) / ((r[r.length - 1][0] - r[0][0]) / 60) : null;
  const total = Math.max(S.targets.size, S.stats.size);
  const tiles = [
    { v: fmtInt(total), l: 'Badges seen', s: 'all time' },
    { v: fmtInt(active), l: 'Active now', s: total ? Math.round(active / total * 100) + '% of badges' : '', f: 'active' },
    { v: fmtInt(stale), l: 'Stale', s: `not heard for > ${fmtDur(S.gap)}`, f: 'stale' },
    { v: busiest ? busiest[0] : '—', l: 'Busiest zone', s: busiest ? busiest[1] + ' active badges' : '', z: busiest?.[0] },
    { v: fmtInt(S.receivers.length), l: 'Receivers', s: S.health && !S.replay ? `${S.health.filter(h => h.state === 'ok').length} online · ${perZone.size} with active badges` : perZone.size + ' with active badges' },
    { v: compact(S.totalSightings || 0), l: 'Sightings', s: S.replay ? 'all time (live)' : perMin != null ? '≈ ' + fmtInt(perMin) + ' per minute' : '' },
    { v: fmtInt(S.watch.size), l: 'Watched', s: watchedActive + ' active', f: 'watched' },
  ];
  setHtml($('#tiles'), tiles.map((t, i) => {
    const body = `<div class="lbl">${esc(t.l)}</div><div class="val">${esc(t.v)}</div><div class="sub">${esc(t.s)}</div>`;
    return t.f || t.z ? `<button class="tile clickable" data-i="${i}" title="Filter the badge list">${body}</button>` : `<div class="tile">${body}</div>`;
  }).join(''), 'data-i');
  $('#tiles').onclick = e => {
    const b = e.target.closest('button[data-i]');
    if (!b) return;
    const t = tiles[+b.dataset.i];
    if (t.f) setFilter({ status: S.f.status === t.f ? 'all' : t.f });
    if (t.z) setFilter({ zone: t.z });
    setTab('live');
  };
}

// ---------------------------------------------------------------- virtual table
const ROW_H = 40;
const COLS = [
  { k: 'watch', t: '★', title: 'Watched' }, { k: 'label', t: 'Badge' },
  { k: 'zone', t: 'Zone', title: 'Receiver the badge is closest to (last known zone for stale badges)' }, { k: 'status', t: 'Status' },
  { k: 'rssi', t: 'RSSI', num: 1, title: 'Latest signal strength in dBm (higher = closer)' },
  { k: 'dist', t: '~Dist', num: 1, title: 'Rough distance estimate from signal strength, in metres' }, { k: 'last_seen', t: 'Last seen' },
  { k: 'sightings', t: 'Sightings', num: 1, title: 'Advertisements received, all time' },
  { k: 'encounters', t: 'Encounters', num: 1, title: 'Separate visits in the last 24 h (a new one starts after the badge went unheard for longer than the stale gap)' },
  { k: 'nearby', t: 'Nearby', num: 1, title: 'Other badges heard by the same receiver in the same 5 minutes, last 2 h' },
  { k: 'first_seen', t: 'First seen' },
];
let view = [];
const pool = [];
function buildHead() {
  $('#thead').innerHTML = COLS.map(c => `<div role="columnheader" data-k="${c.k}" class="${c.num ? 'num' : ''}" title="${esc((c.title ? c.title + '. ' : '') + 'Click to sort.')}">${esc(c.t)}</div>`).join('');
  $('#thead').onclick = e => {
    const h = e.target.closest('[data-k]');
    if (!h) return;
    const k = h.dataset.k;
    S.sort = S.sort.col === k ? { col: k, dir: -S.sort.dir } : { col: k, dir: ['label', 'zone', 'status', 'watch'].includes(k) ? 1 : -1 };
    saveHash();
    invalidate('table');
  };
}
function makeRow() {
  const row = document.createElement('div');
  row.className = 'vrow';
  row.setAttribute('role', 'row');
  row.innerHTML = '<div><button class="star" aria-label="Watch">☆</button></div><div class="cell-badge"><b></b><span></span></div><div class="zone-chip"><i></i><span></span></div><div><span class="st"></span></div><div class="num"></div><div class="num"></div><div></div><div class="num"></div><div class="num"></div><div class="num"></div><div></div>';
  row.c = [...row.children];
  row.c.forEach(c => c.setAttribute('role', 'cell'));
  row.parts = { star: $('.star', row), label: $('b', row), id: $('.cell-badge span', row), sw: $('.zone-chip i', row), zone: $('.zone-chip span', row), st: $('.st', row) };
  $('#tbody').append(row);
  return row;
}
function fillRow(row, r, i) {
  row.dataset.id = r.id;
  row.style.transform = `translateY(${i * ROW_H}px)`;
  row.classList.toggle('sel', r.id === S.selected || i === S.cursor);
  const p = row.parts, w = S.watch.has(r.id), st = isStale(r);
  p.star.textContent = w ? '★' : '☆';
  p.star.classList.toggle('on', w);
  p.star.setAttribute('aria-pressed', w);
  p.label.textContent = r.label;
  p.id.textContent = r.label !== r.id ? r.id : '';
  p.sw.style.background = r.zone ? zoneVar(r.zone) : 'transparent';
  p.zone.textContent = r.zone || '—';
  p.st.textContent = st ? 'Stale' : 'Active';
  p.st.className = 'st' + (st ? ' stale' : '');
  row.c[4].textContent = r.rssi != null ? r.rssi : '—';
  row.c[5].textContent = r.dist != null ? r.dist + ' m' : '—';
  row.c[6].textContent = ago(r.last_seen);
  row.c[6].title = fmtDT(r.last_seen);
  row.c[7].textContent = fmtInt(r.sightings);
  row.c[8].textContent = fmtInt(r.encounters);
  row.c[9].textContent = fmtInt(r.nearby);
  row.c[10].textContent = fmtDTm(r.first_seen);
  row.c[10].title = fmtDT(r.first_seen);
}
function renderTable(mode) {
  const body = $('#tbody');
  const timeDependent = S.f.status === 'active' || S.f.status === 'stale' || S.sort.col === 'status';
  const refiltered = mode === 'full' || (mode === 'tick' && timeDependent);
  if (refiltered) {
    view = viewRows();
    $('#tspacer').style.height = view.length * ROW_H + 'px';
    body.style.height = Math.min(window.innerHeight * 0.68, Math.max(ROW_H * 4, view.length * ROW_H + 2)) + 'px';
    const total = allRows().length;
    $('#count').textContent = view.length === total ? `${fmtInt(total)} badges` : `${fmtInt(view.length)} of ${fmtInt(total)} badges`;
    $('#empty').hidden = view.length > 0 || total === 0;
  }
  if (mode === 'full') {
    $$('#thead [data-k]').forEach(h => h.setAttribute('aria-sort', h.dataset.k === S.sort.col ? (S.sort.dir > 0 ? 'ascending' : 'descending') : 'none'));
  }
  const first = Math.max(0, Math.floor(body.scrollTop / ROW_H) - 4);
  const count = Math.ceil((body.clientHeight || 600) / ROW_H) + 8;
  const last = Math.min(view.length, first + count);
  while (pool.length < count) pool.push(makeRow());
  for (let j = 0; j < pool.length; j++) {
    const i = first + j, row = pool[j];
    if (i < last) { row.hidden = false; fillRow(row, view[i], i); } else row.hidden = true;
  }
}
function moveCursor(delta) {
  if (!view.length) return;
  S.cursor = Math.max(0, Math.min(view.length - 1, (S.cursor < 0 ? -1 : S.cursor) + delta));
  const body = $('#tbody'), top = S.cursor * ROW_H;
  if (top < body.scrollTop) body.scrollTop = top;
  else if (top + ROW_H > body.scrollTop + body.clientHeight) body.scrollTop = top + ROW_H - body.clientHeight;
  renderTable('scroll');
}
function exportCsv() {
  const cols = ['target_id', 'label', 'zone', 'status', 'rssi_dbm', 'distance_m', 'last_seen', 'sightings', 'encounters', 'nearby', 'first_seen', 'watched'];
  const iso = ts => ts ? isoLocal(ts) : '';
  const lines = [cols.join(',')].concat(viewRows().map(r => [r.id, r.label, r.zone, isStale(r) ? 'stale' : 'active', r.rssi, r.dist, iso(r.last_seen), r.sightings, r.encounters, r.nearby, iso(r.first_seen), S.watch.has(r.id) ? 'yes' : ''].map(csvCell).join(',')));
  download(`badges-${isoLocal(Date.now() / 1000).slice(0, 16).replace(/[:T]/g, '')}.csv`, lines.join('\n'));
}

// ---------------------------------------------------------------- map
const MAP_W = 1000, MAP_H = 520, MAX_DOTS = 600;
function layoutReceivers() {
  const auto = S.receivers.filter(r => r.x == null || r.y == null);
  return S.receivers.map(r => {
    if (r.x != null && r.y != null) return { ...r, px: r.x / 100 * MAP_W, py: r.y / 100 * MAP_H };
    const k = auto.indexOf(r), n = auto.length, a = n === 1 ? 0 : (k / n) * Math.PI * 2 - Math.PI / 2;
    return { ...r, px: MAP_W / 2 + (n === 1 ? 0 : Math.cos(a) * MAP_W * 0.36), py: MAP_H / 2 + (n === 1 ? 0 : Math.sin(a) * MAP_H * 0.34) };
  });
}
function renderMap() {
  const rx = layoutReceivers(), byName = new Map(rx.map(r => [r.name, r]));
  const rows = viewRows(true).filter(r => T().has(r.id));
  const showStale = S.f.status === 'stale' || S.f.status === 'watched';
  const perZone = new Map();
  let unlocated = 0, staleHidden = 0;
  for (const r of rows) {
    const st = isStale(r);
    if (st && !showStale && !S.watch.has(r.id) && r.id !== S.selected) { staleHidden++; continue; }
    if (!r.zone || !byName.has(r.zone)) { unlocated++; continue; }
    if (!perZone.has(r.zone)) perZone.set(r.zone, []);
    perZone.get(r.zone).push(r);
  }
  const inZone = l => S.f.zone ? (S.f.zone === '__none' ? [] : l.filter(t => t.zone === S.f.zone)) : l;
  const shown = [...perZone.values()].reduce((n, l) => n + inZone(l).length, 0);
  const dots = shown <= MAX_DOTS;
  // The SVG scales with its width; enlarge text and dots on narrow screens so they stay legible.
  const k = Math.min(2.6, Math.max(1, MAP_W / ($('#map').clientWidth || MAP_W)));
  $('#map').style.setProperty('--mk', k);
  const out = [];
  out.push(S.mapImage
    ? `<image href="${esc(S.mapImage)}" x="0" y="0" width="${MAP_W}" height="${MAP_H}" preserveAspectRatio="xMidYMid meet" opacity="0.85"/>`
    : `<defs><pattern id="g" width="40" height="40" patternUnits="userSpaceOnUse"><path d="M40 0H0V40" fill="none" stroke="var(--grid)"/></pattern></defs><rect width="${MAP_W}" height="${MAP_H}" fill="url(#g)"/>`);
  const labels = [];
  let spacing = 200;
  for (const a of rx) for (const b of rx) if (a !== b) spacing = Math.min(spacing, Math.hypot(a.px - b.px, a.py - b.py));
  const rMax = Math.max(24, spacing * 0.42);
  const actives = new Map(rx.map(r => [r.name, (perZone.get(r.name) || []).filter(t => !isStale(t)).length]));
  const maxActive = Math.max(1, ...actives.values());
  for (const r of rx) {
    const list = perZone.get(r.name) || [];
    const active = actives.get(r.name);
    const R = 12 + Math.sqrt(active / maxActive) * (rMax - 12);
    const c = zoneVar(r.name), on = S.f.zone === r.name;
    out.push(`<circle class="bubble" cx="${r.px}" cy="${r.py}" r="${R}" style="fill:${c};stroke:${c}"/>`);
    if (dots) {
      for (const t of inZone(list)) {
        const h = hash32(t.id), a = (h % 3600) / 3600 * Math.PI * 2;
        const d = t.dist != null ? Math.max(14, Math.min(R + 30, t.dist * 16)) : 14 + ((h >>> 12) % 1000) / 1000 * (R - 6);
        const cx = Math.max(6, Math.min(MAP_W - 6, r.px + Math.cos(a) * d)), cy = Math.max(6, Math.min(MAP_H - 6, r.py + Math.sin(a) * d));
        const sel = t.id === S.selected, w = S.watch.has(t.id);
        out.push(`<circle class="dev${isStale(t) ? ' stale' : ''}${sel ? ' sel' : ''}" data-id="${esc(t.id)}" data-ti="${esc(t.id)}" cx="${cx}" cy="${cy}" r="${(sel || w ? 7 : 4.5) * k}" style="fill:${c}"/>`);
        if (sel || w) labels.push(`<text class="devlabel" x="${cx + 9 * k}" y="${cy + 4 * k}">${esc(t.label)}</text>`);
      }
    }
    out.push(`<rect class="rx${on ? ' on' : ''}" data-zone="${esc(r.name)}" data-ti="rx:${esc(r.name)}" x="${r.px - 10 * k}" y="${r.py - 10 * k}" width="${20 * k}" height="${20 * k}"/>`);
    labels.push(`<text class="rxlabel" x="${r.px}" y="${Math.max(16 * k, r.py - Math.max(R, 10 * k) - 6)}" text-anchor="middle">${esc(r.name)} · ${active}</text>`);
    if (r.location) labels.push(`<text class="rxsub" x="${r.px}" y="${r.py + 26 * k}" text-anchor="middle">${esc(r.location)}</text>`);
  }
  out.push(...labels);
  if (!rx.length) out.push(`<text class="note" x="${MAP_W / 2}" y="${MAP_H / 2}" text-anchor="middle">no receivers yet</text>`);
  const host = $('#map');
  host.innerHTML = `<svg viewBox="0 0 ${MAP_W} ${MAP_H}" role="img" aria-label="Floor map: active badges per receiver">${out.join('')}</svg>`;
  host.dataset.tiphost = '';
  tipFns.set(host, key => {
    if (key.startsWith('rx:')) {
      const name = key.slice(3), list = perZone.get(name) || [], r = byName.get(name);
      return { h: name + (r?.location ? ' — ' + r.location : ''), rows: [{ v: list.filter(t => !isStale(t)).length, l: 'active badges' }, { v: 'click', l: S.f.zone === name ? 'to clear the zone filter' : 'to filter by this zone' }] };
    }
    const t = allRows().find(x => x.id === key);
    return t && { h: t.label, rows: [{ v: t.zone || '—', l: 'zone' }, { v: ago(t.last_seen), l: 'last seen' }, { v: t.rssi != null ? t.rssi + ' dBm' : '—', l: 'RSSI' }] };
  });
  const notes = [];
  if (!dots) notes.push(`${fmtInt(shown)} badges — showing counts per zone (pick a zone or filter below ${MAX_DOTS} to see each badge)`);
  if (unlocated) notes.push(`${fmtInt(unlocated)} not located`);
  if (staleHidden) notes.push(`${fmtInt(staleHidden)} stale hidden`);
  $('#map-note').textContent = notes.join(' · ');
}

// ---------------------------------------------------------------- zones panel
function renderZones() {
  const counts = new Map(S.receivers.map(r => [r.name, { a: 0, s: 0 }]));
  let none = 0;
  for (const t of T().values()) {
    if (!t.zone || !counts.has(t.zone)) { if (!isStale(t)) none++; continue; }
    counts.get(t.zone)[isStale(t) ? 's' : 'a']++;
  }
  const list = [...counts].sort((a, b) => b[1].a - a[1].a || (a[0] < b[0] ? -1 : 1));
  const max = Math.max(1, ...list.map(([, c]) => c.a));
  const row = (name, label, c, color) => `<button class="zone-row${S.f.zone === name ? ' on' : ''}" data-zone="${esc(name)}" aria-pressed="${S.f.zone === name}">
    <i class="sw" style="background:${color}"></i><span class="name">${esc(label)}</span><span class="cnt">${fmtInt(c.a)}${c.s ? ` <span class="muted small">+${fmtInt(c.s)} stale</span>` : ''}</span>
    <span class="bar"><i style="width:${c.a / max * 100}%;background:${color}"></i></span></button>`;
  setHtml($('#zones'), list.map(([z, c]) => row(z, z, c, zoneVar(z))).join('') + (none ? row('__none', 'Not located', { a: none, s: 0 }, 'var(--c-other)') : '')
    || '<div class="nodata">No receivers yet.</div>', 'data-zone');
}

// ---------------------------------------------------------------- receiver health
async function loadHealth() {
  if (document.hidden) return;
  try {
    const d = await (await fetch('/api/receivers')).json();
    S.health = d.receivers.map(h => {
      const silent = h.last_ts ? d.server_time - h.last_ts : null;
      return { ...h, silent, perMin: h.recent / (d.window_seconds / 60),
        state: silent == null ? 'down' : silent <= QUIET_AFTER ? 'ok' : silent <= 300 ? 'quiet' : 'down' };
    });
    invalidate('health', 'tiles');
  } catch { /* keep previous */ }
}
function renderHealth() {
  const host = $('#rx-health');
  if (!S.health) return;
  if (!S.health.length) return setHtml(host, '<div class="nodata">No receiver has reported yet.</div>');
  const word = { ok: 'Online', quiet: 'Quiet', down: 'Offline' };
  setHtml(host, S.health.map(h => `<div class="rx-row ${h.state}" title="${esc(h.last_ts ? 'last packet ' + fmtDT(h.last_ts) : 'never heard')}">
    <span class="st ${h.state}">${word[h.state]}</span><span class="name">${esc(h.receiver)}</span>
    <span class="muted small num">${h.silent == null ? 'never' : fmtDur(h.silent).split(' ')[0] + ' ago'} · ${fmtInt(h.perMin)}/min</span></div>`).join(''));
}

// ---------------------------------------------------------------- tooltip layer
const tipFns = new Map();
let tipMark = null;
function showTip(x, y, spec) {
  const tip = $('#tip');
  tip.replaceChildren();
  if (spec.h) { const h = document.createElement('div'); h.className = 't-h'; h.textContent = spec.h; tip.append(h); }
  for (const r of spec.rows) {
    const line = document.createElement('div');
    line.className = 't-r';
    if (r.c) { const k = document.createElement('i'); k.className = 't-k'; k.style.background = r.c; line.append(k); }
    const b = document.createElement('b');
    b.textContent = r.v;
    line.append(b, document.createTextNode(' ' + (r.l || '')));
    tip.append(line);
  }
  tip.hidden = false;
  const w = tip.offsetWidth, h = tip.offsetHeight;
  tip.style.left = Math.min(window.innerWidth - w - 8, x + 14) + 'px';
  tip.style.top = (y + h + 20 > window.innerHeight ? y - h - 12 : y + 14) + 'px';
}
function hideTip() {
  $('#tip').hidden = true;
  if (tipMark) { tipMark.classList.remove('hl'); tipMark = null; }
}
document.addEventListener('pointermove', e => {
  const t = e.target.closest?.('[data-ti]');
  const host = t?.closest('[data-tiphost]');
  const fn = host && tipFns.get(host);
  if (e.target.tagName === 'CANVAS') return; // the heatmap canvas runs its own hit-testing
  const spec = fn && fn(t.dataset.ti, e);
  if (!spec) return hideTip();
  if (tipMark) tipMark.classList.remove('hl');
  tipMark = host.querySelector(`[data-mi="${CSS.escape(t.dataset.ti)}"]`);
  tipMark?.classList.add('hl');
  showTip(e.clientX, e.clientY, spec);
});
document.addEventListener('pointerleave', hideTip);
function setTips(host, fn, onClick) {
  host.dataset.tiphost = '';
  tipFns.set(host, fn);
  host.onclick = onClick ? e => { const t = e.target.closest('[data-ti]'); if (t) onClick(t.dataset.ti); } : null;
}

// ---------------------------------------------------------------- charts
function noData(host, msg) { host.innerHTML = `<div class="nodata">${esc(msg || 'No data yet.')}</div>`; tipFns.delete(host); }
function yAxis(ticks, top, pad, W, ch, fmt = compact) {
  return '<g class="grid">' + ticks.map(t => {
    const y = pad.t + ch - (t / top) * ch;
    return `<line x1="${pad.l}" x2="${W - pad.r}" y1="${y}" y2="${y}"/><text x="${pad.l - 6}" y="${y + 4}" text-anchor="end">${fmt(t)}</text>`;
  }).join('') + '</g>';
}
function vbars(host, data, o) {
  if (!data.length) return noData(host, o.empty);
  const W = host.clientWidth || 600, H = o.height || 200, pad = { l: 44, r: 8, t: 10, b: o.xLabel2 ? 34 : 22 };
  const cw = W - pad.l - pad.r, ch = H - pad.t - pad.b;
  const ticks = niceTicks(Math.max(0, ...data.map(o.value))), top = ticks[ticks.length - 1] || 1;
  const n = data.length, slot = cw / n, gap = slot > 6 ? 2 : slot > 3 ? 1 : 0, bw = Math.max(1, slot - gap);
  let s = `<svg viewBox="0 0 ${W} ${H}" height="${H}">` + yAxis(ticks, top, pad, W, ch);
  data.forEach((d, i) => {
    const v = o.value(d), h = v / top * ch, x = pad.l + i * slot + gap / 2;
    if (h > 0) s += `<path class="m" data-mi="${i}" d="${barPath(x, pad.t + ch - h, bw, h)}"${o.fill ? ` style="fill:${o.fill(d)}"` : ''}/>`;
  });
  data.forEach((d, i) => { s += `<rect class="hit" data-ti="${i}" x="${pad.l + i * slot}" y="${pad.t}" width="${slot}" height="${ch}"/>`; });
  s += `<line class="base" x1="${pad.l}" x2="${W - pad.r}" y1="${pad.t + ch}" y2="${pad.t + ch}"/>`;
  const every = Math.max(1, Math.ceil(70 / slot));
  let prev2 = null;
  for (let i = 0; i < n; i += every) {
    const x = pad.l + i * slot + slot / 2;
    s += `<text x="${x}" y="${pad.t + ch + 15}" text-anchor="middle">${esc(o.xLabel(data[i], i))}</text>`;
    if (o.xLabel2) { const l2 = o.xLabel2(data[i]); if (l2 !== prev2) s += `<text class="lbl" x="${x}" y="${pad.t + ch + 29}" text-anchor="middle">${esc(l2)}</text>`; prev2 = l2; }
  }
  host.innerHTML = s + '</svg>';
  setTips(host, i => o.tip(data[+i]));
}
function hbars(host, items, o) {
  if (!items.length) return noData(host, o.empty);
  const W = host.clientWidth || 500, rowH = 26, pad = { t: 4, b: 4 }, H = pad.t + items.length * rowH + pad.b;
  const lw = Math.min(180, Math.round(W * 0.38)), vw = 56, cw = Math.max(20, W - lw - vw - 8);
  const max = Math.max(1, ...items.map(d => d.value));
  let s = `<svg viewBox="0 0 ${W} ${H}" height="${H}">`;
  items.forEach((d, i) => {
    const y = pad.t + i * rowH, w = Math.max(2, d.value / max * cw);
    s += `<text class="lbl" x="${lw - 8}" y="${y + rowH / 2 + 4}" text-anchor="end">${esc(trunc(d.label, lw / 6.5))}</text>`;
    s += `<path class="m" data-mi="${i}" d="${barPath(lw, y + 5, w, rowH - 10, true)}"${d.color ? ` style="fill:${d.color}"` : ''}/>`;
    s += `<text class="val" x="${lw + w + 6}" y="${y + rowH / 2 + 4}">${esc(o.fmt ? o.fmt(d.value) : fmtInt(d.value))}</text>`;
    s += `<rect class="hit" data-ti="${i}" x="0" y="${y}" width="${W}" height="${rowH}"/>`;
  });
  host.innerHTML = s + '</svg>';
  setTips(host, i => o.tip(items[+i]), o.onClick && (i => o.onClick(items[+i])));
}
const trunc = (s, n) => s.length > n ? s.slice(0, Math.max(1, Math.floor(n) - 1)) + '…' : s;

function renderAnalytics() {
  const a = S.analytics;
  if (!a) return;
  $('#analytics-note').textContent = `aggregates refreshed ${ago(a.generated_at)} · ${fmtInt(a.tag_stats.length)} badges`;
  renderActivity(a);
  vbars($('#c-rssi'), a.rssi_distribution, {
    value: d => d.count, xLabel: d => d.rssi, empty: 'No sightings yet.',
    tip: d => ({ h: `${d.rssi} to ${d.rssi - 4} dBm`, rows: [{ v: fmtInt(d.count), l: 'sightings' }] }),
  });
  // Hours are bucketed in the server's local time; say so only when this browser's zone differs.
  const off = a.utc_offset_minutes ?? 0, tz = off === -new Date().getTimezoneOffset() ? ''
    : ` (server time, UTC${off < 0 ? '−' : '+'}${Math.floor(Math.abs(off) / 60)}${Math.abs(off) % 60 ? ':' + String(Math.abs(off) % 60).padStart(2, '0') : ''})`;
  $('#hourly-note').textContent = 'unique badges · all time' + tz;
  const hours = Array.from({ length: 24 }, (_, h) => a.hourly_activity.find(d => d.hour === h) || { hour: h, count: 0, tags: 0 });
  vbars($('#c-hourly'), a.hourly_activity.length ? hours : [], {
    value: d => d.tags, xLabel: d => String(d.hour).padStart(2, '0') + ':00', empty: 'No sightings yet.',
    tip: d => ({ h: `${String(d.hour).padStart(2, '0')}:00–${String(d.hour).padStart(2, '0')}:59${tz}`, rows: [{ v: fmtInt(d.tags), l: 'unique badges' }, { v: fmtInt(d.count), l: 'sightings' }] }),
  });
  hbars($('#c-flow'), a.zone_flow.slice(0, 12).map(f => ({ label: `${f.from} → ${f.to}`, value: f.count, f })), {
    empty: 'No zone transitions in the last 4 h.',
    tip: d => ({ h: `${d.f.from} → ${d.f.to}`, rows: [{ v: fmtInt(d.value), l: 'transitions' }] }),
    onClick: d => setFilter({ zone: d.f.to }, true),
  });
  hbars($('#c-top'), a.tag_stats.slice(0, 12).map(s => ({ label: s.label, value: s.sightings, s })), {
    tip: d => ({ h: d.s.label, rows: [{ v: fmtInt(d.s.sightings), l: 'sightings' }, { v: fmtInt(d.s.encounters), l: 'encounters (24 h)' }, { v: 'click', l: 'for details' }] }),
    onClick: d => openDetail(d.s.target_id),
  });
  renderGantt(a.tag_stats.slice(0, 15));
  renderPairs($('#c-pairs'), a.colocation.slice(0, 24).map(p => ({ a: p.a, b: p.b, n: p.count })), 'No pairs yet. Time slots where a receiver hears more than 50 badges at once are skipped.');
}
function renderActivity(a) {
  const hours = S.routeHours, bucket = hours <= 1 ? 60 : hours <= 6 ? 300 : hours <= 24 ? 900 : 3600;
  const hist = a.histogram;
  const anchor = hist.length ? hist[hist.length - 1].ts : now();
  const since = anchor - hours * 3600;
  const merged = new Map();
  for (const h of hist) {
    if (h.ts < since) continue;
    const b = Math.floor(h.ts / bucket) * bucket;
    const m = merged.get(b) || { ts: b, count: 0, tags: 0 };
    m.count += h.count;
    m.tags = Math.max(m.tags, h.tags);
    merged.set(b, m);
  }
  const data = [...merged.values()].sort((x, y) => x.ts - y.ts);
  const per = { 60: 'minute', 300: '5 min', 900: '15 min', 3600: 'hour' }[bucket];
  const tags = S.activityMode === 'tags';
  $('#activity-title').textContent = (tags ? (bucket === 60 ? 'Unique badges per minute' : `Peak unique badges per minute (per ${per})`) : `Sightings per ${per}`) + ` · last ${RANGE_NAMES[hours] || hours + ' h'}`;
  vbars($('#c-activity'), data, {
    height: 220, value: d => tags ? d.tags : d.count, xLabel: d => fmtTime(d.ts), xLabel2: d => fmtDay(d.ts), empty: 'No sightings in this range.',
    tip: d => ({ h: `${fmtDay(d.ts)} ${fmtTime(d.ts)}–${fmtTime(d.ts + bucket)}`, rows: [{ v: fmtInt(d.tags), l: bucket === 60 ? 'unique badges' : 'peak unique badges / min' }, { v: fmtInt(d.count), l: 'sightings' }] }),
  });
}
function renderGantt(stats) {
  const host = $('#c-gantt');
  if (!stats.length) return noData(host);
  const W = host.clientWidth || 500, rowH = 24, pad = { t: 4, b: 22 }, H = pad.t + stats.length * rowH + pad.b;
  const lw = Math.min(160, Math.round(W * 0.32)), cw = W - lw - 12;
  const t0 = Math.min(...stats.map(s => s.first_seen)), t1 = Math.max(...stats.map(s => s.last_seen)), span = t1 - t0 || 1;
  const x = ts => lw + (ts - t0) / span * cw;
  let s = `<svg viewBox="0 0 ${W} ${H}" height="${H}"><g class="grid">`;
  for (let i = 0; i <= 4; i++) {
    const ts = t0 + span * i / 4;
    s += `<line x1="${x(ts)}" x2="${x(ts)}" y1="${pad.t}" y2="${H - pad.b}"/><text x="${x(ts)}" y="${H - 6}" text-anchor="${i === 0 ? 'start' : i === 4 ? 'end' : 'middle'}">${esc(fmtTime(ts))}</text>`;
  }
  s += '</g>';
  // One bar per continuous visit; a gap means the badge went unheard for longer than the stale gap.
  const spansOf = st => S.analytics.presence?.[st.target_id] || [[st.first_seen, st.last_seen]];
  stats.forEach((st, i) => {
    const y = pad.t + i * rowH;
    s += `<text class="lbl" x="${lw - 8}" y="${y + rowH / 2 + 4}" text-anchor="end">${esc(trunc(st.label, lw / 6.5))}</text>`;
    s += `<path class="m" data-mi="${i}" d="${spansOf(st).map(([a, b]) => barPath(x(a), y + 5, Math.max(2, x(b) - x(a)), rowH - 10, true)).join('')}"/>`;
    s += `<rect class="hit" data-ti="${i}" x="0" y="${y}" width="${W}" height="${rowH}"/>`;
  });
  host.innerHTML = s + '</svg>';
  setTips(host, i => {
    const st = stats[+i], sp = spansOf(st), present = sp.reduce((n, [a, b]) => n + b - a, 0);
    return { h: st.label, rows: [{ v: fmtInt(sp.length), l: sp.length === 1 ? 'visit' : 'visits' }, { v: fmtDur(present), l: 'present in total' }, { v: fmtDT(st.first_seen), l: 'first seen' }, { v: fmtDT(st.last_seen), l: 'last seen' }] };
  }, i => openDetail(stats[+i].target_id));
}
const ONE_RX_NOTE = 'Needs at least two receivers. With one, every badge in range counts as "together", so this list would show every pair.';
function renderPairs(host, pairs, empty) {
  if (S.receivers.length < 2) return noData(host, ONE_RX_NOTE);
  if (!pairs.length) return noData(host, empty);
  const named = id => labelOf(id) !== id; // only repeat the raw ID under a human-readable label
  host.innerHTML = pairs.map((p, i) => {
    const ids = [p.a, p.b].filter(Boolean), sub = ids.some(named) ? `<div class="mono muted small">${ids.map(esc).join(' · ')}</div>` : '';
    return `<button class="pair" data-i="${i}"><span class="who"><div>${ids.map(id => esc(labelOf(id))).join(' &amp; ')}</div>${sub}</span><span class="cnt">${fmtInt(p.n)}×</span></button>`;
  }).join('');
  host.onclick = e => { const b = e.target.closest('[data-i]'); if (b) openDetail(pairs[+b.dataset.i].a === S.selected && pairs[+b.dataset.i].b ? pairs[+b.dataset.i].b : pairs[+b.dataset.i].a); };
}

// ---------------------------------------------------------------- heatmap (canvas: one draw call path, no DOM per cell)
function seqColor(t) {
  const stops = [1, 2, 3, 4, 5].map(i => css('--seq-' + i)).map(h => [1, 3, 5].map(j => parseInt(h.slice(j, j + 2), 16)));
  const p = Math.min(0.9999, Math.max(0, t)) * (stops.length - 1), i = Math.floor(p), f = p - i;
  return `rgb(${stops[i].map((c, k) => Math.round(c + (stops[i + 1][k] - c) * f)).join(',')})`;
}
function renderHeat() {
  const host = $('#c-heat'), cv = $('canvas', host), R = S.route;
  $('#heat-note').textContent = `unique badges per receiver · last ${RANGE_NAMES[S.routeHours] || S.routeHours + ' h'}`;
  if (!R || !R.rows.length) { cv.hidden = true; if (!$('.nodata', host)) host.insertAdjacentHTML('beforeend', '<div class="nodata">No route data in this range.</div>'); return; }
  cv.hidden = false;
  $('.nodata', host)?.remove();
  host.style.height = Math.min(320, Math.max(104, R.receivers.length * 56 + 26)) + 'px'; // ~56 px per receiver row
  const dpr = window.devicePixelRatio || 1, W = host.clientWidth, H = host.clientHeight;
  cv.width = W * dpr; cv.height = H * dpr;
  const ctx = cv.getContext('2d');
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, W, H);
  const lw = Math.min(140, Math.max(60, ...R.receivers.map(r => r.length * 7))), pad = { t: 4, b: 22, r: 8 };
  const t1 = Math.max(...R.rows.map(r => r[0])) + R.bucket, t0 = Math.max(Math.min(...R.rows.map(r => r[0])), t1 - R.hours * 3600);
  const cols = Math.max(1, Math.round((t1 - t0) / R.bucket)), rowsN = R.receivers.length;
  const cw = (W - lw - pad.r) / cols, ch = (H - pad.t - pad.b) / rowsN;
  const max = Math.max(1, ...R.rows.map(r => r[2]));
  ctx.fillStyle = css('--surface-2');
  ctx.fillRect(lw, pad.t, W - lw - pad.r, H - pad.t - pad.b);
  const grid = new Map();
  for (const r of R.rows) {
    const c = Math.floor((r[0] - t0) / R.bucket);
    if (c < 0 || c >= cols) continue;
    grid.set(r[1] * cols + c, r);
    ctx.fillStyle = seqColor(Math.sqrt(r[2] / max));
    ctx.fillRect(lw + c * cw, pad.t + r[1] * ch + 1, Math.max(1, cw - (cw > 4 ? 1 : 0)), ch - 2);
  }
  ctx.font = '12px "IBM Plex Sans", system-ui, sans-serif';
  ctx.fillStyle = css('--ink-2');
  ctx.textBaseline = 'middle';
  ctx.textAlign = 'right';
  R.receivers.forEach((name, i) => ctx.fillText(trunc(name, lw / 6.5), lw - 8, pad.t + i * ch + ch / 2));
  ctx.fillStyle = css('--muted');
  ctx.textAlign = 'center';
  ctx.textBaseline = 'alphabetic';
  const ticks = Math.max(2, Math.floor((W - lw) / 110));
  for (let i = 0; i <= ticks; i++) {
    const ts = t0 + (t1 - t0) * i / ticks;
    ctx.textAlign = i === 0 ? 'left' : i === ticks ? 'right' : 'center';
    ctx.fillText((R.hours > 24 ? fmtDay(ts) + ' ' : '') + fmtTime(ts), lw + (ts - t0) / (t1 - t0) * (W - lw - pad.r), H - 6);
  }
  cv.onpointermove = e => {
    const b = cv.getBoundingClientRect(), x = e.clientX - b.left, y = e.clientY - b.top;
    const c = Math.floor((x - lw) / cw), ri = Math.floor((y - pad.t) / ch);
    const r = c >= 0 && ri >= 0 && ri < rowsN ? grid.get(ri * cols + c) : null;
    if (!r) return hideTip();
    showTip(e.clientX, e.clientY, { h: `${R.receivers[r[1]]} · ${fmtDay(r[0])} ${fmtTime(r[0])}–${fmtTime(r[0] + R.bucket)}`, rows: [{ v: fmtInt(r[2]), l: 'unique badges' }, { v: fmtInt(r[3]), l: 'sightings' }, { v: r[4] + ' dBm', l: 'avg RSSI' }] });
  };
  cv.onpointerleave = hideTip;
}

// ---------------------------------------------------------------- detail drawer
let detailTimer = 0, detailReq = 0;
let returnFocus = null;
function openDetail(id) {
  if (!id) return;
  const wasOpen = !!S.selected;
  S.selected = id;
  $('#drawer').hidden = false;
  $('#scrim').hidden = window.innerWidth > 1200;
  document.body.classList.add('drawer-open'); // wide screens make room instead of covering the table
  if (!wasOpen && !$('#drawer').contains(document.activeElement)) returnFocus = document.activeElement;
  $('#d-title').focus();
  S.truth = null;
  renderTruth();
  loadTruth();
  renderWatchBtn();
  renderFacts();
  S.detail = null;
  ['#d-strip', '#d-rssi', '#d-segments', '#d-receivers', '#d-peers'].forEach(s => $(s).classList.add('loading'));
  loadDetail();
  clearInterval(detailTimer);
  detailTimer = setInterval(loadDetail, 10000);
  saveHash();
  invalidate('table', 'map');
}
function closeDetail() {
  S.selected = null;
  S.detail = null;
  clearInterval(detailTimer);
  $('#drawer').hidden = true;
  $('#scrim').hidden = true;
  document.body.classList.remove('drawer-open');
  hideTip();
  saveHash();
  invalidate('table', 'map');
  if (returnFocus?.isConnected) returnFocus.focus(); else $('#tbody').focus();
  returnFocus = null;
}

// ---------------------------------------------------------------- ground truth (where a badge really was)
async function loadTruth() {
  const id = S.selected;
  try {
    const t = await (await fetch('/api/truth/' + encodeURIComponent(id))).json();
    if (id === S.selected) { S.truth = t; renderTruth(); }
  } catch { /* optional panel */ }
}
async function markTruth() {
  const id = S.selected, zone = $('#d-truth-zone').value, btn = $('#d-truth-mark');
  if (!id || !zone) return;
  btn.disabled = true;
  try {
    const r = await fetch('/api/truth', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ target_id: id, zone }) });
    if (!r.ok) throw new Error(r.status);
    if (id === S.selected) { S.truth = await r.json(); renderTruth(); }
    toast(id, `marked as in ${zone}`);
  } catch { toast(id, 'could not save the mark'); } finally { btn.disabled = false; }
}
function renderTruth() {
  const sel = $('#d-truth-zone'), cur = sel.value;
  sel.replaceChildren(...S.receivers.map(r => new Option(r.location ? `${r.name} — ${r.location}` : r.name, r.name)));
  sel.value = cur || T().get(S.selected)?.zone || S.receivers[0]?.name || '';
  $('#d-truth-mark').disabled = S.replay != null || !S.receivers.length;
  const t = S.truth, host = $('#d-truth');
  if (!t) { host.innerHTML = ''; return; }
  if (!t.n) { host.innerHTML = '<div class="nodata">No marks yet. Stand in a zone with this badge and mark it, to measure how often the localizer agrees.</div>'; return; }
  host.innerHTML = `<p class="truth-sum"><b>${fmtInt(t.correct)} of ${fmtInt(t.n)}</b> marks matched the localizer (${Math.round(t.accuracy * 100)}%)</p>
    <div class="mini-table"><table><thead><tr><th>Marked at</th><th>Really in</th><th>Localizer said</th><th></th></tr></thead><tbody>${t.marks.slice().reverse().map(m =>
    `<tr><td>${esc(fmtDT(m.ts))}</td><td>${esc(m.zone)}</td><td>${esc(m.predicted || 'not heard')}</td><td>${m.ok ? '✓' : '✗'}</td></tr>`).join('')}</tbody></table></div>`;
}
async function loadDetail() {
  const id = S.selected, req = ++detailReq;
  if (!id || document.hidden) return;
  try {
    const r = await fetch(`/api/tag/${encodeURIComponent(id)}?hours=${S.detailHours}`);
    if (req !== detailReq) return;
    if (!r.ok) { S.detail = { missing: true }; } else S.detail = await r.json();
  } catch { return; }
  ['#d-strip', '#d-rssi', '#d-segments', '#d-receivers', '#d-peers'].forEach(s => $(s).classList.remove('loading'));
  invalidate('detail');
}
function renderWatchBtn() {
  const w = S.watch.has(S.selected), b = $('#d-watch');
  b.textContent = w ? '★ Watching' : '☆ Watch';
  b.setAttribute('aria-pressed', w);
}
function renderFacts() {
  const id = S.selected;
  if (!id) return;
  const t = T().get(id), s = S.stats.get(id), r = allRows().find(x => x.id === id);
  $('#d-title').textContent = labelOf(id);
  $('#d-id').textContent = labelOf(id) !== id ? id : '';
  const facts = [
    ['Status', r ? (isStale(r) ? 'Stale' : 'Active') : '—'], [r && isStale(r) ? 'Last zone' : 'Zone', t?.zone || '—'], ['Last seen', r ? ago(r.last_seen) : '—'],
    ['RSSI', t?.rssi != null ? t.rssi + ' dBm' : '—'], ['~Distance', t?.distance_m != null ? t.distance_m + ' m (approx.)' : '—'],
    ['Sightings', fmtInt(s?.sightings)], ['Encounters (24 h)', fmtInt(s?.encounters)], ['Nearby badges (2 h)', fmtInt(s?.nearby_tags)],
    ['First seen', fmtDT(s?.first_seen)],
  ];
  $('#d-facts').innerHTML = facts.map(([k, v]) => `<div class="fact"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`).join('');
}
function renderDetailCharts() {
  const d = S.detail;
  if (d.missing) { ['#d-strip', '#d-rssi', '#d-segments', '#d-receivers', '#d-peers'].forEach(s => noData($(s), 'No history for this badge.')); return; }
  renderStrip(d);
  renderRssiLines(d);
  const segs = d.segments.filter(s => s.kind === 'zone').reverse();
  $('#d-segments').innerHTML = segs.length ? `<table><thead><tr><th>Zone</th><th>Enter</th><th>Leave</th><th class="num">Dwell</th></tr></thead><tbody>${segs.map(s =>
    `<tr><td><span class="zone-chip"><i style="background:${zoneVar(s.zone)}"></i>${esc(s.zone)}</span></td><td>${esc(fmtDT(s.enter_ts))}</td><td>${esc(fmtDT(s.leave_ts))}</td><td class="num">${esc(fmtDur(s.dwell_seconds))}</td></tr>`).join('')}</tbody></table>` : '<div class="nodata">No visits in this range.</div>';
  $('#d-receivers').innerHTML = d.receivers.length ? `<table><thead><tr><th>Receiver</th><th class="num">Sightings</th><th class="num">Avg RSSI</th><th class="num">Best</th><th>Last heard</th></tr></thead><tbody>${d.receivers.map(r =>
    `<tr><td><span class="zone-chip"><i style="background:${zoneVar(r.receiver)}"></i>${esc(r.receiver)}</span></td><td class="num">${fmtInt(r.sightings)}</td><td class="num">${r.avg_rssi}</td><td class="num">${r.best_rssi}</td><td>${esc(ago(r.last_seen))}</td></tr>`).join('')}</tbody></table>` : '<div class="nodata">Not heard in this range.</div>';
  renderPairs($('#d-peers'), d.peers.map(p => ({ a: p.target_id, n: p.shared })), 'No other badges heard nearby in the last 2 h.');
}
function renderStrip(d) {
  const host = $('#d-strip'), segs = d.segments;
  if (!segs.length) return noData(host, 'No visits in this range.');
  const W = host.clientWidth || 560, H = 46, t0 = d.since, t1 = Math.max(d.last_seen, segs[segs.length - 1].leave_ts), span = t1 - t0 || 1;
  const x = ts => (ts - t0) / span * W;
  let s = `<svg viewBox="0 0 ${W} ${H}" height="${H}"><rect x="0" y="2" width="${W}" height="24" rx="4" fill="var(--surface-2)"/>`;
  segs.forEach((g, i) => {
    if (g.kind !== 'zone') return;
    const x1 = x(g.enter_ts), w = Math.max(2, x(g.leave_ts) - x1);
    s += `<rect class="m" data-mi="${i}" data-ti="${i}" x="${x1}" y="2" width="${w}" height="24" style="fill:${zoneVar(g.zone)}"/>`;
  });
  for (let i = 0; i <= 4; i++) {
    const ts = t0 + span * i / 4;
    s += `<text x="${x(ts)}" y="${H - 4}" text-anchor="${i === 0 ? 'start' : i === 4 ? 'end' : 'middle'}">${esc((d.hours > 24 ? fmtDay(ts) + ' ' : '') + fmtTime(ts))}</text>`;
  }
  host.innerHTML = s + '</svg>';
  setTips(host, i => { const g = segs[+i]; return { h: g.zone, rows: [{ v: fmtDur(g.dwell_seconds), l: 'dwell' }, { v: `${fmtTime(g.enter_ts)}–${fmtTime(g.leave_ts)}`, l: '' }] }; });
}
function renderRssiLines(d) {
  const host = $('#d-rssi'), pts = d.rssi.points, B = d.rssi.bucket;
  if (!pts.length) return noData(host, 'No signal data in this range.');
  const byRx = new Map();
  for (const [b, rx, a] of pts) { if (!byRx.has(rx)) byRx.set(rx, new Map()); byRx.get(rx).set(b, a); }
  const series = [...byRx.keys()].sort((a, b) => (zoneIdx.get(a) ?? 99) - (zoneIdx.get(b) ?? 99));
  const W = host.clientWidth || 560, H = 200, pad = { l: 40, r: 8, t: 8, b: 22 }, cw = W - pad.l - pad.r, ch = H - pad.t - pad.b;
  const t0 = d.since, t1 = d.last_seen, span = t1 - t0 || 1;
  const vals = pts.map(p => p[2]), lo = Math.floor((Math.min(...vals) - 3) / 10) * 10, hi = Math.ceil((Math.max(...vals) + 3) / 10) * 10;
  const x = ts => pad.l + (ts + B / 2 - t0) / span * cw, y = v => pad.t + (hi - v) / (hi - lo || 1) * ch;
  let s = `<svg viewBox="0 0 ${W} ${H}" height="${H}"><g class="grid">`;
  for (let v = lo; v <= hi; v += (hi - lo) / 4 >= 10 ? 10 * Math.ceil((hi - lo) / 40) : 5) s += `<line x1="${pad.l}" x2="${W - pad.r}" y1="${y(v)}" y2="${y(v)}"/><text x="${pad.l - 6}" y="${y(v) + 4}" text-anchor="end">${v}</text>`;
  s += '</g>';
  for (const rx of series) {
    const m = byRx.get(rx), keys = [...m.keys()].sort((a, b) => a - b);
    let path = '', prev = null;
    for (const b of keys) { path += (prev != null && b - prev <= B * 2 ? 'L' : 'M') + x(b).toFixed(1) + ',' + y(m.get(b)).toFixed(1); prev = b; }
    s += `<path d="${path}" fill="none" stroke="${zoneVar(rx)}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
  }
  for (let i = 0; i <= 4; i++) {
    const ts = t0 + span * i / 4;
    s += `<text x="${pad.l + (ts - t0) / span * cw}" y="${H - 6}" text-anchor="${i === 0 ? 'start' : i === 4 ? 'end' : 'middle'}">${esc((d.hours > 24 ? fmtDay(ts) + ' ' : '') + fmtTime(ts))}</text>`;
  }
  s += `<line class="xhair" x1="0" x2="0" y1="${pad.t}" y2="${pad.t + ch}" visibility="hidden"/><rect class="hit" data-ti="x" x="${pad.l}" y="${pad.t}" width="${cw}" height="${ch}"/></svg>`;
  const legend = series.length > 1 ? `<div class="legend small muted">${series.map(rx => `<span><i class="sw" style="border-radius:1px;height:2px;width:14px;background:${zoneVar(rx)}"></i>${esc(rx)}</span>`).join('')}</div>` : `<div class="legend small muted"><span>${esc(series[0])} · dBm (higher = closer)</span></div>`;
  host.innerHTML = s + legend;
  host.dataset.xhair = '';
  const xh = $('.xhair', host), svg = $('svg', host);
  setTips(host, (_, e) => {
    const bb = svg.getBoundingClientRect(), px = (e.clientX - bb.left) / bb.width * W;
    const b = Math.round(((px - pad.l) / cw * span + t0 - B / 2) / B) * B;
    xh.setAttribute('x1', x(b)); xh.setAttribute('x2', x(b)); xh.setAttribute('visibility', 'visible');
    const rows = series.filter(rx => byRx.get(rx).has(b)).map(rx => ({ v: byRx.get(rx).get(b) + ' dBm', l: rx, c: zoneVar(rx) }));
    return { h: `${fmtTime(b)}–${fmtTime(b + B)}`, rows: rows.length ? rows : [{ v: '—', l: 'not heard' }] };
  });
  svg.onpointerleave = () => xh.setAttribute('visibility', 'hidden');
}
function detailCsv() {
  const d = S.detail;
  if (!d || d.missing) return;
  const lines = ['kind,zone,enter,leave,dwell_seconds'].concat(d.segments.map(s => [s.kind, s.zone, isoLocal(s.enter_ts), isoLocal(s.leave_ts), s.dwell_seconds].map(csvCell).join(',')));
  download(`timeline-${d.target_id}.csv`, lines.join('\n'));
}

// ---------------------------------------------------------------- time scrubber (show the floor at a past moment)
let replayReq = 0, playTimer = 0, scrubTimer = 0, scrubbing = false;
function scrubLow() {
  const firsts = [...S.stats.values()].map(s => s.first_seen).filter(Boolean);
  return firsts.length ? Math.min(...firsts) : (S.replay?.at ?? liveNow() - 3600);
}
async function showAt(ts) {
  const req = ++replayReq;
  if (liveNow() - ts < 10) return goLive();
  try {
    const d = await (await fetch('/api/at?ts=' + Math.round(ts))).json();
    if (req !== replayReq) return;
    S.replay = { at: d.at, targets: new Map(d.targets.map(unpack).map(t => [t.target_id, t])) };
  } catch { return; }
  replayChanged();
}
function goLive() {
  replayReq++;
  stopPlay();
  if (!S.replay) return syncScrub();
  S.replay = null;
  replayChanged();
}
function replayChanged() {
  syncScrub();
  saveHash();
  if (S.selected) renderTruth();
  invalidate('rows', 'tiles', 'map', 'zones', 'facts');
}
function syncScrub() {
  const r = $('#scrub');
  r.min = Math.floor(scrubLow() / 10) * 10; // aligned to the step, so values land on whole 10 s marks
  r.max = Math.ceil(liveNow());
  r.step = 10;
  if (!scrubbing) r.value = S.replay ? S.replay.at : r.max;
  $('#scrub-time').textContent = S.replay ? fmtDT(S.replay.at) : 'Live';
  $('#scrub-live').hidden = !S.replay;
  $('#view-live').classList.toggle('replaying', !!S.replay);
  renderLivePill();
}
function stopPlay() {
  clearInterval(playTimer);
  playTimer = 0;
  $('#scrub-play').setAttribute('aria-pressed', 'false');
  $('#scrub-play').textContent = '▶ Play';
}
function togglePlay() {
  if (playTimer) return stopPlay();
  if (!S.replay) showAt(scrubLow());
  $('#scrub-play').setAttribute('aria-pressed', 'true');
  $('#scrub-play').textContent = '❚❚ Pause';
  playTimer = setInterval(() => { if (S.replay) showAt(S.replay.at + 60); }, 700); // one minute per step
}

// ---------------------------------------------------------------- filters, tabs, URL state
function setFilter(patch, goLive) {
  Object.assign(S.f, patch);
  $('#zone-filter').value = S.f.zone;
  $('#min-sightings').value = String(S.f.minS);
  $$('#status-filter button').forEach(b => b.setAttribute('aria-pressed', b.dataset.v === S.f.status));
  $('#clear-filters').hidden = !(S.f.zone || S.f.minS || S.f.status !== 'all');
  S.cursor = -1;
  $('#tbody').scrollTop = 0;
  if (goLive) setTab('live');
  saveHash();
  invalidate('table', 'map', 'zones');
}
function setTab(tab) {
  S.tab = tab;
  $$('.tab').forEach(b => b.setAttribute('aria-selected', b.dataset.tab === tab));
  $('#view-live').hidden = tab !== 'live';
  $('#view-analytics').hidden = tab !== 'analytics';
  if (tab === 'analytics') loadRoute();
  saveHash();
  invalidate('all');
}
let hashTimer = 0;
function saveHash() {
  clearTimeout(hashTimer);
  hashTimer = setTimeout(() => {
    const p = new URLSearchParams();
    if (S.tab !== 'live') p.set('tab', S.tab);
    if (S.f.zone) p.set('zone', S.f.zone);
    if (S.f.status !== 'all') p.set('status', S.f.status);
    if (S.f.minS) p.set('min', S.f.minS);
    if (S.sort.col !== 'last_seen' || S.sort.dir !== -1) p.set('sort', (S.sort.dir < 0 ? '-' : '') + S.sort.col);
    if (S.routeHours !== 24) p.set('range', S.routeHours);
    if (S.selected) p.set('tag', S.selected);
    if (S.replay) p.set('at', S.replay.at);
    const h = p.toString();
    history.replaceState(null, '', h ? '#' + h : location.pathname);
  }, 150);
}
function readHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  S.f = { zone: p.get('zone') || '', status: p.get('status') || 'all', minS: +p.get('min') || 0 };
  const sort = p.get('sort');
  if (sort && SORTERS[sort.replace(/^-/, '')]) S.sort = { col: sort.replace(/^-/, ''), dir: sort.startsWith('-') ? -1 : 1 };
  S.routeHours = +p.get('range') || 24;
  $$('#range button').forEach(b => b.setAttribute('aria-pressed', +b.dataset.v === S.routeHours));
  setFilter({});
  setTab(p.get('tab') === 'analytics' ? 'analytics' : 'live');
  if (p.get('tag')) openDetail(p.get('tag'));
  if (+p.get('at')) showAt(+p.get('at'));
}
function segmented(sel, fn) {
  $(sel).addEventListener('click', e => {
    const b = e.target.closest('button[data-v]');
    if (!b) return;
    $$(sel + ' button').forEach(x => x.setAttribute('aria-pressed', x === b));
    fn(b.dataset.v);
  });
}

// ---------------------------------------------------------------- theme
const THEMES = ['auto', 'light', 'dark'];
function applyTheme(t) {
  if (t === 'auto') delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = t;
  const name = { auto: 'follow system', light: 'White', dark: 'Gray 100' }[t];
  $('#theme').title = 'Theme: ' + name;
  $('#theme').setAttribute('aria-label', 'Theme: ' + name);
  $('#theme').textContent = t === 'auto' ? '◐' : t === 'light' ? '○' : '●';
  cssCache = {};
  invalidate('all');
}

// ---------------------------------------------------------------- wiring
function init() {
  buildHead();
  applyTheme(store.get('theme', 'auto'));
  $$('.tab').forEach(b => b.onclick = () => setTab(b.dataset.tab));
  segmented('#status-filter', v => setFilter({ status: v }));
  $('#zone-filter').onchange = e => setFilter({ zone: e.target.value });
  $('#min-sightings').onchange = e => setFilter({ minS: +e.target.value });
  $('#clear-filters').onclick = () => setFilter({ zone: '', status: 'all', minS: 0 });
  $('#export').onclick = exportCsv;
  $('#zones').onclick = e => { const b = e.target.closest('[data-zone]'); if (b) setFilter({ zone: S.f.zone === b.dataset.zone ? '' : b.dataset.zone }); };
  $('#map').addEventListener('click', e => {
    const z = e.target.closest('[data-zone]'), d = e.target.closest('[data-id]');
    if (d) openDetail(d.dataset.id);
    else if (z) setFilter({ zone: S.f.zone === z.dataset.zone ? '' : z.dataset.zone });
  });
  const body = $('#tbody');
  body.addEventListener('scroll', () => renderTable('scroll'), { passive: true });
  body.addEventListener('click', e => {
    const row = e.target.closest('.vrow');
    if (!row) return;
    if (e.target.closest('.star')) toggleWatch(row.dataset.id); else openDetail(row.dataset.id);
  });
  body.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown' || e.key === 'j') { moveCursor(1); e.preventDefault(); }
    else if (e.key === 'ArrowUp' || e.key === 'k') { moveCursor(-1); e.preventDefault(); }
    else if (e.key === 'PageDown') { moveCursor(Math.floor(body.clientHeight / ROW_H)); e.preventDefault(); }
    else if (e.key === 'PageUp') { moveCursor(-Math.floor(body.clientHeight / ROW_H)); e.preventDefault(); }
    else if (e.key === 'Enter' && view[S.cursor]) openDetail(view[S.cursor].id);
    else if (e.key === 'w' && view[S.cursor]) toggleWatch(view[S.cursor].id);
  });
  segmented('#range', v => { S.routeHours = +v; saveHash(); loadRoute(); invalidate('analytics'); });
  segmented('#activity-mode', v => { S.activityMode = v; invalidate('analytics'); });
  segmented('#d-range', v => { S.detailHours = +v; loadDetail(); });
  $('#d-close').onclick = closeDetail;
  $('#scrim').onclick = closeDetail;
  $('#d-watch').onclick = () => toggleWatch(S.selected);
  $('#d-csv').onclick = detailCsv;
  $('#d-truth-mark').onclick = markTruth;
  const scrub = $('#scrub');
  scrub.addEventListener('input', () => {
    scrubbing = true;
    stopPlay();
    $('#scrub-time').textContent = +scrub.value >= +scrub.max - 10 ? 'Live' : fmtDT(+scrub.value);
    clearTimeout(scrubTimer);
    scrubTimer = setTimeout(() => showAt(+scrub.value), 120);
  });
  scrub.addEventListener('change', () => { scrubbing = false; clearTimeout(scrubTimer); showAt(+scrub.value); });
  $('#scrub-back').onclick = () => { stopPlay(); showAt((S.replay?.at ?? liveNow()) - 300); };
  $('#scrub-fwd').onclick = () => { stopPlay(); if (S.replay) showAt(S.replay.at + 300); };
  $('#scrub-play').onclick = togglePlay;
  $('#scrub-live').onclick = goLive;
  $('#pause').onclick = () => {
    S.paused = !S.paused;
    $('#pause').setAttribute('aria-pressed', S.paused);
    $('#pause').textContent = S.paused ? 'Resume' : 'Pause';
    setLive(S.paused ? 'paused' : 'live');
    if (!S.paused) invalidate('all');
  };
  $('#theme').onclick = () => {
    const t = THEMES[(THEMES.indexOf(store.get('theme', 'auto')) + 1) % THEMES.length];
    store.set('theme', t);
    applyTheme(t);
  };
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { cssCache = {}; invalidate('all'); });
  document.addEventListener('keydown', e => {
    const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement?.tagName);
    if (e.key === 'Escape') { if (S.selected) closeDetail(); else if (typing) document.activeElement.blur(); else if (S.replay) goLive(); }
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { invalidate('all'); if (S.selected) loadDetail(); } });
  new ResizeObserver(() => invalidate('map', 'analytics', 'heat', 'detail', 'tick')).observe($('main'));
  let ticks = 0;
  setInterval(() => {
    ticks++;
    watchStaleCheck();
    invalidate('tick', 'facts');
    if (ticks % 5 === 0) { invalidate('tiles', 'map', 'zones', 'health'); loadHealth(); if (!S.replay) syncScrub(); }
    if (S.tab === 'analytics' && ticks % 30 === 0) loadRoute();
  }, 1000);
  readHash();
  connect();
  loadHealth();
  syncScrub();
}
init();
