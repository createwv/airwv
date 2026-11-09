// Ambient haze (experiment): tint the whole site to reflect West Virginia's air
// *right now*. The worse the typical community PM2.5, the browner/denser the veil —
// awareness you feel, not a number you have to read. Opt-in (Learn → Health & AQI has the
// switch); dismissible; clear for good air.
(function () {
  const el = document.getElementById('haze');
  const chip = document.getElementById('haze-chip');
  if (!el || !chip) return;
  // Preview/demo override: ?haze=<pm2.5> forces a value so the effect can be seen even
  // when the real air is good (it usually is). Works without opting in. Handy for testing.
  const override = parseFloat(new URLSearchParams(location.search).get('haze'));
  const KEY = 'airwv_haze_on';
  const enabled = () => !isNaN(override) || localStorage.getItem(KEY) === '1';

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

  function clear() { el.style.opacity = 0; chip.classList.remove('on'); }

  async function tick() {
    if (!enabled()) { clear(); return; }
    if (!isNaN(override)) { apply(override); return; }
    try {
      const sensors = await (await fetch('/api/sensors')).json();
      // reflect *general* conditions: the typical (median) community reading, not one bad sensor
      const vals = sensors.filter(s => s.kind === 'community' && s.latest_pm2_5 != null)
        .map(s => s.latest_pm2_5);
      if (vals.length) apply(median(vals));
    } catch (e) { /* leave the air clear on error */ }
  }

  chip.querySelector('.haze-off').addEventListener('click', () => {
    localStorage.removeItem(KEY);
    clear();
  });
  // the Learn page's switch turns it on/off without a reload
  window.AIRWV_HAZE = {
    enabled: () => localStorage.getItem(KEY) === '1',
    set(on) { if (on) localStorage.setItem(KEY, '1'); else localStorage.removeItem(KEY); tick(); },
  };

  tick();
  setInterval(tick, 10 * 60 * 1000);   // refresh every 10 minutes
})();
