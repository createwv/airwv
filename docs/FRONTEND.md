# Frontend / UX Architecture — Plan

The dashboard has outgrown "one long scroll." This plans the restructure into
**modes**, a **layered map-control hierarchy**, better **labeling**, and a
**framework decision** — so we build the next phase deliberately.

## Current stack (for the record)
- **FastAPI** backend (`src/airwv/web/app.py`) — read-only JSON API + serves the page.
- **One HTML page** (a Python string, `INDEX_HTML`) with **vanilla JS**, **Plotly**
  (charts), **Leaflet** (map), via CDN. **No build step, no framework.** Not Django.
- Virtue to preserve: zero toolchain — contributors edit HTML/JS directly.

## Modes (top-level navigation)

Move from one scroll to **three modes**, switched by a top nav. Same app, same API.

### 1. Overview ("opener" — the public landing)
For anyone arriving cold. Fast, friendly, low-density.
- Hero (branding).
- **At a glance:** current statewide air snapshot; any **active alerts / notable
  flags** surfaced simply ("Elevated PM2.5 near Institute this morning").
- **Simplified map** — current conditions, not all the controls.
- **Calls to action:** ⭐ *Sign up for alerts* · 📣 *Report a concern* · *Learn more*.
- **Recent updates** feed (notable events, project news).
- Footer: *Feedback / report a problem*, About, GitHub.

### 2. Analysis / Tracking (the power dashboard)
Essentially today's dashboard, reorganized. For engaged residents & researchers.
- Layered map (hierarchy below), the **layers/sensor tree**, date-range,
  time series + events + trend, diurnal, day/night compare, **validation panel**
  (raw/EPA-corrected), export.
- Full controls; density is fine here.

### 3. Admin (token-gated, maintainers)
Panels, not a scroll. See [`COMMUNITY-REPORTING.md`](COMMUNITY-REPORTING.md).
- **Reports queue** (verify / enrich / approve-field / merge / remove / respond).
- **Feedback queue** (bug / idea / question).
- **Air-quality flagging** — sensor health, suspect readings, A/B divergence,
  malfunction candidates (e.g. 196533).
- **Trends & analytics** — network trends, baselines, per-area rollups.
- (later) sensor/source management.

## Map layers — hierarchical, collapsible control

Replace the flat checkboxes with a **layers tree** (closed triangles ▸ by default,
parent + child checkboxes, tri-state parent = checked / unchecked / indeterminate).
Toggle a whole group at the parent, or individuals underneath.

```
▸ ☑ ⭐ My Sensors                (user's followed sensors — persists locally)
    ☑ EWV Glasgow 1
▸ ☑ Community Sensors           (parent toggles all)
  ▸ ☑ Kanawha Valley            (sub-grouped BY REGION, not org)
      ☑ EWV Nitro 1
      ☑ EWV Glasgow 1
  ▸ ☑ Ohio Valley
  ▸ ☑ Eastern Panhandle …
▸ ☑ EPA / Reference Monitors
    ☑ EPA #24589  (or real AirNow site name)
    ☑ EPA #1833
▸ ☑ Pollution Sources          (category sub-toggles — see below)
    ☑ Power plants
    ☑ Chemical
    ☑ Oil & gas
    ☑ TRI-listed / other
▸ ☑ Community Reports           (by domain, once built)
    ☑ Air ☑ Water ☑ Soil ☑ Wildlife …
```

- **Community Sensors / EPA Monitors** are the two sensor parents the user asked
  for — checkable at parent or leaf level, collapsed by default. Community sensors
  **sub-group by region** (Kanawha Valley, Ohio Valley, Eastern Panhandle, North
  Central, …), derived from county → WV region (not by org).
- **⭐ My Sensors** — users can follow sensors they care about; the set persists
  (localStorage for anonymous now; account-based later) and surfaces as a pinned
  group at the top of the tree.
- **Pollution Sources get categories** so you can, e.g., turn *all power plants*
  off. Needs a `category` field on sources (derive from EPA/EIA type: power,
  chemical, oil-gas, TRI-other; later rail/highway as their own groups).

## Labeling

