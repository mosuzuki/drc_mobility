# DRC–Uganda Bundibugyo Ebola Dashboard

**Epidemiological data update:** SitRep N24/MVB_07/06/2026, reporting date 07 June 2026, publication date 08 June 2026.

Static GitHub Pages dashboard for visualising estimated movement from Ebola outbreak health zones in eastern DRC toward:

1. Kinshasa health zones; and
2. Uganda-border proxy health zones.

The included CSV files are **illustrative sample data**. Replace them with Flowminder / HDX, HDX/GRID3, IOM DTM, UNHCR, and outbreak line-list derived extracts before analytical use.

## Live dashboard structure

- `index.html` — static page
- `assets/app.js` — dashboard logic, Leaflet map, Plotly charts
- `assets/style.css` — styling
- `data/outbreak_zones.csv` — outbreak-origin health zones
- `data/destinations.csv` — destination health zones and categories
- `data/monthly_flows.csv` — origin-destination movement matrix by month
- `data/scenarios.csv` — onward-crossing assumptions for Uganda
- `scripts/prepare_data.py` — template converter for real data
- `.github/workflows/pages.yml` — GitHub Pages deployment workflow

## Expected data schemas

### data/outbreak_zones.csv

```csv
zone_id,zone_name,province,lat,lon,is_outbreak,is_uganda_border,is_kinshasa
```

### data/destinations.csv

```csv
zone_id,zone_name,province,lat,lon,category,is_uganda_border,is_kinshasa
```

Use `is_kinshasa=1` for Kinshasa health zones. Use `is_uganda_border=1` for DRC health zones representing Uganda-border proxy destinations.

### data/monthly_flows.csv

```csv
month,origin_id,destination_id,movement
```

`movement` should be the monthly estimated movement from the origin health zone to the destination health zone.

### data/scenarios.csv

```csv
scenario_id,scenario_name,cross_border_fraction,description
```

`cross_border_fraction` is the assumed fraction of movement toward Uganda-border proxy zones that continues onward into Uganda.

## Data sources to connect

- Flowminder / HDX DRC population and mobility estimates: https://data.humdata.org/dataset/democratic-republic-of-congo-population-and-relocation-estimates
- HDX DRC health-zone boundaries: https://data.humdata.org/dataset/drc-health-data
- GRID3 DRC geospatial data: https://grid3.org/geospatial-data-drc
- IOM DTM DRC: https://dtm.iom.int/democratic-republic-congo
- UNHCR Uganda Operational Data Portal: https://data.unhcr.org/en/country/uga
- UNHCR DRC situation: https://data.unhcr.org/en/situations/drc

## How to publish on GitHub Pages

1. Create a new GitHub repository, for example `drc-ebola-mobility-dashboard`.
2. Upload all files in this folder.
3. Go to **Settings → Pages**.
4. Set **Source** to **GitHub Actions**.
5. Push to the `main` branch. The included workflow will deploy the site.

## Local preview

Because the dashboard uses `fetch()` to read local CSV files, preview it through a local web server:

```bash
python -m http.server 8000
```

Then open:

```text
http://localhost:8000
```

## Analytical interpretation

The Uganda value is not directly observed cross-border movement in the sample implementation. It is computed as:

```text
movement from outbreak health zones to Uganda-border proxy health zones
× assumed onward-crossing fraction
```

This should be calibrated with UNHCR Uganda, IOM DTM, or border-monitoring data when available.

## Recommended next steps

1. Replace `monthly_flows.csv` with Flowminder / HDX health-zone OD estimates.
2. Replace point coordinates with health-zone polygons from HDX or GRID3.
3. Add observed DRC-to-Uganda refugee or border-flow data from UNHCR/IOM for calibration.
4. Add outbreak intensity by origin health zone and compute an export pressure index:

```text
Export pressure_j,t = sum_i outbreak_intensity_i,t × movement_i,j,t
```

5. Add GitHub Actions scheduled data refresh if public downloadable data endpoints are stable.

## Limitations

- Included data are synthetic and for demonstration only.
- CDR-derived movement is affected by phone ownership, operator share, and representativeness.
- Uganda-border proxy movement should not be described as confirmed cross-border movement.
- Age- and socioeconomic-stratified movement requires additional reweighting using DHS/MICS/MAFE/UNHCR/IOM data.


## Actual Flowminder data loaded

The `data/` folder in this package has been replaced with reshaped Flowminder DRC estimated relocation data from 2020-04 to 2026-04, restricted to outbreak proxy origins: Bunia, Mongbwalu, Nyankunde, and Rwampara.
See `DATA_NOTES.md` for processing assumptions.

### Spread risk layer

