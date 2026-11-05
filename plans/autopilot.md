# Autopilot plan (airwv)

CZadmin's autopilot works the items Corey tagged `[auto]`, top to bottom, on branch `corey/auto`. Rules and goals:
CZadmin `projects/airwv/CHARTER.md`. Workers tick an item when it's done and never add `[auto]` tags.
Direction: the 2026-11-05 review (basic vs advanced views, one system) decides what goes here next.

## Now
- [ ] Product harness: `tests/product/run.sh` starts the site locally on the local `airwv.sqlite` (copied, never the
      production db), and as a resident on a phone and a researcher on desktop: home, air now, water, "What's near me?"
      for 3 WV towns, a source page, Events, /data. Screenshots at 390x844 and 1280x800, console errors, page weight,
      and a short `report.md` judging each page against the charter's goals (can a resident tell if their air and
      water are OK in 10 seconds?). Baseline committed [auto:M]
