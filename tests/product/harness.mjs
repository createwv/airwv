// Product harness driver: acts as a user against a running AirWV server, headless Chromium.
// Usage: node tests/product/harness.mjs <config.json>   → prints one JSON result to stdout.
// Config: {base, out, pages:[{name, path}], viewports:[{w, h}], vendorDirs:[...], geo:{lat, lon}}
// Offline by design: only the local server is reachable. CDN libraries (Leaflet, Plotly…) are
// served from a vendor dir when a copy is there, else replaced by a no-op shim (and reported).
import { createRequire } from "node:module";
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { basename, join } from "node:path";

const cfg = JSON.parse(readFileSync(process.argv[2], "utf8"));
const pwDir = process.env.AIRWV_PLAYWRIGHT_DIR || join(homedir(), ".czadmin/tools");
const { chromium } = createRequire(join(pwDir, "package.json"))("playwright");

// A function-proxy that absorbs any call/property chain, so page code that needs a missing
// library runs to the end instead of throwing (never thenable, so `await` doesn't hang).
const SHIM = `(function(){const h={get:(t,k)=>k==='then'?undefined:k===Symbol.toPrimitive?()=>0:p,
  apply:()=>p,construct:()=>p};const p=new Proxy(function(){},h);return p;})()`;
const SHIMS = { "leaflet.js": `window.L=${SHIM};`, "leaflet.markercluster.js": "",
                "plotly": `window.Plotly=${SHIM};` };

// Record every Leaflet map that pages create, so bounds can be read after load.
const INIT = `(() => { let _L; Object.defineProperty(window, 'L', { configurable: true,
  get: () => _L, set(v) { _L = v; try { v.Map.addInitHook(function () {
    (window.__maps = window.__maps || []).push(this); }); } catch (e) {} } }); })();`;

// In-page probe: nav, maps, and whether a reading (number + unit) is visible above the fold.
// "Visible" = the number's text box is inside the first screen and not covered by anything
// (checked with elementFromPoint). Readings in fixed overlays (e.g. the haze toast) are
// recorded as `overlayReading` but don't count: the page itself has to show one.
function probe() {
  const UNIT = /\d(?:[\d,]*)(?:\.\d+)?\s*(µg\/m³|ug\/m3|ppb|ppm|AQI|mg\/L|µS\/cm|°[FC]|ft³\/s|cfs)/;
  const vh = window.innerHeight, vw = window.innerWidth;
  const fixed = el => { for (let e = el; e; e = e.parentElement)
    if (["fixed", "sticky"].includes(getComputedStyle(e).position)) return true; return false; };
  let reading = null, overlayReading = null;
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n && !reading; n = walker.nextNode()) {
    const m = UNIT.exec(n.textContent), el = n.parentElement;
    if (!m || !el || el.closest("nav, footer, .modal, [hidden]")) continue;
    const range = document.createRange();
    range.setStart(n, m.index); range.setEnd(n, m.index + m[0].length);
    const r = range.getBoundingClientRect(), cs = getComputedStyle(el);
    if (!r.width || r.top < 0 || r.bottom > vh || r.left < 0 || r.right > vw
        || cs.visibility === "hidden" || +cs.opacity === 0) continue;
    const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    if (!hit || !(el === hit || el.contains(hit) || hit.contains(el))) continue;
    const found = { text: el.textContent.trim().replace(/\s+/g, " ").slice(0, 120),
                    value: m[0], top: Math.round(r.top) };
    if (fixed(el)) overlayReading = overlayReading || found; else reading = found;
  }
  const maps = (window.__maps || []).filter(m => m._container && m._container.isConnected)
    .map(m => { const b = m.getBounds(), s = m.getSize();
      return { id: m._container.id, width: s.x, height: s.y, zoom: m.getZoom(),
               south: b.getSouth(), west: b.getWest(), north: b.getNorth(), east: b.getEast() }; });
  return {
    title: document.title,
    // top-level items only: links in the "More" menu are one item
    navCount: document.querySelectorAll("nav.modenav > a, nav.modenav > details > summary").length,
    reading, overlayReading, maps,
    scrollWidth: document.documentElement.scrollWidth, innerWidth: window.innerWidth,
  };
}

