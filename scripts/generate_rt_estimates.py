#!/usr/bin/env python3
"""Generate a lightweight reported-case Rt series for the dashboard.

This is intended for situational visualization, not formal inference. It uses
reported confirmed cases by SitRep date, expands multi-day reporting gaps into
average daily increments, and applies a renewal-equation approximation with a
fixed serial/generation interval distribution.
"""
from __future__ import annotations

import csv
import math
from datetime import date, timedelta, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = DATA / "rt_estimates.csv"
STATUS = ROOT / ".rt_estimates_status.md"

MEAN_GT = 15.0
SD_GT = 6.0
MAX_LAG = 30
MIN_CASES_FOR_RT = 3.0


def to_float(x):
    try:
        return float(str(x).replace(',', '').strip())
    except Exception:
        return float('nan')


def read_report_summary():
    path = DATA / "report_summary.csv"
    with path.open(newline='', encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    clean = []
    for r in rows:
        d = r.get('reporting_date')
        c = to_float(r.get('drc_confirmed_cases'))
        if not d or not math.isfinite(c):
            continue
        try:
            dd = date.fromisoformat(d)
        except Exception:
            continue
        clean.append({**r, 'date_obj': dd, 'cum_cases': c})
    clean.sort(key=lambda r: r['date_obj'])
    # deduplicate by date, keeping the latest row in file order after sort stability
    by_date = {}
    for r in clean:
        by_date[r['date_obj']] = r
    return [by_date[d] for d in sorted(by_date)]


def gamma_discrete_weights(mean=MEAN_GT, sd=SD_GT, max_lag=MAX_LAG):
    shape = (mean / sd) ** 2
    scale = (sd ** 2) / mean
    weights = []
    for lag in range(1, max_lag + 1):
        # approximate mass at integer lag by gamma pdf at lag
        x = float(lag)
        pdf = (x ** (shape - 1) * math.exp(-x / scale)) / (math.gamma(shape) * (scale ** shape))
        weights.append(max(pdf, 0.0))
    s = sum(weights) or 1.0
    return [w / s for w in weights]


def expand_daily(rows):
    if not rows:
        return []
    daily = []
    prev = None
    for r in rows:
        if prev is None:
            daily.append({'date': r['date_obj'], 'report_no': r.get('report_no',''), 'cumulative_cases': r['cum_cases'], 'new_cases': max(r['cum_cases'], 0.0), 'observed_sitrep': '1'})
            prev = r
            continue
        gap = max((r['date_obj'] - prev['date_obj']).days, 1)
        delta = max(r['cum_cases'] - prev['cum_cases'], 0.0)
        per_day = delta / gap
        for i in range(1, gap + 1):
            dd = prev['date_obj'] + timedelta(days=i)
            is_obs = dd == r['date_obj']
            cum = prev['cum_cases'] + per_day * i
            daily.append({'date': dd, 'report_no': r.get('report_no','') if is_obs else '', 'cumulative_cases': cum, 'new_cases': per_day, 'observed_sitrep': '1' if is_obs else '0'})
        prev = r
    return daily


def moving_average(values, idx, window=3):
    start = max(0, idx - window + 1)
    xs = values[start:idx+1]
    return sum(xs) / max(len(xs), 1)


def main():
    rows = read_report_summary()
    daily = expand_daily(rows)
    weights = gamma_discrete_weights()
    new_cases = [r['new_cases'] for r in daily]
    out_rows = []
    for i, r in enumerate(daily):
        lam = 0.0
        for lag, w in enumerate(weights, start=1):
            if i - lag >= 0:
                lam += new_cases[i - lag] * w
        obs = moving_average(new_cases, i, 3)
        rt = obs / lam if lam > 0 else float('nan')
        if i < 7 or obs < MIN_CASES_FOR_RT or not math.isfinite(rt):
            rt_med = rt_low = rt_high = ''
        else:
            # Approximate uncertainty using Poisson information in recent reports.
            se_log = 1.0 / math.sqrt(max(obs, 1.0))
            rt_low = max(0.0, rt * math.exp(-1.96 * se_log))
            rt_high = rt * math.exp(1.96 * se_log)
            rt_med = rt
        out_rows.append({
            'date': r['date'].isoformat(),
            'report_no': r['report_no'],
            'observed_sitrep': r['observed_sitrep'],
            'new_confirmed': round(r['new_cases'], 3),
            'new_confirmed_smoothed': round(obs, 3),
            'rt_median': '' if rt_med == '' else round(rt_med, 3),
            'rt_low': '' if rt_low == '' else round(rt_low, 3),
            'rt_high': '' if rt_high == '' else round(rt_high, 3),
            'generation_time_mean_days': MEAN_GT,
            'generation_time_sd_days': SD_GT,
            'method': 'renewal_equation_approx_reported_cases'
        })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open('w', newline='', encoding='utf-8') as f:
        fieldnames = ['date','report_no','observed_sitrep','new_confirmed','new_confirmed_smoothed','rt_median','rt_low','rt_high','generation_time_mean_days','generation_time_sd_days','method']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(out_rows)
    latest = rows[-1] if rows else {}
    STATUS.write_text('\n'.join([
        '# Rt estimates status','',
        f'Updated at: {datetime.now(timezone.utc).isoformat(timespec="seconds")}',
        f'- Latest report: {latest.get("report_no", "")} / {latest.get("reporting_date", "")}',
        f'- Rows written: {len(out_rows)}',
        f'- Method: renewal equation approximation from reported confirmed cases; mean GT {MEAN_GT:g} d, SD {SD_GT:g} d.'
    ]) + '\n', encoding='utf-8')
    print(f'Wrote {OUT} ({len(out_rows)} rows)')


if __name__ == '__main__':
    main()
