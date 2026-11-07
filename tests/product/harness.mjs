// Product harness driver: acts as a user against a running AirWV server, headless Chromium.
// Usage: node tests/product/harness.mjs <config.json>   → prints one JSON result to stdout.
// Config: {base, out, pages:[{name, path, viewports?, openMore?}], viewports:[{w, h}],
//          vendorDirs:[...], geo:{lat, lon}}. A page with its own viewports ({w, h, device, scale,
//          mobile, touch}, from devices.json) is visited at those instead of the shared list; one
//          with openMore taps the nav's "More" menu open before the screenshots (see moreProbe).
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

// In-page "More" menu probe, run once the menu is open: where the dropdown sits, and which of
// its links a user can't get to. A link is cut off if, scrolled into view, it still isn't
// wholly on screen (sideways overflow, a clipping parent) or something else covers its middle.
// `belowFold` lists links past the first screen before any scrolling (info, not a failure).
function moreProbe() {
  const det = document.querySelector("nav.modenav details.more");
  const menu = det && det.querySelector(".more-menu");
  if (!det || !menu) return { error: "no nav.modenav details.more .more-menu" };
  const vh = window.innerHeight, vw = window.innerWidth;
  const box = r => ({ top: Math.round(r.top), left: Math.round(r.left),
                      right: Math.round(r.right), bottom: Math.round(r.bottom) });
  const rect = box(menu.getBoundingClientRect());
  const links = [...menu.querySelectorAll("a")];
  const belowFold = links.filter(a => a.getBoundingClientRect().bottom > vh)
    .map(a => a.textContent.trim());
  const cut = [];
  for (const a of links) {
    a.scrollIntoView({ block: "nearest", inline: "nearest" });
    const r = a.getBoundingClientRect(), label = a.textContent.trim();
    if (!r.width || !r.height) { cut.push(`${label} (no size)`); continue; }
    if (r.left < 0 || r.right > vw || r.top < 0 || r.bottom > vh) {
      cut.push(`${label} (${JSON.stringify(box(r))} in ${vw}x${vh})`); continue; }
    const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    if (!hit || !(a === hit || a.contains(hit)))
      cut.push(`${label} (covered by ${hit ? hit.tagName.toLowerCase()
        + (hit.classList[0] ? "." + hit.classList[0] : "") : "nothing"})`);
  }
  window.scrollTo(0, 0);
  return { open: det.open, rect, links: links.length, belowFold, cut,
           scrollWidth: document.documentElement.scrollWidth, innerWidth: vw, innerHeight: vh };
}

