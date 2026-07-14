"""Enrich the raw pollution-source list for the /sources page.

The raw ``sources.json`` is a union of several public datasets (EPA TRI, EIA power
plants, NPDES water dischargers…), so the *same physical facility* shows up more than
once under different names and categories — e.g. the John Amos plant appears as
"John Amos Power Plant", "American Electric Power Amos Plant", and
"Appalachian Power Company - John E. Amos Plant".

This module (pure, no I/O) does three things, leaving the raw file untouched:
  1. **merge_sources** — cluster duplicates by proximity + a shared distinctive
     name token into one facility that carries every original record.
  2. **attach_compliance** — name/proximity-match EPA ECHO facilities onto each
     merged source so a "significant violation" badge can show on the card.
  3. **former_name_note** — a small curated map so a facility's prior corporate
     identity (Chemours←DuPont, Nitro←Monsanto…) isn't lost, framed cautiously.
"""

from __future__ import annotations

from math import asin, cos, radians, sin, sqrt

# Generic corporate/facility words that don't identify a *site* — stripped before
# comparing names so a shared word like "power" or "company" can't merge two
# unrelated plants. The site token ("amos") is what survives.
_STOP = {
    "the", "and", "of", "a", "inc", "llc", "co", "corp", "corporation", "company",
    "companies", "plant", "plants", "power", "station", "energy", "electric",
    "generating", "generation", "gen", "facility", "works", "plt", "unit", "units",
    "no", "number", "group", "holdings", "resources", "operating", "operations",
    "international", "industries", "industrial", "mfg", "manufacturing", "services",
    "service", "systems", "us", "usa", "american", "appalachian", "wv", "west",
    "virginia", "e", "l", "p", "lp", "ii", "iii", "site", "facilities", "chemical",
    "chemicals", "coal", "mining", "mine", "aep",
}

# Category priority — when merging, the most specific/impactful category wins as the
# facility's primary, and the canonical name is drawn from that record's cohort.
_CAT_PRIORITY = ["power", "chemical", "oil_gas", "water_discharge", "waste", "materials", "other"]

# Curated, cautious former-name notes for well-known WV facilities. Matched on a
# distinctive token appearing in the name or operator. Framed as "formerly / successor
# to" — the companies are legally distinct; this preserves history without asserting
# present-day liability.
_FORMER = [
    # Each rule needs ALL its tokens to match, keeping notes to unambiguous cases —
    # e.g. "nitro" alone would wrongly tag the Nitro wastewater plant with Monsanto history.
    (("chemours",), "Successor to DuPont (the site's longtime operator before the 2015 spin-off)."),
    (("washington", "works"), "Historically DuPont Washington Works."),
    (("union", "carbide"), "Union Carbide is now a subsidiary of Dow."),
    (("bayer",), "Institute-area operations historically ran under Union Carbide, then Rhône-Poulenc/Aventis."),
    (("solutia",), "Formerly part of Monsanto."),
    (("monsanto",), "Legacy Monsanto operation (later Solutia/Bayer)."),
]


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * 6371.0 * asin(sqrt(a))


def _tokens(name: str) -> set[str]:
    """Distinctive lowercase tokens of a facility name (site-identifying words only)."""
    out = set()
    for raw in (name or "").replace("-", " ").replace(".", " ").replace(",", " ").split():
        t = "".join(ch for ch in raw.lower() if ch.isalnum())
        if len(t) >= 3 and t not in _STOP:
            out.add(t)
    return out


def _canonical_name(records: list[dict]) -> str:
    """The cleanest name for a merged facility: fewest words, then shortest —
    "John Amos Power Plant" beats "Appalachian Power Company - John E. Amos Plant"."""
    names = [r.get("name", "") for r in records if r.get("name")]
    if not names:
        return ""
    return min(names, key=lambda n: (len(n.split()), len(n)))


def former_name_note(name: str, operator: str) -> str | None:
    """A cautious 'formerly …' note for a facility, or None."""
    hay = f"{name or ''} {operator or ''}".lower()
    toks = _tokens(name) | _tokens(operator) | {w for w in hay.replace("-", " ").split()}
    for needles, note in _FORMER:
        if all(n in toks or n in hay for n in needles):
            return note
    return None