const vendorFile = name => cfg.vendorDirs.map(d => join(d, name)).find(existsSync);

// Sandboxed shells (agent runs) deny Chromium's Mach-port rendezvous; one process avoids it,
// but a single-process browser doesn't survive many pages, so each visit gets a fresh one.
const launch = () => chromium.launch().catch(() =>
  chromium.launch({ args: ["--single-process", "--no-zygote", "--no-sandbox"] }));

async function visit(vp, pg) {
  const browser = await launch();
  const ctx = await browser.newContext({
    viewport: { width: vp.w, height: vp.h }, isMobile: vp.w < 600, hasTouch: vp.w < 600,
    geolocation: { latitude: cfg.geo.lat, longitude: cfg.geo.lon }, permissions: ["geolocation"],
  });
  const shimmed = new Set();
  await ctx.route("**/*", async route => {
    const url = route.request().url();
    if (url.startsWith(cfg.base)) return route.continue();
    const name = basename(new URL(url).pathname);
    const local = vendorFile(name);
    if (local) return route.fulfill({ path: local });
    const key = name.startsWith("plotly") ? "plotly" : name;
    if (key in SHIMS) { shimmed.add(name);
      return route.fulfill({ contentType: "application/javascript", body: SHIMS[key] }); }
    if (name.endsWith(".css")) return route.fulfill({ contentType: "text/css", body: "" });
    return route.abort("blockedbyclient");   // map tiles, fonts, anything off-box
  });
  await ctx.addInitScript(INIT);
  const page = await ctx.newPage();
  const consoleErrors = [], pageErrors = [], failed = [];
  page.on("console", m => {
    if (m.type() !== "error") return;
    const loc = m.location().url || "";
    // requests we blocked on purpose (tiles etc.) aren't page bugs
    if (/ERR_BLOCKED_BY_CLIENT/.test(m.text()) || (loc && !loc.startsWith(cfg.base))) return;
    consoleErrors.push(m.text().slice(0, 300));
  });
  page.on("pageerror", e => pageErrors.push(String(e.message || e).slice(0, 300)));
  page.on("response", r => { if (r.url().startsWith(cfg.base) && r.status() >= 400)
    failed.push(`${r.status()} ${r.url().slice(cfg.base.length)}`); });
  const t0 = Date.now();
  let status = 0, err = null, info = {};
  try {
    const resp = await page.goto(cfg.base + pg.path, { waitUntil: "load", timeout: 60000 });
    status = resp ? resp.status() : 0;
    await page.waitForLoadState("networkidle", { timeout: 30000 }).catch(() => {});
    await page.waitForTimeout(300);   // let fitBounds / late renders settle
    info = await page.evaluate(probe);
  } catch (e) { err = String(e.message || e).split("\n")[0]; }
  const ms = Date.now() - t0;
  const shot = join(cfg.out, `${pg.name}-${vp.w}.png`);
  await page.screenshot({ path: shot }).catch(() => {});   // viewport = above the fold
  await page.screenshot({ path: shot.replace(/\.png$/, "-full.png"), fullPage: true })
    .catch(() => {});
  await browser.close().catch(() => {});
  return { page: pg.name, path: pg.path, width: vp.w, height: vp.h, status, error: err, ms,
           consoleErrors, pageErrors, failed, shot, shimmed: [...shimmed], ...info };
}

mkdirSync(cfg.out, { recursive: true });
const results = [];
for (const vp of cfg.viewports) for (const pg of cfg.pages) results.push(await visit(vp, pg));
process.stdout.write(JSON.stringify(results));
