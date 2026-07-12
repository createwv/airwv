"""Seed the Events page with the notable, non-routine events our data (or the record)
captures. Idempotent: skips any event whose title already exists. Run against whatever
DB AIRWV_DATABASE_URL points at (defaults to local sqlite:///airwv.sqlite):

    python scripts/seed_events.py
"""

from __future__ import annotations

import datetime as _dt
import os

from airwv.storage import Store


def _utc(s: str) -> _dt.datetime:
    return _dt.datetime.fromisoformat(s).replace(tzinfo=_dt.timezone.utc)


EVENTS = [
    {
        "title": "Peoples Cartage warehouse fire — Parkersburg",
        "kind": "fire", "region": "Parkersburg / Mid-Ohio Valley",
        "lat": 39.2546, "lon": -81.5601,
        "start_ts": _utc("2026-07-04T00:00"), "end_ts": _utc("2026-07-05T12:00"),
        "origin": "Fire at the Peoples Cartage warehouse (Camden Ave)",
        "scope": "Local", "regions_affected": "Parkersburg and Vienna (Mid-Ohio Valley)",
        "source_refs": ["Peoples Cartage (Camden Ave warehouse)"],
        "captured": True,
        "sensor_ids": ["214373", "214357", "214355"],  # Parkersburg 1, Parkersburg 4, Vienna 1
        "description": (
            "A fire at Peoples Cartage's Camden Avenue warehouse campus began Saturday "
            "July 4, 2026 and reignited early Sunday July 5. Our Parkersburg sensors caught "
            "the reignition smoke as a clean plume: 10-minute PM2.5 jumped sharply at ~05:50 "
            "ET July 5 and peaked ~70-84 µg/m3, passing south-to-north through Parkersburg 1 "
            "-> Parkersburg 4 -> Vienna 1 before clearing by 08:00 as winds shifted SW. Note "
            "the separate spikes the evenings of July 3 & 4 are Fourth-of-July fireworks, not "
            "the fire."),
        "sources": [
            {"label": "WVDEP violations history (WTAP)",
             "url": "https://www.wtap.com/2026/07/09/wvdep-report-shows-history-code-violations-peoples-cartage-warehouse/"},
            {"label": "Peoples Cartage violations (Intermountain)",
             "url": "https://www.theintermountain.com/news/local-news/2026/07/peoples-cartage-documents-show-violations/"},
        ],
    },
    {
        "title": "Canadian wildfire smoke — June 2025",
        "kind": "wildfire", "region": "Statewide (Kanawha Valley sensors shown)",
        "lat": 38.35, "lon": -81.63,
        "start_ts": _utc("2025-06-03T00:00"), "end_ts": _utc("2025-06-06T00:00"),
        "origin": "Wildfires across central & western Canada (smoke transported ~1000s of miles)",
        "scope": "Continental",
        "regions_affected": ("Much of Canada and the eastern U.S. — the Midwest, Northeast, "
                             "and Appalachia including WV, VA, KY and TN"),
        "captured": True,
        "sensor_ids": ["197127", "196533", "216019", "216007", "196535", "197121"],
        "description": (
            "Smoke from major wildfires across central and western Canada was carried into "
            "Appalachia in early June 2025, pushing air into the Moderate range. Our Kanawha "
            "Valley sensors registered the incursion network-wide (a regional signature — all "
            "sites rise together), with further bumps mid-month (June 11-13). Appalachian "
            "Voices documented the same event using community PurpleAir data across WV, VA, "
            "KY and TN."),
        "sources": [
            {"label": "Appalachian Voices — Canadian wildfires & Appalachian air quality (uses PurpleAir data)",
             "url": "https://appvoices.org/2025/06/18/canadian-wildfires-appalachian-air-quality/"},
            {"label": "Washington Post — Canadian wildfire smoke air-quality maps",
             "url": "https://www.washingtonpost.com/weather/2025/06/03/air-quality-canadian-wildfire-smoke-maps/"},
        ],
    },
    {
        "title": "West Virginia fall wildfires (drought) — November 2024",
        "kind": "wildfire", "region": "Southern WV / Kanawha Valley",
        "lat": 38.35, "lon": -81.63,
        "start_ts": _utc("2024-11-06T00:00"), "end_ts": _utc("2024-11-09T00:00"),
        "origin": "Drought-driven forest fires in the southern WV coalfields",
        "scope": "Regional",
        "regions_affected": ("Southern WV coalfields (Logan, Mingo, Kanawha, Wayne, Lincoln) "
                             "and the Kanawha Valley"),
        "captured": True,
        "sensor_ids": ["197127", "197121", "196535", "216019", "215965", "196533"],
        "description": (
            "Severe drought fueled a heavy fall fire season across West Virginia in November "
            "2024 — the Division of Forestry fought multiple large fires in the southern "
            "coalfields (Logan, Mingo, Kanawha, Wayne, Lincoln), and Gov. Justice issued a "
            "statewide burn ban on Nov 4. Our Kanawha Valley sensors picked up the smoke, "
            "with Glasgow peaking near 194 µg/m3 around Nov 7."),
        "sources": [
            {"label": "WV MetroNews — drought fuels forest fires (Nov 8, 2024)",
             "url": "https://wvmetronews.com/2024/11/08/drought-conditions-continue-to-fuel-forest-fires-in-w-va/"},
            {"label": "WV Watch — statewide burn ban (Nov 4, 2024)",
             "url": "https://westvirginiawatch.com/2024/11/04/justice-issues-statewide-burn-ban-as-drought-conditions-continue-in-west-virginia/"},
        ],
    },
    {
        "title": "Canadian wildfire smoke — June 2023 (before our sensors)",
        "kind": "wildfire", "region": "Eastern U.S. / West Virginia",
        "lat": None, "lon": None,
        "start_ts": _utc("2023-06-06T00:00"), "end_ts": _utc("2023-06-08T00:00"),
        "origin": "Historic Canadian wildfire season (Quebec & elsewhere)",
        "scope": "Continental",
        "regions_affected": "Eastern U.S. and Canada — among the worst air-quality days in decades",
        "captured": False, "sensor_ids": [],
        "description": (
            "The historic June 2023 Canadian wildfire smoke event turned skies orange across "
            "the eastern U.S. and drove some of the worst air-quality readings in decades, "
            "including in West Virginia. It predates our community network (our sensors came "
            "online November 2023), so we have no measurements of our own — included here as "
            "documented context."),
        "sources": [
            {"label": "NOAA NESDIS — Wildfire smoke and air quality",
             "url": "https://www.nesdis.noaa.gov/news/wildfire-smoke-and-air-quality"},
        ],
    },
]


def main() -> None:
    store = Store(os.environ.get("AIRWV_DATABASE_URL", "").strip() or "sqlite:///airwv.sqlite")
    store.create_schema()  # ensure the events table exists
    existing = {e.title: e.id for e in store.events_for_admin()}
    added = updated = 0
    for ev in EVENTS:
        if ev["title"] in existing:
            store.update_event(existing[ev["title"]], **ev)
            updated += 1
            print(f"updated: {ev['title']}")
        else:
            store.add_event(**ev)
            added += 1
            print(f"added: {ev['title']}")
    print(f"done: {added} added, {updated} updated")


if __name__ == "__main__":
    main()
