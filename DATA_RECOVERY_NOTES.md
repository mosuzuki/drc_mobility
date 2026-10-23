# October 2026 national series recovery

* Original ZIP national SitRep series ended at N116 / 2026-09-07; SitRep discovery/status stopped at N105.
* Dates after that are entered in `data/report_summary.csv` with **ECDC-YYYY-MM-DD** labels, and source/notes cite ECDC or documented INSP summaries. They are national-level snapshots, **not recovered full INSP SitReps**. Missing dates are **not observed daily values**; Rt/CFR plots interpolate where their algorithm does, and should be treated as approximate.
* Last externally verified DRC national snapshot entered: **2026-10-20, confirmed 9,704, deaths 4,687** (ECDC update issued 22 October 2026).
* Detailed health-zone and response tables remain at the last genuine fully extracted SitRep unless the INSP PDF is fetched later. This is deliberately labeled out-of-date in the display and must **not** be used for zone-level quarantine decisions.
* Recovery steps: GitHub Actions now invokes `update_dashboard_all.py`, attempting INSP PDF extraction, ECDC national fallback, regeneration of all downstream files and validation. Any new official PDF replaces an ECDC-only national date once ingested.
* ECDC: https://www.ecdc.europa.eu/en/public-health-events/2026-eip-00026/2026-10-22
* WHO Uganda end: https://afro.who.int/countries/uganda/news/uganda-ends-ebola-outbreak-following-completion-42-day-countdown
* Official DRC SitRep catalogue: https://sante.gouv.cd/documents/sitreps
