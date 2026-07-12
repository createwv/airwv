"""Community-report intake helpers: the automated pre-screen, location jitter, and
IP hashing. See docs/COMMUNITY-REPORTING.md.

The pre-screen implements the "post-moderation with a safety net" model: a clean
report publishes as *unverified*, but anything spammy/abusive — or that names a
facility, or flags a suspected violation — is **held** for a maintainer instead.
Over-holding is safe (held = reviewed, not rejected).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

DOMAINS = {"air", "water", "soil", "wildlife", "violation", "other"}

# minimal profanity set (whole-word); expand as needed
_PROFANITY = {"fuck", "fucking", "shit", "asshole", "bitch", "bastard", "cunt", "dick", "slut"}
# links / emails / phone numbers in the free text → spam hold
_CONTACT_RE = re.compile(r"https?://|www\.|@[\w.]+\.\w|\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b")
# generic words that shouldn't count as a facility "name"
_GENERIC = {"plant", "power", "chemical", "corp", "corporation", "company", "station",
            "facility", "works", "manufacturing", "operations", "refinery", "terminal",
            "group", "energy", "services", "systems", "products", "industries",
            "international", "north", "south", "river", "creek", "county", "center"}
# short but well-known WV emitter names the token rule would miss
_CURATED = {"dow", "amos", "aep", "dupont", "eqt"}


def load_facility_triggers(sources_path) -> set[str]:
    """Distinctive facility name tokens (≥5 chars, non-generic) from the sources file,
    plus a curated set — used to hold reports that name a facility for review."""
    triggers = set(_CURATED)
    try:
        data = json.loads(Path(sources_path).read_text(encoding="utf-8"))
        for s in data.get("sources", []):
            for word in re.findall(r"[a-z]{5,}", (s.get("name") or "").lower()):
                if word not in _GENERIC:
                    triggers.add(word)
    except Exception:
        pass
    return triggers


def screen(description: str, domain: str, suspected_org: str | None,
           facility_triggers: set[str]) -> tuple[str, str | None]:
    """Return (stage, screen_reason). stage = 'published_unverified' or 'held'."""
    text = description or ""
    words = set(re.findall(r"[a-z]+", text.lower()))
    reasons = []
    if _CONTACT_RE.search(text):
        reasons.append("link/contact in text")
    if words & _PROFANITY:
        reasons.append("language")
    if len(text) > 1500:
        reasons.append("very long")
    alpha = sum(c.isalpha() for c in text)
    if alpha > 20 and sum(c.isupper() for c in text) > 0.7 * alpha:
        reasons.append("all caps")
    if suspected_org and suspected_org.strip():
        reasons.append("names an org")
    if domain == "violation":
        reasons.append("suspected violation")
    if words & facility_triggers:
        reasons.append("mentions a facility")
    return ("held" if reasons else "published_unverified"), (", ".join(reasons) or None)


def jitter(lat: float, lon: float, seed, meters: float = 150) -> tuple[float, float]:
    """Deterministically offset a point by ~``meters`` (stable per ``seed``) so a
    public report can't pinpoint someone's home."""
    h = int(hashlib.sha1(str(seed).encode()).hexdigest(), 16)
    r1 = ((h & 0xFFFF) / 0xFFFF) * 2 - 1
    r2 = (((h >> 16) & 0xFFFF) / 0xFFFF) * 2 - 1
    dlat = r1 * meters / 111_000
    dlon = r2 * meters / (111_000 * max(0.15, math.cos(math.radians(lat))))
    return round(lat + dlat, 5), round(lon + dlon, 5)


def ip_hash(ip: str, salt: str = "airwv") -> str:
    """Salted short hash of a client IP — for rate limiting/abuse, never stored raw."""
    return hashlib.sha256(f"{salt}:{ip}".encode()).hexdigest()[:32]


def client_ip(request) -> str:
    """Real client IP behind Cloudflare/NPM (CF-Connecting-IP / X-Forwarded-For)."""
    h = request.headers
    return (h.get("cf-connecting-ip")
            or (h.get("x-forwarded-for", "").split(",")[0].strip())
            or (request.client.host if request.client else "unknown"))
