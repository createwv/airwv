# Community Reporting Layer — Design

Let West Virginia residents report an air-quality concern and place it on the
map, so lived experience (odors, smoke, dust, symptoms) sits alongside sensor
data and documented sources. This is a **community-reported tier** — clearly
separated from measured data and documented public-record sources, and governed
by the source-labeling policy in [`SOURCE-POLICY.md`](SOURCE-POLICY.md).

## Decisions (locked)

| Decision | Choice | Why it's safe |
|---|---|---|
| **Publishing** | **Post-moderation** — reports go live on submit, removed if flagged/reviewed | Acceptable *because* of the no-naming rule below; the residual risk is abusive free-text/photos, handled by an automated pre-screen |
| **Facility naming** | **No naming** — location + category only, no facility field | Nobody is named, so an individual report can't defame a facility. Nearby documented sources remain their own separate, cited layer |
| **v1 scope** | Pin-drop **+** address geocoding **+** optional private contact **+** photo upload | Full-featured, but each feature carries a guard (below) |

The tension in "post-moderation + immediately public + free text + photos" is real.
We reconcile it with a **hybrid**: normal text-only reports post instantly, but an
**automated pre-screen** quarantines clear-bad content to `pending`, and **photos
are held until approved** (the one element we pre-moderate — an abusive image
being briefly public is the worst-case we refuse to accept).

## Principles

1. **Separate tier, always labeled.** Distinct marker + color; every popup reads
   *"Community-reported concern — unverified, not a finding of fact."*
2. **No naming, no blame.** No facility field. Free-text that names a documented
   facility is auto-held for review (enforces the policy without censoring people).
3. **Privacy first.** Contact info is private and never served publicly. Public
   pin location is **jittered ~150 m** so a report can't pinpoint someone's home.
4. **Provisional, correctable.** Standing disclaimer + a removal/right-of-reply path.

## Data model — `reports`

```
id             int   pk
created_at     utc   server time
observed_at    utc   optional — when the reporter experienced it
lat, lon       float required — concern location (exact, stored)
area_label     text  optional — coarse reverse-geocoded area ("Institute, Kanawha Co."), NOT street address
category       text  enum below
description    text  sanitized, length-capped (e.g. 1000 chars)
photo_path     text  optional — stored file (EXIF/GPS stripped, re-encoded)
photo_ok       bool  photo approved for display (false until moderated)
contact_email  text  optional, PRIVATE — never serialized publicly
contact_phone  text  optional, PRIVATE — never serialized publicly
status         text  visible | pending | removed
screen_reason  text  why auto-held (spam/profanity/possible-name/photo), nullable
flags_count    int   public flags received
ip_hash        text  PRIVATE — salted hash for rate-limit/abuse, short retention
mod_note       text  PRIVATE — maintainer notes
```

**Public API projection** (what leaves the server for the map): `id, created_at,
observed_at, category, description, jittered lat/lon, area_label, photo (only if
photo_ok), tier="community-reported"`. Contact, `ip_hash`, `mod_note`, and exact
coordinates **never** appear in the public response.

### Categories (v1)

`odor / chemical smell` · `smoke or soot` · `dust` · `haze / poor visibility` ·
`flaring or open burning` · `heavy diesel / truck traffic` · `physical symptoms
(eye, throat, breathing, headache)` · `other`

## Submission flow (UX)

1. **"Report a concern"** button on the map card opens a form.
2. **Locate:** click the map to drop/drag a pin **or** type an address → geocode
   (drops a pin the user can nudge). Pin is required.
3. **Details:** category (required), description (optional), when it happened.
4. **Optional:** private contact (email/phone) for follow-up; photo upload.
5. **Consent line:** "This will be shown publicly (except your contact info).
   Report only what you experienced; don't name or accuse specific businesses."
6. Submit → validated + pre-screened → returns `visible` (live now) or `pending`
   (held for review, with a friendly "thanks, a maintainer will review this" note).

## Guards (the heart of post-moderation safety)

