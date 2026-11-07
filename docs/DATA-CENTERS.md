# Data centers and crypto mining in West Virginia: what the public record shows

Residents keep asking about **water, energy and noise**. This page is desk research done on
2026-11-05. It lists every data center and crypto-mining site we could find (proposed,
permitted, under construction or operating), what public records say about each, and,
just as important, **what nobody has disclosed**.

Data file: [`src/airwv/data/data_centers.json`](../src/airwv/data/data_centers.json). It
carries the usual provenance block (`source`, `scope`, `disclaimer`, `fetched_at`) and lives
next to the other layers. `data/` is gitignored, so the file isn't kept there.

**On the site:** each entry is a `data_center` site (read model layer `data_centers`).
`/site/data_center/<id>` is its page: a plain card by default (water, energy, noise and
what it's for: what we know / what's not disclosed, with sources, and where to ask), and
`?view=adv` for the full record, its filings and the statewide dockets. **Near me** shows
the same card for sites within 10 miles (or the chosen radius, if larger) and for sites in
the nearest county that have no public coordinates, and puts mapped ones on its map.
API: `/api/data-centers[?lat=&lon=&km=]`; `/api/query?kind=data_center`.

**How to read the values**
- **not disclosed**: the developer or agency hasn't made this public.
- **none found**: we searched public records and news and found nothing. That isn't proof
  nothing exists.
- **not applicable**: the question doesn't fit, e.g. water withdrawal at an air-cooled site.
- Every source is tagged `primary` (agency, court or SEC record), `company`, `news` or
  `tracker` (aggregator sites like Cleanview, Compute Atlas, Data Center Map). News and
  tracker claims can be wrong or out of date. Where sources disagree, the file says so.
- **Use** labels (generative AI / cloud / crypto mining / storage / government or
  surveillance / enterprise / unknown) are our read of the evidence, with a confidence. They
  aren't findings. A site can have more than one.

---

## Watch list: open comment periods and hearings

**Last checked: 2026-11-07**, against the DEP notices and dockets already cited in the data
file. Dates are as the notice states them. Agencies can extend a deadline or add a hearing, so
check the notice before you rely on a date.

**Open now**

