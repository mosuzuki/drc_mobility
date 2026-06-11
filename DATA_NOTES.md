**Epidemiological data update: SitRep N26/MVB_09/06/2026, reporting date 09 June 2026, publication date 10 June 2026.**

# DRC–Uganda Bundibugyo Ebola dashboard data

Generated from:
- drc-estimated-relocations-2020_03-2026_04-v2.0-external.csv

Files to upload into the GitHub repository `data/` folder:
- monthly_flows.csv
- destinations.csv
- outbreak_zones.csv
- scenarios.csv

Processing:
- Source file is wide-format Flowminder estimated relocation data.
- Main estimate columns `est_flows_YYYY_MM` were reshaped to long format.
- Lower/upper bound columns (`_LB`, `_UB`) were not used.
- Redacted cells reported as `redacted (count <15)` were imputed as 7.5 and rounded to 8 for dashboard display.
- Blank cells were treated as 0 and omitted from `monthly_flows.csv`.
- Origins were restricted to the current outbreak proxy health zones:
  Bunia, Mongbwalu, Nyankunde, Rwampara

Caveat:
- Latitude/longitude values in `destinations.csv` are approximate fallback coordinates for dashboard visualization.
- For analytic maps, replace these with official health-zone centroids from HDX/GRID3 boundary data.
- Uganda estimates are proxy/scenario-based, using movement to selected Uganda-border DRC health zones.

## Population layer

The uploaded relocation file contains OD movement estimates only. To enable the population layer, add a Flowminder population extract at:

`data/population_by_hz.csv`

Required columns:

- `month` (e.g. `2026-04`)
- `zone_id`
- `zone_name`
- `province`
- `lat`
- `lon`
- `population`

Once this file is populated, the dashboard's Population / Movement buttons will switch between the population bubble map and the movement flow map.


## Population data added

`data/population_by_hz.csv` was generated from `drc-estimated-residents-2020_03-2026_04-v2.0-external.csv`. Columns `est_pop_YYYY_MM` were reshaped to long format. Coordinates were merged from the existing dashboard health-zone coordinate table where available. For health zones without a coordinate in the existing table, province-level approximate coordinates with small deterministic jitter were used for display only; replace with official GRID3/HDX health-zone centroids for analytical mapping.


## Optional health-zone polygon layer

To display population and population density as health-zone choropleth polygons, add a GeoJSON file at:

```text
data/health_zones.geojson
```

The GeoJSON should contain one polygon or multipolygon feature per health zone. The dashboard tries to join polygons to `data/population_by_hz.csv` using common property names such as `zone_id`, `hz_id`, `HZ_ID`, `health_zone_id`, or by health-zone name such as `zone_name`, `hz_name`, `HZ_NAME`, or `name`.

Population density is calculated in the browser as:

```text
population density = estimated population / polygon area in km²
```

If the GeoJSON has an `area_km2` property, that value is used. Otherwise, the dashboard calculates polygon area with Turf.js.

## Spread risk layer

The dashboard includes a `Spread risk` map layer. This is a mobility-based prioritization index, not a predicted transmission probability.

For each destination health zone and selected month:

```text
risk index = incoming estimated movements from selected outbreak health zone(s)
```

If `data/health_zones.geojson` is provided, health zones are colored as polygons. If not, the dashboard falls back to proportional risk bubbles using available latitude/longitude coordinates.

To make this epidemiologically stronger, add an outbreak intensity file in a future version, for example cases by outbreak health zone and month. Then the numerator can be weighted by case counts or transmission intensity rather than treating all outbreak health zones equally.


## Uganda projection layer

The Uganda projection layer is intentionally labelled as a scenario-based estimate. It combines DRC-side Flowminder health-zone movement toward Uganda-border proxy zones with a historical IOM DTM Uganda-DRC border Flow Monitoring Point destination profile from January-March 2020. It is not observed 2026 cross-border movement and is not a prediction of Ebola transmission.

