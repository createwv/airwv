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

## 1. Create WV sensors — full 10-min history (recommended)

14 sensors, install→today, ~1.6M rows ≈ **~20M points (~$197)**:

```bash
python -m airwv.ingest backfill --org "Create WV" --average 10 --start 2023-01-01
```

Cheaper hourly version (~3.3M points / ~$33):

```bash
python -m airwv.ingest backfill --org "Create WV" --average 60 --start 2023-01-01
```

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