| Date | What | Site | How to take part | Source |
|---|---|---|---|---|
| 2026-11-13 | Written comments close on air permit **R14-0042** (major source / PSD; DEP's preliminary determination 2026-09-28) | Monarch, Mason | Written comments to WV DEP, as the notice says. The same notice covers state waters permits SWP-26-0002/0003; their deadline isn't recorded here | [DEP notice, 2026-09-29](https://dep.wv.gov/news/Pages/Public-Comment-Period-and-Public-Participation-Opportunities-for-Monarch-Cloud-Campus.aspx) |

**Other dates to watch** (not comment periods)

| Date | What | Site | Source |
|---|---|---|---|
| 2026-11-15 | Listed start date for 148 acres of grading under stormwater permit WVR113565 | Bedington, Berkeley | the site's sources in the data file |
| ~March 2027 | PSC decision expected on 26-0108-E-CN (Mon Power 1,200 MW gas plant at Fort Martin); the hearing was July 2026 | statewide | [docket entry](#energy-the-psc-dockets) |
| 2027 session | PSC's HB 2014 power rules (26-0633-E-GI) go to the Legislative Rule-Making Review Committee, then the Legislature | statewide | [docket entry](#energy-the-psc-dockets) |
| no date found | Ridgeline permit opponents say they'll go to the WV Supreme Court after the 2026-08-26 dismissals | Ridgeline, Tucker | WV Highlands Conservancy, 2026-08-28 |

**Recently closed:** Adams Fork R13-3714/R13-3715 (DEP public meeting August 2025; permits
issued 2025-10-02 after 119 comments). Ridgeline R13-3713 (issued 2025-08-15; upheld by the
Air Quality Board 2026-02-05).

**How to refresh it:** check DEP's DAQ permit applications and notices page
(<https://dep.wv.gov/daq/permitting/Pages/NSR-Permit-Applications.aspx>), DEP news
(<https://dep.wv.gov/news/>) and events (<https://dep.wv.gov/events/>) for any permit
number or operator on this page, plus the Air Quality Board and PSC links under *Where to look
next*. Add each new comment period or hearing with its notice date and link (and the notice to
that site's `sources`), move past dates to *Recently closed*, and update *Last checked*.
`tests/test_data_centers.py` fails if a comment deadline in the data file is missing here.

---

## Why so much is missing: HB 2014

The 2025 Power Generation and Consumption Act (HB 2014) and its rule 145 CSR 20 (authorized
by HB 4983, 2026) create **High Impact Data Center** (HIDC, at least 90 MW) and **certified
microgrid district** programs run by the Commerce Department's Data Economy Office. For
certified projects:
- Counties and cities **can't apply** zoning, noise, lighting or land-use ordinances.
- Microgrid power is outside PSC rate and certificate jurisdiction.
- **Certification petitions are confidential.** The state publishes no list of certified
  projects. We built ours from governor's releases and news.

Bills to restore local control (HB 4509, SB 658, HB 5620) didn't pass in 2026. The PSC's
rules for HB 2014 power (26-0633-E-GI) go to the Legislature in 2027.

The **WV DEP air permit** is usually the only primary record with engineering detail
(engines, fuel, emissions). DEP has said it can't weigh water or noise in an air permit, and
can't require a developer to say what a facility is for.

---

## The big proposals (gigawatt scale)

| Site | County | Operator | Status | Power | Water | Use (confidence) |
|---|---|---|---|---|---|---|
| Monarch Compute Campus | Mason | Nscale (ex-Fidelis) | under construction; HIDC | 2,160 MW **off-grid gas**: 864 engines, R14-0042 | Ohio River (limited); volume not disclosed; closed loop | generative AI (high) |
| Project Tamarack | Mason | Starwood Digital Ventures | HIDC certified | ~2,160 MW **grid** (Appalachian Power) | not disclosed; closed loop | unknown |
| Ridgeline Facility | Tucker | Fundamental Data LLC | air permit R13-3713 upheld; appeals continue | turbine MW confidential (785 MW reported); gas + diesel | well/mine water; volume not disclosed | unknown (AI low) |
| Adams Fork (Twisted Gun) | Mingo | TransGas Development Systems | air permit R13-3715 | 2,457 MW off-grid gas engines | mine pool; volume not disclosed | unknown |
| Adams Fork (Harless) | Mingo | TransGas Development Systems | air permit R13-3714 | 2,457 MW off-grid gas engines | mine pool; volume not disclosed | unknown |
| Bedington Campus | Berkeley | Penzance Management | first HIDC (Feb 2026) | 600 MW IT, grid (Potomac Edison) | reclaimed wastewater, up to 60% | cloud / AI (low) |
| Google, Buffalo | Putnam | Google | HIDC (Mar 2026) | MW not disclosed; grid (Appalachian Power) | not disclosed | cloud (medium) |

What each one's public record says:

- **Monarch (Mason):** best documented. DEP's notice gives potential emissions of NOx 399,
  CO 985, VOC 899, HAPs 619 tons/yr and 11 million tons/yr CO2e. Besides the 864 gas engines
  there are **12 diesel emergency generators** and 6 diesel fire pumps. A neighbor reported
  **up to 94 dB** of construction noise "for hours at a time" (HuffPost; method not given).
  DEP issued sediment and dust violation notices in May–Aug 2026 and an enforcement order in
  July. Microsoft has a 1.35 GW letter of intent. Bloomberg reports an Anthropic deal; the
  companies haven't confirmed it. Written comments on R14-0042 are due **2026-11-13** per
  DEP.
- **Tamarack (Mason):** grid-connected through existing APCo/AEP infrastructure on the
  property. No tenant, water source or permits found.
- **Ridgeline (Tucker):** DEP permitted it as a *synthetic minor* source. The Air Quality
  Board upheld the permit (25-02-AQB), found that potential emissions "greatly exceed" the
  permit limits, and added stack testing. Turbine count, model and MW are redacted as trade
  secrets. The permit allows three 10-million-gallon diesel tanks. The application says the
  plant won't sell to the grid, but its plot plan shows a 138 kV line out. The company calls
  the data centers "conceptual." DEP's evaluation says there's "not definitive information
  on what Fundamental Data's end game is." Davis held a first reading of a 50 dB(A) noise
  ordinance (Aug 2026); adoption isn't confirmed.
- **Adams Fork (Mingo):** two identical off-grid plants, each 117 × 21 MW engines that can
  also burn diesel (40 tanks on site). They're classed as minor sources, so no dispersion
  modeling was required. The engine make is confidential. Cooling is a closed loop on mine
  pool water. A residents' federal suit (3:25-cv-714) was dismissed, the claims against the
  federal agencies without prejudice. Not built.
- **Bedington (Berkeley):** a stormwater permit for 148 acres of grading (WVR113565) lists a
  start date of 2026-11-15. Part of the site is in Opequon Creek flood zones.
- **Google (Putnam):** "100% of electricity" means Google **pays for** its use. The "120% of
  water" figure is Google's company-wide replenishment goal, not a site commitment. The county
  passed a non-binding resolution (2026-09-29) asking for water studies, sound enclosures and
  dark-sky lighting.

## Smaller or earlier-stage proposals

- **QTS Kearneysville** (Berkeley/Jefferson, Blackstone): concept plan approved March 2025.
  No MW published.
- **Summit Point** (Jefferson, Ryan Stuart Development, ~235 acres): presented to the Charles
  Town Utility Board in April 2026.
- **Hog Lick Aggregates, White Hall** (Marion): feasibility study for 1 GW+ that would be
  gas-powered and cooled with flooded mine water.
- **Silicon Foundation, Warwood** (Ohio Co.): first reported as 100 MW; the company now says
  it will build modules.
- **Pleasants Power Station** (Willow Island): Digi Power X non-binding AI/HPC load study up
  to 1.3 GW, plus Bitari's ~100 MW bitcoin hosting plan. Both stalled after the plant owner's
  Chapter 11 (July 2026).
- **NETL Computational Science & Engineering Center** (Morgantown, DOE): planned, per a
  tracker only.
- **Shady Spring** (Raleigh): rezoning tabled indefinitely in January 2024.

## Crypto mining

| Site | County | Status | Power | Cooling / water | Confidence it's crypto |
|---|---|---|---|---|---|
| Blockchain Power Corp.: Blacksville No. 2, Miracle Run | Monongalia | operating | 107 MW across all five sites; per-site not disclosed | Blacksville water-cooled (Monongahela River); others air-cooled with big fans | high |
| Blockchain Power Corp.: Hazelton, Tunnelton | Preston | operating | (same) | air-cooled | high |
| Blockchain Power Corp.: Ben's Run | Tyler | operating | (same) | air-cooled | high |
| Grant Town Power Plant (AMBIT) | Marion | containers photographed Apr 2026 | waste-coal plant | not disclosed | medium |
| Bitari, Pleasants | Pleasants | stalled | ~100 MW behind the meter (coal) | not disclosed | high (planned) |
| Blockware SunPark, former Hobet mine | Boone/Lincoln | announced 2022; likely dormant | 150 MW planned, grid + solar | immersion (tracker, unverified) | high (planned) |
| Marion Energy Partners "science facility" | Monongalia | air permit R13-3533 (2022) | 4 gas engines from a well pad | not disclosed | low (purpose never disclosed) |
| BOTS Inc. | not disclosed | lease announced 2021; likely never built | not disclosed | not disclosed | low |

We found **no noise complaints or measurements** for any WV crypto site. The air-cooled
sites are the type that draws complaints elsewhere. A 2022 Washington Post noise story
sometimes cited as West Virginia is actually about Limestone, Tennessee.

## Government, enterprise and colocation (operating)

- **FBI CJIS, Clarksburg:** criminal-justice databases and biometrics, with diesel generators
  in an adjacent power plant. An FBI solicitation for AI servers names it as the delivery
  point. *Government / surveillance, high.*
- **IRS Enterprise Computing Center, Kearneysville:** the IRS Master File. It was on a 2025
  disposal list; the outcome is unconfirmed.
- **USCG Operations Systems Center, Kearneysville:** all Coast Guard IT systems; opened 2014.
- **NASA Katherine Johnson IV&V Facility, Fairmont:** small on-site data center.
- **WV Office of Technology:** state data centers in Charleston and Sutton.
- **Continent 8** (South Charleston; Petersburg, Grant County): regulated online-gaming
  hosting.
- **Small colocation:** Citynet (Bridgeport, Charleston, Morgantown), DC Corp (Martinsburg),
  Dedispec (Berkeley Springs), Alpha Technologies and SecureNet (South Charleston), Pinnacle
  and Cogent (Charleston), WVNET (Morgantown). Listed from directories, not researched one by
  one.

## Energy: the PSC dockets

| Docket | What | Status |
|---|---|---|
| 24-0611-E-T-PW | Appalachian Power large-load tariff. APCo said data centers could add 8,000 MW. Google settlement: 100 MW/site, 12-year minimum, 80% minimum demand | reportedly approved March 2025 (order not found) |
| 26-0108-E-CN | Mon Power 1,200 MW gas plant at Fort Martin. PSC staff call data center load "the primary reason" for it | decision expected ~March 2027 |
| 26-0633-E-GI | PSC rules for HB 2014 power (the 69% coal capacity factor is now a goal) | to Legislature 2027 |
| 21-0658-E-ENEC | Grant Town plan to become a 95.7 MW crypto mine | rejected 2021 |

Appalachian Power told Virginia regulators (July 2026) it has contracted **1,080+ MW** of
WV data center load, customers unnamed. Mon Power says it's in talks with three unnamed
data center developers.

## What doesn't exist (yet)

- **No water-withdrawal volume** for any site. We found no WV DEP water-use (large-quantity
  user) report or withdrawal permit tied to a data center. *We didn't query DEP's water-use
  database directly. That's the next step.*
- **No operating noise measurement** anywhere in WV. The only figure is a neighbor's
  construction reading at Monarch.
- **No official list** of HIDC or microgrid certifications. Commerce's program page lists
  none; Monarch calls itself a certified AI microgrid, but we found no record of it.
- **No named tenant** anywhere except Monarch (Microsoft LOI; Anthropic reported).
- **No DEP air permit** found for the grid-connected campuses (Tamarack, Bedington, Google,
  QTS), so their backup generators are not disclosed. Hyperscale campuses usually need diesel
  generator permits before operating, so watch DEP's DAQ application list.

## Where to look next (all public, no keys)

- WV DEP DAQ permit applications and notices:
  <https://dep.wv.gov/daq/permitting/Pages/NSR-Permit-Applications.aspx>. The engineering
  evaluations for R14-0042, R13-3713, R13-3714 and R13-3715 are the best primary detail. We
  couldn't read some of the PDFs.
- WV DEP water-use reporting (large-quantity users) and NPDES/stormwater permit search.
- WV PSC case search: <https://www.psc.state.wv.us/> (search operator names).
- WV Air Quality Board appeals: <https://aqb.wv.gov/>.
- Commerce Data Economy Office: <https://westvirginia.gov/datacenters/>.

Sources for every fact are in the JSON, per site.
