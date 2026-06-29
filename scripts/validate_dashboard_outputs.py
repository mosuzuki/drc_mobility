#!/usr/bin/env python3
"""Validate that generated dashboard outputs are aligned with latest data.

This script is intended to run after the SitRep, Uganda, final-size and true
infection update steps. It does not fetch external sources; it checks local
output consistency so stale projection JSON files are caught by GitHub Actions.
"""
from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
STATUS = ROOT / ".dashboard_validation_status.md"


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def latest_by_date(rows: list[dict[str, str]], date_key: str) -> dict[str, str]:
    good = [r for r in rows if r.get(date_key)]
    if not good:
        return rows[-1] if rows else {}
    return sorted(good, key=lambda r: str(r.get(date_key) or ""))[-1]


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def iso_dates_between(start_exclusive: str, end_inclusive: str) -> list[str]:
    try:
        start = date.fromisoformat(start_exclusive)
        end = date.fromisoformat(end_inclusive)
    except ValueError:
        return []
    out: list[str] = []
    cur = start
    while cur < end:
        cur = date.fromordinal(cur.toordinal() + 1)
        out.append(cur.isoformat())
    return out


def main() -> None:
    errors: list[str] = []
    warnings: list[str] = []

    reports = read_csv(DATA / "report_summary.csv")
    latest_report = latest_by_date(reports, "reporting_date")
    latest_no = str(latest_report.get("report_no") or "")
    latest_date = str(latest_report.get("reporting_date") or "")
    latest_cases = str(latest_report.get("drc_confirmed_cases") or "")

    if not latest_no or not latest_date:
        errors.append("Could not determine latest report_no/reporting_date from data/report_summary.csv.")

    final_size = load_json(DATA / "final_size_projection.json")
    true_inf = load_json(DATA / "true_infection_estimate.json")

    if latest_no and final_size.get("source_sitrep") != latest_no:
        errors.append(
            f"final_size_projection.json source_sitrep={final_size.get('source_sitrep')} does not match report_summary latest {latest_no}."
        )
    if latest_date and final_size.get("report_date") != latest_date:
        errors.append(
            f"final_size_projection.json report_date={final_size.get('report_date')} does not match report_summary latest {latest_date}."
        )

    if latest_no and true_inf.get("source_sitrep") != latest_no:
        errors.append(
            f"true_infection_estimate.json source_sitrep={true_inf.get('source_sitrep')} does not match report_summary latest {latest_no}."
        )
    if latest_date and true_inf.get("report_date") != latest_date:
        errors.append(
            f"true_infection_estimate.json report_date={true_inf.get('report_date')} does not match report_summary latest {latest_date}."
        )
    if latest_cases and str(true_inf.get("reported_confirmed_cases") or "") != str(int(float(latest_cases))):
        errors.append(
            f"true_infection_estimate.json reported_confirmed_cases={true_inf.get('reported_confirmed_cases')} does not match report_summary latest {latest_cases}."
        )

    ug_summary_rows = read_csv(DATA / "uganda_evd_summary.csv")
    ug_history_rows = read_csv(DATA / "uganda_evd_history.csv")
    ug_daily_rows = read_csv(DATA / "uganda_evd_daily_cases.csv")
    ug_latest = latest_by_date(ug_summary_rows, "as_of_date")
    ug_as_of = str(ug_latest.get("as_of_date") or "")
    if not ug_as_of:
        errors.append("Could not determine Uganda as_of_date from data/uganda_evd_summary.csv.")
    else:
        history_dates = {str(r.get("as_of_date") or "") for r in ug_history_rows}
        if ug_as_of not in history_dates:
            warnings.append(f"Uganda summary as_of_date {ug_as_of} is not present in uganda_evd_history.csv.")
        daily_dates = {str(r.get("date") or "") for r in ug_daily_rows}
        if ug_as_of not in daily_dates:
            warnings.append(f"Uganda daily cases does not contain a row for current as_of_date {ug_as_of}.")
        # Check that gaps from last historical as-of date are filled in daily cases.
        sorted_hist = sorted(d for d in history_dates if d)
        if len(sorted_hist) >= 2:
            prev_as_of = sorted_hist[-2]
            missing = [d for d in iso_dates_between(prev_as_of, ug_as_of) if d not in daily_dates]
            if missing:
                warnings.append(f"Uganda daily cases missing rows between {prev_as_of} and {ug_as_of}: {', '.join(missing[:10])}.")

    lines = ["# Dashboard output validation", ""]
    lines.append(f"Latest DRC SitRep in report_summary.csv: {latest_no or 'unknown'} / {latest_date or 'unknown'}")
    lines.append(f"Latest Uganda as-of date: {ug_as_of or 'unknown'}")
    lines.append("")
    if errors:
        lines.append("## Errors")
        lines.extend(f"- {e}" for e in errors)
        lines.append("")
    if warnings:
        lines.append("## Warnings")
        lines.extend(f"- {w}" for w in warnings)
        lines.append("")
    if not errors and not warnings:
        lines.append("All checks passed.")
    STATUS.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if errors:
        raise SystemExit("; ".join(errors))


if __name__ == "__main__":
    main()
