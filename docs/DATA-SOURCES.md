# Data sources

AirWV combines community sensors with government reference monitors. All of it is
public data; here's exactly where each layer comes from and how we pull it.

## The three layers

| Layer | Source | Resolution | Access | Command |
|---|---|---|---|---|
| **Community sensors** | PurpleAir | 10-min / hourly | API key + points (grant) | `ingest collect` / `backfill` |
| **Live reference** | **EPA AirNow** | hourly | **keyless, no quota** | `ingest airnow` |
| **Historical reference** | **EPA AirData (AQS)** | daily | **keyless, no quota** | `ingest airdata` |

Reference monitors are regulatory-grade FRM/FEM instruments — we plot them next to
community sensors and correlate the two to validate the community network.

## Reference monitors: why we go direct to EPA (and credit to OpenAQ)

We first reached reference data through **[OpenAQ](https://openaq.org)** — an
excellent open-source platform that aggregates air-quality data worldwide into one
clean API. **Their project is genuinely great, and their [GitHub](https://github.com/openaq)
is worth studying** for anyone building in this space. Poking at their API is how we
learned that US real-time data is tagged `provider = "AirNow"` — i.e. OpenAQ is
re-serving EPA's own **AirNow** feed.

That pointed us at the primary sources, which we now use **directly**:

- **Live/real-time → EPA AirNow.** AirNow is EPA's real-time program (aggregating
  EPA + state/local/tribal agencies). Its hourly `HourlyAQObs` files are published
  at <https://files.airnowtech.org> — **no key, no rate limit, no quota**. One
  download has every US monitor for the hour; we keep WV + bordering states. Data
  is *preliminary* (not QA'd), for public awareness — not regulatory use.
- **Deep history → EPA AirData (AQS).** EPA publishes pre-generated annual daily
  files (`daily_88101_YYYY.zip`, PM2.5) at
  <https://aqs.epa.gov/aqsweb/airdata/download_files.html> — again **keyless, no
  quota**. This is fully QA'd regulatory data, back to the 2000s. We import
  WV + border, 2007–present.

Going direct means we're **independent of any single provider's quota or terms** —
AirNow and AirData are plain public file downloads, so our reference layer can't be
rate-limited or suspended.

### Why we moved off the OpenAQ API (honestly)
OpenAQ's free tier is **60 requests/min and 2,000/hour**, and their Terms of Use
prohibit **bulk downloading** and leaving requests running. Our early decade-wide
historical backfill did exactly that and got the key suspended — a mistake on our
part, not a fault of theirs. The lesson: **their API is for light querying, not bulk
extraction** — so bulk history belongs on EPA's own bulk files (which is where it is
now). We stay well within their limits if we use them at all (see below).

### When we might still use OpenAQ
We don't need it for WV today, but it's a good tool to keep in mind for:
- **Non-US / global** comparisons (AirNow is US-only).
- **Networks AirNow doesn't carry** — OpenAQ aggregates research, low-cost, and
  international networks beyond the US regulatory set.
- **Redundancy** — a fallback if an AirNow file format or endpoint changes.

If we do, it's **light, real-time queries only** — never bulk history — via the
`ingest reference` client, which enforces a hard daily cap + a stop-on-error circuit
breaker so we respect their limits.

## Attribution
- **AirNow / AirData:** data courtesy of the **US EPA** (and state/local/tribal
  partners). AirNow data is preliminary; AQS/AirData is finalized. Not for
  regulatory decisions.
- **OpenAQ:** thanks to the OpenAQ project for their open platform and API, which
  helped us find the primary EPA sources — <https://openaq.org> ·
  <https://github.com/openaq>.
- **PurpleAir:** community sensor data via the PurpleAir API.

## Pulling each source

```bash
# Live reference (hourly, keyless) — run on a timer:
python -m airwv.ingest airnow

# Historical reference (daily, keyless) — one-time bulk, WV + border, 2007–2024:
python -m airwv.ingest airdata --start-year 2007 --end-year 2024

# Community sensors (PurpleAir — needs a key + points):
python -m airwv.ingest collect        # current readings
python -m airwv.ingest backfill --days N --average 60
```

Deploy timers are in `deploy/systemd/` (`airwv-airnow.*` for live reference).
