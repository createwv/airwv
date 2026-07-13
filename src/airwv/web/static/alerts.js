// Air-quality alert sign-up — populate the sensor picker, then POST the form.
const $ = id => document.getElementById(id);
const started = Date.now();

async function loadSensors() {
  try {
    const sensors = await (await fetch('/api/sensors')).json();
    const sel = $('a-sensor');
    sensors.filter(s => s.kind === 'community')
      .sort((a, b) => a.name.localeCompare(b.name))
      .forEach(s => {
        const o = document.createElement('option');
        o.value = s.sensor_id;
        o.textContent = s.name + (s.region ? ` — ${s.region}` : '');
        sel.appendChild(o);
      });
  } catch (e) { /* leave the "Any sensor" default */ }
}

async function submit(e) {
  e.preventDefault();
  const btn = $('a-submit'), note = $('a-note');
  const email = $('a-email').value.trim();
  if (!email) { note.textContent = 'Please enter your email.'; return; }
  const sensor = $('a-sensor');
  const level = (document.querySelector('input[name="level"]:checked') || {}).value || 'unhealthy';
  const label = sensor.value ? sensor.options[sensor.selectedIndex].textContent : '';
  btn.disabled = true; note.style.color = ''; note.textContent = 'Signing you up…';
  try {
    const r = await fetch('/api/alerts/subscribe', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email, sensor_id: sensor.value || null, level, label,
                            website: $('a-website').value, elapsed_ms: Date.now() - started}),
    });
    const d = await r.json();
    if (!r.ok) { note.style.color = '#b00'; note.textContent = d.detail || 'Something went wrong.'; btn.disabled = false; return; }
    note.style.color = '#137333';
    note.textContent = d.message;
    $('alertform').querySelectorAll('input,select,button').forEach(el => { el.disabled = true; });
  } catch (err) {
    note.style.color = '#b00'; note.textContent = 'Network error — please try again.'; btn.disabled = false;
  }
}

$('alertform').addEventListener('submit', submit);
loadSensors();
