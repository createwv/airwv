# Glossary — one word, one meaning

The site uses each of these words for one thing only ([DIRECTION.md](DIRECTION.md) §3). Pages,
nav, popups, API docs and plan items should use them the same way. Add a term here before using a
new one on the site. `tests/test_terms.py` fails if "source" appears alone in page text, nav or popups.

| Term | Means | Where it comes from |
|---|---|---|
| **sensor** | a community air device (PurpleAir and similar), set up by residents or partners; provisional, corrected where we can | PurpleAir API, partner uploads |
| **monitor** | an EPA / WV DEP reference air monitor, the regulatory-grade instrument the sensors are checked against | AirNow / EPA AQS |
| **gauge** | a USGS stream gauge reporting water conditions (pH, oxygen, conductance, flow) in near real time | USGS Water Services |
| **sample site** | a place where water samples were taken and lab-tested, at intervals rather than live | Water Quality Portal (WQP) |
| **facility** | a regulated place with public records: permits, reported releases, inspections, violations (power plants, plants, mines, discharges, data centers, …) | EPA ECHO, TRI, WV DEP |
| **data source** | where we got a piece of data (an agency, a feed, a file). Never "source" alone: on its own it reads as a pollution source | see [DATA-SOURCES.md](DATA-SOURCES.md) and `/data` |

## Notes

- The page that lists facilities is **Facilities** (`/facilities`; `/sources` still works for old links).
- "Reading" is one value from a sensor, monitor or gauge; "sample" is one lab result from a sample site.
- Health and legal words (e.g. *unhealthy*, *violation*) follow the agency's own definition and link to it; see `/learn`.
