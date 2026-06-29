#!/usr/bin/env python3
from __future__ import annotations
import csv, json, sys
from pathlib import Path
from datetime import datetime, timezone

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

errors=[]
report=latest(read_csv(DATA/'report_summary.csv'),'reporting_date')
latest_no=report.get('report_no') or report.get('source_sitrep')
latest_date=report.get('reporting_date')
for fname in ['final_size_projection.json','true_infection_estimate.json']:
    d=load_json(DATA/fname)
    if not d:
        errors.append(f'{fname} missing or empty')
        continue
    if latest_no and d.get('source_sitrep') != latest_no:
        errors.append(f'{fname}: source_sitrep {d.get("source_sitrep")} != latest report_summary {latest_no}')
    if latest_date and d.get('report_date') != latest_date:
        errors.append(f'{fname}: report_date {d.get("report_date")} != latest report_summary {latest_date}')

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

body=['# Dashboard validation status','',f'Updated at: {datetime.now(timezone.utc).isoformat(timespec="seconds")}','']
if errors:
    body.append('## ❌ Errors')
    body += [f'- {e}' for e in errors]
    STATUS.write_text('\n'.join(body)+'\n',encoding='utf-8')
    sys.exit(1)
else:
    body.append('## ✅ All checks passed')
    body.append(f'- Latest DRC SitRep: {latest_no} / {latest_date}')
    body.append(f'- Uganda as-of date: {ug.get("as_of_date","")}')
    STATUS.write_text('\n'.join(body)+'\n',encoding='utf-8')
