/**
 * utils.js — Pure utility functions shared across all modules.
 * @module utils
 */

/** Escape HTML special characters to prevent XSS. */
export function escapeHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/** Format seconds as MM:SS or HH:MM:SS. */
export function fmtDur(s) {
  s = Math.round(s || 0);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}:${m < 10 ? '0' : ''}${m}:${sec < 10 ? '0' : ''}${sec}`;
  return `${m}:${sec < 10 ? '0' : ''}${sec}`;
}

/** WiFi RSSI signal bars icon. */
export function rssiIcon(rssi) {
  if (!rssi || rssi === 0) return '—';
  const abs = Math.abs(rssi);
  if (abs <= 50) return '▂▄▆█ ' + rssi;
  if (abs <= 60) return '▂▄▆░ ' + rssi;
  if (abs <= 70) return '▂▄░░ ' + rssi;
  if (abs <= 80) return '▂░░░ ' + rssi;
  return '░░░░ ' + rssi;
}

/** Compare semantic version strings. Returns -1, 0, or 1. */
export function cmpVer(a, b) {
  const pa = (a || '0').split('.').map(Number);
  const pb = (b || '0').split('.').map(Number);
  for (let i = 0; i < 3; i++) {
    if ((pa[i] || 0) < (pb[i] || 0)) return -1;
    if ((pa[i] || 0) > (pb[i] || 0)) return 1;
  }
  return 0;
}

/** Button saving/saved animation helpers. */
export function btnSaving(btn) {
  if (!btn) return;
  btn._origText = btn.textContent;
  btn.textContent = 'Saving...';
  btn.disabled = true;
}

export function btnSaved(btn, ok) {
  if (!btn) return;
  btn.textContent = ok ? '✓ Saved' : '✗ Failed';
  btn.disabled = false;
  setTimeout(() => { btn.textContent = btn._origText || 'Save'; }, 1500);
}
