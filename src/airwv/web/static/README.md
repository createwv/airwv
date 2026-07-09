# Dashboard static assets

Drop brand assets here — they're served at `/static/`.

- **Logo:** add `logo.svg` (or `logo.png`) and it appears in the dashboard header
  automatically (the header hides the image slot if the file is absent).
- **Palette:** brand colors are CSS variables in `app.py` (`:root { --brand … }`) —
  change them in one place to rebrand (e.g. Empower WV colors).
