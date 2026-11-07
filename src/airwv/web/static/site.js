// /site/<kind>/<ref> for sensors, monitors, gauges and sample sites: the recent-readings
// chart. The page puts the /api/query series-mode URL in #site-chart's data-api (the same
// URL as its CSV and API links); this fetches the rows and plots them. trace() and
// latest() are pure so tests/test_site_js.py can run them under node.
(function (root) {
  function trace(rows, label) {
    return {x: rows.map(r => r.ts), y: rows.map(r => r.value), name: label,
            mode: rows.length > 60 ? 'lines' : 'lines+markers', line: {color: '#2c6a9e'}};
  }
  function latest(rows, label, unit) {
    if (!rows.length) return '';
    const r = rows[rows.length - 1];
    return `Latest: ${label} ${r.value}${unit ? ' ' + unit : ''} at ${String(r.ts).slice(0, 16).replace('T', ' ')} UTC`;
  }

  async function draw(box) {
    const label = box.dataset.label, unit = box.dataset.unit;
    const msg = t => { box.innerHTML = `<p class="meta" style="padding:14px">${t}</p>`; };
    let d;
    try { d = await (await fetch(box.dataset.api)).json(); }
    catch (e) { msg('Could not load the readings.'); return; }
    const rows = d.rows || [];
    if (!rows.length) { msg('No readings in this window.'); return; }
    document.getElementById('site-latest').textContent = latest(rows, label, unit);
    if (typeof Plotly === 'undefined') { msg(`${rows.length} readings; the chart library didn't load. Use the CSV link below.`); return; }
    box.innerHTML = '';
    Plotly.newPlot(box, [trace(rows, label)],
      {margin: {t: 10, r: 10, b: 40, l: 48}, height: 300,
       yaxis: {title: label + (unit ? ` (${unit})` : '')}},
      {displayModeBar: false, responsive: true});
  }

  const api = {trace, latest};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else {
    root.AIRWV_SITE = api;
    const box = document.getElementById('site-chart');
    if (box) draw(box);
  }
})(this);
