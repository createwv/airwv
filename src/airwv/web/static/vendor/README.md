# Self-hosted third-party libraries

`base.html` loads each library from here when the file exists, else from its CDN (`vendor()`
in `web/app.py`), so a checkout without the copies still works. Flat file names, pinned
versions; the product harness serves these too (`tests/product/conftest.py`). Fetch, check,
then commit:

| File | Source |
|---|---|
| `plotly-2.32.0.min.js` | https://cdn.plot.ly/plotly-2.32.0.min.js |
| `leaflet.js` | https://unpkg.com/leaflet@1.9.4/dist/leaflet.js |
| `leaflet.css` | https://unpkg.com/leaflet@1.9.4/dist/leaflet.css |
| `images/layers.png`, `images/layers-2x.png`, `images/marker-icon.png`, `images/marker-icon-2x.png`, `images/marker-shadow.png` | https://unpkg.com/leaflet@1.9.4/dist/images/ (`leaflet.css` points at them relatively) |
| `leaflet.markercluster.js` | https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js |
| `MarkerCluster.css` | https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css |
| `MarkerCluster.Default.css` | https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css |

sha256 of the committed copies (fetched 2026-11-06; `shasum -a 256` to check):

```
0a17719a72751704861215da0e5c5cdb3f9a8d50eff5cb84cb6f8b80786682b0  plotly-2.32.0.min.js
db49d009c841f5ca34a888c96511ae936fd9f5533e90d8b2c4d57596f4e5641a  leaflet.js
a7837102824184820dfa198d1ebcd109ff6d0ff9a2672a074b9a1b4d147d04c6  leaflet.css
066daca850d8ffbef007af00b06eac0015728dee279c51f3cb6c716df7c42edf  images/layers-2x.png
1dbbe9d028e292f36fcba8f8b3a28d5e8932754fc2215b9ac69e4cdecf5107c6  images/layers.png
00179c4c1ee830d3a108412ae0d294f55776cfeb085c60129a39aa6fc4ae2528  images/marker-icon-2x.png
574c3a5cca85f4114085b6841596d62f00d7c892c7b03f28cbfa301deb1dc437  images/marker-icon.png
264f5c640339f042dd729062cfc04c17f8ea0f29882b538e3848ed8f10edb4da  images/marker-shadow.png
1e4e1d22972a3926f48598e0caf14e3fe7049835d428a344fed4f9e3665b3508  leaflet.markercluster.js
614dea0a98ff3f4ead74f04918f6b1d1b9ba435c25b5fc23b21a394d1e3e4d87  MarkerCluster.css
61258232d98d64dc2a7b1e02130d67421bc5b9bda5994eef70228ff97570c170  MarkerCluster.Default.css
```

Licenses: Plotly.js MIT, Leaflet BSD-2-Clause, Leaflet.markercluster MIT (keep each file's header).
To upgrade, change the version in the file name/URL here and in `base.html` together.
