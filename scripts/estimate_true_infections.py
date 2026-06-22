#!/usr/bin/env python3
"""
Estimate reporting-adjusted infection size from multiple cumulative data streams.

This script is intentionally lightweight enough for GitHub Actions. It produces
one small JSON file consumed by the static dashboard. It does not run in the
browser.

Inputs:
  - data/report_summary.csv: latest DRC cumulative confirmed cases/deaths
  - data/uganda_evd_summary.csv: latest Uganda imported/local/deaths

Output:
  - data/true_infection_estimate.json

Model sketch:
  Latent infections I are linked to observed DRC confirmed cases C through an
  ascertainment probability p_confirm: C ~= I * p_confirm.
  We sample p_confirm and nuisance parameters for death observation and export
  detection, then importance-weight samples by their compatibility with observed
  DRC deaths and Uganda imported cases. The output is a reporting multiplier
  m = I / C and the corresponding estimated infection size.

The result is a scenario/uncertainty estimate, not a definitive case count.
"""
from __future__ import annotations

import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = DATA / "true_infection_estimate.json"


def read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def num(x: Any, default: float = 0.0) -> float:
    try:
        if x is None or x == "":
            return default
        return float(str(x).replace(",", ""))
    except Exception:
        return default


def latest_by_date(rows: List[Dict[str, str]], date_key: str) -> Dict[str, str]:
    good = [r for r in rows if r.get(date_key)]
    if not good:
        return rows[-1] if rows else {}
    return sorted(good, key=lambda r: str(r.get(date_key) or ""))[-1]


def log_nb_pmf(k_obs: float, mean: np.ndarray, dispersion: float) -> np.ndarray:
    """Negative-binomial log PMF with mean and NB size/dispersion.
    Variance = mean + mean^2 / dispersion.
    k_obs may be non-integer in source data; round for likelihood use.
    """
    k = int(round(max(0.0, float(k_obs))))
    mean = np.maximum(mean, 1e-9)
    r = float(max(dispersion, 1e-6))
    p = r / (r + mean)
    # lgamma form, vectorized except constants
    return (
        math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1)
        + r * np.log(p) + k * np.log1p(-p)
    )


def weighted_quantile(values: np.ndarray, weights: np.ndarray, qs: List[float]) -> List[float]:
    order = np.argsort(values)
    v = values[order]
    w = weights[order]
    cw = np.cumsum(w)
    if cw[-1] <= 0:
        return [float(np.quantile(values, q)) for q in qs]
    cw = cw / cw[-1]
    return [float(np.interp(q, cw, v)) for q in qs]


