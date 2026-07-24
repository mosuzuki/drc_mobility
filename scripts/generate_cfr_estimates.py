#!/usr/bin/env python3
"""Generate crude and delay-adjusted CFR series for the dashboard.

The delay adjustment uses reported confirmed cases and a simple report-to-death
cdf. It is a monitoring indicator, not a clinical CFR estimate.
"""
from __future__ import annotations

import csv
import math
from datetime import date, timedelta, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = DATA / "cfr_estimates.csv"
STATUS = ROOT / ".cfr_estimates_status.md"

DELAY_MEDIAN_DAYS = 7.0
DELAY_SCALE_DAYS = 2.5
MAX_DELAY = 60


def to_float(x):
    try:
        return float(str(x).replace(',', '').strip())
    except Exception:
        return float('nan')


def read_report_summary():
    with (DATA / 'report_summary.csv').open(newline='', encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    clean = []
    for r in rows:
        try:
            dd = date.fromisoformat(r.get('reporting_date',''))
        except Exception:
            continue
        cases = to_float(r.get('drc_confirmed_cases'))
        deaths = to_float(r.get('drc_confirmed_deaths'))
        if math.isfinite(cases) and math.isfinite(deaths):
            clean.append({**r, 'date_obj': dd, 'cum_cases': cases, 'cum_deaths': deaths})
    clean.sort(key=lambda r: r['date_obj'])
    by_date = {}
    for r in clean:
        by_date[r['date_obj']] = r
    return [by_date[d] for d in sorted(by_date)]


def expand_daily(rows):
    daily = []
    prev = None
    for r in rows:
        if prev is None:
            daily.append({'date': r['date_obj'], 'report_no': r.get('report_no',''), 'cum_cases': r['cum_cases'], 'cum_deaths': r['cum_deaths'], 'new_cases': max(r['cum_cases'], 0.0), 'new_deaths': max(r['cum_deaths'], 0.0), 'observed_sitrep': '1'})
            prev = r
            continue
        gap = max((r['date_obj'] - prev['date_obj']).days, 1)
        dc = max(r['cum_cases'] - prev['cum_cases'], 0.0)
        ddth = max(r['cum_deaths'] - prev['cum_deaths'], 0.0)
        for i in range(1, gap + 1):
            day = prev['date_obj'] + timedelta(days=i)
            is_obs = day == r['date_obj']
            daily.append({'date': day, 'report_no': r.get('report_no','') if is_obs else '', 'cum_cases': prev['cum_cases'] + dc * i / gap, 'cum_deaths': prev['cum_deaths'] + ddth * i / gap, 'new_cases': dc / gap, 'new_deaths': ddth / gap, 'observed_sitrep': '1' if is_obs else '0'})
        prev = r
    return daily


def delay_cdf(elapsed_days):
    if elapsed_days < 0:
        return 0.0
    # Logistic approximation: 50% of fatal outcomes observable by DELAY_MEDIAN_DAYS.
    return 1.0 / (1.0 + math.exp(-(elapsed_days - DELAY_MEDIAN_DAYS) / DELAY_SCALE_DAYS))


def main():
    rows = read_report_summary()
    daily = expand_daily(rows)
    out_rows = []
    for i, r in enumerate(daily):
        denom = 0.0
        for j in range(0, i + 1):
            elapsed = (r['date'] - daily[j]['date']).days
            denom += daily[j]['new_cases'] * delay_cdf(elapsed)
        cum_cases = max(r['cum_cases'], 0.0)
        cum_deaths = max(r['cum_deaths'], 0.0)
        crude = cum_deaths / cum_cases if cum_cases > 0 else float('nan')
        # Avoid impossible >100% display when deaths are retroactively classified before
        # enough report-to-death time has accrued in the simple denominator.
        adjusted_denom_display = max(denom, cum_deaths)
        adjusted = cum_deaths / adjusted_denom_display if adjusted_denom_display > 0 else float('nan')
        out_rows.append({
            'date': r['date'].isoformat(),
            'report_no': r['report_no'],
            'observed_sitrep': r['observed_sitrep'],
            'cumulative_confirmed': round(cum_cases, 3),
            'cumulative_deaths': round(cum_deaths, 3),
            'crude_cfr': '' if not math.isfinite(crude) else round(crude, 4),
            'delay_adjusted_cfr': '' if not math.isfinite(adjusted) else round(adjusted, 4),
            'adjusted_denominator': round(adjusted_denom_display, 3),
            'raw_delay_adjusted_denominator': round(denom, 3),
            'delay_median_days': DELAY_MEDIAN_DAYS,
            'delay_scale_days': DELAY_SCALE_DAYS,
            'method': 'report_to_death_delay_adjusted_cfr_logistic_cdf'
        })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open('w', newline='', encoding='utf-8') as f:
        fieldnames = ['date','report_no','observed_sitrep','cumulative_confirmed','cumulative_deaths','crude_cfr','delay_adjusted_cfr','adjusted_denominator','raw_delay_adjusted_denominator','delay_median_days','delay_scale_days','method']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader(); writer.writerows(out_rows)
    latest = rows[-1] if rows else {}
    STATUS.write_text('\n'.join([
        '# CFR estimates status','',
        f'Updated at: {datetime.now(timezone.utc).isoformat(timespec="seconds")}',
        f'- Latest report: {latest.get("report_no", "")} / {latest.get("reporting_date", "")}',
        f'- Rows written: {len(out_rows)}',
        f'- Method: crude CFR and simple report-to-death delay adjustment; median delay {DELAY_MEDIAN_DAYS:g} d.'
    ]) + '\n', encoding='utf-8')
    print(f'Wrote {OUT} ({len(out_rows)} rows)')


if __name__ == '__main__':
    main()
