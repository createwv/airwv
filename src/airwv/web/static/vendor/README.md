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

Licenses: Plotly.js MIT, Leaflet BSD-2-Clause, Leaflet.markercluster MIT (keep each file's header).
To upgrade, change the version in the file name/URL here and in `base.html` together.
