"""Plain-language summaries of the hand-researched data-center list (data/data_centers.json).

The basic view shows one card per data center: for water, energy, noise and purpose,
"what we know" (the record's own words, which carry their attribution, e.g. "(DEP
notice)") and "what's not disclosed", then the site's sources and how to ask for what's
missing. A record's ``cite`` maps a field ("water.source", "use.0") to indexes into its
``sources``, so a card line can link its own source; lines without a key fall back to the
site's source list. Nothing here adds facts; it only sorts the file's fields. See docs/DATA-CENTERS.md.
"""

from __future__ import annotations

STATUS_LABEL = {
    "operating": "Operating", "under_construction": "Under construction",
    "certified": "State-certified (not yet built)", "permitted": "Permitted (not yet built)",
    "proposed": "Proposed", "stalled": "Stalled", "tabled": "Tabled", "unknown": "Status unknown",
}
USE_LABEL = {
    "generative_ai": "Generative AI", "cloud": "Cloud computing", "crypto_mining": "Crypto mining",
    "storage": "Data storage", "government_or_surveillance": "Government or surveillance",
    "enterprise": "Business / enterprise", "unknown": "Unknown",
}

# topic -> [(section, field, plain label)]
TOPICS = {
    "water": [("water", "source", "Water source"), ("water", "withdrawal", "How much water it takes"),
              ("water", "cooling", "Cooling"), ("water", "permits", "Water permits")],
    "energy": [("power", "mw", "Power (megawatts)"), ("power", "supply", "Power supply"),
               ("power", "utility", "Utility"), ("power", "gas_supply", "Gas supply"),
               ("air_permits", "backup_generators", "Backup generators")],
    "noise": [("noise", "local_rules", "Local noise rules"), ("noise", "complaints", "Complaints"),
              ("noise", "measurements", "Measurements"), ("noise", "mitigation", "Noise controls")],
}
TOPIC_LABEL = {"water": "Water", "energy": "Energy", "noise": "Noise", "purpose": "What it's for"}

# Where people can ask for what isn't public (URLs as listed in docs/DATA-CENTERS.md).
HOW_TO_ASK = [
    {"topic": "energy", "where": "WV Public Service Commission (PSC) dockets",
     "how": "Search the operator or utility name in the PSC case system; anyone can read filings, "
            "and you can file a public comment on an open case.",
     "url": "https://www.psc.state.wv.us/"},
    {"topic": "air", "where": "WV DEP air permit comments",
     "how": "Air permits for generators and turbines go out for public comment; the notice gives "
            "the deadline and where to send written comments.",
     "url": "https://dep.wv.gov/daq/permitting/Pages/NSR-Permit-Applications.aspx"},
    {"topic": "water", "where": "WV DEP water permits and water-use reports",
     "how": "Ask DEP for the site's NPDES / stormwater permits and any large-quantity water-use "
            "registration, or comment when a permit is noticed.",
     "url": "https://dep.wv.gov/"},
    {"topic": "records", "where": "Public records request",
     "how": "A West Virginia Freedom of Information Act request (W. Va. Code §29B-1) to the agency "
            "or county commission that holds the record; federal facilities take a federal FOIA request.",
     "url": None},
]

_GAP_PREFIXES = ("not disclosed", "none found")


def is_gap(value) -> bool:
    """True when the record says nobody has made this public ("not disclosed" / "none found")."""
    return isinstance(value, str) and value.strip().lower().startswith(_GAP_PREFIXES)


def cited(dc: dict, key: str) -> list[dict]:
    """The sources a record's ``cite`` gives for one field, each with its 1-based number in the list."""
    srcs = dc.get("sources") or []
    return [{**srcs[i], "n": i + 1} for i in (dc.get("cite") or {}).get(key, []) if 0 <= i < len(srcs)]


def card(dc: dict) -> dict:
    """The basic-view summary of one data center: known / not disclosed per topic."""
    topics = []
    for topic, fields in TOPICS.items():
        known, gaps = [], []
        for section, field, label in fields:
            value = (dc.get(section) or {}).get(field)
            if not value or (isinstance(value, str) and value.strip().lower() == "not applicable"):
                continue
            if is_gap(value):   # the bare phrase adds nothing under the "not disclosed" heading
                bare = value.strip().lower() in _GAP_PREFIXES
                gaps.append({"label": label, "value": None if bare else value,
                             "sources": [] if bare else cited(dc, f"{section}.{field}")})
            else:
                known.append({"label": label, "value": value, "sources": cited(dc, f"{section}.{field}")})
        topics.append({"topic": topic, "label": TOPIC_LABEL[topic], "known": known, "gaps": gaps})
    uses = dc.get("use") or []
    topics.append({
        "topic": "purpose", "label": TOPIC_LABEL["purpose"],
        "known": [{"label": f"{USE_LABEL.get(u['category'], u['category'])} ({u['confidence']} confidence)",
                   "value": u["evidence"], "sources": cited(dc, f"use.{i}")}
                  for i, u in enumerate(uses) if u.get("category") != "unknown"],
        "gaps": [{"label": "Use", "value": u["evidence"], "sources": cited(dc, f"use.{i}")}
                 for i, u in enumerate(uses) if u.get("category") == "unknown"],
    })
    return {"id": dc["id"], "name": dc["name"], "operator": dc.get("operator"),
            "county": dc.get("county"), "place": dc.get("place"),
            "status": dc.get("status"), "status_label": STATUS_LABEL.get(dc.get("status"), dc.get("status")),
            "topics": topics, "not_disclosed": dc.get("not_disclosed") or [],
            "sources": dc.get("sources") or [], "url": f"/site/data_center/{dc['id']}"}