Required file: `data/uganda_projection_profile.csv` with columns `uganda_id, uganda_name, type, district, lat, lon, weight, source_basis`. The current profile allocates projected Uganda-side movement to Bufumbira, Bukonzo, Bwamba, Padyere, Kisoro, Kampala, and other Uganda destinations using approximate weights derived from the uploaded IOM DTM dashboard summaries. Replace this file when current FMP, UNHCR, or Uganda-side settlement data become available.


## Case-count and weighted-risk layers

This version uses `data/cases_by_hz.csv`, updated from SitRep N24/MVB_07/06/2026, reporting date 07 June 2026. The file contains cumulative confirmed Ebola cases and confirmed deaths by affected health zone where ventilated in the SitRep. Ituri also includes an unventilated category, which is stored separately in `data/cases_unventilated.csv` but is not mapped as a case bubble because it cannot be assigned to a specific health zone.

New layers:

- `Cases`: shows cumulative confirmed cases as proportional red bubbles at health-zone centroids.
- `Weighted risk`: colors destination health zones by case-weighted movement pressure, defined as Σ confirmed_cases(origin) × estimated movement(origin→destination) for the selected month.


## Air-adjusted risk layer

`data/air_adjustment.csv` defines suppression factors for air-plausible destinations. Default assumptions: Kinshasa-bound long-distance air-plausible risk is multiplied by 0.25; selected regional air hubs are partially down-weighted. This reflects a scenario in which infected traveller risk via passenger flights is lower than the pre-outbreak baseline because of flight suspension/reopening and health screening. It is not observed airline passenger OD.

`data/cases_by_hz.csv` is updated from SitRep N24/MVB_07/06/2026. Unventilated Ituri cases are stored separately in `data/cases_unventilated.csv` and are not shown as bubbles.

## Contact follow-up adjustment

`data/contact_followup_by_province.csv` contains province-level contact follow-up rates from SitRep N24/MVB_07/06/2026:

- Ituri: 60.1%
- Nord-Kivu: 79.5%
- Sud-Kivu: 99.1%

The contact-adjusted risk layer uses:

`confirmed cases at selected origins × movement to destination × contact follow-up gap multiplier`

where `contact follow-up gap multiplier = 1 + max(0, 0.95 - observed follow-up rate)`.

This is a prioritization indicator, not a transmission probability.


### Origin selector and contact-adjusted risk update

The dashboard now uses mobility flows from all mappable affected health zones in SitRep N24, not only the four major outbreak zones. The origin selector recalculates Spread risk, Case-weighted risk score, Contact-adjusted risk, Air-adjusted risk, Uganda projection, and Movement using the selected origin set. One SitRep health zone, Mangala, has no matching Flowminder zone ID in the current data and is therefore included in the case layer but not used as a mobility origin.

Contact-adjusted risk uses province-level contact follow-up rates from SitRep N24 and a 95% target. Color breaks are intentionally matched to the Case-weighted risk score layer to make the impact of the adjustment visible.

## Uganda border flow and importation-pressure layers

This version adds two Uganda-side layers based on the IOM DTM Uganda Flow Monitoring — Ebola Virus Disease Outbreak snapshot for 15–24 May 2026.

- `Uganda border flow` displays observed movements at selected Uganda–DRC flow monitoring points and Uganda destination districts. These observations are indicative of selected key flows and are not a complete or statistically representative count of all cross-border movement.
- `Uganda importation pressure` combines DRC-side case-weighted movement toward Uganda-border proxy health zones with the observed Uganda destination district allocation from the IOM DTM 15–24 May 2026 snapshot. This is a prioritization score, not observed infected travel and not a transmission probability.

New data files:

- `data/uganda_fmp_flows_2026.csv`
- `data/uganda_district_flows_2026.csv`

## SitRep time series update (N14–N25)

This revision adds a report-date time series based on uploaded DRC SitReps N14 through N25.