- **Name the EPA monitors.** Today they render as "monitor #24589". Store the
  monitor's real **AirNow site name** (the `airnow` ingest already accumulates these in
  `data/airnow_monitors.json`) and display that; **fall back to `EPA #<id>`**. Same for the
  static EPA AirData layer (those already have site names + county).
- Community sensors keep their friendly EWV names.
- Sources show name + type + `state` (already done) + soon `category`.

## Default time window

On first load, **state the window explicitly** instead of leaving it implicit.
Compute the **actual first and last data point** across the current selection and
show, e.g.:

> *Showing all data · first point 2024-02-23 → last point 2026-07-09*

- Default = all time (what it effectively is now), but *labeled*, with real
  min/max timestamps, updating when the selection or date range changes.
- Cheap: a `GET /api/coverage` (or fold min/max into `/api/sensors`) returning
  first/last ts per sensor and overall.

## Framework decision (do this before the restructure build)

We're at the fork. Options, with the no-build virtue weighed heavily:

| Option | What | Pros | Cons |
|---|---|---|---|
| **A. No-build + mode routing** | Split the JS into per-view modules; hash routes (`#/overview`, `#/analysis`, `#/admin`); show/hide. Optionally add **Alpine.js**/**htmx** (single CDN tag) for reactivity. | Zero toolchain kept; fastest path; OSS-friendly | One growing bundle; manual state; admin panels get awkward at scale |
| **B. Jinja2 templates, still no JS build** | Break `INDEX_HTML` into **Jinja2** partials; FastAPI serves `/`, `/analysis`, `/admin` as focused pages sharing a shell; vanilla/Alpine per page. | Clean separation; overview is SEO-friendly; no JS toolchain; each page simple | Full reload between modes; a little shared-shell duplication |
| **C. SPA framework** | **Svelte + Vite** (or Vue/Preact); components; build step. | Scales best; real components/state for admin | Adds a toolchain + CI build; higher contributor bar |

**Recommendation:** **B now, with A's touches** — move the HTML into **Jinja2**
templates, serve the three modes as pages, keep interactivity in small vanilla/
**Alpine** modules (no build). It gives real structure for the admin panels while
preserving the zero-toolchain property. Reserve **C (Svelte/Vite)** for when the
admin console's interactivity clearly outgrows that — a deliberate later call, not
now. **This is a decision to lock before the restructure build begins.** *(Locked:
Jinja2 + Alpine.)*

### When to graduate to Svelte/Vite (and only for `/admin`)
The public Overview/Analysis pages stay no-build indefinitely — Plotly/Leaflet +
Alpine handle them. It's the **Admin console** that eventually justifies a SPA.
Introduce Svelte/Vite (scoped to `/admin`) once ~2–3 of these are true:
- **Shared live state across panels** — queues (reports/feedback/flagging) updating
  in place, optimistic approve/remove, cross-panel filters kept in sync.
- **Real-time** — websockets/polling driving reactive UI (live counts, new-report pings).
- **Reusable components across modes** where Jinja duplication starts causing bugs.
- **Multi-step stateful forms** — e.g. the reporting wizard outgrowing a simple form.
- **Client routing with preserved state** across many admin views.

Rule of thumb: **stay on Jinja2 + Alpine until `/admin` becomes a stateful
single-page tool**, then adopt Svelte/Vite *for `/admin` only*, leaving the public
pages toolchain-free.

## Suggested build order (after the reporting backend lands)
1. **Labeling + time window** (low-risk, high-value): EPA monitor names, source
   `category` field, `/api/coverage`, "showing … first→last" line.
2. **Layers tree** — hierarchical, collapsible, parent/leaf checkboxes; source
   category toggles. (Still on the current single page.)
3. **Framework decision** locked → **Jinja2 shell + three mode pages.**
4. **Overview mode** (public landing: at-a-glance, alerts signup, report CTA, feed).
5. **Analysis mode** = today's dashboard, moved into its page.
6. **Admin mode** panels (reports/feedback/flagging/trends) — ties to the reporting
   + moderation console.

## Open decisions
- Framework: **Jinja2 + Alpine (locked)**; Svelte/Vite for `/admin` only, later.
- Community-sensor sub-grouping: **by region** (locked) — need a county→WV-region map.
- Overview "notable flags" source: reuse `events` / `baseline` / trend outputs.
