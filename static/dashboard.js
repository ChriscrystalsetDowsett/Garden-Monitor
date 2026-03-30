'use strict';

/* ── State ─────────────────────────────────────────────────────────────────── */

let activeIdx     = null;
let recRunning    = false;
let tlRunning     = false;
let camEnabled    = true;
let drawerHasAudio = false;
let pollTimer     = null;
let toastTimer    = null;
// Pull-based stream (iOS Safari compatible — MJPEG is not supported in Safari)
const _TILE_FRAME_MS = Math.round(1000 / 15);   // 15 fps tiles
const _DRAWER_FRAME_MS = Math.round(1000 / 15); // 15 fps drawer
const _tileTimers   = {};   // idx -> timeoutId
let   _drawerTimer  = null;

function _tileFrameUrl(idx) {
  return '/dashboard/cam/' + idx + '/proxy/api/frame?t=' + Date.now();
}
function _drawerFrameUrl() {
  return '/dashboard/cam/' + activeIdx + '/proxy/api/frame?t=' + Date.now();
}

function _startTile(idx) {
  const img = document.querySelector('.cam-tile[data-idx="' + idx + '"] .tile-stream-img');
  if (img) img.src = _tileFrameUrl(idx);
}

function onTileLoad(img) {
  markOnline(img);
  const idx = parseInt(img.closest('.cam-tile').dataset.idx, 10);
  clearTimeout(_tileTimers[idx]);
  _tileTimers[idx] = setTimeout(() => _startTile(idx), _TILE_FRAME_MS);
}

function onTileError(img) {
  markOffline(img);
  const idx = parseInt(img.closest('.cam-tile').dataset.idx, 10);
  clearTimeout(_tileTimers[idx]);
  _tileTimers[idx] = setTimeout(() => _startTile(idx), 4000);
}

function _startDrawerStream() {
  clearTimeout(_drawerTimer);
  if (activeIdx === null) return;
  document.getElementById('drawer-stream-img').src = _drawerFrameUrl();
}

function onDrawerStreamLoad() {
  if (activeIdx === null) return;
  document.getElementById('drawer-offline-msg').classList.remove('show');
  clearTimeout(_drawerTimer);
  _drawerTimer = setTimeout(_startDrawerStream, _DRAWER_FRAME_MS);
}

function onDrawerStreamError() {
  if (activeIdx === null) return;
  document.getElementById('drawer-offline-msg').classList.add('show');
  clearTimeout(_drawerTimer);
  _drawerTimer = setTimeout(_startDrawerStream, 4000);
}

// Timelapse countdown clock state
let tlDashStart    = null;  // Date.now() when TL began (client-interpolated)
let tlDashDuration = 0;     // seconds; 0 = unlimited
let tlDashCount    = 0;     // latest frame count from server
let tlClockTimer   = null;

/* ── Timelapse countdown helpers ─────────────────────────────────────────────── */

function fmtHMS(secs) {
  const s = Math.max(0, Math.round(secs));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return h + ':' + String(m).padStart(2,'0') + ':' + String(sec).padStart(2,'0');
  return String(m).padStart(2,'0') + ':' + String(sec).padStart(2,'0');
}

function _tlDashTick() {
  if (activeIdx === null || !tlRunning || tlDashStart === null) return;
  const elapsed  = (Date.now() - tlDashStart) / 1000;
  const frmEl    = document.getElementById('drawer-tl-frames');
  const barTrack = document.getElementById('drawer-tl-bar-track');
  const fillEl   = document.getElementById('drawer-tl-bar-fill');
  const timeEl   = document.getElementById('drawer-tl-time');
  const subEl    = document.getElementById('drawer-tl-sub');
  if (frmEl) frmEl.textContent = tlDashCount > 0 ? tlDashCount.toLocaleString() + ' frames captured' : '';
  if (tlDashDuration > 0) {
    const rem = Math.max(0, tlDashDuration - elapsed);
    if (timeEl)   timeEl.textContent  = fmtHMS(rem);
    if (subEl)    subEl.textContent   = 'remaining';
    if (barTrack) barTrack.style.display = 'block';
    if (fillEl)   fillEl.style.width  = Math.min(100, elapsed / tlDashDuration * 100) + '%';
    // Update the tile badge with live remaining time
    const badges = document.getElementById('tile-badges-' + activeIdx);
    if (badges) {
      const tlBadge = badges.querySelector('.badge-tl');
      if (tlBadge) tlBadge.textContent = '\u23F1 ' + fmtHMS(rem);
    }
  } else {
    if (timeEl)   timeEl.textContent  = fmtHMS(elapsed);
    if (subEl)    subEl.textContent   = 'elapsed';
    if (barTrack) barTrack.style.display = 'none';
  }
}

