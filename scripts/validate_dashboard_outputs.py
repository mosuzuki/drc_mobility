#!/usr/bin/env python3
from __future__ import annotations
import csv, json, sys, math, re
from pathlib import Path
from datetime import datetime, timezone, date

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data'
STATUS=ROOT/'.dashboard_validation_status.md'

VERIFIED={
 'N47': ('2026-06-30',1406,438),
 'N48': ('2026-07-01',1460,452),
 'N49': ('2026-07-02',1502,473),
 'N50': ('2026-07-03',1528,492),
}

def read_csv(path):
    if not path.exists(): return []
    with path.open(newline='',encoding='utf-8-sig') as f: return list(csv.DictReader(f))

def rno(s):
    m=re.search(r'(\d+)', str(s or ''))
    return int(m.group(1)) if m else -1

def latest(rows, date_key='reporting_date', no_key='report_no'):
    def key(r): return (str(r.get(date_key,'')), rno(r.get(no_key,'')))
    return sorted(rows, key=key)[-1] if rows else {}

def load_json(path):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}

def to_float(x):
    try: return float(str(x).replace(',','').strip())
    except Exception: return float('nan')

def round_int(x):
    return int(round(float(x))) if math.isfinite(float(x)) else None

errors=[]; warnings=[]
report_rows=read_csv(DATA/'report_summary.csv')
report=latest(report_rows)
latest_no=report.get('report_no') or report.get('source_sitrep')
latest_date=report.get('reporting_date')
latest_cases=to_float(report.get('drc_confirmed_cases'))
latest_deaths=to_float(report.get('drc_confirmed_deaths'))

if not latest_no or not latest_date:
    errors.append('report_summary.csv has no latest report_no/reporting_date')
if not math.isfinite(latest_cases) or latest_cases <= 0:
    errors.append('latest report_summary has invalid DRC confirmed cases')
if not math.isfinite(latest_deaths) or latest_deaths < 0:
    errors.append('latest report_summary has invalid DRC confirmed deaths')
if math.isfinite(latest_cases) and math.isfinite(latest_deaths):
    if latest_deaths > latest_cases:
        errors.append(f'latest deaths {latest_deaths} exceed confirmed cases {latest_cases}')
    if latest_cases >= 2000 and latest_date and latest_date <= '2026-07-05':
        errors.append(f'latest confirmed cases {latest_cases} are implausibly high for {latest_date}; check if year 2026 was parsed as cases')
    if latest_deaths in (609, 628, 641):
        errors.append(f'latest deaths {latest_deaths} match isolated/hospitalized card value, not confirmed deaths')
    if latest_deaths / max(latest_cases, 1) > 0.55:
        warnings.append(f'latest CFR is unusually high ({latest_deaths/latest_cases:.1%}); check extraction')

for rep,(dt,cases,deaths) in VERIFIED.items():
    matches=[r for r in report_rows if r.get('report_no')==rep or r.get('reporting_date')==dt]
    if matches:
        r=matches[-1]
        if round_int(to_float(r.get('drc_confirmed_cases'))) != cases or round_int(to_float(r.get('drc_confirmed_deaths'))) != deaths:
            errors.append(f'{rep} must be confirmed={cases}, deaths={deaths}; found {r.get("drc_confirmed_cases")}/{r.get("drc_confirmed_deaths")}')

# Cross-check latest report_summary with health-zone + unventilated totals.
cb=read_csv(DATA/'cases_by_hz.csv')
uv=read_csv(DATA/'cases_unventilated.csv')
if latest_date and cb:
    cb_sum=sum(to_float(r.get('confirmed_cases')) for r in cb if r.get('date')==latest_date and math.isfinite(to_float(r.get('confirmed_cases'))))
    uv_sum=sum(to_float(r.get('confirmed_cases')) for r in uv if r.get('date')==latest_date and math.isfinite(to_float(r.get('confirmed_cases'))))
    if math.isfinite(latest_cases) and round(cb_sum + uv_sum) != round(latest_cases):
        errors.append(f'health-zone total plus unventilated ({cb_sum}+{uv_sum}={cb_sum+uv_sum}) != report_summary cases {latest_cases}')

for fname in ['final_size_projection.json','true_infection_estimate.json']:
    d=load_json(DATA/fname)
    if not d:
        errors.append(f'{fname} missing or empty')
        continue
    if latest_no and d.get('source_sitrep') != latest_no:
        errors.append(f'{fname}: source_sitrep {d.get("source_sitrep")} != latest report_summary {latest_no}')
    if latest_date and d.get('report_date') != latest_date:
        errors.append(f'{fname}: report_date {d.get("report_date")} != latest report_summary {latest_date}')
    if fname == 'true_infection_estimate.json':
        if round_int(to_float(d.get('reported_confirmed_cases'))) != round_int(latest_cases):
            errors.append(f'{fname}: reported_confirmed_cases {d.get("reported_confirmed_cases")} != latest cases {latest_cases}')
        if round_int(to_float(d.get('reported_deaths'))) != round_int(latest_deaths):
            errors.append(f'{fname}: reported_deaths {d.get("reported_deaths")} != latest deaths {latest_deaths}')
    if fname == 'final_size_projection.json':
        dates=d.get('dates') or {}
        if latest_date and latest_date not in dates:
            errors.append(f'{fname}: dates does not contain latest reporting_date {latest_date}')
        elif latest_date:
            cc=to_float((dates.get(latest_date) or {}).get('current_cumulative_cases'))
            if round_int(cc) != round_int(latest_cases):
                errors.append(f'{fname}: latest current_cumulative_cases {cc} != report_summary {latest_cases}')

ug=latest(read_csv(DATA/'uganda_evd_summary.csv'), date_key='as_of_date', no_key='as_of_date')
daily=read_csv(DATA/'uganda_evd_daily_cases.csv')
if not ug:
    errors.append('uganda_evd_summary.csv missing or empty')
else:
    asof=ug.get('as_of_date')
    if not asof:
        errors.append('uganda_evd_summary.csv has no as_of_date')
    elif daily:
        if asof not in {r.get('date') for r in daily}:
            errors.append(f'uganda_evd_daily_cases.csv has no row for Uganda as_of_date {asof}')
    try:
        if asof and (date.today() - date.fromisoformat(asof)).days > 5:
            warnings.append(f'Uganda as_of_date {asof} is more than 5 days old')
    except Exception:
        pass

body=['# Dashboard validation status','',f'Updated at: {datetime.now(timezone.utc).isoformat(timespec="seconds")}','']
if errors:
    body.append('## ❌ Errors'); body += [f'- {e}' for e in errors]
if warnings:
    body.append('## ⚠️ Warnings'); body += [f'- {w}' for w in warnings]
if not errors:
    body.append('## ✅ All required checks passed')
    body.append(f'- Latest DRC SitRep: {latest_no} / {latest_date} / confirmed {round_int(latest_cases)} / deaths {round_int(latest_deaths)}')
    body.append(f'- Uganda as-of date: {ug.get("as_of_date","")}')
STATUS.write_text('\n'.join(body)+'\n',encoding='utf-8')
if errors:
    print('\n'.join(body)); sys.exit(1)
print('\n'.join(body))