**Automated pre-screen** (runs on every submit; a hit sets `status=pending`):
- Contains a URL / phone / email in the *description* → spam hold.
- Matches a profanity/abuse wordlist → hold.
- **Matches a known facility name** (from `sources.json`) → hold (enforces no-naming).
- Pathological text (very long, all-caps, repeated chars, gibberish ratio) → hold.
- **Any photo present** → photo held (`photo_ok=false`) until a maintainer approves,
  even if the text goes live.

**Anti-spam / abuse:**
- Rate limit per hashed IP (e.g. ≤5/hour, ≤20/day); return 429 over limit.
- Hidden **honeypot** field + minimum time-on-form (bots fail both).
- (v2) hCaptcha/Turnstile if bot pressure appears.
- Public **flag** button; auto-hide at a flag threshold pending review.

**Photo handling:**
- Accept JPEG/PNG/WebP, ≤5 MB; re-encode to JPEG/WebP; cap dimensions (~1600px);
  generate a thumbnail.
- **Strip all EXIF including GPS** on upload (Pillow — already a dependency).
- Store outside the repo (gitignored dir in dev; object storage in prod). Never
  serve until `photo_ok`.

**Privacy:**
- Public coordinates **jittered ~150 m**; exact coords kept server-side only.
- `area_label` is coarse (town/county), never a street address.
- Contact + `ip_hash` retention-limited; documented purge policy.

## API

Public:
- `POST /api/reports` — create (validated + pre-screened). Body: lat, lon,
  category, description?, observed_at?, contact?, photo?, honeypot?, elapsed_ms.
- `GET /api/reports?bbox=&category=&since=` — visible reports (public projection).
- `POST /api/reports/{id}/flag` — increment flags; auto-hide at threshold.

Admin (gated by `X-Admin-Token` == `AIRWV_ADMIN_TOKEN` env; proper auth later):
- `GET /api/admin/reports?status=pending|all` — full records incl. private fields.
- `POST /api/admin/reports/{id}/moderate` — `{action: approve|remove|approve_photo, note}`.

Optional CLI mirror for maintainers without the web UI:
`ingest reports --list-pending`, `--approve ID`, `--remove ID`.

## Display

- Toggleable **📣 community reports** map layer (on/off like sensors/sources/monitors),
  clustered when dense. Distinct color from the measured/documented layers.
- Popup: category, date, description, "unverified community report" disclaimer,
  link to the policy; photo thumbnail only if approved.
- (v2) **Aggregate view** — heatmap / "N odor reports near X in the last 30 days" —
  useful for spotting patterns without over-weighting any single pin.

## Moderation

- Post-moderation queue = anything the pre-screen held (`pending`) + anything
  flagged. A minimal token-gated `/admin` page (or the CLI) lists these with
  approve / remove / approve-photo actions.
- (v2) email/Slack ping to maintainers on new `pending` items.

## Legal / policy hooks

- Ties into [`SOURCE-POLICY.md`](SOURCE-POLICY.md): community-reported is the
  explicitly-hedged tier; no assertions of fact or causation.
- Form terms: report only firsthand experience; be respectful; content is public;
  don't name/accuse businesses. Visible removal/right-of-reply contact.
- Even with no naming, a cluster of pins near one facility can imply blame — the
  standing disclaimer + the separate, cited sources layer keep the framing neutral.

## Phasing

**v1 (this design):** form (pin-drop + address geocode + optional contact +
photo), `POST/GET /api/reports`, flag, pre-screen (spam/profanity/name/photo-hold),
EXIF strip, rate limit + honeypot, jittered public location, admin moderate
(token) + CLI, 📣 map layer with disclaimer.

**v2+:** aggregate/heatmap view, maintainer notifications, captcha, richer
moderation UI, report status (acknowledged/resolved), and a **"Report to WV DEP"**
hand-off next to community reports (see the Report-to-DEP roadmap item — verify the
exact DEP URL/number before shipping).

## Dependencies / notes

- **Pillow** (have it) for EXIF strip + re-encode.
- **Geocoding:** OpenStreetMap **Nominatim** (free) needs a valid User-Agent,
  ≤1 req/sec, result caching, and attribution; swap to a self-hosted/paid geocoder
  if volume grows. Address is a convenience that drops an adjustable pin; pin-drop
  is the source of truth.
- New `reports` table via the storage layer; needs the migration story we already
  flagged (Alembic) rather than `create_all` on the shared DB.