// In-page accessibility probe (WCAG 2.1 AA, the checks a script can make reliably):
//  contrast  visible text below 4.5:1 (3:1 for large text) against its painted background;
//            text over an image/gradient background is skipped (can't be computed)
//  noAlt     <img> with no alt attribute (alt="" is fine: decorative)
//  noName    buttons, links, form fields, summaries and role=button/tab/… with no accessible name
//  clickable elements styled as clickable (cursor:pointer) that a keyboard can't reach
//  motion    animations still running infinitely with prefers-reduced-motion: reduce
// Leaflet's own map internals are left out (tiles, panes); its zoom/attribution controls aren't.
function a11yProbe() {
  const vis = el => { const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== "hidden" && +cs.opacity > 0
      && !el.closest("[hidden], [aria-hidden=true], .hp"); };
  const desc = el => {
    const t = (el.getAttribute("aria-label") || el.textContent || el.getAttribute("title")
               || el.getAttribute("src") || "").trim().replace(/\s+/g, " ").slice(0, 50);
    return `${el.tagName.toLowerCase()}${el.id ? "#" + el.id : ""}${el.classList[0]
      ? "." + el.classList[0] : ""} "${t}"`; };
  const rgba = s => { const m = s.match(/[\d.]+/g) || []; return m.length < 3 ? [0, 0, 0, 0]
    : [+m[0], +m[1], +m[2], m[3] === undefined ? 1 : +m[3]]; };
  const lum = ([r, g, b]) => { const c = v => (v /= 255) <= 0.03928 ? v / 12.92
    : ((v + 0.055) / 1.055) ** 2.4; return 0.2126 * c(r) + 0.7152 * c(g) + 0.0722 * c(b); };
  const over = (top, bot) => { const a = top[3];
    return [0, 1, 2].map(i => top[i] * a + bot[i] * (1 - a)).concat(1); };
  // background actually painted under el: blend translucent layers down to an opaque one
  const bgOf = el => { const layers = [];
    for (let e = el; e; e = e.parentElement) { const cs = getComputedStyle(e);
      if (cs.backgroundImage !== "none") return null;
      const c = rgba(cs.backgroundColor); if (c[3] > 0) layers.push(c);
      if (c[3] >= 1) break; }
    return layers.reverse().reduce((acc, c) => over(c, acc), [255, 255, 255, 1]); };

  const contrast = [], seen = new Set();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const el = n.parentElement;
    // disabled controls are exempt from contrast (WCAG 1.4.3)
    if (!el || seen.has(el) || !n.textContent.trim()
        || el.closest(".leaflet-pane, script, style, :disabled"))
      continue;
    seen.add(el);
    if (!vis(el)) continue;
    const cs = getComputedStyle(el), bg = bgOf(el);
    if (!bg) continue;
    let op = 1; for (let e = el; e; e = e.parentElement) op *= +getComputedStyle(e).opacity;
    const fg = over([...rgba(cs.color).slice(0, 3), rgba(cs.color)[3] * op], bg);
    const [a, b] = [lum(fg), lum(bg)].sort((x, y) => y - x);
    const ratio = (a + 0.05) / (b + 0.05), px = parseFloat(cs.fontSize);
    const large = px >= 24 || (px >= 18.66 && +cs.fontWeight >= 700);
    // symbol-only text (●, ☆, ×) is a graphic: 3:1 inside a control, and a legend swatch
    // outside one (its color has to match the map marker; the label next to it carries it)
    const glyph = !/[\p{L}\p{N}]/u.test(el.textContent);
    if (glyph && !el.closest("button, a[href], summary, [role], [tabindex]")) continue;
    if (ratio < (large || glyph ? 3 : 4.5))
      contrast.push(`${desc(el)} ${ratio.toFixed(2)}:1 (${cs.color} on rgb(${bg.slice(0, 3)
        .map(Math.round)}), ${px}px)`);
  }
  const imgs = [...document.querySelectorAll("img:not([alt])")].filter(vis).map(desc);
  const NAMED = "button, a[href], input:not([type=hidden]), select, textarea, summary, "
    + "[role=button], [role=tab], [role=switch], [role=checkbox], [role=link], [role=menuitem]";
  const name = el => { if ((el.getAttribute("aria-label") || el.getAttribute("title") || "").trim())
      return true;
    const lb = el.getAttribute("aria-labelledby");
    if (lb && lb.split(/\s+/).some(id => document.getElementById(id)?.textContent.trim())) return true;
    if (el.labels && [...el.labels].some(l => l.textContent.trim())) return true;
    if (["submit", "button", "reset"].includes(el.type) && el.value) return true;
    if (el.placeholder && el.matches("input, textarea")) return true;  // weak, but announced
    if (el.textContent.trim()) return true;
    return [...el.querySelectorAll("img[alt]")].some(i => i.alt.trim()); };
  const noName = [...document.querySelectorAll(NAMED)]
    .filter(el => vis(el) && !el.closest(".leaflet-pane") && !name(el)).map(desc);
  const FOCUSABLE = "a[href], button, input, select, textarea, summary, label, [tabindex], "
    + "[contenteditable], .leaflet-container";
  const clickable = [...document.querySelectorAll("body *")].filter(el =>
    getComputedStyle(el).cursor === "pointer" && vis(el) && !el.closest(FOCUSABLE)
    && !el.closest(".leaflet-pane") && !(el.parentElement
      && getComputedStyle(el.parentElement).cursor === "pointer")).map(desc);
  const motion = document.getAnimations().filter(an => an.playState === "running"
    && an.effect?.getTiming().iterations === Infinity)
    .map(an => desc(an.effect.target || document.body) + ` ${an.animationName || "animation"}`);
  const cap = a => a.slice(0, 25);
  return { contrast: cap(contrast), noAlt: cap(imgs), noName: cap(noName),
           clickable: cap(clickable), motion: cap(motion) };
}

