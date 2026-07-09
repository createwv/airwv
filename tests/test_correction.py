"""EPA PurpleAir correction (Barkjohn 2021)."""
from datetime import datetime

from airwv.correction import corrected_daily_medians, epa_pm25
from airwv.sources.base import Reading


def test_epa_pm25_formula():
    # 0.524*20 - 0.0862*50 + 5.75 = 10.48 - 4.31 + 5.75 = 11.92
    assert round(epa_pm25(20, 50), 2) == 11.92


def test_epa_pm25_clamps_and_handles_missing():
    assert epa_pm25(0, 90) == 0.0        # would go negative -> clamped
    assert epa_pm25(None, 50) is None
    assert epa_pm25(20, None) is None


def test_corrected_daily_medians_uses_per_reading_rh():
    rows = [
        Reading(source="purpleair", sensor_id="1", ts=datetime(2026, 6, 1, 8), pm2_5=20, humidity=50),
        Reading(source="purpleair", sensor_id="1", ts=datetime(2026, 6, 1, 9), pm2_5=20, humidity=50),
        Reading(source="purpleair", sensor_id="1", ts=datetime(2026, 6, 2, 8), pm2_5=20, humidity=50),
        Reading(source="purpleair", sensor_id="1", ts=datetime(2026, 6, 3, 8), pm2_5=20, humidity=None),  # dropped
    ]
    out = corrected_daily_medians(rows)
    assert round(out[datetime(2026, 6, 1).date()], 2) == 11.92
    assert datetime(2026, 6, 3).date() not in out