/* ── Online / offline indicators ────────────────────────────────────────────── */

function markOnline(img) {
  const tile = img.closest('.cam-tile');
  if (!tile) return;
  tile.classList.remove('offline');
  const dot = document.getElementById('tile-dot-' + tile.dataset.idx);
  if (dot) dot.classList.add('online');
}

function markOffline(img) {
  const tile = img.closest('.cam-tile');
  if (!tile) return;
  tile.classList.add('offline');
  const dot = document.getElementById('tile-dot-' + tile.dataset.idx);
  if (dot) dot.classList.remove('online');
}

/* ── Drawer open / close ─────────────────────────────────────────────────────── */

function openDrawer(tile) {
  activeIdx = parseInt(tile.dataset.idx, 10);

  document.getElementById('drawer-title').textContent    = tile.dataset.name;
  document.getElementById('drawer-full-link').href       = tile.dataset.fullUrl;
  document.getElementById('drawer-full-btn').href        = tile.dataset.fullUrl;
  document.getElementById('drawer-offline-msg').classList.remove('show');
  document.getElementById('drawer-status').textContent   = '';
  document.getElementById('drawer-tl-strip').style.display = 'none';
  tlDashStart = null;

  recRunning = false;
  tlRunning  = false;
  camEnabled = true;
  updateButtons();
  updateCamToggle();
  setActionsDisabled(false);

  document.getElementById('drawer-backdrop').classList.add('visible');
  document.getElementById('drawer').classList.add('open');

  drawerHasAudio = false;
  _startDrawerStream();
  fetchStatus();
  fetchCamInfo();
  pollTimer = setInterval(fetchStatus, 3000);
}

function closeDrawer() {
  clearInterval(pollTimer);
  if (tlClockTimer) { clearInterval(tlClockTimer); tlClockTimer = null; }
  pollTimer    = null;
  activeIdx    = null;
  recRunning   = false;
  tlRunning    = false;
  tlDashStart  = null;

  document.getElementById('drawer').classList.remove('open');
  document.getElementById('drawer-backdrop').classList.remove('visible');

  // Stop the pull-based stream and clear src after slide-out
  clearTimeout(_drawerTimer);
  _drawerTimer = null;
  setTimeout(() => {
    document.getElementById('drawer-stream-img').src = '';
  }, 360);
}

/* ── Status polling ──────────────────────────────────────────────────────────── */

async function fetchStatus() {
  if (activeIdx === null) return;
  try {
    const [recResp, tlResp, camResp] = await Promise.all([
      camFetch(activeIdx, 'api/record/status'),
      camFetch(activeIdx, 'api/timelapse/status'),
      camFetch(activeIdx, 'api/camera/enabled'),
    ]);

    if (!recResp.ok || !tlResp.ok) { setOffline(true); return; }

    const rec = await recResp.json();
    const tl  = await tlResp.json();

    if (rec.error || tl.error) { setOffline(true); return; }

    if (camResp.ok) {
      const camData = await camResp.json();
      camEnabled = camData.enabled !== false;
    }

    setOffline(false);
    recRunning = rec.running || false;
    tlRunning  = tl.running  || false;

    if (tl.running) {
      tlDashCount    = tl.count    || 0;
      tlDashDuration = tl.duration || 0;
      tlDashStart    = Date.now() - ((tl.elapsed || 0) * 1000);
      document.getElementById('drawer-tl-strip').style.display = '';
      if (!tlClockTimer) tlClockTimer = setInterval(_tlDashTick, 1000);
      _tlDashTick();
    } else {
      document.getElementById('drawer-tl-strip').style.display = 'none';
      if (tlClockTimer) { clearInterval(tlClockTimer); tlClockTimer = null; }
    }

    updateButtons();
    updateCamToggle();
    updateTileBadges(activeIdx);
    updateStatusText(rec, tl);
  } catch {
    setOffline(true);
  }
}

