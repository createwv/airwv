"""Dedupe + compliance-tag + former-name enrichment for the /sources page."""

from airwv.sources_enrich import attach_compliance, enrich_sources, former_name_note, merge_sources


def _amos():
    # the three real-world Amos duplicates: same site, different datasets/names
    return [
        {"name": "John Amos Power Plant", "operator": "Appalachian Power (AEP)",
         "category": "materials", "lat": 38.4728, "lon": -81.8236},
        {"name": "American Electric Power Amos Plant", "operator": "American Electric Power",
         "category": "power", "lat": 38.4728, "lon": -81.8236},
        {"name": "Appalachian Power Company - John E. Amos Plant", "operator": "Appalachian Power Company",
         "category": "water_discharge", "lat": 38.478, "lon": -81.829, "permit": "WV0005525", "echo": "https://echo.epa.gov/x"},
    ]


def test_merges_same_facility_by_token_and_proximity():
    merged = merge_sources(_amos())
    assert len(merged) == 1
    fac = merged[0]
    assert fac["record_count"] == 3
    assert fac["name"] == "John Amos Power Plant"          # cleanest name wins
    assert fac["category"] == "power"                       # highest-priority category
    assert set(fac["categories"]) == {"power", "water_discharge", "materials"}
    assert len(fac["names"]) == 3 and fac.get("permit") == "WV0005525"


def test_place_name_does_not_merge_distinct_facilities():
    # two different facilities in the same town — a shared *place* token must not fuse them
    items = [
        {"name": "Solutia-Ops At Flexsys Nitro", "operator": "Solutia Inc",
         "category": "chemical", "lat": 38.42, "lon": -81.84},
        {"name": "Nitro WWTP", "operator": "Nitro WWTP",
         "category": "water_discharge", "lat": 38.415, "lon": -81.845},
    ]
    merged = merge_sources(items, place_tokens={"nitro"})
    assert len(merged) == 2                                 # stayed separate


def test_distant_same_name_does_not_merge():
    items = [
        {"name": "Acme Plant", "operator": "Acme", "category": "power", "lat": 38.0, "lon": -81.0},
        {"name": "Acme Plant", "operator": "Acme", "category": "power", "lat": 39.5, "lon": -80.0},
    ]
    assert len(merge_sources(items)) == 2                   # too far apart


def test_attach_compliance_takes_worst_status():
    merged = merge_sources(_amos())
    echo = [
        {"name": "John Amos Plant", "status": "compliant", "lat": 38.473, "lon": -81.824,
         "echo_url": "https://echo/ok"},
        {"name": "Appalachian Amos", "status": "violation", "lat": 38.474, "lon": -81.824,
         "echo_url": "https://echo/bad", "snc": False},
    ]
    attach_compliance(merged, echo)
    assert merged[0]["compliance"] == "violation"          # worst of the two matches
    assert merged[0]["echo_url"] == "https://echo/bad"


def test_former_name_note_precise():
    assert "DuPont" in (former_name_note("Chemours Belle Plant", "The Chemours Company") or "")
    assert "Monsanto" in (former_name_note("Solutia-Ops At Flexsys Nitro", "Solutia Inc") or "")
    # a plain municipal plant in Nitro must NOT get Monsanto history
    assert former_name_note("Nitro WWTP", "Nitro WWTP") is None


def test_enrich_pipeline_end_to_end():
    merged = enrich_sources(_amos(), [
        {"name": "John Amos", "status": "significant_violation", "lat": 38.4728, "lon": -81.8236,
         "echo_url": "https://echo/x", "city": "Winfield", "county": "Putnam"}])
    assert len(merged) == 1 and merged[0]["compliance"] == "significant_violation"
