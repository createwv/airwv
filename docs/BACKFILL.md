# Backfill runbook

Run these once PurpleAir points are available (free tier is currently exhausted).
Everything reads the API key from `.env`. Estimated cost uses the measured rate
(~12 points/row incl. VOC; 1M points ≈ $10).

## 0. Refresh resolution (do this first, ~cheap)

Picks up offline sensors too (uses `max_age=0`):

```bash
source .venv/bin/activate
python -m airwv.ingest resolve
```

## Points-safety built in

- **Backfill skips windows already stored** — re-running never re-spends points on
  data you own. Add `--refresh` only when you *want* to re-pull (e.g. to enrich old
  rows with newly-added fields).
- **Writes upsert** — a `--refresh` re-pull updates existing rows in place (fills in
  new fields), never duplicating.

## 1. Create WV sensors — full history (recommended)

14 sensors, install→today. With the rich field set (~16 fields incl. A/B channels,
confidence, particle counts), measured cost drives the choice — the 25M grant
covers **hourly** (~7M pts) or **30-min** (~14M pts) for all 14, but **not 10-min
everywhere** (~41M > 25M). Recommended:

```bash
# Hourly full history for all 14 (deep, rich, ~7M points — fits with room to spare)
python -m airwv.ingest backfill --org "Create WV" --average 60 --start 2023-01-01

# Then spend the remaining headroom on targeted 10-min for priority sensors/windows:
python -m airwv.ingest backfill --sensor "Glasgow" --average 10 \
    --start 2024-02-01 --end 2024-12-31 --refresh   # --refresh enriches the rows we already have
```

> Verify first (a few cents): `--limit 1` a short window and check your points
> dashboard before/after to confirm the new fields return and the real per-row cost.

## 2. Priority event verification (tiny, do right after points arrive)

Confirm the Glasgow 2024-09-24 PM2.5=1086 spike was real vs. a glitch by pulling
its neighbors for those days (a few hundred rows each):

```bash
python -m airwv.ingest backfill --sensor "Montgomery" --sensor "Belle" \
    --start 2024-09-22 --end 2024-09-26 --average 10
python -m airwv.ingest events --sensor "Montgomery" --field pm2_5
python -m airwv.ingest events --sensor "Belle" --field pm2_5
```

If the neighbors also spike on 2024-09-24 → real area event. If they're clean →
Glasgow-local (or a Glasgow sensor glitch — check further).

## 3. Whole WVCAG network (optional, larger)

54 sensors: hourly ≈ 8.2M pts (~$82); 10-min ≈ 49M pts (~$490).

```bash
python -m airwv.ingest backfill --average 60 --start 2023-01-01      # all resolved sensors
```

## 4. Ongoing collection (after backfill)

Hourly is sustainable; schedule it (see `deploy/`):

```bash
python -m airwv.ingest run          # or the systemd timer
```

## Tips

- Add `--limit N` to any backfill to test on a few sensors first.
- Analysis commands cost **zero** API points — run freely:
  `analyze`, `patterns`, `compare`, `events`.
