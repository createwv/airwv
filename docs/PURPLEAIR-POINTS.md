# PurpleAir API points — grant & request protocol

PurpleAir bills API usage in **points** (~100,000 points per $1). This documents
our current grant and the format PurpleAir asks us to use when requesting more,
so future asks are quick and well-scoped.

> Note: point grants are at PurpleAir's discretion. They have been generous with
> our nonprofit community-monitoring use, but a grant is never guaranteed — scope
> requests honestly and to actual need.

## Current grant

- **25,000,000 points** granted to **Organization `35056C7A`** (under
  **info@createwv.org**), July 2026 — for retroactive/historical pulls of our
  organization's sensors.
- Contact: **Ryan Farmer**, PurpleAir — contact@purpleair.com
- API docs / field reference: <https://api.purpleair.com/>

## How to request more points (PurpleAir's questions)

When asking for additional points, answer these (verbatim from PurpleAir):

1. **How many sensors** will you be requesting data from?
2. **Which data fields** will you be using? (see the API docs)
3. **Which data resolution?** (10-minute, 1-hour, etc.)
4. **Historical requests?** If so, **which time ranges**?
5. **Regular real-time requests** for new data? If so, **expected frequency**?

## Our current answers (Create WV / Kanawha Valley scope)

1. **Sensors:** 14 Create WV community sensors (of ~54 in the broader WVCAG
   network). Statewide/all-EWV would be a later, separate ask.
2. **Fields:** timestamp, PM1.0/2.5/10, **PM2.5 A/B channels**, **confidence**,
   VOC, temperature, humidity, pressure, and **0.3–10 µm particle counts**
   (see `SENSOR_FIELDS` / `HISTORY_FIELDS` in `sources/purpleair.py`).
3. **Resolution:** 10-minute for history (1-hour where cost matters); hourly for
   ongoing real-time collection.
4. **Historical:** each sensor's full record since install (2023–2025 → present).
5. **Real-time:** hourly collection going forward (~24 calls/day × ~14 sensors).

## Future asks (when we scale)

- **All EWV / WVCAG network** (~54 sensors) — same fields, 10-min history.
- **All WV public sensors** (~560) — only if a statewide net is wanted; much larger.
- Estimate points before asking (measured rate ~10–12 pts/row incl. our field set;
  see README "API points & history strategy") and request to actual need.

## Etiquette

- Be specific and scoped; don't over-request.
- Cache `resolve` (expensive) and collect hourly, not every few minutes.
- Backfill once, then append cheaply — don't re-pull history we already own.