def merge_sources(sources: list[dict], max_km: float = 0.9, place_tokens=None) -> list[dict]:
    """Cluster duplicate facility records into one merged facility each.

    Two records merge when they're within ``max_km`` **and** share a distinctive
    name token (so proximity alone can't fuse a chemical plant next to a power plant).
    ``place_tokens`` (town/county names) are excluded from that shared-token test — a
    shared *place* like "nitro" is not evidence of the same facility, only the same town.
    Greedy single-linkage; order-independent enough for this data.
    """
    places = set(place_tokens or ())
    clusters: list[dict] = []   # each: {"lat","lon","tokens",set; "records":[...]}
    for s in sources:
        if s.get("lat") is None or s.get("lon") is None:
            continue
        toks = _tokens(s.get("name", ""))
        placed = False
        for cl in clusters:
            shared = (toks & cl["tokens"]) - places   # site tokens only, not place names
            if shared and _haversine_km(s["lat"], s["lon"], cl["lat"], cl["lon"]) <= max_km:
                cl["records"].append(s)
                cl["tokens"] |= toks
                # keep the running centroid so a chain of records stays anchored
                n = len(cl["records"])
                cl["lat"] += (s["lat"] - cl["lat"]) / n
                cl["lon"] += (s["lon"] - cl["lon"]) / n
                placed = True
                break
        if not placed:
            clusters.append({"lat": s["lat"], "lon": s["lon"], "tokens": set(toks), "records": [s]})

    merged = []
    for cl in clusters:
        recs = cl["records"]
        cats = [r.get("category") for r in recs if r.get("category")]
        primary = min(cats, key=lambda c: _CAT_PRIORITY.index(c) if c in _CAT_PRIORITY else 99) if cats else "other"
        name = _canonical_name(recs)
        # operator: prefer one that isn't just the name echoed back
        ops = [r.get("operator") for r in recs if r.get("operator") and r.get("operator") != r.get("name")]
        operator = min(ops, key=len) if ops else next((r.get("operator") for r in recs if r.get("operator")), "")
        echo = next((r.get("echo") for r in recs if r.get("echo")), None)
        permit = next((r.get("permit") for r in recs if r.get("permit")), None)
        fac = {
            "name": name,
            "type": next((r.get("type") for r in recs if r.get("type")), ""),
            "operator": operator,
            "lat": round(cl["lat"], 6),
            "lon": round(cl["lon"], 6),
            "category": primary,
            "categories": sorted(set(cats), key=lambda c: _CAT_PRIORITY.index(c) if c in _CAT_PRIORITY else 99),
            "names": sorted({r.get("name") for r in recs if r.get("name")}),
            "citation": next((r.get("citation") for r in recs if r.get("citation")), ""),
            "state": next((r.get("state") for r in recs if r.get("state")), None),
            "record_count": len(recs),
        }
        if echo:
            fac["echo"] = echo
        if permit:
            fac["permit"] = permit
        note = former_name_note(name, operator)
        if note:
            fac["former"] = note
        merged.append(fac)
    return merged


_STATUS_RANK = {"significant_violation": 3, "violation": 2, "compliant": 1, "unknown": 0}


def attach_compliance(merged: list[dict], echo_facilities: list[dict], max_km: float = 1.0) -> list[dict]:
    """Match ECHO facilities to merged sources (proximity + shared token) and attach
    the *worst* compliance status found, so a violation badge can render on the card."""
    echo = [e for e in echo_facilities if e.get("lat") is not None and e.get("lon") is not None]
    echo_tokens = [(_tokens(e.get("name", "")), e) for e in echo]
    for fac in merged:
        best = None
        for toks, e in echo_tokens:
            if not (toks & _tokens(fac["name"])):
                continue
            if _haversine_km(fac["lat"], fac["lon"], e["lat"], e["lon"]) > max_km:
                continue
            if best is None or _STATUS_RANK.get(e.get("status"), 0) > _STATUS_RANK.get(best.get("status"), 0):
                best = e
        if best is not None:
            fac["compliance"] = best.get("status")
            fac["compliance_label"] = best.get("compliance_status")
            fac["echo_url"] = best.get("echo_url")
            if best.get("snc"):
                fac["snc"] = True
    return merged


def _place_tokens(echo_facilities: list[dict] | None) -> set[str]:
    """WV place names (town + county) that must not drive a merge. Data-driven from the
    ECHO facilities' city/county fields, plus the WV county list."""
    places: set[str] = set()
    try:
        from airwv.wvgeo import WV_COUNTY_FIPS
        for county in WV_COUNTY_FIPS:
            places |= {t for t in _tokens(county)}
    except Exception:
        pass
    for e in (echo_facilities or []):
        for fld in ("city", "county"):
            for t in (e.get(fld) or "").replace("-", " ").split():
                t = "".join(ch for ch in t.lower() if ch.isalnum())
                if len(t) >= 3:
                    places.add(t)
    return places


def enrich_sources(sources: list[dict], echo_facilities: list[dict] | None = None) -> list[dict]:
    """Full pipeline: dedupe (place-aware), then attach ECHO compliance (if provided)."""
    merged = merge_sources(sources, place_tokens=_place_tokens(echo_facilities))
    if echo_facilities:
        attach_compliance(merged, echo_facilities)
    return merged
