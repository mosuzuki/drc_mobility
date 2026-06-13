#!/usr/bin/env python3
"""Generate final-size projections for the DRC Ebola dashboard.

The browser should only read the JSON produced by this script.  The calculation is
run by GitHub Actions after SitRep data are updated.
"""
from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
REPORT_SUMMARY = ROOT / "data" / "report_summary.csv"
OUTPUT = ROOT / "data" / "final_size_projection.json"

SEED = 20260613
N_SIM = 800
MAX_FUTURE_DAYS = 260
END_ZERO_DAYS = 42
MIN_REPORT_DATES = 5
DISPERSION_K = 0.35


@dataclass
class ReportPoint:
    report_no: str
    reporting_date: date
    publication_date: str
    cumulative: float


def parse_date(s: str) -> date | None:
    try:
        return datetime.strptime(str(s).strip()[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def read_reports() -> list[ReportPoint]:
    out: list[ReportPoint] = []
    with REPORT_SUMMARY.open(newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            d = parse_date(r.get("reporting_date", ""))
            try:
                c = float(r.get("drc_confirmed_cases") or 0)
            except Exception:
                c = 0.0
            if d and c > 0:
                out.append(ReportPoint(str(r.get("report_no") or ""), d, str(r.get("publication_date") or ""), max(0.0, c)))
    out.sort(key=lambda x: x.reporting_date)
    return out


def add_days(d: date, n: int) -> str:
    return (d + timedelta(days=int(n))).isoformat()


def interpolate_daily(points: list[ReportPoint]) -> list[dict]:
    rows: list[dict] = []
    prev_d: date | None = None
    prev_c = 0.0
    for p in points:
        if prev_d is None:
            rows.append({"date": p.reporting_date.isoformat(), "incidence": p.cumulative, "cumulative": p.cumulative, "report_no": p.report_no})
        else:
            gap = max(1, (p.reporting_date - prev_d).days)
            inc = max(0.0, p.cumulative - prev_c)
            daily = inc / gap
            for j in range(1, gap + 1):
                rows.append({
                    "date": (prev_d + timedelta(days=j)).isoformat(),
                    "incidence": daily,
                    "cumulative": prev_c + daily * j,
                    "report_no": p.report_no if j == gap else "",
                })
        prev_d = p.reporting_date
        prev_c = p.cumulative
    return rows


def gamma_pdf(x: float, shape: float, scale: float) -> float:
    if x <= 0 or shape <= 0 or scale <= 0:
        return 0.0
    return math.exp((shape - 1.0) * math.log(x) - x / scale - math.lgamma(shape) - shape * math.log(scale))


def generation_interval_weights(mean: float = 12.0, sd: float = 5.0, max_lag: int = 40) -> np.ndarray:
    shape = (mean / sd) ** 2
    scale = (sd * sd) / mean
    w = np.zeros(max_lag + 1, dtype=float)
    for k in range(1, max_lag + 1):
        w[k] = gamma_pdf(k - 0.5, shape, scale)
    s = w.sum()
    return w / s if s > 0 else w


def infectiousness_1d(inc: list[float], t: int, w: np.ndarray) -> float:
    max_lag = min(len(w) - 1, t)
    if max_lag <= 0:
        return 0.0
    return float(sum(max(0.0, inc[t - lag]) * w[lag] for lag in range(1, max_lag + 1)))


def estimate_rt(incidence: list[float], w: np.ndarray, window: int = 10) -> dict:
    n = len(incidence)
    start = max(1, n - window)
    num = sum(max(0.0, incidence[t]) for t in range(start, n))
    den = sum(infectiousness_1d(incidence, t, w) for t in range(start, n))
    prior_shape, prior_rate = 1.2, 1.2
    shape = prior_shape + num
    rate = prior_rate + den
    return {"shape": shape, "rate": rate, "mean": shape / rate if rate > 0 else 1.0, "cases_window": num, "infectiousness_window": den}


def rt_path(scenario: str, sampled_rt: np.ndarray, h: int) -> np.ndarray:
    r = np.clip(sampled_rt, 0.05, 4.0)
    if scenario == "improved":
        target, transition = 0.55, 21.0
        return target + (r - target) * np.exp(-h / transition)
    if scenario == "delayed":
        r0 = np.minimum(3.0, r * 1.03)
        if h <= 21:
            return r0
        target, transition = 0.90, 60.0
        return target + (r0 - target) * np.exp(-(h - 21) / transition)
    target, transition = 0.75, 35.0
    return target + (r - target) * np.exp(-h / transition)


def simulate(points: list[ReportPoint], scenario: str, rng: np.random.Generator) -> dict | None:
    daily = interpolate_daily(points)
    if len(points) < MIN_REPORT_DATES or len(daily) < 8:
        return None
    w = generation_interval_weights(12, 5, 40)
    obs_inc = np.array([max(0.0, float(r["incidence"])) for r in daily], dtype=float)
    rt = estimate_rt(obs_inc.tolist(), w, 10)
    selected = points[-1].reporting_date
    current_cum = max(0.0, points[-1].cumulative)

    sampled_rt = rng.gamma(shape=rt["shape"], scale=1.0 / max(rt["rate"], 1e-12), size=N_SIM)
    inc = np.repeat(obs_inc[None, :], N_SIM, axis=0)
    cum = np.full(N_SIM, current_cum, dtype=float)
    traj = np.zeros((N_SIM, MAX_FUTURE_DAYS), dtype=float)
    zero_run = np.zeros(N_SIM, dtype=np.int16)
    end_offset = np.full(N_SIM, MAX_FUTURE_DAYS, dtype=np.int16)
    end_seen = np.zeros(N_SIM, dtype=bool)

    # Simulate day by day, vectorized across simulations.
    for h in range(1, MAX_FUTURE_DAYS + 1):
        max_lag = min(len(w) - 1, inc.shape[1])
        recent = inc[:, -max_lag:]
        weights = w[1:max_lag + 1][::-1]
        infectious = recent @ weights
        mu = np.maximum(0.0, rt_path(scenario, sampled_rt, h) * infectious)
        # Gamma-Poisson mixture for negative binomial overdispersion.
        lam = rng.gamma(shape=DISPERSION_K, scale=np.where(mu > 0, mu / DISPERSION_K, 0.0))
        new = rng.poisson(lam)
        cum += new
        traj[:, h - 1] = cum
        inc = np.concatenate([inc, new[:, None].astype(float)], axis=1)
        zero_run = np.where(new == 0, zero_run + 1, 0)
        newly_ended = (~end_seen) & (zero_run >= END_ZERO_DAYS)
        end_offset[newly_ended] = h - END_ZERO_DAYS + 1
        end_seen |= newly_ended

    wanted = sorted(set(list(range(1, 22)) + list(range(28, MAX_FUTURE_DAYS + 1, 7)) + [MAX_FUTURE_DAYS]))
    trajectory = []
    for h in wanted:
        vals = traj[:, h - 1]
        trajectory.append({
            "date": add_days(selected, h),
            "median": round(float(np.quantile(vals, 0.50)), 1),
            "q25": round(float(np.quantile(vals, 0.25)), 1),
            "q75": round(float(np.quantile(vals, 0.75)), 1),
            "q05": round(float(np.quantile(vals, 0.05)), 1),
            "q95": round(float(np.quantile(vals, 0.95)), 1),
        })
    fs = traj[:, -1]
    return {
        "scenario": scenario,
        "rt": {
            "median": round(float(np.quantile(sampled_rt, 0.50)), 3),
            "q025": round(float(np.quantile(sampled_rt, 0.025)), 3),
            "q975": round(float(np.quantile(sampled_rt, 0.975)), 3),
            "prob_gt_1": round(float((sampled_rt > 1.0).mean()), 4),
            "cases_window": round(float(rt["cases_window"]), 1),
            "infectiousness_window": round(float(rt["infectiousness_window"]), 1),
        },
        "final_size": {
            "median": round(float(np.quantile(fs, 0.50))),
            "pi50": [round(float(np.quantile(fs, 0.25))), round(float(np.quantile(fs, 0.75)))],
            "pi90": [round(float(np.quantile(fs, 0.05))), round(float(np.quantile(fs, 0.95)))],
        },
        "end_date": {
            "median": add_days(selected, round(float(np.quantile(end_offset, 0.50)))),
            "pi90": [add_days(selected, round(float(np.quantile(end_offset, 0.05)))), add_days(selected, round(float(np.quantile(end_offset, 0.95))))],
        },
        "trajectory": trajectory,
    }


def main() -> None:
    reports = read_reports()
    rng = np.random.default_rng(SEED)
    dates: dict[str, dict] = {}
    for i in range(len(reports)):
        subset = reports[: i + 1]
        if len(subset) < MIN_REPORT_DATES:
            continue
        key = subset[-1].reporting_date.isoformat()
        dates[key] = {
            "report_no": subset[-1].report_no,
            "reporting_date": key,
            "current_cumulative_cases": round(subset[-1].cumulative),
            "scenarios": {s: simulate(subset, s, rng) for s in ["baseline", "improved", "delayed"]},
        }
    latest = reports[-1] if reports else None
    payload = {
        "updated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "method": "renewal_branching_process_negative_binomial",
        "source": "data/report_summary.csv",
        "source_sitrep": latest.report_no if latest else "",
        "report_date": latest.reporting_date.isoformat() if latest else "",
        "definition_of_end_date": "First date followed by 42 consecutive days with zero incident confirmed cases in the simulated reporting process.",
        "caveat": "Scenario-based projection from reported confirmed cases. Not adjusted for onset date, reporting delay, under-ascertainment, spatial heterogeneity or future response changes outside scenario assumptions.",
        "scenario_labels": {
            "baseline": {"ja": "現状維持", "en": "Baseline"},
            "improved": {"ja": "制御改善", "en": "Improved control"},
            "delayed": {"ja": "制御遅延", "en": "Delayed control"},
        },
        "scenario_definitions": {
            "baseline": "Recent Rt draw transitions toward Rt 0.75 over about 35 days.",
            "improved": "Recent Rt draw transitions toward Rt 0.55 over about 21 days.",
            "delayed": "Recent Rt is maintained for 21 days, then transitions toward Rt 0.90 over about 60 days.",
        },
        "generation_interval": {"distribution": "discretized_gamma", "mean_days": 12, "sd_days": 5, "max_lag_days": 40},
        "observation_model": {"type": "negative_binomial", "dispersion_k": DISPERSION_K, "simulations_per_scenario": N_SIM},
        "dates": dates,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUTPUT} with {len(dates)} reporting dates")


if __name__ == "__main__":
    main()