def main() -> None:
    report_rows = read_csv(DATA / "report_summary.csv")
    ug_rows = read_csv(DATA / "uganda_evd_summary.csv")
    latest = latest_by_date(report_rows, "reporting_date")
    ug = latest_by_date(ug_rows, "as_of_date")

    C = num(latest.get("drc_confirmed_cases"))
    D = num(latest.get("drc_confirmed_deaths"))
    U_imported = num(ug.get("imported_cases"), num(ug.get("cumulative_confirmed_cases")))
    U_deaths = num(ug.get("cumulative_deaths"))

    # If no valid case count is present, write a transparent placeholder.
    if C <= 0:
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "model": "multiple_data_stream_reporting_adjustment",
            "status": "insufficient_data",
            "message": "No positive DRC confirmed case count was available.",
        }
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return

    rng = np.random.default_rng(20260622)
    n = 120_000

    # Ascertainment probability for DRC confirmed reporting. The broad range is
    # deliberately transparent; the likelihood below down-weights combinations
    # inconsistent with deaths and Uganda exports.
    p_confirm = rng.beta(2.6, 9.0, n)
    p_confirm = np.clip(p_confirm, 0.055, 0.65)
    infections = C / p_confirm

    # Infection fatality / symptomatic fatality range informed by ECDC summary of
    # Epiforecasts CFR estimates (0.28--0.61), with broad uncertainty.
    cfr = rng.triangular(0.20, 0.42, 0.68, n)

    # Fraction of all eventual deaths observed/reported by the latest SitRep,
    # absorbing reporting delay and incomplete death ascertainment. This is a
    # nuisance parameter; broad range prevents overconfidence.
    death_observation_fraction = rng.beta(2.0, 5.5, n) * 0.62 + 0.04

    # Uganda imported confirmed cases as a cumulative export/detection stream.
    # Use a log-uniform prior because this probability is small and uncertain.
    log_export = rng.uniform(math.log(0.00035), math.log(0.018), n)
    p_export_detected = np.exp(log_export)

    mu_deaths = infections * cfr * death_observation_fraction
    mu_exports = infections * p_export_detected

    # Overdispersed likelihoods. Export stream is especially noisy.
    logw = log_nb_pmf(D, mu_deaths, dispersion=10.0) + log_nb_pmf(U_imported, mu_exports, dispersion=4.0)

    # Keep the prior loosely compatible with the observed confirmed/death ratio;
    # avoid pathological samples with implausibly low/high multipliers dominating.
    multiplier = infections / C
    logw += np.where((multiplier >= 1.15) & (multiplier <= 18.0), 0.0, -25.0)

    logw -= np.max(logw)
    w = np.exp(logw)
    if not np.isfinite(w).all() or w.sum() <= 0:
        w = np.ones_like(multiplier)
    w = w / w.sum()

    mult_q = weighted_quantile(multiplier, w, [0.05, 0.25, 0.5, 0.75, 0.95])
    inf_q = weighted_quantile(infections, w, [0.05, 0.25, 0.5, 0.75, 0.95])

    # Effective sample size as a simple diagnostic.
    ess = float(1.0 / np.sum(w * w))

    report_no = latest.get("report_no") or ""
    report_date = latest.get("reporting_date") or ""
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source_sitrep": report_no,
        "report_date": report_date,
        "uganda_as_of_date": ug.get("as_of_date") or "",
        "model": "multiple_data_stream_reporting_adjustment",
        "status": "ok",
        "reported_confirmed_cases": int(round(C)),
        "reported_deaths": int(round(D)),
        "uganda_imported_cases": int(round(U_imported)),
        "uganda_deaths": int(round(U_deaths)),
        "estimated_infections": {
            "median": int(round(inf_q[2])),
            "pi50": [int(round(inf_q[1])), int(round(inf_q[3]))],
            "pi90": [int(round(inf_q[0])), int(round(inf_q[4]))]
        },
        "multiplier": {
            "median": round(mult_q[2], 2),
            "pi50": [round(mult_q[1], 2), round(mult_q[3], 2)],
            "pi90": [round(mult_q[0], 2), round(mult_q[4], 2)]
        },
        "inputs": [
            "DRC cumulative confirmed cases",
            "DRC cumulative confirmed deaths",
            "Uganda cumulative imported confirmed cases",
            "Uganda cumulative deaths"
        ],
        "assumptions": {
            "drc_confirmed_reporting_probability_prior": "Beta(2.6, 9.0), clipped to 0.055-0.65",
            "cfr_prior": "Triangular(0.20, 0.42, 0.68), informed by ECDC/Epiforecasts CFR range",
            "death_observation_fraction_prior": "0.04 + 0.62 * Beta(2.0, 5.5)",
            "uganda_export_detection_probability_prior": "log-uniform 0.00035-0.018",
            "likelihood": "overdispersed negative-binomial for deaths and exported cases"
        },
        "diagnostics": {
            "samples": int(n),
            "effective_sample_size": round(ess, 1)
        },
        "external_reference": {
            "source": "ECDC overview of modelling evidence, 17 June 2026, summarising Epiforecasts estimate as of 13 June 2026",
            "reported_multiplier_pi90": [3.0, 10.2],
            "note": "External reference only; not directly used as the dashboard estimate."
        },
        "interpretation_note": "Reporting-adjusted estimate of infections, not confirmed cases. It depends on assumptions about reporting delay, CFR, and detection of exported cases in Uganda."
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)}: multiplier median {payload['multiplier']['median']}x, infections median {payload['estimated_infections']['median']}")


if __name__ == "__main__":
    main()