- The case file `data/cases_by_hz.csv` now stores health-zone-level cumulative confirmed cases by reporting date.
- `data/report_summary.csv` stores report-level DRC confirmed cases and confirmed deaths, plus the Uganda figures used in the dashboard.
- `data/cases_unventilated.csv` stores unventilated / unknown-health-zone cases separately. These are included in report-level totals where available but are intentionally excluded from mappable case bubbles and map-based risk layers because they cannot be assigned to a health zone.
- The new map slider displays either cumulative cases at the selected reporting date or the recent increase between the selected date and the closest available SitRep at least seven days earlier.
- Weighted risk, contact-adjusted risk, air-adjusted risk, and Uganda importation-pressure layers use the same selected case definition as the Cases layer.

Important caveat: most SitReps provide clear health-zone cumulative tables. For N14 and N16, the uploaded report text did not provide a complete health-zone cumulative table in the parsed content available here. The dashboard therefore uses a partial reconstruction from adjacent SitReps and report-level totals for those dates. These dates should be interpreted cautiously for health-zone-level visualization.


## SitRep timeline visibility fix

This version moves the SitRep time-point slider to a prominent position immediately above the map-layer buttons and initializes the static KPI cards to SitRep N25: 598 DRC confirmed cases and 115 confirmed deaths, reporting 08 Jun 2026.

## SitRep timeline expansion (N1–N25)

This version extends the epidemiological time series from the first uploaded SitRep through SitRep N25.

- Earliest reporting date included: 14 May 2026 (SitRep N1; 8 confirmed cases).
- Latest reporting date included: 08 June 2026 (SitRep N25; 598 confirmed cases and 115 confirmed deaths).
- The map slider uses the available reporting dates from `data/report_summary.csv` and `data/cases_by_hz.csv`.
- The right-side cumulative case chart uses official DRC total confirmed cases from `data/report_summary.csv`; the selected slider date is highlighted on the chart.
- `data/cases_by_hz.csv` contains mappable health-zone rows. Cases reported as unassigned, no case form, or non-ventilated are stored in `data/cases_unventilated.csv` and are intentionally excluded from mappable risk layers.
- For early SitReps that reported totals but did not provide complete cumulative health-zone tables, health-zone allocation is reconstructed from the available daily increments and adjacent SitReps where necessary. Such rows include notes in the `notes` column.

## Relative Wealth Index extension

`data/health_zone_rwi.csv` was derived from the uploaded `cod_relative_wealth_index(1).csv` by spatially joining RWI point/grid values to DRC health-zone polygons. For each health zone the dashboard stores mean, median, interquartile range, min/max, average RWI error, and the number of RWI points within the polygon.

The dashboard uses the median health-zone RWI for the map and scatter plot. Higher RWI values indicate relatively wealthier areas within DRC. This is an ecological contextual indicator and should not be interpreted as individual socioeconomic status or as a causal determinant of Ebola case occurrence.

The RWI scatter plot compares health-zone RWI with cumulative confirmed cases, or confirmed cases per 100,000 population, for the selected SitRep reporting date. Correlations are shown as exploratory Pearson and Spearman coefficients and are not adjusted for population mobility, surveillance intensity, healthcare access, or distance from the outbreak origin.

## Relative Wealth Index percentile update

The dashboard now displays Relative Wealth Index (RWI) as a within-DRC percentile rather than the original standardized RWI value. The original RWI values are retained in `data/health_zone_rwi.csv` (`rwi_median`, `rwi_mean`, and `rwi_original_median`), while `rwi_percentile` is used for the map, ranking, and scatter plot. Percentile values range from 0 to 100, where higher values indicate health zones that are relatively wealthier within DRC.

The RWI scatter plot defaults to affected health zones only and cases per 100,000 population. All health zones can still be shown; in that mode, zero-case health zones are displayed as small, transparent grey points. The plot also supports a Top 25 affected view and a log1p y-axis scale.

## Short-term projection model

