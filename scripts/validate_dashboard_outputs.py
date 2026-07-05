#!/usr/bin/env python3
from __future__ import annotations
import csv, json, sys, math
from pathlib import Path
from datetime import datetime, timezone, date

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data'
STATUS=ROOT/'.dashboard_validation_status.md'

def read_csv(path):
    if not path.exists(): return []
    with path.open(newline='',encoding='utf-8-sig') as f: return list(csv.DictReader(f))

def latest(rows, key):
    rows=[r for r in rows if r.get(key)] or rows
    return sorted(rows, key=lambda r: str(r.get(key,'')))[-1] if rows else {}

def load_json(path):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}

def to_float(x):
    try:
        return float(str(x).replace(',','').strip())
    except Exception:
        return float('nan')

errors=[]
warnings=[]
report=latest(read_csv(DATA/'report_summary.csv'),'reporting_date')
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
    if latest_deaths / max(latest_cases, 1) > 0.55:
        warnings.append(f'latest CFR is unusually high ({latest_deaths/latest_cases:.1%}); check if hospitalised/isolated count was mistaken for deaths')
    # Known verified values from uploaded SitRep N47.
    if latest_no == 'N47' and latest_date == '2026-06-30':
        if round(latest_cases) != 1406 or round(latest_deaths) != 438:
            errors.append(f'N47 values must be confirmed=1406 and deaths=438, found {latest_cases}/{latest_deaths}')

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
        if round(to_float(d.get('reported_confirmed_cases'))) != round(latest_cases):
            errors.append(f'{fname}: reported_confirmed_cases {d.get("reported_confirmed_cases")} != latest cases {latest_cases}')
        if round(to_float(d.get('reported_deaths'))) != round(latest_deaths):
            errors.append(f'{fname}: reported_deaths {d.get("reported_deaths")} != latest deaths {latest_deaths}')
    if fname == 'final_size_projection.json':
        dates=d.get('dates') or {}
        if latest_date and latest_date not in dates:
            errors.append(f'{fname}: dates does not contain latest reporting_date {latest_date}')
        elif latest_date:
            cc=to_float((dates.get(latest_date) or {}).get('current_cumulative_cases'))
            if round(cc) != round(latest_cases):
                errors.append(f'{fname}: latest current_cumulative_cases {cc} != report_summary {latest_cases}')


# Response indicators must be available for the latest SitRep, because the
# left-lower response panel defaults to contact_followup_rate.
resp_rows = read_csv(DATA/'response_indicators.csv')
resp_latest = [r for r in resp_rows if r.get('report_no') == latest_no and str(r.get('admin_level','')).lower() == 'national']
if not resp_latest:
    errors.append(f'response_indicators.csv has no national row for latest SitRep {latest_no}')
else:
    rr = resp_latest[-1]
    cf = to_float(rr.get('contact_followup_rate'))
    if not math.isfinite(cf):
        errors.append(f'response_indicators.csv latest {latest_no} has no contact_followup_rate')
    elif not (0.3 <= cf <= 1.0):
        errors.append(f'response_indicators.csv latest contact_followup_rate {cf} is outside plausible range')
    ar = to_float(rr.get('alert_investigation_rate'))
    if not math.isfinite(ar):
        warnings.append(f'response_indicators.csv latest {latest_no} has no alert_investigation_rate')
    elif not (0 <= ar <= 1.0):
        errors.append(f'response_indicators.csv latest alert_investigation_rate {ar} is outside 0-1')

# Cross-check summary totals against health-zone plus unventilated totals for latest date.
hz_rows = [r for r in read_csv(DATA/'cases_by_hz.csv') if r.get('date') == latest_date]
uv_rows = [r for r in read_csv(DATA/'cases_unventilated.csv') if r.get('date') == latest_date]
if hz_rows:
    hz_cases = sum(to_float(r.get('confirmed_cases')) for r in hz_rows if math.isfinite(to_float(r.get('confirmed_cases'))))
    uv_cases = sum(to_float(r.get('confirmed_cases')) for r in uv_rows if math.isfinite(to_float(r.get('confirmed_cases'))))
    hz_deaths = sum(to_float(r.get('confirmed_deaths')) for r in hz_rows if math.isfinite(to_float(r.get('confirmed_deaths'))))
    uv_deaths = sum(to_float(r.get('confirmed_deaths')) for r in uv_rows if math.isfinite(to_float(r.get('confirmed_deaths'))))
    if math.isfinite(latest_cases) and abs((hz_cases + uv_cases) - latest_cases) > 1:
        errors.append(f'health-zone + unventilated cases {(hz_cases + uv_cases):.0f} != report_summary latest cases {latest_cases:.0f}')
    if math.isfinite(latest_deaths) and abs((hz_deaths + uv_deaths) - latest_deaths) > 1:
        errors.append(f'health-zone + unventilated deaths {(hz_deaths + uv_deaths):.0f} != report_summary latest deaths {latest_deaths:.0f}')

ug=latest(read_csv(DATA/'uganda_evd_summary.csv'),'as_of_date')
daily=read_csv(DATA/'uganda_evd_daily_cases.csv')
if not ug:
    errors.append('uganda_evd_summary.csv missing or empty')
else:
    asof=ug.get('as_of_date')
    if not asof:
        errors.append('uganda_evd_summary.csv has no as_of_date')
    elif daily:
        daily_dates={r.get('date') for r in daily}
        if asof not in daily_dates:
            errors.append(f'uganda_evd_daily_cases.csv has no row for Uganda as_of_date {asof}')
    try:
        if asof and (date.today() - date.fromisoformat(asof)).days > 3:
            warnings.append(f'Uganda as_of_date {asof} is more than 3 days old')
    except Exception:
        pass

body=['# Dashboard validation status','',f'Updated at: {datetime.now(timezone.utc).isoformat(timespec="seconds")}','']
if errors:
    body.append('## ❌ Errors')
    body += [f'- {e}' for e in errors]
if warnings:
    body.append('## ⚠️ Warnings')
    body += [f'- {w}' for w in warnings]
if not errors:
    body.append('## ✅ All required checks passed')
    body.append(f'- Latest DRC SitRep: {latest_no} / {latest_date} / confirmed {round(latest_cases)} / deaths {round(latest_deaths)}')
    body.append(f'- Uganda as-of date: {ug.get("as_of_date","")}')
STATUS.write_text('\n'.join(body)+'\n',encoding='utf-8')
if errors:
    sys.exit(1)
