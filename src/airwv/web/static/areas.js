// "How's your area doing?" — per-region rollup + trend, driven by the main metric
// picker (#field). Self-contained (IIFE) so it doesn't clash with app.js globals.
(function () {
  const el = id => document.getElementById(id);
  const AREA_FIELDS = new Set(['pm2_5', 'pm1_0', 'pm10', 'voc', 'temperature', 'humidity']);
  const LABEL = {pm2_5: 'PM2.5', pm1_0: 'PM1.0', pm10: 'PM10', voc: 'VOC',
                 temperature: 'Temp', humidity: 'Humidity'};
  const UNIT = {pm2_5: ' µg/m³', pm1_0: ' µg/m³', pm10: ' µg/m³', voc: '', temperature: '°', humidity: '%'};
  let openRegion = null;

  // Rising is bad for pollutants (red), good to fall (green); temp/humidity are neutral.
  function trendCell(t, field) {
    const pollutant = field.startsWith('pm') || field === 'voc';
    if (t.direction === 'insufficient') return '<span class="meta">—</span>';
    if (t.direction === 'flat') return '<span class="meta">→ steady</span>';
    const up = t.direction === 'rising';
    const arrow = up ? '↑' : '↓';
    const color = !pollutant ? '#667' : (up ? '#c0392b' : '#137333');
    const pct = t.pct_change == null ? '' : ` ${t.pct_change > 0 ? '+' : ''}${t.pct_change}%`;
    return `<span style="color:${color};font-weight:600">${arrow}${pct}</span>`
      + (t.watch ? ' <span title="worsening" style="color:#c0392b">⚠</span>' : '');
  }

  async function showChart(region, field) {
    const box = el('area-chart'), note = el('area-chartnote');
    box.style.display = 'block'; box.innerHTML = 'Loading…';
    try {
      const d = await (await fetch(`/api/areas/series?region=${encodeURIComponent(region)}&field=${field}`)).json();
      const pts = d.points || [];
      if (!pts.length) { box.innerHTML = ''; box.style.display = 'none'; note.textContent = `No history yet for ${region} — trends fill in as the collector runs.`; return; }
      Plotly.newPlot(box, [{x: pts.map(p => p.date), y: pts.map(p => p.value),
        mode: 'lines', line: {color: '#3b2a6b'}, name: LABEL[field]}],
        {margin: {t: 10, r: 10, b: 40, l: 48}, height: 260,
         yaxis: {title: LABEL[field] + UNIT[field]},
         title: {text: `${region} — daily median ${LABEL[field]}`, font: {size: 13}, x: 0}},
        {displayModeBar: false, responsive: true});
      const tr = d.trend || {};
      note.textContent = tr.direction && tr.direction !== 'insufficient'
        ? `Trend over ${tr.n_days} days: ${tr.direction}${tr.pct_change != null ? ` (${tr.pct_change}%)` : ''}. Median across ${d.sensor_count} area sensor(s).`
        : `Median across ${d.sensor_count} area sensor(s); not enough history for a trend yet.`;
    } catch (e) { box.innerHTML = ''; box.style.display = 'none'; note.textContent = 'Could not load the area chart.'; }
  }

  async function render(field) {
    const body = el('areasbody'), note = el('areas-note');
    if (!AREA_FIELDS.has(field)) {
      note.textContent = 'Per-area rollups cover community pollutants — ozone is reference-only.';
      body.innerHTML = '<tr><td colspan="5" style="color:#999">Pick PM, VOC, temperature, or humidity to see areas.</td></tr>';
      el('area-chart').style.display = 'none'; el('area-chartnote').textContent = '';
      return;
    }
    note.textContent = 'community sensors grouped by region · worst air first';
    body.innerHTML = '<tr><td colspan="5" style="color:#999">Loading…</td></tr>';
    try {
      const d = await (await fetch(`/api/areas?field=${field}`)).json();
      const rows = (d.areas || []).map(a => {
        const now = a.value == null
          ? '<span class="meta">no data</span>'
          : `<b style="color:${a.color}">●</b> ${a.value}${UNIT[field]}`;
        const worst = a.max_sensor
          ? `${a.max_sensor} <span class="meta">(${a.max_value}${UNIT[field]})</span>` : '';
        return `<tr class="arow" data-region="${encodeURIComponent(a.region)}" style="cursor:pointer">
          <td><b>${a.region}</b></td><td>${now}</td>
          <td class="meta">${a.reporting}/${a.sensor_count}</td>
          <td>${trendCell(a.trend, field)}</td><td class="meta">${worst}</td></tr>`;
      }).join('');
      body.innerHTML = rows || '<tr><td colspan="5" style="color:#999">No community sensors resolved yet.</td></tr>';
      body.querySelectorAll('.arow').forEach(tr => tr.addEventListener('click', () => {
        const region = decodeURIComponent(tr.dataset.region);
        openRegion = region;
        body.querySelectorAll('.arow').forEach(x => x.style.background = '');
        tr.style.background = '#f2effa';
        showChart(region, field);
      }));
      if (openRegion) {
        const keep = body.querySelector(`.arow[data-region="${encodeURIComponent(openRegion)}"]`);
        if (keep) { keep.style.background = '#f2effa'; showChart(openRegion, field); }
      }
    } catch (e) {
      body.innerHTML = '<tr><td colspan="5" style="color:#999">Could not load areas.</td></tr>';
    }
  }

  const picker = el('field');
  const current = () => (picker && picker.value) || 'pm2_5';
  if (picker) picker.addEventListener('change', () => render(current()));
  render(current());
})();
