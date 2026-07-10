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

## Tiered points estimate (hard calculation, 2026-07-10)

**Cost model (measured):** our 15-field rich set costs **~28 points/row** (row =
one sensor at one timestamp; 688k rows ≈ 19.3M pts confirms it). 100k points = $1.
Rows per sensor-year: **hourly 8,760** (~245k pts), **10-min 52,560** (~1.47M pts).
Registry `date_installed` gives real history depth per sensor.

**Budget:** 25M grant · ~19.3M spent · **~5.7M remaining** *(verify live balance).*

| Tier | Sensors to pull | Sensor-yrs | **Hourly** | **10-min** |
|---|---|---|---|---|
| **1. Finish Create WV** | 5 (9 of 14 done) | 11.9 | **2.9M** ($29) | 17.6M ($176) |
| **2. EWV / WVCAG network** | 40 | 46.6 | **11.4M** ($114) | 68.6M ($686) |
| **1+2 = all owned (54)** | 45 remaining | 58.5 | **14.4M** ($144) | 86.1M ($861) |
| **3. All WV public (~3.5 yr)** | **~170** | 595 | **146M** ($1.5k) | 876M ($8.8k) |
| **3. All WV public (~6.5 yr)** | **~170** | 1,105 | **271M** ($2.7k) | 1.63B ($16.3k) |

> **Note on the WV public count:** the raw listing has **1,351** PurpleAir units, but
> that's the whole WV *bounding box* (spills into 5 neighboring states). Only **~170
> are inside West Virginia** (and that still includes indoor + inactive). Tier 3 above
> uses ~170; the full-bbox 1,351 would be ~8× larger (billions of points).

**Reading it:**
- **Tier 1 fits inside the ~5.7M we already have** (2.9M hourly) — no new ask needed.
- **All owned (54) at hourly = ~14.4M** → ask PurpleAir for **~10M more** beyond the
  remaining balance. Honest, nonprofit-scoped.
- **Tier 3 is not feasible as a full historical backfill** (1–13 *billion* points).
  Reframe: lean fields + hourly + shallow depth; **real-time-only going forward**;
  or a **near-source subset** (sensors within ~3 mi of documented polluters — ties
  to the source-proximity panel).

**Levers:** 10-min ≈ **6×** hourly; a **lean field set** (~5 core fields) ≈ **⅓ the
cost** (~9 pts/row) — big for Tier 3; depth scales linearly. Ongoing real-time uses
the cheaper `/sensors` endpoint (not the history rate), so recurring collection is
comparatively inexpensive vs. the one-time backfill.

**Recommended ask (all owned, scoped):** 54 sensors (14 Create WV + 40 WVCAG/EWV),
15-field rich set, **hourly** history since install (~2024–2026), hourly real-time
ongoing → **~14.4M total, ~10M new**. (10-min reserved for a small near-source subset.)

## Future asks (when we scale)

- **All WV public sensors (~1,351)** — only for a statewide historical net, and only
  with a lean field set / shallow depth / subset; the full 15-field decade pull is
  1–13 billion points ($11k–$129k), so scope hard or go real-time-only.
- Re-estimate before every ask with the cost model above; request to actual need.

## Etiquette

- Be specific and scoped; don't over-request.
- Cache `resolve` (expensive) and collect hourly, not every few minutes.
- Backfill once, then append cheaply — don't re-pull history we already own.
