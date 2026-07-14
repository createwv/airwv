// Learn page: tabbed sections (deep-linkable via #hash) + a live "right now" air
// snapshot that ties the AQI scale to today's actual WV readings.
(function () {
  const tabs = [...document.querySelectorAll('.ltab')];
  const panels = [...document.querySelectorAll('.lpanel')];
  function show(name) {
    if (!document.getElementById('lp-' + name)) return;
    tabs.forEach(t => t.classList.toggle('on', t.dataset.tab === name));
    panels.forEach(p => { p.hidden = p.id !== 'lp-' + name; });
  }
  tabs.forEach(t => t.addEventListener('click', () => {
    show(t.dataset.tab);
    history.replaceState(null, '', '#' + t.dataset.tab);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }));
  // in-page links like <a href="#law"> switch tabs
  window.addEventListener('hashchange', () => show(location.hash.slice(1)));
  const h = location.hash.slice(1);
  if (h) show(h);

  // ---- live "right now" snapshot (Health & AQI tab) ----
  const PM_BANDS = [
    {max: 12, label: 'Good', color: '#00e400', dark: '#1a7a1a'},
    {max: 35.4, label: 'Moderate', color: '#ffff00', dark: '#8a7a00'},
    {max: 55.4, label: 'Unhealthy for sensitive groups', color: '#ff7e00', dark: '#b85c00'},
    {max: 150.4, label: 'Unhealthy', color: '#ff0000', dark: '#c00'},
    {max: 250.4, label: 'Very unhealthy', color: '#8f3f97', dark: '#8f3f97'},
    {max: Infinity, label: 'Hazardous', color: '#7e0023', dark: '#7e0023'},
  ];
  const band = v => PM_BANDS.find(b => v <= b.max) || PM_BANDS[PM_BANDS.length - 1];
  const median = a => { const s = [...a].sort((x, y) => x - y); const m = s.length >> 1;
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };

  async function liveNow() {
    const el = document.getElementById('learn-now');
    if (!el) return;
    try {
      const sensors = await (await fetch('/api/sensors')).json();
      const vals = sensors.filter(s => s.kind === 'community' && s.latest_pm2_5 != null)
        .map(s => s.latest_pm2_5);
      if (!vals.length) { el.remove(); return; }
      const typ = median(vals), b = band(typ);
      const good = vals.filter(v => v <= 12).length;
      el.innerHTML = `<div class="lnow-dot" style="background:${b.color}"></div>
        <div><b>Right now across West Virginia:</b> typical community reading is
        <b style="color:${b.dark}">${typ.toFixed(1)} µg/m³ — ${b.label}</b>.
        ${good} of ${vals.length} sensors are in the Good range.
        <a href="/air">See the live map →</a></div>`;
      el.style.borderLeftColor = b.color;
    } catch (e) { document.getElementById('learn-now')?.remove(); }
  }
  liveNow();

  // ---- climate trend from our own archived data (Climate tab) ----
  async function climateTrend() {
    const note = document.getElementById('climate-note'), chart = document.getElementById('climate-chart');
    if (!chart) return;
    try {
      const d = await (await fetch('/api/climate/trend?field=ozone')).json();
      const yrs = (d.years || []).filter(y => y.n >= 24);
      if (yrs.length < 2) {
        chart.style.display = 'none';
        note.innerHTML = "We're still building enough multi-year history to chart a solid local trend — "
          + "our EPA ozone archive is filling in. Check back as it grows, or explore what we have on the "
          + "<a href='/air'>Air map</a>.";
        return;
      }
      const x = yrs.map(y => y.year);
      const mean = yrs.map(y => y.mean), max = yrs.map(y => y.max);
      // simple least-squares fit on the annual means, drawn as a dashed trend line
      const n = x.length, sx = x.reduce((a, b) => a + b, 0), sy = mean.reduce((a, b) => a + b, 0);
      const sxx = x.reduce((a, b) => a + b * b, 0), sxy = x.reduce((a, b, i) => a + b * mean[i], 0);
      const slope = (n * sxy - sx * sy) / (n * sxx - sx * sx), intercept = (sy - slope * sx) / n;
      const fit = x.map(v => slope * v + intercept);
      Plotly.newPlot(chart, [
        { x, y: max, name: 'yearly peak', type: 'scatter', mode: 'lines+markers',
          line: { color: '#e0b050', width: 1 }, marker: { size: 5 } },
        { x, y: mean, name: 'yearly average', type: 'scatter', mode: 'lines+markers',
          line: { color: '#2c7fb8', width: 2 }, marker: { size: 6 } },
        { x, y: fit, name: 'trend', type: 'scatter', mode: 'lines',
          line: { color: '#c0392b', width: 1.5, dash: 'dash' }, hoverinfo: 'skip' },
      ], {
        margin: { t: 10, r: 10, b: 36, l: 46 }, height: 300,
        legend: { orientation: 'h', y: 1.12 },
        xaxis: { dtick: 1, tickformat: 'd' },
        yaxis: { title: `ozone (${d.unit || 'ppb'})`, rangemode: 'tozero' },
      }, { displayModeBar: false, responsive: true });
      const dir = slope > 0.05 ? 'rising' : slope < -0.05 ? 'falling' : 'roughly flat';
      note.innerHTML = `WV reference-monitor ozone, ${x[0]}–${x[x.length - 1]} — the annual average is `
        + `<b>${dir}</b> (${slope >= 0 ? '+' : ''}${slope.toFixed(2)} ${d.unit || 'ppb'}/yr over this span). `
        + `From EPA AirNow/AirData monitors in our archive; year-to-year weather makes any single year noisy.`;
    } catch (e) {
      chart.style.display = 'none';
      if (note) note.textContent = 'Could not load the local climate trend right now.';
    }
  }
  climateTrend();
})();
