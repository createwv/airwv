# Community Reporting & Feedback — Design

Two ways for people to talk back to the project:

- **A. Environmental concern reports** — residents flag something they notice
  (air/water/soil/wildlife/possible violation) and place it on the map.
- **B. Site feedback** — "this is broken" / "I'd like to do or know X" about the
  website itself. Not on the map; routed to maintainers.

Both feed a **maintainer pipeline** (notifications + an admin console) so a small
team can triage, verify, enrich, and publish. Governed by the source-labeling
policy in [`SOURCE-POLICY.md`](SOURCE-POLICY.md).

## Guiding idea

**Dead-simple for the average person; power tools for those who know what they're
talking about.** The default report is ~20 seconds (what + where). Everything else
— naming a business, entering a measurement, attaching a photo — is optional
"advanced" that stays out of the way until asked for.

And a **staged trust model**: a light report passes a quick automated screen and
goes up as *unverified*, a maintainer gets pinged, and it can then be **verified /
enriched** on the back end before it becomes a *confirmed* report. Sensitive inputs
(a named business, a suspected violation, a measurement) are **captured but withheld
from the public view until a maintainer verifies them**.

## Decisions (revised)

| Topic | Decision |
|---|---|
| **Trust model** | **Staged**: light auto pre-screen → published *unverified* (or *held*) → maintainer **verify/enrich** → *confirmed*. Not pure post-moderation, not full pre-moderation. |
| **Scope** | Broadened beyond air: **air / water / soil-land / wildlife / suspected violation / other**. |
| **Naming orgs** | **Allowed as input, gated by verification.** A reporter *may* name a business/org; it's stored privately and only appears publicly if a maintainer verifies + approves it. Unverified reports never name anyone. |
| **Complexity** | **Progressive disclosure** — simple by default, optional advanced fields (org, readings, photo, contact). |
| **Pipeline** | New reports/feedback **ping a Slack/Discord webhook**; a token-gated **admin console** handles triage/verify/publish. |
| **Feedback** | Separate lightweight **site-feedback** form (bug / idea / question), maintainer-routed, not mapped. |

---

## Part A — Environmental concern reports

### Domains & categories

| Domain | Example categories (auto-suggested; free "other" always allowed) |
|---|---|
| **Air** | odor / chemical smell · smoke or soot · dust · haze · flaring / open burning · diesel-truck traffic · symptoms (eye/throat/breathing) |
| **Water** | discoloration · odor · foam/sheen · fish kill · discharge/outfall · flooding of a site |
| **Soil / land** | dumping · staining/residue · erosion into water · odor from ground |
| **Wildlife / animals** | dead or sick wildlife · livestock/pet illness · fish/bird die-off |
| **Suspected violation** | possible permit or regulatory violation (e.g. burning, discharge, hours) — routed for review, **never published as fact** |
| **Other** | anything that doesn't fit |

Categorizing the obvious ones keeps it a couple of taps for most users.

### Progressive disclosure — what the form asks

**Simple (everyone, required):**
1. **Domain** (big buttons: 💨 Air · 💧 Water · 🟤 Soil · 🐾 Wildlife · ⚠️ Violation · Other)
2. **Category** (chips suggested from the domain) + one-line **description**
3. **Location** — drop/drag a pin, or type an address → geocoded pin

**Advanced (optional, collapsed under "Add more detail"):**
- **When** it happened (`observed_at`)
- **Name a business/org** you think is involved — *"Private. It may not appear in
  the public report; a reviewer decides."*
- **Enter a reading** — a measurement you took (see Part B)
- **Photo** (held until approved)
- **Contact** (email/phone — private, for follow-up only)

### Verification lifecycle

```
submit → [auto pre-screen] ─┬─ clean → PUBLISHED (badge: "Unverified community report")
                            └─ flagged → HELD (not public; in the queue)
                                   │
        maintainer (pinged) ───────┴──► VERIFY / ENRICH ─┬─ CONFIRMED (badge: "Verified"),
                                                          │   org/readings may now show
                                                          ├─ keep unverified (leave as-is)
                                                          ├─ MERGE into a related report
                                                          └─ REMOVE (spam/abuse/off-topic)
```

Public badges: **"Unverified community report"** (default) vs **"Verified"** (a
maintainer confirmed it). Suspected-violation and named-org details are **only**
visible on `CONFIRMED` reports where the reviewer approved that specific field.

### Data model — `reports`

```
id            int   pk
created_at    utc
observed_at   utc   optional
domain        text  air | water | soil | wildlife | violation | other
category      text  from the domain list, or free text
description   text  sanitized, length-capped
lat, lon      float exact (stored); public view is jittered ~150 m
area_label    text  coarse reverse-geocode ("Institute, Kanawha Co."), never a street address
stage         text  published_unverified | held | confirmed | removed | merged
suspected_org text  PRIVATE until confirmed+approved
org_public    bool  reviewer approved showing the org (default false)
photo_path    text  optional
photo_ok      bool  approved for display (false until reviewed)
contact_*     text  PRIVATE — email/phone, never served publicly
screen_reason text  why held (spam/profanity/name/violation/photo)
flags_count   int
ip_hash       text  PRIVATE — salted, short retention
mod_note      text  PRIVATE
verified_by   text  maintainer/org that confirmed it
```

**Public projection:** `id, created_at, observed_at, domain, category, description,
jittered lat/lon, area_label, stage badge, photo (if photo_ok), org (only if
org_public), verified_by`. Contact, exact coords, ip_hash, mod_note never leave.