async function fetchCamInfo() {
  if (activeIdx === null) return;
  try {
    const resp = await camFetch(activeIdx, 'api/info');
    if (!resp.ok) return;
    const d = await resp.json();
    drawerHasAudio = d.audio_available === true;
    const hint = document.querySelector('#btn-record .btn-hint');
    if (hint) hint.textContent = drawerHasAudio ? 'High quality · audio' : 'High quality';
  } catch (_) {}
}

function setOffline(offline) {
  document.getElementById('drawer-offline-msg').classList.toggle('show', offline);
  setActionsDisabled(offline);
  if (offline) {
    document.getElementById('drawer-status').textContent = 'Camera unreachable';
  }
}

function setActionsDisabled(disabled) {
  ['btn-photo', 'btn-record', 'btn-timelapse', 'btn-cam-toggle'].forEach(id => {
    document.getElementById(id).disabled = disabled;
  });
}

function updateStatusText(rec, tl) {
  const parts = [];
  if (rec.running) {
    const secs = rec.duration ? Math.floor(rec.duration) : 0;
    parts.push(`Recording — ${secs}s, ${rec.frame_count || 0} frames`);
  }
  // TL info shown in the countdown strip — omit from plain-text status
  document.getElementById('drawer-status').textContent = parts.join('  ·  ') || (tl.running ? '' : 'Ready');
}

function updateTileBadges(idx) {
  const el = document.getElementById('tile-badges-' + idx);
  if (!el) return;
  el.innerHTML = '';
  if (!camEnabled) {
    const b = document.createElement('span');
    b.className   = 'status-badge badge-paused';
    b.textContent = '⏸ Paused';
    el.appendChild(b);
    return;
  }
  if (recRunning) {
    const b = document.createElement('span');
    b.className   = 'status-badge badge-rec';
    b.textContent = '● REC';
    el.appendChild(b);
  }
  if (tlRunning) {
    const b = document.createElement('span');
    b.className   = 'status-badge badge-tl';
    b.textContent = '⏱ TL';
    el.appendChild(b);
  }
}

function updateCamToggle() {
  const btn  = document.getElementById('btn-cam-toggle');
  const hint = document.getElementById('cam-enable-hint');
  btn.classList.toggle('off', !camEnabled);
  hint.textContent = camEnabled ? 'Enabled' : 'Paused';
}

async function toggleCameraEnabled() {
  if (activeIdx === null) return;
  const btn = document.getElementById('btn-cam-toggle');
  btn.disabled = true;
  try {
    const resp = await camFetch(activeIdx, 'api/camera/enabled', 'POST', { enabled: !camEnabled });
    const data = await resp.json();
    if (!data.error) {
      camEnabled = data.enabled !== false;
      updateCamToggle();
      updateTileBadges(activeIdx);
      showToast(camEnabled ? '📷 Camera enabled' : '⏸ Camera paused');
    }
  } catch {
    showToast('Failed to toggle camera');
  } finally {
    btn.disabled = false;
  }
}

/* ── Button state ────────────────────────────────────────────────────────────── */

function updateButtons() {
  const recBtn = document.getElementById('btn-record');
  const tlBtn  = document.getElementById('btn-timelapse');

  document.getElementById('rec-label').textContent = recRunning ? 'Stop Rec' : 'Record';
  recBtn.classList.toggle('active', recRunning);

  document.getElementById('tl-label').textContent = tlRunning ? 'Stop TL' : 'Timelapse';
  tlBtn.classList.toggle('active', tlRunning);
}

