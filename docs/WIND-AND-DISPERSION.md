# Wind & dispersion — the science, and how AirWV handles it

Air pollution doesn't sit still. To interpret a community sensor's reading near a
pollution source, you have to think about **how the pollutant got there** — which is
mostly about wind and atmospheric dispersion. This documents the relevant science and,
next to each part, exactly how far AirWV models it today (and where we deliberately
stop).

## Why distance alone isn't enough

The source-proximity panel first ranks sensors by **distance**. That's the right
starting point, but it treats every direction equally. In reality a plume travels
**downwind** and spreads as it goes — so a sensor 3 miles *downwind* of a plant can be
far more affected than one 0.5 miles *upwind*. "Which sensors see this source" depends
on wind at least as much as distance.

## The Gaussian plume model (the standard mental model)

The workhorse concept for a continuous point source is the **Gaussian plume model**.
Its picture:

- The plume travels along a centerline pointing **downwind** from the source.
- Concentration **falls off with distance** downwind (dilution) and **spreads
  laterally and vertically** as a bell curve (Gaussian) around the centerline.
- The **ground-level maximum** for an elevated release (a tall stack) is usually
  **not at the fenceline** — the buoyant, elevated plume "touches down" some distance
  downwind (often **1–10+ miles**, depending on stack height and stability).

The concentration at a point depends on: **wind speed** (faster = more dilution but
faster transport), **atmospheric stability** (stable nights trap pollution near the
ground → the overnight buildup we see near industrial corridors; unstable days mix it
up), **stack height & plume buoyancy** (higher/hotter release → farther touchdown),
and **distance + off-axis angle**.

## Terrain — the West Virginia asterisk

Standard plume models assume flat terrain and a single prevailing wind. **WV is
neither.** Its **river valleys channel wind along the corridor** regardless of the
broader (synoptic) wind, and ridges block and redirect flow. So:

- Wind measured at an airport is only an **approximation** of the wind at a given
  source or sensor, especially across ridgelines.
- Valley **inversions** trap pollution overnight (again, the buildup pattern we
  measured — Nitro/John Amos ~1.98× overnight vs. a flat upriver control ~0.97×).

This is why we treat wind weighting as a **guide to attention**, not a verdict.

## Regulatory-grade modeling (what we're *not* doing)

EPA's **AERMOD** is the regulatory Gaussian-plume dispersion model. It ingests hourly
meteorology, stack parameters (height, diameter, exit velocity, temperature), terrain
elevation grids, and building downwash to predict concentrations. It's the right tool
for permitting and enforcement — and **massive overkill** for a community-awareness
map. We don't run it; we point at the same *primary* wind data it uses and apply a
transparent, lightweight heuristic.

## How AirWV handles it — the tiers

| Level | What it does | Status |
|---|---|---|
| **0 · Distance** | Rank sensors by distance from the source; 1 mi & 3 mi zone rings | ✅ shipped |
| **1 · Prevailing wind** | Weight sensors by how often the wind blows *toward* them (downwind exposure) | ✅ shipped |
| **2 · Event-based** | Correlate a sensor's *elevated* readings with the hours it's actually downwind | 🔭 roadmap |
| **3 · Full plume model** | AERMOD-style modeling with stack + terrain | ✗ out of scope (use regulators') |

### Level 1 — prevailing wind weighting (current)

- **Data:** a **wind rose** per WV airport — the frequency the wind blows *from* each
  of 8 compass directions — built from a year of **NWS/ASOS** observations via the
  **Iowa Environmental Mesonet** (keyless, no quota; `scripts/fetch_wind.py` →
  `wind_roses.json`). WV's roses are strongly **westerly** (W/SW prevailing).
- **The weight:** for a sensor at compass bearing β from the source, the wind that
  carries the plume to it blows *from* the opposite direction. So its **downwind
  frequency** = `rose[opposite(β)]`. We combine that with a distance falloff:

  ```
  exposure_score = downwind_frequency × exp(−distance_miles / 3)
  ```

  Toggle **🌀 weight by wind** in the panel and sensors re-rank by this score; the
  "Downwind" column shows how often the wind carries the plume toward each one.
- **Assumptions / limits (stated plainly):** uses the *nearest airport's* climatology
  (not the source's micro-scale wind), ignores terrain channeling, stack height, plume
  rise, and stability. It answers *"which sensors are typically downwind and close,"*
  not *"what concentration."*

### Level 2 — event-based (roadmap)

Instead of climatology, use **actual hourly wind × the sensor's actual PM**: test
whether a sensor's readings are elevated **specifically during the hours it's
downwind** of the source. That's near-causal evidence a source affects a sensor, and
it operationalizes the overnight-accumulation finding. Needs hourly wind in the DB
(same IEM source) aligned to sensor readings.

## Framing (important)

Wind weighting **guides where to look** — it is not attribution. The UI says a sensor
is "more often downwind," never that a facility "caused" a reading. This matches the
source-labeling policy in [`SOURCE-POLICY.md`](SOURCE-POLICY.md): factual, hedged, and
non-accusatory. Concentrations and causation are for regulators and their models.

## Data & attribution
- **Wind:** NWS/ASOS via the **Iowa Environmental Mesonet** (mesonet.agron.iastate.edu),
  keyless. See `scripts/fetch_wind.py`.
- **Sources & sensors:** as in [`DATA-SOURCES.md`](DATA-SOURCES.md).
