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
        <a href="/analysis">See the live map →</a></div>`;
      el.style.borderLeftColor = b.color;
    } catch (e) { document.getElementById('learn-now')?.remove(); }
  }
  liveNow();
})();