/* ── Actions ─────────────────────────────────────────────────────────────────── */

async function doPhoto() {
  if (activeIdx === null) return;
  const btn = document.getElementById('btn-photo');
  btn.disabled = true;
  try {
    const resp = await camFetch(activeIdx, 'api/snapshot', 'POST', { quality: 95 });
    const data = await resp.json();
    showToast(data.filename ? `📷 Saved: ${data.filename}` : 'Snapshot failed');
  } catch {
    showToast('Error taking photo');
  } finally {
    btn.disabled = false;
  }
}

async function toggleRecord() {
  if (activeIdx === null) return;
  const btn  = document.getElementById('btn-record');
  btn.disabled = true;
  const path = recRunning ? 'api/record/stop' : 'api/record/start';
  const body = recRunning ? {} : { quality: 18, audio: drawerHasAudio };
  try {
    const resp = await camFetch(activeIdx, path, 'POST', body);
    const data = await resp.json();
    if (data.error) {
      showToast('Camera unreachable');
    } else {
      const wasStarting = !recRunning;
      recRunning = !recRunning;
      updateButtons();
      updateTileBadges(activeIdx);
      if (!recRunning && data.audio_ok === false && body.audio) {
        showToast('⏹ Recording stopped (no audio captured)');
      } else {
        showToast(recRunning ? (drawerHasAudio ? '🔴 Recording started (with audio)' : '🔴 Recording started') : '⏹ Recording stopped');
      }
    }
  } catch {
    showToast('Error controlling recording');
  } finally {
    btn.disabled = false;
  }
}

async function toggleTimelapse() {
  if (activeIdx === null) return;
  const btn  = document.getElementById('btn-timelapse');
  btn.disabled = true;
  const path = tlRunning ? 'api/timelapse/stop' : 'api/timelapse/start';
  const body = tlRunning ? {} : { interval: 10, duration: 1800 };
  try {
    const resp = await camFetch(activeIdx, path, 'POST', body);
    const data = await resp.json();
    if (data.error) {
      showToast('Camera unreachable');
    } else {
      tlRunning = !tlRunning;
      updateButtons();
      updateTileBadges(activeIdx);
      showToast(tlRunning ? '⏱ Timelapse started' : '⏹ Timelapse stopped');
    }
  } catch {
    showToast('Error controlling timelapse');
  } finally {
    btn.disabled = false;
  }
}

/* ── Helpers ─────────────────────────────────────────────────────────────────── */

function camFetch(idx, path, method = 'GET', body = null) {
  const opts = { method };
  if (body !== null) {
    opts.headers = { 'Content-Type': 'application/json' };
    opts.body    = JSON.stringify(body);
  }
  return fetch(`/dashboard/cam/${idx}/proxy/${path}`, opts);
}

function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 3200);
}

/* ── Keyboard ────────────────────────────────────────────────────────────────── */

document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && activeIdx !== null) closeDrawer();
});

/* ── Tile stream init + visibility handling ──────────────────────────────────── */

function _startAllTiles() {
  document.querySelectorAll('.cam-tile').forEach(tile => {
    _startTile(parseInt(tile.dataset.idx, 10));
  });
}

function _stopAllTiles() {
  Object.keys(_tileTimers).forEach(idx => {
    clearTimeout(_tileTimers[idx]);
    delete _tileTimers[idx];
  });
}

// Resume streams when tab/app becomes visible again
document.addEventListener('visibilitychange', function () {
  if (document.visibilityState === 'visible') {
    _startAllTiles();
    if (activeIdx !== null) _startDrawerStream();
  }
});

// iOS web apps fire pageshow when restored from background
window.addEventListener('pageshow', function (e) {
  if (e.persisted) {
    _startAllTiles();
    if (activeIdx !== null) _startDrawerStream();
  }
});

// Kick off tile streams on page load
_startAllTiles();
