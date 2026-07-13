"""West Virginia county geography — FIPS codes and approximate centroids.

Centroids are approximate (county-level), used to place records that only carry a
county name, not coordinates (e.g. NRC spill reports, SDWA drinking-water systems).
Anything placed this way should be labelled "approximate — county location".
"""
from __future__ import annotations

# 3-digit FIPS county code (as WV DEP stores it) -> name
WV_COUNTY_FIPS = {
    "001": "Barbour", "003": "Berkeley", "005": "Boone", "007": "Braxton", "009": "Brooke",
    "011": "Cabell", "013": "Calhoun", "015": "Clay", "017": "Doddridge", "019": "Fayette",
    "021": "Gilmer", "023": "Grant", "025": "Greenbrier", "027": "Hampshire", "029": "Hancock",
    "031": "Hardy", "033": "Harrison", "035": "Jackson", "037": "Jefferson", "039": "Kanawha",
    "041": "Lewis", "043": "Lincoln", "045": "Logan", "047": "McDowell", "049": "Marion",
    "051": "Marshall", "053": "Mason", "055": "Mercer", "057": "Mineral", "059": "Mingo",
    "061": "Monongalia", "063": "Monroe", "065": "Morgan", "067": "Nicholas", "069": "Ohio",
    "071": "Pendleton", "073": "Pleasants", "075": "Pocahontas", "077": "Preston", "079": "Putnam",
    "081": "Raleigh", "083": "Randolph", "085": "Ritchie", "087": "Roane", "089": "Summers",
    "091": "Taylor", "093": "Tucker", "095": "Tyler", "097": "Upshur", "099": "Wayne",
    "101": "Webster", "103": "Wetzel", "105": "Wirt", "107": "Wood", "109": "Wyoming",
}

# county name -> (lat, lon) approximate centroid
WV_COUNTY_CENTROID = {
    "Barbour": (39.13, -80.00), "Berkeley": (39.45, -78.02), "Boone": (38.02, -81.72),
    "Braxton": (38.70, -80.72), "Brooke": (40.27, -80.57), "Cabell": (38.42, -82.24),
    "Calhoun": (38.85, -81.12), "Clay": (38.46, -81.08), "Doddridge": (39.27, -80.70),
    "Fayette": (38.03, -81.08), "Gilmer": (38.92, -80.85), "Grant": (39.10, -79.15),
    "Greenbrier": (37.93, -80.45), "Hampshire": (39.32, -78.62), "Hancock": (40.52, -80.57),
    "Hardy": (39.00, -78.87), "Harrison": (39.28, -80.38), "Jackson": (38.83, -81.67),
    "Jefferson": (39.30, -77.87), "Kanawha": (38.32, -81.53), "Lewis": (38.99, -80.50),
    "Lincoln": (38.17, -82.07), "Logan": (37.83, -81.93), "Marion": (39.51, -80.24),
    "Marshall": (39.86, -80.66), "Mason": (38.76, -82.02), "McDowell": (37.37, -81.65),
    "Mercer": (37.40, -81.10), "Mineral": (39.42, -78.94), "Mingo": (37.72, -82.13),
    "Monongalia": (39.63, -80.05), "Monroe": (37.55, -80.55), "Morgan": (39.55, -78.25),
    "Nicholas": (38.28, -80.79), "Ohio": (40.10, -80.62), "Pendleton": (38.68, -79.35),
    "Pleasants": (39.37, -81.16), "Pocahontas": (38.33, -80.00), "Preston": (39.47, -79.67),
    "Putnam": (38.51, -81.90), "Raleigh": (37.77, -81.22), "Randolph": (38.77, -79.87),
    "Ritchie": (39.17, -81.06), "Roane": (38.72, -81.35), "Summers": (37.65, -80.85),
    "Taylor": (39.33, -80.05), "Tucker": (39.11, -79.56), "Tyler": (39.47, -80.89),
    "Upshur": (38.89, -80.23), "Wayne": (38.15, -82.42), "Webster": (38.49, -80.42),
    "Wetzel": (39.60, -80.64), "Wirt": (39.02, -81.38), "Wood": (39.20, -81.52),
    "Wyoming": (37.61, -81.55),
}


def canon_county(name: str | None) -> str | None:
    """Canonical WV county name — fixes casing (str.title() breaks 'McDowell')."""
    if not name:
        return None
    n = name.strip().title()
    return "McDowell" if n == "Mcdowell" else n


def county_centroid(name: str | None) -> tuple[float, float] | None:
    return WV_COUNTY_CENTROID.get(canon_county(name))
