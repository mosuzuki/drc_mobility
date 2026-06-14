#!/usr/bin/env python3
"""Generate final-size projections for the DRC Ebola dashboard.

The browser only reads the JSON produced by this script. The calculation is run
by GitHub Actions after SitRep data are updated.

Outputs three model layers for the dashboard:
  1. Ensemble (recommended)
  2. Branching process
  3. AI-assisted historical matching
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
HISTORICAL_LIBRARY = ROOT / "data" / "historical_matching_library.json"
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


def add_days(d: date, n: int) -> str:
    return (d + timedelta(days=int(n))).isoformat()


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
    # If duplicate reporting dates exist, keep the latest row in file order / highest cumulative.
    by_date: dict[date, ReportPoint] = {}
    for p in out:
        old = by_date.get(p.reporting_date)
        if old is None or p.cumulative >= old.cumulative:
            by_date[p.reporting_date] = p
    return [by_date[d] for d in sorted(by_date)]


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


def rt_path(sampled_rt: np.ndarray, h: int) -> np.ndarray:
    """Baseline future Rt path for the branching-process layer."""
    r = np.clip(sampled_rt, 0.05, 4.0)
    target, transition = 0.75, 35.0
    return target + (r - target) * np.exp(-h / transition)


def simulate_branching(points: list[ReportPoint], rng: np.random.Generator) -> dict | None:
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

    for h in range(1, MAX_FUTURE_DAYS + 1):
        max_lag = min(len(w) - 1, inc.shape[1])
        recent = inc[:, -max_lag:]
        weights = w[1:max_lag + 1][::-1]
        infectious = recent @ weights
        mu = np.maximum(0.0, rt_path(sampled_rt, h) * infectious)
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
        "model": "branching_process",
        "scenario": "baseline_branching_process",
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


def load_historical_library() -> dict | None:
    if not HISTORICAL_LIBRARY.exists():
        return None
    return json.loads(HISTORICAL_LIBRARY.read_text(encoding="utf-8"))


def weighted_quantile(values: list[float], weights: list[float], q: float) -> float:
    arr = sorted((float(v), max(0.0, float(w))) for v, w in zip(values, weights) if math.isfinite(float(v)) and math.isfinite(float(w)) and float(w) > 0)
    if not arr:
        return float("nan")
    total = sum(w for _, w in arr)
    target = q * total
    c = 0.0
    for v, w in arr:
        c += w
        if c >= target:
            return v
    return arr[-1][0]


def curve_value(curve: list[dict], day: int) -> float:
    if not curve:
        return 0.0
    if day <= 0:
        return float(curve[0].get("cases_cumulative") or 0)
    if day >= len(curve):
        return float(curve[-1].get("cases_cumulative") or 0)
    return float(curve[day].get("cases_cumulative") or 0)


def historical_matching(points: list[ReportPoint], library: dict | None) -> dict | None:
    if not library or len(points) < MIN_REPORT_DATES:
        return None
    daily = interpolate_daily(points)
    if len(daily) < 8:
        return None
    selected = points[-1].reporting_date
    current_cum = max(0.0, points[-1].cumulative)
    current_curve = np.array([max(0.0, float(r["cumulative"])) for r in daily], dtype=float)
    current_day = len(current_curve) - 1
    hist_features = {f["outbreak_id"]: f for f in library.get("features", [])}
    candidates: list[dict] = []

    for oid, curve in (library.get("curves") or {}).items():
        feat = hist_features.get(oid, {})
        if not curve or len(curve) < 8:
            continue
        n = min(len(current_curve), len(curve))
        hist_curve = np.array([curve_value(curve, i) for i in range(n)], dtype=float)
        cur = current_curve[:n]
        if hist_curve[-1] <= 0 or cur[-1] <= 0:
            continue
        denom = float(np.sum(hist_curve ** 2))
        scale = float(np.sum(cur * hist_curve) / denom) if denom > 0 else float(cur[-1] / hist_curve[-1])
        scale = float(np.clip(scale, 0.05, 80.0))
        pred = np.maximum(0.0, scale * hist_curve)
        # Emphasize recent part of the observed trajectory while keeping the full shape.
        day_weights = np.linspace(0.65, 1.35, n)
        rmse = float(np.sqrt(np.average((np.log1p(cur) - np.log1p(pred)) ** 2, weights=day_weights)))
        base_w = float(feat.get("base_match_weight") or 0.7)
        # Conservative extra down-weighting for the very large North Kivu-Ituri outbreak.
        if "North Kivu" in str(feat.get("outbreak_label", "")) or "NORTH_KIVU" in oid:
            base_w *= 0.60
        sim_weight = base_w * math.exp(-(rmse ** 2) / (2 * 0.45 ** 2))
        final_size = float(scale * float(feat.get("final_size_for_projection") or curve_value(curve, len(curve) - 1)))
        duration = int(feat.get("duration_days") or (len(curve) - 1))
        remaining = max(0, duration - current_day)
        candidates.append({
            "outbreak_id": oid,
            "outbreak_label": feat.get("outbreak_label") or oid,
            "year": feat.get("year"),
            "location": feat.get("location"),
            "data_quality": feat.get("data_quality"),
            "event_date_type": feat.get("event_date_type"),
            "match_comparability": feat.get("match_comparability"),
            "distance": round(rmse, 4),
            "match_weight": sim_weight,
            "scale": round(scale, 4),
            "estimated_final_size": final_size,
            "estimated_end_offset_days": remaining,
            "notes": feat.get("notes") or "",
        })

    candidates = [c for c in candidates if c["match_weight"] > 0]
    if not candidates:
        return None
    total_w = sum(c["match_weight"] for c in candidates)
    for c in candidates:
        c["normalized_weight"] = c["match_weight"] / total_w if total_w > 0 else 0.0
    candidates.sort(key=lambda x: x["normalized_weight"], reverse=True)

    vals = [c["estimated_final_size"] for c in candidates]
    wts = [c["normalized_weight"] for c in candidates]
    end_offsets = [c["estimated_end_offset_days"] for c in candidates]
    fs_q05 = weighted_quantile(vals, wts, 0.05)
    fs_q25 = weighted_quantile(vals, wts, 0.25)
    fs_q50 = weighted_quantile(vals, wts, 0.50)
    fs_q75 = weighted_quantile(vals, wts, 0.75)
    fs_q95 = weighted_quantile(vals, wts, 0.95)
    ed_q05 = weighted_quantile(end_offsets, wts, 0.05)
    ed_q50 = weighted_quantile(end_offsets, wts, 0.50)
    ed_q95 = weighted_quantile(end_offsets, wts, 0.95)

    wanted = sorted(set(list(range(1, 22)) + list(range(28, MAX_FUTURE_DAYS + 1, 7)) + [MAX_FUTURE_DAYS]))
    trajectory = []
    curves = library.get("curves") or {}
    for h in wanted:
        hv = []
        for c in candidates:
            curve = curves.get(c["outbreak_id"], [])
            raw = curve_value(curve, current_day + h)
            # Once the analog has ended, use the scaled final size.
            v = max(current_cum, c["scale"] * raw)
            hv.append(v)
        trajectory.append({
            "date": add_days(selected, h),
            "median": round(weighted_quantile(hv, wts, 0.50), 1),
            "q25": round(weighted_quantile(hv, wts, 0.25), 1),
            "q75": round(weighted_quantile(hv, wts, 0.75), 1),
            "q05": round(weighted_quantile(hv, wts, 0.05), 1),
            "q95": round(weighted_quantile(hv, wts, 0.95), 1),
        })

    return {
        "model": "ai_assisted_historical_matching",
        "scenario": "ai_assisted_historical_matching",
        "final_size": {
            "median": round(fs_q50),
            "pi50": [round(fs_q25), round(fs_q75)],
            "pi90": [round(fs_q05), round(fs_q95)],
        },
        "end_date": {
            "median": add_days(selected, round(ed_q50)),
            "pi90": [add_days(selected, round(ed_q05)), add_days(selected, round(ed_q95))],
        },
        "trajectory": trajectory,
        "matches": candidates[:5],
        "method_note": "AI-assisted analog forecast using weighted log1p cumulative-curve similarity to previous DRC Ebola outbreaks; analog curves are scaled to the current observed cumulative trajectory.",
    }


def mix_value(a: float, b: float, wb: float, wh: float) -> float:
    return wb * float(a) + wh * float(b)


def ensemble_projection(branching: dict | None, historical: dict | None, current_cum: float) -> dict | None:
    if branching is None and historical is None:
        return None
    if branching is None:
        out = dict(historical)
        out["model"] = "ensemble"
        out["ensemble_note"] = "Branching-process projection unavailable; AI-assisted historical matching used as fallback."
        return out
    if historical is None:
        out = dict(branching)
        out["model"] = "ensemble"
        out["ensemble_note"] = "AI-assisted historical matching unavailable; branching-process projection used as fallback."
        return out

    rt = branching.get("rt") or {}
    rt_med = float(rt.get("median") or 1.0)
    # Adaptive weights: early outbreaks lean more on analogs; later or controlled phases lean more on branching.
    if rt_med < 1.0 and current_cum >= 100:
        wh = 0.20
    elif current_cum < 100:
        wh = 0.60
    elif current_cum <= 500:
        wh = 0.40
    else:
        wh = 0.30
    wb = 1.0 - wh

    bf, hf = branching["final_size"], historical["final_size"]
    be, he = branching["end_date"], historical["end_date"]

    # Dates: blend offsets from today's projection date by reading from existing dates outside this function is awkward;
    # use weighted quantile over the two date strings as an interpretable conservative interval. Median chooses the
    # heavier model, interval spans both model uncertainty ranges.
    med_end = be.get("median") if wb >= wh else he.get("median")
    pi90_end = [min(be.get("pi90", [be.get("median")])[0], he.get("pi90", [he.get("median")])[0]),
                max(be.get("pi90", [be.get("median"), be.get("median")])[-1], he.get("pi90", [he.get("median"), he.get("median")])[-1])]

    # Combine trajectories by common index; generation uses identical wanted dates.
    btraj, htraj = branching.get("trajectory", []), historical.get("trajectory", [])
    n = min(len(btraj), len(htraj))
    trajectory = []
    for i in range(n):
        br, hr = btraj[i], htraj[i]
        trajectory.append({
            "date": br.get("date") or hr.get("date"),
            "median": round(mix_value(br.get("median", 0), hr.get("median", 0), wb, wh), 1),
            "q25": round(mix_value(br.get("q25", 0), hr.get("q25", 0), wb, wh), 1),
            "q75": round(mix_value(br.get("q75", 0), hr.get("q75", 0), wb, wh), 1),
            "q05": round(mix_value(br.get("q05", 0), hr.get("q05", 0), wb, wh), 1),
            "q95": round(mix_value(br.get("q95", 0), hr.get("q95", 0), wb, wh), 1),
        })

    return {
        "model": "ensemble",
        "scenario": "ensemble_recommended",
        "weights": {"branching_process": round(wb, 2), "ai_assisted_historical_matching": round(wh, 2)},
        "rt": rt,
        "final_size": {
            "median": round(mix_value(bf.get("median"), hf.get("median"), wb, wh)),
            "pi50": [round(mix_value(bf.get("pi50", [bf.get("median")])[0], hf.get("pi50", [hf.get("median")])[0], wb, wh)),
                     round(mix_value(bf.get("pi50", [bf.get("median"), bf.get("median")])[-1], hf.get("pi50", [hf.get("median"), hf.get("median")])[-1], wb, wh))],
            "pi90": [round(mix_value(bf.get("pi90", [bf.get("median")])[0], hf.get("pi90", [hf.get("median")])[0], wb, wh)),
                     round(mix_value(bf.get("pi90", [bf.get("median"), bf.get("median")])[-1], hf.get("pi90", [hf.get("median"), hf.get("median")])[-1], wb, wh))],
        },
        "end_date": {"median": med_end, "pi90": pi90_end},
        "trajectory": trajectory,
        "matches": historical.get("matches", [])[:3],
        "ensemble_note": "Weighted combination of branching process and AI-assisted historical matching. Weights adapt to outbreak stage and recent Rt.",
    }


def main() -> None:
    reports = read_reports()
    hist_lib = load_historical_library()
    rng = np.random.default_rng(SEED)
    dates: dict[str, dict] = {}
    for i in range(len(reports)):
        subset = reports[: i + 1]
        if len(subset) < MIN_REPORT_DATES:
            continue
        key = subset[-1].reporting_date.isoformat()
        branching = simulate_branching(subset, rng)
        historical = historical_matching(subset, hist_lib)
        ensemble = ensemble_projection(branching, historical, subset[-1].cumulative)
        dates[key] = {
            "report_no": subset[-1].report_no,
            "reporting_date": key,
            "current_cumulative_cases": round(subset[-1].cumulative),
            "models": {
                "ensemble": ensemble,
                "branching": branching,
                "historical_ai": historical,
            },
            # Backward-compatible alias for older dashboard code.
            "scenarios": {
                "ensemble": ensemble,
                "branching": branching,
                "historical_ai": historical,
            },
        }
    latest = reports[-1] if reports else None
    payload = {
        "updated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "method": "ensemble_branching_process_ai_assisted_historical_matching",
        "source": "data/report_summary.csv; data/historical_matching_library.json",
        "source_sitrep": latest.report_no if latest else "",
        "report_date": latest.reporting_date.isoformat() if latest else "",
        "definition_of_end_date": "First date followed by 42 consecutive days with zero incident confirmed cases in the simulated or analog-projected reporting process.",
        "caveat": "Scenario-based projection from reported confirmed cases. Not adjusted for onset date, reporting delay, under-ascertainment, spatial heterogeneity or future response changes outside model assumptions. AI-assisted historical matching is an auxiliary analog forecast, not a deterministic prediction.",
        "model_labels": {
            "ensemble": {"ja": "Ensemble（推奨）", "en": "Ensemble (recommended)", "fr": "Ensemble (recommandé)"},
            "branching": {"ja": "Branching process", "en": "Branching process", "fr": "Processus de branchement"},
            "historical_ai": {"ja": "AI支援による過去流行マッチング", "en": "AI-assisted historical matching", "fr": "Appariement historique assisté par IA"},
        },
        # Backward-compatible name consumed by app.js.
        "scenario_labels": {
            "ensemble": {"ja": "Ensemble（推奨）", "en": "Ensemble (recommended)", "fr": "Ensemble (recommandé)"},
            "branching": {"ja": "Branching process", "en": "Branching process", "fr": "Processus de branchement"},
            "historical_ai": {"ja": "AI支援による過去流行マッチング", "en": "AI-assisted historical matching", "fr": "Appariement historique assisté par IA"},
        },
        "model_definitions": {
            "ensemble": "Weighted combination of branching process and AI-assisted historical matching. Initial display uses this recommended layer.",
            "branching": "Renewal / branching-process negative-binomial projection using recent reported confirmed cases and a discretized generation-interval distribution.",
            "historical_ai": "AI-assisted analog forecast using weighted similarity to processed historical DRC Ebola outbreak curves, scaled to the current observed cumulative trajectory.",
        },
        "generation_interval": {"distribution": "discretized_gamma", "mean_days": 12, "sd_days": 5, "max_lag_days": 40},
        "observation_model": {"type": "negative_binomial", "dispersion_k": DISPERSION_K, "simulations_per_scenario": N_SIM},
        "historical_matching": {
            "library": "data/historical_matching_library.json",
            "distance": "weighted RMSE on log1p cumulative confirmed cases after scaling each historical curve",
            "top_matches_are_returned": True,
        },
        "dates": dates,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUTPUT} with {len(dates)} reporting dates")


if __name__ == "__main__":
    main()
