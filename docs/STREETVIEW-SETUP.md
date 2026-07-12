# Street View photos on the Sources page — setup checklist

The `/sources` page shows a **front-of-business photo** for each facility using Google's
**Street View Static API**. Until a key is configured it falls back to clean
category-tile placeholders, so this is entirely optional — do it when you're ready.

## Cost summary (verify current numbers — Google changed this March 2025)
- The old flat **$200/month** Maps credit is **retired**. It's now **per-API free tiers**.
- Street View Static is a "Pro" SKU: **~5,000 free panorama loads / month**, then roughly
  **$5.60–$7 per 1,000**.
- **A billing account (card on file) is required even for the free tier** — that's why
  Cloud Console asks for payment. You are **not charged** while under the free tier.
- **Nonprofit credit:** Google for Nonprofits offers **Maps Platform credits from
  $250/month** (verified via Goodstack). Create WV is very likely eligible — this alone
  would cover our usage many times over.
- Our page lazy-loads photos and uses placeholders in the grid, so real-world usage
  should stay comfortably under the free tier regardless.

## To-do
- [ ] **Apply for the Google for Nonprofits Maps Platform credit** (~$250/mo).
      Sign in to the Create WV Google for Nonprofits account →
      https://support.google.com/nonprofits/answer/3367237 . (Optional but recommended;
      the key works without it, this just adds a safety margin.)
- [ ] **Create/pick a Google Cloud project** under that same Google account:
      https://console.cloud.google.com/
- [ ] **Enable billing** on the project (attach a card). Required even for free usage.
- [ ] **Enable the API:** APIs & Services → Library → **"Street View Static API"** → Enable.
- [ ] **Create an API key:** APIs & Services → Credentials → Create credentials → API key.
- [ ] **Restrict the key** (important — it's used client-side):
      - Application restriction → **HTTP referrers**: `air.createwv.org/*`
        (add `http://localhost:8000/*` if testing locally).
      - API restriction → restrict to **Street View Static API** only.
- [ ] **Add it to the server** `~/airwv/.env`:  `AIRWV_GOOGLE_MAPS_KEY=<key>`
- [ ] **Restart the web service:**
      `kill "$(systemctl show -p MainPID --value airwv-web)"`  (systemd auto-restarts)
- [ ] **Verify:** open https://air.createwv.org/sources — cards/detail should show real
      photos; facilities with no Street View coverage still fall back to placeholders.
- [ ] **(Later) monitor usage** in Cloud Console → Billing to confirm you're within the
      free tier / nonprofit credit.

## How it's wired (for reference)
- The key is read from env in `app.py` (`/sources` route) and injected into the page as
  `window.SV_KEY`. No key → `sources.js` renders category-tile placeholders.
- Image URL: `https://maps.googleapis.com/maps/api/streetview?size=WxH&location=LAT,LON&fov=78&source=outdoor&return_error_code=true&key=...`
  — `return_error_code=true` makes missing-panorama locations 404, and the `<img>`
  `onerror` swaps in the placeholder tile.
- Free-billing alternative if you'd rather skip Google entirely: **Mapillary** (free,
  crowd-sourced street imagery — patchy rural WV coverage) or satellite thumbnails
  (Mapbox/ESRI/USGS). Ask and we can wire one of those instead.