The `Spread risk` button colors health zones by mobility-based Ebola spread pressure from the selected outbreak health zone(s). The current index is the estimated number of arrivals from outbreak zones to each destination health zone in the selected month. It is not divided by destination population. It is intended for relative prioritization of surveillance and preparedness; it should not be interpreted as the probability of local Ebola transmission.


## Uganda projection layer

The Uganda projection layer is intentionally labelled as a scenario-based estimate. It combines DRC-side Flowminder health-zone movement toward Uganda-border proxy zones with a historical IOM DTM Uganda-DRC border Flow Monitoring Point destination profile from January-March 2020. It is not observed 2026 cross-border movement and is not a prediction of Ebola transmission.

Required file: `data/uganda_projection_profile.csv` with columns `uganda_id, uganda_name, type, district, lat, lon, weight, source_basis`. The current profile allocates projected Uganda-side movement to Bufumbira, Bukonzo, Bwamba, Padyere, Kisoro, Kampala, and other Uganda destinations using approximate weights derived from the uploaded IOM DTM dashboard summaries. Replace this file when current FMP, UNHCR, or Uganda-side settlement data become available.


## Case-count and weighted-risk layers

This version uses `data/cases_by_hz.csv`, updated from SitRep N24/MVB_07/06/2026, reporting date 07 June 2026. The file contains cumulative confirmed Ebola cases and confirmed deaths by affected health zone where ventilated in the SitRep. Ituri also includes an unventilated category, which is stored separately in `data/cases_unventilated.csv` but is not mapped as a case bubble because it cannot be assigned to a specific health zone.

New layers:

- `Cases`: shows cumulative confirmed cases as proportional red bubbles at health-zone centroids.
- `Weighted risk`: colors destination health zones by case-weighted movement pressure, defined as Σ confirmed_cases(origin) × estimated movement(origin→destination) for the selected month.


## Air-adjusted risk layer

This version adds an Air-adjusted risk layer. It applies route-specific suppression factors in `data/air_adjustment.csv` to case-weighted movement scores for long-distance, air-plausible destinations such as Kinshasa. This is a scenario-based prioritization indicator and does not represent observed passenger OD data. Flight-route lines are not displayed on the map; the layer is shown only as a health-zone risk surface.

Case counts are updated from SitRep N24/MVB_07/06/2026. The Ituri unventilated category (94 cases, 10 deaths) is not mapped as a case bubble because it cannot be assigned to a specific health zone.

## Origin selector and contact-adjusted risk

This version adds an origin selector for recalculating movement-based risk from different sets of affected health zones:

- Major outbreak zones only
- All affected health zones
- Ituri only
- North Kivu only
- South Kivu only
- Custom selection

It also adds a `Contact-adjusted risk` layer. This layer uses the case-weighted movement score and applies an upward multiplier where province-level contact follow-up is below the 95% target. The contact follow-up inputs are stored in `data/contact_followup_by_province.csv` and are based on SitRep N24/MVB_07/06/2026.


### Origin selector and contact-adjusted risk update

The dashboard now uses mobility flows from all mappable affected health zones in SitRep N24, not only the four major outbreak zones. The origin selector recalculates Spread risk, Case-weighted risk score, Contact-adjusted risk, Air-adjusted risk, Uganda projection, and Movement using the selected origin set. One SitRep health zone, Mangala, has no matching Flowminder zone ID in the current data and is therefore included in the case layer but not used as a mobility origin.

Contact-adjusted risk uses province-level contact follow-up rates from SitRep N24 and a 95% target. Color breaks are intentionally matched to the Case-weighted risk score layer to make the impact of the adjustment visible.

### Uganda-side expansion-risk layers

This dashboard includes two Uganda-side layers:

1. **Uganda border flow** — observed IOM DTM EVD flow monitoring data for selected Uganda–DRC FMPs, 15–24 May 2026.
2. **Uganda importation pressure** — DRC case-weighted border-proxy movement allocated to Uganda destination districts using the IOM DTM 2026 destination profile.

Both layers should be interpreted for preparedness prioritization. They do not estimate transmission probability.


## Current dashboard revision

- Title updated to **DRC–Uganda Bundibugyo Ebola Dashboard**.
- Default origin set is **All affected health zones**.
- Top KPI cards show latest reported DRC cases/deaths from SitRep N24 and latest available Uganda cases/deaths from the IOM DTM EVD snapshot.
- Month is controlled by the dropdown; the map slider has been removed.

### SitRep time slider

The dashboard now includes a reporting-date slider based on uploaded SitReps N14–N25. The default is the latest SitRep. Users can switch between cumulative cases and recent one-week increase. Risk layers are recalculated using the selected case definition.


## SitRep timeline visibility fix

This version moves the SitRep time-point slider to a prominent position immediately above the map-layer buttons and initializes the static KPI cards to SitRep N25: 598 DRC confirmed cases and 115 confirmed deaths, reporting 08 Jun 2026.

