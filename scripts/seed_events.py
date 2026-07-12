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
                             "and Appalachia. Our sensors registered it across WV statewide: "
                             "the Kanawha Valley, North Central (Fairmont), the Eastern "
                             "Panhandle (Shenandoah Jct), and the Mid-Ohio Valley (Parkersburg)"),
        "captured": True,
        # region-spanning set (Kanawha / N-Central / E-Panhandle / Mid-Ohio) — shows statewide reach
        "sensor_ids": ["197127", "263579", "263585", "214373", "263581", "263587", "264043"],
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
        "regions_affected": ("Southern WV coalfields (Logan, Mingo, Kanawha, Wayne, Lincoln); "
                             "smoke settled across the Kanawha Valley, where our sensors caught it"),
        "captured": True,
        "sensor_ids": ["197127", "197121", "215985", "187103", "196533", "216019", "208639"],
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
        "title": "Canadian wildfire smoke — June 2023",
        "kind": "wildfire", "region": "Eastern U.S. / West Virginia",
        "lat": None, "lon": None,
        "start_ts": _utc("2023-06-06T00:00"), "end_ts": _utc("2023-06-08T00:00"),
        "origin": "Historic Canadian wildfire season (Quebec & elsewhere)",
        "scope": "Continental",
        "regions_affected": "Eastern U.S. and Canada — among the worst air-quality days in decades",
        "captured": True,
        # EPA regulatory reference monitors (daily) — our community network wasn't online yet
        "sensor_ids": ["54-003-0003", "54-051-1002", "54-039-0020", "54-011-0007"],
        "description": (
            "The historic June 2023 Canadian wildfire smoke event turned skies orange across "
            "the eastern U.S. and drove some of the worst air-quality readings in decades. Our "
            "community sensors weren't online yet (they came online November 2023), but EPA's "
            "regulatory reference monitors captured it across West Virginia — the chart below is "
            "their daily PM2.5, with the Eastern Panhandle (Berkeley Co.) peaking near 49 µg/m3."),
        "sources": [
            {"label": "NOAA NESDIS — Wildfire smoke and air quality",
             "url": "https://www.nesdis.noaa.gov/news/wildfire-smoke-and-air-quality"},
        ],
    },
    # ---- historic / documented events (pre-sensor; context, no measurements of our own) ----
    {
        "title": "Kanawha Valley “blue haze” episodes",
        "kind": "haze", "region": "Kanawha Valley (Poca to Belle)",
        "lat": 38.37, "lon": -81.75,
        "start_ts": _utc("2008-07-11T00:00"), "end_ts": _utc("2008-07-11T23:59"),
        "origin": "Debated — fine particles/sulfates pooling under valley inversions; "
                  "industrial and power-plant emissions implicated",
        "scope": "Regional", "regions_affected": "The ~35-mile Kanawha River valley from Poca to Belle",
        "captured": True,
        # EPA regulatory monitors in the Kanawha Valley (daily; 1-in-3-day sampling)
        "sensor_ids": ["54-039-0010", "54-039-1005"],
        "description": (
            "For decades a bluish haze has periodically settled over the Kanawha Valley under "
            "temperature inversions. WV DEP formally investigated notable episodes on Jan 25 and "
            "July 11, 2008; the July report identified the AEP John Amos power plant as the "
            "largest nearby source of PM, SO2 and NOx. EPA's Kanawha Valley PM2.5 monitors "
            "recorded the July episode at roughly 54 µg/m3 (unhealthy for sensitive groups). A "
            "long-running illustration of how the valley's terrain traps pollution — the same "
            "effect our community sensors watch for today."),
        "sources": [
            {"label": "WV DEP — Blue Haze Incident, July 11 2008",
             "url": "https://dep.wv.gov/daq/pubs/documents/bluehaze-7-11-08.pdf"},
            {"label": "WV DEP — Blue Haze Incident, Jan 25 2008",
             "url": "https://dep.wv.gov/daq/Pubs/Documents/BlueHaze-1-25-08.pdf"},
        ],
    },
    {
        "title": "Bayer CropScience explosion — Institute",
        "kind": "explosion", "region": "Institute, WV (Kanawha Valley)",
        "lat": 38.38, "lon": -81.77,
        "start_ts": _utc("2008-08-28T00:00"), "end_ts": _utc("2008-08-28T23:59"),
        "origin": "Runaway chemical reaction in a pesticide waste (methomyl) unit",
        "scope": "Local", "regions_affected": "Institute and the surrounding Kanawha Valley; "
                 "a shelter-in-place was ordered",
        "captured": False, "sensor_ids": [],
        "description": (
            "On Aug 28, 2008 a runaway reaction blew apart a pressure vessel at the Bayer "
            "CropScience plant in Institute, killing two workers. Debris struck a tank holding "
            "methyl isocyanate (MIC) — the same chemical as the 1984 Bhopal disaster — "
            "narrowly averting a far larger release. The U.S. Chemical Safety Board found serious "
            "process-safety failures; Bayer later paid a $5.6M settlement."),
        "sources": [
            {"label": "U.S. Chemical Safety Board — Bayer CropScience investigation",
             "url": "https://www.csb.gov/bayer-cropscience-pesticide-waste-tank-explosion/"},
        ],
    },
    {
        "title": "Union Carbide toxic gas leak — Institute",
        "kind": "other", "region": "Institute, WV (Kanawha Valley)",
        "lat": 38.38, "lon": -81.77,
        "start_ts": _utc("1985-08-11T00:00"), "end_ts": _utc("1985-08-11T23:59"),
        "origin": "Leak of aldicarb oxime and other gases from the Union Carbide plant",
        "scope": "Local", "regions_affected": "Institute; roughly 135 people sought medical treatment",
        "captured": False, "sensor_ids": [],
        "description": (
            "Union Carbide's Institute plant was the only U.S. facility that made methyl "
            "isocyanate — the sister plant to Bhopal, India. Months after the 1984 Bhopal "
            "catastrophe, on Aug 11, 1985 a leak of aldicarb oxime drifted over Institute and "
            "sent about 135 people to hospitals, putting a national spotlight on the safety of "
            "the valley's chemical plants."),
        "sources": [
            {"label": "Washington Post — chemical leaks at the W.Va. Union Carbide plant (1985)",
             "url": "https://www.washingtonpost.com/archive/politics/1985/02/01/more-chemical-leaks-at-wva-plant-disclosed-by-union-carbide-corp/a5047a4d-363c-4a5b-aeb0-ef7dcf49cc00/"},
        ],
    },
    {
        "title": "Monsanto 2,4,5-T reactor explosion — Nitro",
        "kind": "explosion", "region": "Nitro, WV (Kanawha Valley)",
        "lat": 38.42, "lon": -81.84,
        "start_ts": _utc("1949-03-08T00:00"), "end_ts": _utc("1949-03-08T23:59"),
        "origin": "Explosion in a trichlorophenol / 2,4,5-T herbicide unit",
        "scope": "Local", "regions_affected": "Nitro; ~226 workers developed chloracne; "
                 "dioxin later found well beyond the plant",
        "captured": False, "sensor_ids": [],
        "description": (
            "On March 8, 1949 an explosion at Monsanto's Nitro plant — while making the "
            "herbicide 2,4,5-T — sent up a black cloud and coated workers in soot; about 226 "
            "developed chloracne. The 2,4,5-T was contaminated with dioxin (TCDD), one of the most "
            "toxic compounds known. Monsanto made 2,4,5-T here (a component of Agent Orange) into "
            "1969; elevated dioxin has since been found in nearby soil, streams and fish. Nitro "
            "itself was founded to make WWI explosives."),
        "sources": [
            {"label": "Barlett & Steele / Vanity Fair — Monsanto's Nitro history",
             "url": "http://www.barlettandsteele.com/journalism/vf_monsanto_3"},
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
