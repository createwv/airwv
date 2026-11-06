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

  const api = {WV_BBOX, fitWV, haversineMi, pickDefaultSensor, clampY};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.AIRWV_WV = api;
})(this);
