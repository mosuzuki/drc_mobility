"""Explicit daily notifications override cumulative differences (which include revisions)."""
import csv
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / 'data/sitrep_daily_notifications.csv'

def load_notifications():
    if not PATH.exists():
        return {}
    with PATH.open(encoding='utf-8', newline='') as f:
        return {(r['date'], r['admin_level'], r['province'], r['health_zone']): r for r in csv.DictReader(f)}

def overlay_national(rows, field='incidence', date_field='date'):
    observations = load_notifications()
    for row in rows:
        d = str(row[date_field])[:10]
        source = observations.get((d, 'national', '', ''))
        if source:
            row[field] = float(source['new_confirmed_cases'])
            if 'new_deaths' in row:
                row['new_deaths'] = float(source['new_confirmed_deaths'])
    return rows
