// Ambient haze (experiment): tint the whole site to reflect West Virginia's air
// *right now*. The worse the typical community PM2.5, the browner/denser the veil —
// awareness you feel, not a number you have to read. Dismissible; off for good air.
(function () {
  const el = document.getElementById('haze');
  const chip = document.getElementById('haze-chip');
  if (!el || !chip) return;
  if (localStorage.getItem('airwv_haze_off') === '1') return;   // user turned it off

  // PM2.5 bands → tint color + strength. "o" is the veil strength (0 = clear).
  const BANDS = [
    { max: 12,    name: 'good',                         o: 0,    c: null },
    { max: 35.4,  name: 'moderate',                     o: 0.06, c: '#b7ab8c' },
    { max: 55.4,  name: 'unhealthy for sensitive groups', o: 0.12, c: '#c39a5f' },
    { max: 150.4, name: 'unhealthy',                    o: 0.18, c: '#b56f3b' },
    { max: 250.4, name: 'very unhealthy',               o: 0.24, c: '#96574d' },
    { max: Infinity, name: 'hazardous',                 o: 0.30, c: '#7f4842' },
  ];
  const band = v => BANDS.find(b => v <= b.max) || BANDS[BANDS.length - 1];
  const median = a => { const s = [...a].sort((x, y) => x - y); const m = s.length >> 1;
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
  const rgba = (hex, a) => { const n = parseInt(hex.slice(1), 16);
    return `rgba(${n >> 16 & 255},${n >> 8 & 255},${n & 255},${a})`; };

  function apply(v) {
    const b = band(v);
    if (!b.c || b.o <= 0) { el.style.opacity = 0; chip.classList.remove('on'); return; }
    // top-anchored gradient (strongest at the "sky"), fading down so cards stay readable
    el.style.background = 'linear-gradient(to bottom, '
      + `${rgba(b.c, Math.min(0.85, b.o * 2.4))} 0%, `
      + `${rgba(b.c, b.o * 1.1)} 20%, `
      + `${rgba(b.c, b.o * 0.5)} 55%, `
      + `${rgba(b.c, b.o * 0.35)} 100%)`;
    el.style.opacity = 1;
    chip.querySelector('.dot').style.background = b.c;
    chip.querySelector('.haze-band').textContent = b.name;
    chip.querySelector('.haze-val').textContent = Math.round(v);
    chip.classList.add('on');
  }

  async function tick() {
    try {
      const sensors = await (await fetch('/api/sensors')).json();
      // reflect *general* conditions: the typical (median) community reading, not one bad sensor
      const vals = sensors.filter(s => s.kind === 'community' && s.latest_pm2_5 != null)
        .map(s => s.latest_pm2_5);
      if (vals.length) apply(median(vals));
    } catch (e) { /* leave the air clear on error */ }
  }

  chip.querySelector('.haze-off').addEventListener('click', () => {
    localStorage.setItem('airwv_haze_off', '1');
    el.style.opacity = 0; chip.classList.remove('on');
  });

  tick();
  setInterval(tick, 10 * 60 * 1000);   // refresh every 10 minutes
})();
