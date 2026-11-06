// Shared map + chart helpers (loaded on every page from base.html, after Leaflet).
// Pure functions where possible so tests/test_wv_js.py can run them under node.
(function (root) {
  // West Virginia's bounding box (state line, no margin). Every map opens here on first
  // paint, never on its data: a few border sensors used to pull Home and Air out to five states.
  const WV_BBOX = [[37.20, -82.65], [40.64, -77.72]];

  function fitWV(map, opts) {
    return map.fitBounds(WV_BBOX, Object.assign({padding: [8, 8]}, opts || {}));
  }

  function haversineMi(la1, lo1, la2, lo2) {
    const r = Math.PI / 180, dLa = (la2 - la1) * r, dLo = (lo2 - lo1) * r;
    const a = Math.sin(dLa / 2) ** 2 + Math.cos(la1 * r) * Math.cos(la2 * r) * Math.sin(dLo / 2) ** 2;
    return 3958.8 * 2 * Math.asin(Math.sqrt(a));
  }

  // Which community sensor /air charts first, and why (shown to the visitor):
  // nearest to `at` ({lat, lon, label}) if given, else the highest current PM2.5 among
  // sensors reporting in the last 3 hours, else the first community sensor with a location.
  function pickDefaultSensor(sensors, at) {
    const comm = (sensors || []).filter(s => s.kind === 'community' && s.lat != null && s.lon != null);
    if (!comm.length) return null;
    if (at && at.lat != null && at.lon != null) {
      let best = null, bestMi = Infinity;
      comm.forEach(s => { const d = haversineMi(at.lat, at.lon, s.lat, s.lon); if (d < bestMi) { best = s; bestMi = d; } });
      return {sensor: best, why: `closest to ${at.label || 'you'} (${bestMi.toFixed(1)} mi)`};
    }
    const newest = comm.map(s => s.last_ts).filter(Boolean).sort().pop();
    const cutoff = newest ? Date.parse(newest) - 3 * 3600e3 : null;
    const live = comm.filter(s => s.latest_pm2_5 != null && s.last_ts && Date.parse(s.last_ts) >= cutoff);
    if (live.length) {
      const worst = live.reduce((a, b) => (b.latest_pm2_5 > a.latest_pm2_5 ? b : a));
      return {sensor: worst, why: `highest PM2.5 reading right now among community sensors `
        + `(${worst.latest_pm2_5.toFixed(1)} µg/m³)`};
    }
    return {sensor: comm[0], why: 'no sensor has a current reading, so the first one in the list'};
  }

  // Y-axis clamp for time series: one huge spike (e.g. 1,800 µg/m³) flattens weeks of data.
  // If the top value is more than 3x the 99th percentile, cap the axis at 1.5x that
  // percentile and report the points above it so the chart can mark them. Else null.
  function clampY(values) {
    const v = (values || []).filter(x => x != null && isFinite(x)).sort((a, b) => a - b);
    if (v.length < 20) return null;
    const p99 = v[Math.min(v.length - 1, Math.floor(v.length * 0.99))];
    const top = v[v.length - 1];
    if (p99 <= 0 || top <= 3 * p99) return null;
    const cap = Math.ceil(p99 * 1.5);
    return {cap, above: v.filter(x => x > cap).length, max: top};
  }

  // A status color made readable as text on white: the same hue, darkened in steps until it
  // reaches WCAG AA contrast (4.5:1). Swatches and borders keep the bright color; numbers
  // and words drawn in it use this. '#rrggbb' in, '#rrggbb' out.
  function ink(hex, min) {
    const lum = c => { const v = c / 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
    if (!/^#[0-9a-f]{6}$/i.test(hex || '')) return hex;
    const rgb = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16));
    for (let k = 1; k > 0; k -= 0.04) {
      const c = rgb.map(v => Math.round(v * k));
      const l = 0.2126 * lum(c[0]) + 0.7152 * lum(c[1]) + 0.0722 * lum(c[2]);
      if (1.05 / (l + 0.05) >= (min || 4.5))
        return k === 1 ? hex : '#' + c.map(v => v.toString(16).padStart(2, '0')).join('');
    }
    return '#000000';
  }

  // A `.modal` overlay as a dialog: open() moves focus to its close button, close() (the × ,
  // a click on the backdrop, or Escape) hands focus back to whatever opened it.
  function dialog(el, closeBtn) {
    let opener = null;
    const close = () => {
      if (!el.classList.contains('on')) return;
      el.classList.remove('on');
      if (opener && opener.isConnected) opener.focus();
      opener = null;
    };
    const open = () => {
      if (!el.classList.contains('on')) opener = document.activeElement;
      el.classList.add('on');
      closeBtn.focus();
    };
    closeBtn.addEventListener('click', close);
    el.addEventListener('click', e => { if (e.target === el) close(); });
    document.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });
    return {open, close};
  }

  // 'smooth' for scrollIntoView/scrollTo, unless the visitor asked for reduced motion
  // (CSS scroll-behavior doesn't override a script's explicit 'smooth').
  function scrollBehavior() {
    const m = root.matchMedia && root.matchMedia('(prefers-reduced-motion: reduce)');
    return m && m.matches ? 'auto' : 'smooth';
  }

  const api = {WV_BBOX, fitWV, haversineMi, pickDefaultSensor, clampY, ink, dialog, scrollBehavior};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.AIRWV_WV = api;
})(this);