### Submission UX notes
- Consent line: *"Shown publicly (except your contact). Report what you experienced.
  If you name a business it's kept private unless a reviewer confirms it."*
- Returns `published_unverified` (live now, unverified badge) or `held` (thanks,
  under review) — reporter always gets a friendly confirmation.

### Guards (carried over, still essential)
- **Auto pre-screen** → `held`: links/contact in description (spam), profanity,
  pathological text, **any suspected-violation or named-org report** (needs eyes),
  and **any photo** (held until approved).
- **Anti-spam:** per-IP-hash rate limit, hidden honeypot + min time-on-form,
  public flag → auto-hide at threshold, captcha in v2.
- **Photos:** JPEG/PNG/WebP ≤5 MB, re-encoded, dimension-capped, **EXIF/GPS
  stripped** (Pillow), stored outside the repo, never served until `photo_ok`.
- **Privacy:** public location jittered ~150 m; coarse `area_label`; private
  contact + `ip_hash` with a documented retention/purge policy.

### Display
- Toggleable **📣 community reports** map layer (on/off like the others), clustered
  when dense, distinct color, filterable by domain. Popups show the badge +
  disclaimer + link to policy; org/photo only when approved.
- v2: **aggregate/heatmap** ("6 water-odor reports near X, last 30 days").

---

## Part B — Community readings (optional, for the knowledgeable)

Let people who take their own measurements attach them. Clearly community-submitted
and **not mixed into the sensor-network data** unless verified.

`readings_community` table: `id, report_id? (nullable — can stand alone), domain,
parameter, value, unit, method_or_device, taken_at, lat/lon, verified (bool),
notes`. Per-domain parameter presets to keep units sane:

- **Air:** PM2.5 (µg/m³), PM10, VOC (index), CO (ppm), O₃ (ppb)
- **Water:** pH, turbidity (NTU), conductivity (µS/cm), temperature (°C),
  dissolved O₂ (mg/L), nitrate (mg/L)
- **Soil:** pH, moisture (%)

Free "other parameter + unit" always available. Verified community readings could
later render as their own map layer distinct from the sensor network.

---

## Part C — Site feedback (about the website)

A tiny form, reachable from the footer ("Feedback / report a problem"):

`feedback` table: `id, created_at, kind (bug | idea | question), message, page/url
context, contact? (private), status (new | triaged | done), ip_hash`.

Not mapped. Pings the maintainer channel, shows in the admin console alongside
reports. This is how "it's broken" and "I'd like to do/know X" reach the team.

---

## The maintainer pipeline

### Notifications (Slack / Discord / etc.)
On a new report (especially `held`, `violation`, named-org) or feedback, POST a
compact summary to an **incoming webhook** (`AIRWV_REPORT_WEBHOOK`) — reuse the
existing webhook-notifier pattern from alerts. Message: domain/category, area,
unverified badge, and a link to the admin item. Keep PII out of the ping.

### Admin / moderation & verification console (priority)
Token-gated (`X-Admin-Token` == `AIRWV_ADMIN_TOKEN`; real auth later), a simple
page + endpoints to run the queue:
- **Queues:** held · unverified · flagged · feedback.
- **Per report:** view full record (incl. private fields), **Verify/Confirm**,
  edit/enrich (fix category, add area, write a public note), **approve field**
  (org / photo / reading), **Merge**, **Remove**, and respond to the reporter
  (if contact given).
- **Audit:** who did what, when (`mod_note`, `verified_by`).
- Optional CLI mirror: `ingest reports --queue held|flagged`, `--confirm ID`,
  `--remove ID`, `--approve-org ID`.

This is the "admin end" to build toward — moderation + verification is what makes
the staged trust model real.

---

## APIs

Public:
- `POST /api/reports` — create (simple or with advanced fields); validated + screened.
- `GET /api/reports?domain=&bbox=&since=` — published reports (public projection).
- `POST /api/reports/{id}/flag`
- `POST /api/readings` — attach/submit a community reading.
- `POST /api/feedback` — site feedback.

Admin (token):
- `GET /api/admin/queue?type=held|unverified|flagged|feedback`
- `POST /api/admin/reports/{id}` — `{action: confirm|enrich|approve_org|approve_photo|merge|remove, ...}`
- `POST /api/admin/feedback/{id}` — `{status, note}`

---

## Policy / legal alignment
- Ties into [`SOURCE-POLICY.md`](SOURCE-POLICY.md). Naming is **input-allowed,
  publish-gated**: unverified reports never name anyone; a business appears only
  after a maintainer verifies and approves that field, framed factually.
- "Suspected violation" is a **routing category, never a public accusation** — it
  goes to review (and potentially a DEP referral), not straight to the map as fact.
- Standing disclaimer on every community item; visible removal / right-of-reply.

---

## Build sequence (toward the admin end)
1. **Migrations (Alembic)** + tables: `reports`, `readings_community`, `feedback`.
2. **Notification util** — Slack/Discord webhook (reuse alerts webhook pattern).
3. **Public intake** — `POST /api/reports` + `/feedback` + pre-screen + rate-limit
   + honeypot; `GET /api/reports` (published projection).
4. **Admin console** — token-gated queues + verify/enrich/approve/remove/respond
   (the priority) + CLI mirror.
5. **Map UX** — progressive-disclosure form + 📣 layer with badges + domain filter.
6. **Photo pipeline** (EXIF strip, held-until-approved) + **readings** entry.
7. v2 — aggregate/heatmap, captcha, maintainer notifications polish, DEP hand-off.