// Keyboard: Tab through the first stops and record any that show no focus indicator
// (neither an outline nor a box-shadow while focused).
async function focusProbe(page, stops = 15) {
  const missing = [];
  for (let i = 0; i < stops; i++) {
    await page.keyboard.press("Tab");
    const r =await page.evaluate(() => {
      const el = document.activeElement;
      if (!el || el === document.body) return null;
      const f = getComputedStyle(el);
      const shown = (f.outlineStyle !== "none" && parseFloat(f.outlineWidth) > 0) || f.boxShadow !== "none";
      const t = (el.getAttribute("aria-label") || el.textContent || "").trim().replace(/\s+/g, " ").slice(0, 40);
      return { shown, el: `${el.tagName.toLowerCase()}${el.id ? "#" + el.id : ""} "${t}" `
        + `(outline ${f.outlineStyle} ${f.outlineWidth}, focus-visible ${el.matches(":focus-visible")})` };
    });
    if (!r) break;
    if (!r.shown) missing.push(r.el);
  }
  return missing;
}

const vendorFile = name => cfg.vendorDirs.map(d => join(d, name)).find(existsSync);

// Sandboxed shells (agent runs) deny Chromium's Mach-port rendezvous; one process avoids it,
// but a single-process browser doesn't survive many pages, so each visit gets a fresh one.
const launch = () => chromium.launch().catch(() =>
  chromium.launch({ args: ["--single-process", "--no-zygote", "--no-sandbox"] }));

async function visit(vp, pg) {
  const browser = await launch();
  const ctx = await browser.newContext({
    viewport: { width: vp.w, height: vp.h }, deviceScaleFactor: vp.scale || 1,
    isMobile: vp.mobile ?? vp.w < 600, hasTouch: vp.touch ?? vp.w < 600,
    geolocation: { latitude: cfg.geo.lat, longitude: cfg.geo.lon }, permissions: ["geolocation"],
    reducedMotion: "reduce",   // the a11y probe checks the page honors it (screenshots hold still too)
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
    if (pg.openMore) {
      await page.click("nav.modenav details.more > summary");
      await page.waitForTimeout(200);
      info.more = await page.evaluate(moreProbe);
    }
  } catch (e) { err = String(e.message || e).split("\n")[0]; }
  const ms = Date.now() - t0;
  const shot = join(cfg.out, `${pg.name}-${vp.w}.png`);
  await page.screenshot({ path: shot }).catch(() => {});   // viewport = above the fold
  await page.screenshot({ path: shot.replace(/\.png$/, "-full.png"), fullPage: true })
    .catch(() => {});
  // after the screenshots: tabbing scrolls and moves focus
  let a11y = null;
  if (!err) try {
    a11y = await page.evaluate(a11yProbe);
    await page.evaluate(() => window.scrollTo(0, 0));
    a11y.noFocus = await focusProbe(page);
  } catch (e) { a11y = { error: String(e.message || e).split("\n")[0] }; }
  await browser.close().catch(() => {});
  return { page: pg.name, path: pg.path, width: vp.w, height: vp.h, device: vp.device, status, error: err, ms,
           consoleErrors, pageErrors, failed, shot, shimmed: [...shimmed], a11y, ...info };
}

mkdirSync(cfg.out, { recursive: true });
const results = [];
for (const vp of cfg.viewports) for (const pg of cfg.pages.filter(p => !p.viewports))
  results.push(await visit(vp, pg));
for (const pg of cfg.pages.filter(p => p.viewports))
  for (const vp of pg.viewports) results.push(await visit(vp, pg));
process.stdout.write(JSON.stringify(results));
