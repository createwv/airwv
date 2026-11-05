# Autopilot plan (airwv)

CZadmin's autopilot works the items Corey tagged `[auto]`, top to bottom, on branch `corey/auto`. Rules and goals:
CZadmin `projects/airwv/CHARTER.md`. Workers tick an item when it's done and never add `[auto]` tags.
Direction: `~/Sites/clients/airwv-direction-review-2026-11-05.md` (outside the repo; approved by Corey 2026-11-05).

## Now
- [ ] Product harness: a pytest + Playwright smoke suite that boots the app on the local SQLite copy, screenshots the key pages at 390/1280, asserts a reading is visible above the fold, nav count, map bounds, and no console errors. Every later item cites it. Do first. [auto:M]
- [ ] Direction spec: Corey approved the review's direction on 2026-11-05 (six-noun model, Basic/Advanced toggle, nav collapse, demo place Rutledge). Write the one-page spec in docs/ from the review so later items build on it [auto:S]
- [ ] Unified read model, views + `/api/query`: SQL views (`site`, `measurement`, `record`) over the existing tables and JSON layers; `/api/near` reimplemented on them with a snapshot test proving identical output. (sized L in the review: fit it in one M job or split it, and say so) [auto:M]
- [ ] URL-state everywhere: a shared `state.js` that reads/writes place, time, param, layers, view to the query string; Home/Air/Water/Nearby adopt it. [auto:M]
- [ ] Place picker + "my place now" basic view: new `/place` route and template; status lines per medium from `/api/query`; "what changed" from the same. [auto:M]
- [ ] Depth toggle + advanced view: `view=adv` shows today's /air controls under the place; /air redirects to `/place?…&view=adv`. [auto:M]
- [ ] Site pages: `/site/<kind>/<id>` for sensor, monitor, gauge, facility, water system, well, spill; popups and lists link to them. [auto:M]
- [ ] Nav collapse + terms pass: six-item nav, "More" menu, glossary file, rename Sources→Facilities, emoji removed, Admin to footer. [auto:S]
- [ ] Map bounds + y-axis + default sensor: items from §6. [auto:S]
- [ ] /sources pagination + /water heading fix + haze opt-in. [auto:S]
- [ ] API caching, gzip, bbox for heavy layers, self-hosted libs. [auto:M]
- [ ] Accessibility pass: alt/aria/focus on custom controls, reduced-motion, contrast check in the harness. [auto:M]
- [ ] Explore v1 (query builder UI): form over `/api/query`, table + CSV + API link. [auto:M]
- [ ] (session with Corey) Postgres + Timescale migration — session with Corey, L: steps 1–6 from ROADMAP; touches prod. SQLite-ism fixes beforehand are.
- [ ] (session with Corey) Read-only SQL role + `/explore/sql` — session (needs Postgres), M.
- [ ] Nightly public dumps + rate limit + CORS after Postgres; S on SQLite for the dumps alone. [auto:S]
- [ ] Place-based alerts, any medium: subscriptions keyed to place + rule; spill/violation triggers; SMTP go-live is a session item. [auto:M]
- [ ] (session with Corey) Land medium ingest (Superfund/RCRA/AML) — session S to fetch (network), then to model and surface.
- [ ] Neighbor cross-check + auto event candidates. [auto:M]
- [ ] (session with Corey) Demo script + partner walkthrough S with Corey: the five-step narrative from §4, one URL per step.
