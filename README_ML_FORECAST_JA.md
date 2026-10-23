# DRC Ebola dashboard — ML prediction update (2026-10-23)

## Overview

The existing branching process is preserved, and the obsolete analog-curve “AI” method is removed from the final-size panel. An actual LightGBM predictive model is trained on historical national, province and health-zone time series and run in GitHub Actions. The short-term panel offers 7/14/21/28-day horizons, while the other panel shows branching and hybrid LightGBM→branching scenarios. These are **experimental forecasts**, not validated policy predictions.

## Forecast pipeline

- `scripts/generate_ml_short_forecast.py` reads `data/report_summary.csv` and `data/cases_by_hz.csv`, derives incidence differences from **reported cumulative counts**, trains pooled-region LightGBM regression predicting **weekly incidence for each of the next 4 weeks**. Revisions can cause positive-difference biases; gaps between SitRep reporting dates are linearly distributed, not backdated to illness-onset. National and region timelines must agree; if they do not, forecasts are withheld.
- Features are restricted to available history at each origin (past 7, 14, 28 days; local and province and national summaries, local epidemic age; no future covariates).
- Time-forward backtest at 8 recent origins refits training using labels fully available by that origin. Results are compared with a persistence/momentum baseline. Cross-validation does **not** eliminate retrospective SitRep revisions or within-outbreak spatial correlation.
- The LightGBM forecast is combined with simulated renewal-branching samples, with a conservative weight determined by backtest evidence (0.4 if it beats actual branching MAE, otherwise 0. The separate **experimental** hybrid may use 0.2 if LightGBM beats persistence, even when it loses to branching). Ensemble uncertainty uses weighted Monte-Carlo paths, not arithmetic averages of interval endpoints. The experimental ensemble is not recommended as more accurate if LightGBM performs worse than branching. Its intervals have not received independent coverage calibration.
- `scripts/generate_final_size_projection.py` links the first **28 days** of the short forecast to daily renewal/branching simulations; days 29–260 assume future control scenarios with Rt reverting to **0.75 (baseline), 0.52 (improved) or 0.96 (delayed)**. These are **illustrative assumptions**, not causal estimates of interventions.
- A final total is a **260-day cumulative projection**. If at least 5% of simulations fail to achieve 42 consecutive days with zero cases by the 260-day horizon, the end date is withheld, instead of wrongly claiming termination at day 260.
- Retrospective dates retain the branching projection. The ML model is explicitly **latest SitRep only** because the current model is retrained on all currently recorded history; showing it at older slider positions without an as-of training snapshot would leak future information.

## Chronos-2 optional, not silently simulated

Optional pretrained Transformer code is implemented but **not run by default** on GitHub Actions. The auto-update workflow provides an `enable_chronos2` dispatch input to install `requirements-chronos-optional.txt` and download model weights. Chronos-2 is visibly disabled until its forecasts actually exist in the generated JSON. Even when manually enabled, Chronos-2 predictions are **experimental comparisons** and are not given a positive automatic ensemble weight without backtesting. CPU inference could be slow, or the model download may fail; the rest of the pipeline is not dependent on Chronos-2.

## Update process and tests

GitHub Actions now runs `python scripts/update_dashboard_all.py`, which regenerates the ML JSON **before** rebuilding linked final-size JSON and then executes `scripts/test_ml_forecast_outputs.py` and existing validation tests. New files include:

- `data/short_term_ml_forecast.json`
- `scripts/generate_ml_short_forecast.py`
- `scripts/test_ml_forecast_outputs.py`
- `scripts/requirements-chronos-optional.txt`

Limitations: model training depends on one outbreak, reporting-date incidence is not onset-date incidence, some SitReps are missing or retrospectively revised, uncertainty intervals have not been calibrated on independent outbreaks, and inter-region features are not a causal mobility transmission model. The latest origin (and data source) should always be visible in the UI.