### SitRep timeline update

This build includes SitRep N1–N25. The reporting-date slider controls cumulative health-zone cases, recent one-week increases, and the case-weighted risk layers. The right-side chart shows official cumulative DRC confirmed cases by reporting date and highlights the currently selected SitRep date.

### Relative Wealth Index layer

This version adds a health-zone Relative Wealth Index (RWI) layer and an exploratory RWI-versus-cases scatter plot. The RWI layer is built by spatially aggregating the uploaded DRC RWI grid/point data to health-zone polygons. The right-side scatter plot can show either confirmed cases or cases per 100,000 population at the selected SitRep reporting date.

These visualizations are intended for exploratory ecological assessment only. They do not imply that wealth causes higher or lower Ebola incidence.

### RWI percentile display

The Relative Wealth Index layer converts original standardized RWI values into within-DRC percentiles for easier interpretation. The RWI scatter plot defaults to affected health zones only and cases per 100,000 population, with controls for all-zone display, Top 25 affected zones, and log1p scaling.

### Short-term projection

The dashboard includes a short-term renewal / branching-process projection displayed directly below the cumulative case time-series chart. It updates with the selected SitRep reporting date and can be shown for 7 or 14 days using generation-interval sensitivity assumptions of 9, 12, or 15 days.

The projection is intended for situational awareness only. It is based on reported confirmed cases by SitRep date and does not adjust for onset date, reporting delay, or future changes in response intensity.

## Response indicator extension

This version adds a response-indicator layer and timeline based on available SitRep response sections. The added CSV is `data/response_indicators.csv` and includes contact follow-up, alert investigation, PoE/PoC screening, laboratory testing, and traveller screening fields when reported. The dashboard treats these as operational indicators; they are heterogeneous across SitReps and should not be interpreted as direct measures of intervention effectiveness.

Response map layers include Contact gap and Response intensity. Alert investigation and PoE/PoC screening are retained in the response timeline but are not shown as map layers. Where health-zone values are unavailable, the dashboard uses the latest province-level or national/operational value available by the selected reporting date.

## AI-assisted situational assessment

This version adds a top-level AI-assisted situational assessment panel. The panel is rule-based rather than generative: it summarizes predefined dashboard indicators for (1) local outbreak trajectory, (2) risk of spread to Kinshasa/capital region, and (3) cross-border/international spread risk. It uses recent SitRep incidence, renewal-model Rt estimates, response indicators, air-adjusted mobility risk, Uganda-border importation pressure, and PoE/PoC screening indicators. The assessment is intended for situational awareness and requires expert review; it is not an official risk assessment.

The map layer list has also been simplified. Alert investigation and PoE/PoC screening remain available in the response timeline but are no longer shown as map layers. Response map layers now focus on Contact gap and Response intensity.

## Automated SitRep updates

This repository includes a scheduled GitHub Actions workflow: `.github/workflows/update-sitrep.yml`.
It runs every 6 hours and attempts to update the dashboard from the latest INSP SitRep page.

Workflow logic:

1. Read `https://insp.cd/category/sitrep/` and identify the newest article whose title/URL contains a SitRep number.
2. Open the article page.
3. Try to obtain the embedded PDF in this order:
   - direct `.pdf` links in the HTML;
   - iframe/embed/object sources and PDF.js `file=` parameters;
   - Playwright/Chromium network responses with `application/pdf`;
   - PDF viewer download button click as a last resort.
4. Extract SitRep number, reporting date, publication date, cumulative confirmed cases/deaths, health-zone table, unassigned cases, and available response indicators using deterministic Python rules.
5. Validate that mapped health-zone cases plus unassigned cases match the national total.
6. If deterministic extraction fails validation and `OPENAI_API_KEY` is configured in GitHub Secrets, call OpenAI as a fallback to produce a structured JSON extraction from the PDF text/tables.
7. Validate the OpenAI-assisted extraction again before any CSV is updated.
8. Update the dashboard CSVs and commit the changes.
9. If the PDF cannot be downloaded, OpenAI is unavailable, or validation still fails, open/comment on a GitHub Issue titled `SitRep auto-update needs review` and do not publish the extracted values.

The workflow uses no OpenAI tokens during normal successful rule-based updates. OpenAI is used only as a fallback after deterministic extraction fails validation. To enable fallback, add `OPENAI_API_KEY` as a GitHub Actions secret. You may optionally set repository variable `OPENAI_MODEL`; otherwise the workflow uses `gpt-4.1-mini`.

Manual run:

```bash
python scripts/update_from_insp_sitrep.py
```

Local test with a downloaded PDF:

```bash
python scripts/update_from_insp_sitrep.py --pdf path/to/SitRep.pdf --force
```
