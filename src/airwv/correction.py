"""EPA nationwide PurpleAir correction (Barkjohn et al. 2021).

Raw PurpleAir PM2.5 reads high vs regulatory monitors (our `validate` pass
measured ~+3-5 µg/m³ in the Kanawha cluster). The EPA-derived correction brings
community sensors close to reference grade:

    PM2.5_corrected = 0.524 * PA_cf1 - 0.0862 * RH + 5.75   (clamped at 0)

We apply it to our stored PM2.5 (pm2.5_atm), which tracks cf_1 closely at the
low-to-moderate concentrations typical for WV; the two diverge only at high
smoke levels. RH is the sensor's own humidity reading, as the formula intends.
"""

from __future__ import annotations

import statistics
from collections import defaultdict


def epa_pm25(pm_cf1: float | None, rh: float | None) -> float | None:
    """Corrected PM2.5 (µg/m³), or None if either input is missing."""
    if pm_cf1 is None or rh is None:
        return None
    return max(0.0, 0.524 * pm_cf1 - 0.0862 * rh + 5.75)


def corrected_daily_medians(readings) -> dict:
    """Daily-median EPA-corrected PM2.5 keyed by date (uses each reading's RH)."""
    by_day: dict = defaultdict(list)
    for r in readings:
        c = epa_pm25(r.pm2_5, r.humidity)
        if c is not None:
            by_day[r.ts.date()].append(c)
    return {day: statistics.median(vals) for day, vals in by_day.items()}