This version adds a right-panel **Short-term projection** chart below the reported cumulative cases chart.
The projection is generated in the browser from `data/report_summary.csv` using a simple renewal / branching-process model:

- Daily reported confirmed cases are derived from SitRep cumulative totals. When there is a gap between SitRep dates, the cumulative increment is spread evenly across the intervening reporting days.
- The generation-interval distribution is a discretized gamma distribution. The default mean is 12 days with SD approximately 5 days. Sensitivity options with mean 9 and 15 days are available in the dashboard.
- Rt is estimated from the most recent 10 days using the renewal equation and a weak gamma prior.
- Future daily cases are simulated with a gamma-Poisson / negative-binomial branching process with overdispersion.
- The chart shows median, 50% prediction interval, and 90% prediction interval for 7 or 14 days after the selected SitRep date.

The projection uses reporting-date data, not onset-date data, and does not adjust for reporting delays, changes in case definitions, or changes in control measures. It should be interpreted as a short-term scenario under recent transmission/reporting conditions, not as a prediction of final outbreak size.

## Response indicators

`data/response_indicators.csv` was manually structured from uploaded SitRep response sections. Fields include `contacts_under_followup`, `contacts_seen`, `contact_followup_rate`, `alerts_reported`, `alerts_investigated`, `alert_investigation_rate`, `samples_analysed`, `positive_samples`, `travellers_total`, `travellers_screened`, and `poe_screening_coverage`.

Important limitations:

- Administrative level varies by SitRep: national, province-level, and operational-site summaries are mixed.
- Missing values indicate that the field was not extracted or not reported in the SitRep, not that the activity was absent.
- Response intensity is a simple exploratory composite of available contact follow-up, alert-investigation, and PoE/PoC screening coverage indicators.
- Contact tracing gap is defined as `max(0, 0.95 - contact_followup_rate)` and is intended as a gap indicator relative to a 95% operational target.

## AI-assisted assessment logic

The assessment cards are generated from predefined rules in `assets/app.js`. They do not use an external AI API and do not produce an official risk classification. The local trajectory card uses recent reported incidence, renewal-model Rt, P(Rt>1), and contact-tracing gap. The capital-region card uses Kinshasa-linked cases and air-adjusted case-weighted mobility indicators. The cross-border card uses Ituri case burden, Uganda-border importation pressure, and PoE/PoC screening coverage. Categories are intended for rapid interpretation only and should be reviewed by public health experts.

Alert investigation coverage and PoE/PoC screening coverage are retained in `response_indicators.csv` and the response timeline. They are no longer offered as choropleth map layers because they are usually reported at national, province, or operational-site level rather than consistently by health zone.

## Automated update pipeline

The dashboard can be updated automatically from INSP SitRep articles using `scripts/update_from_insp_sitrep.py` and the scheduled workflow `.github/workflows/update-sitrep.yml`.

The auto-extractor updates these files when a newer SitRep passes validation:

- `data/report_summary.csv`
- `data/cases_by_hz.csv`
- `data/cases_unventilated.csv`
- `data/response_indicators.csv`
- `raw/sitreps/sitrep_N###.pdf` for audit
- `extracted/sitrep_N###.txt` and `extracted/latest_sitrep_update.json` for audit

Validation is intentionally conservative. The update is blocked if:

- no newer SitRep is found;
- the embedded PDF cannot be downloaded;
- total confirmed cases cannot be extracted;
- health-zone counts plus unassigned cases do not match the extracted total;
- the PDF/table structure changes in a way that prevents reliable extraction.

The pipeline first uses deterministic parsing and validation. It does not call OpenAI during normal successful updates. If deterministic extraction fails and `OPENAI_API_KEY` is configured as a GitHub Actions secret, the pipeline sends the relevant PDF text/tables to OpenAI for structured JSON extraction, then validates the returned values before updating any CSV. If OpenAI is unavailable or validation still fails, the pipeline stops and opens/comments on a GitHub Issue for manual review.
