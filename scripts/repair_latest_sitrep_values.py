#!/usr/bin/env python3
"""Repair verified SitRep values that are commonly misread from card layouts.

The DRC SitRep PDFs place KPI-card numbers before/around labels. Generic PDF
text extraction can mistake the reporting year (2026) for cumulative cases, or
patients isolated/hospitalised (e.g. 609/628) for confirmed deaths. This script
is run after the automated SitRep extraction and before projection generation.
It applies verified table/card values for recent SitReps and keeps response
indicators aligned with the same reports.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
STATUS = ROOT / ".sitrep_repair_status.md"

SUMMARY_FIXES = {
    "N44": ("2026-06-27", "2026-06-28", 1274, 360),
    "N46": ("2026-06-29", "2026-06-30", 1333, 399),
    "N47": ("2026-06-30", "2026-07-01", 1406, 438),
    "N48": ("2026-07-01", "2026-07-02", 1460, 452),
    "N49": ("2026-07-02", "2026-07-03", 1502, 473),
    "N50": ("2026-07-03", "2026-07-04", 1528, 492),
    "N51": ("2026-07-04", "2026-07-05", 1561, 506),
}

RESPONSE_FIXES = {
    "N44": dict(reporting_date="2026-06-27", contacts_under_followup=9145, contacts_seen=7964, contact_followup_rate=0.871, alerts_reported=1283, alerts_investigated=1271, alert_investigation_rate=0.991, samples_analysed="", positive_samples="", travellers_total=152371, poe_screening_coverage=0.961),
    "N46": dict(reporting_date="2026-06-29", contacts_under_followup=11796, contacts_seen=9756, contact_followup_rate=0.827, alerts_reported=1413, alerts_investigated=1078, alert_investigation_rate=0.763, samples_analysed=141, positive_samples=26, travellers_total=159385, poe_screening_coverage=0.971),
    "N47": dict(reporting_date="2026-06-30", contacts_under_followup=11646, contacts_seen=9605, contact_followup_rate=0.825, alerts_reported=1234, alerts_investigated=1035, alert_investigation_rate=0.839, samples_analysed=244, positive_samples=73, travellers_total=230008, poe_screening_coverage=0.979),
    "N48": dict(reporting_date="2026-07-01", contacts_under_followup=10821, contacts_seen=8954, contact_followup_rate=0.827, alerts_reported=958, alerts_investigated=842, alert_investigation_rate=0.879, samples_analysed=228, positive_samples=54, travellers_total=357189, poe_screening_coverage=0.982),
    "N49": dict(reporting_date="2026-07-02", contacts_under_followup=11360, contacts_seen=9291, contact_followup_rate=0.818, alerts_reported=1078, alerts_investigated=767, alert_investigation_rate=0.712, samples_analysed=196, positive_samples=42, travellers_total=119245, poe_screening_coverage=0.959),
    "N50": dict(reporting_date="2026-07-03", contacts_under_followup=9971, contacts_seen=8126, contact_followup_rate=0.815, alerts_reported=1131, alerts_investigated=902, alert_investigation_rate=0.797, samples_analysed=147, positive_samples=26, travellers_total=148579, poe_screening_coverage=0.959),
    "N51": dict(reporting_date="2026-07-04", contacts_under_followup=10079, contacts_seen=8221, contact_followup_rate=0.816, alerts_reported=1244, alerts_investigated=1002, alert_investigation_rate=0.805, samples_analysed=172, positive_samples=33, travellers_total=153930, poe_screening_coverage=0.952),
}

UNVENTILATED_FIXES = {
    "N44": ("2026-06-27", 17),
    "N46": ("2026-06-29", 17),
    "N47": ("2026-06-30", 17),
    "N48": ("2026-07-01", 17),
    "N49": ("2026-07-02", 17),
    "N50": ("2026-07-03", 17),
    "N51": ("2026-07-04", 17),
}


def upsert_by(df: pd.DataFrame, mask: pd.Series) -> int:
    if mask.any():
        return int(df[mask].index[-1])
    idx = len(df)
    df.loc[idx] = {c: "" for c in df.columns}
    return idx


def repair_report_summary(notes: list[str]) -> None:
    path = DATA / "report_summary.csv"
    if not path.exists():
        return
    df = pd.read_csv(path, dtype=str)
    for report_no, (date, pub_date, cases, deaths) in SUMMARY_FIXES.items():
        mask = (df.get("report_no", "").astype(str) == report_no) | (df.get("reporting_date", "").astype(str) == date)
        idx = upsert_by(df, mask)
        before = (str(df.loc[idx].get("drc_confirmed_cases", "")), str(df.loc[idx].get("drc_confirmed_deaths", "")))
        df.loc[idx, "report_no"] = report_no
        df.loc[idx, "reporting_date"] = date
        df.loc[idx, "publication_date"] = pub_date
        df.loc[idx, "drc_confirmed_cases"] = str(cases)
        df.loc[idx, "drc_confirmed_deaths"] = str(deaths)
        if "uganda_confirmed_cases" in df.columns:
            df.loc[idx, "uganda_confirmed_cases"] = "20"
        if "uganda_confirmed_deaths" in df.columns:
            df.loc[idx, "uganda_confirmed_deaths"] = "2"
        df.loc[idx, "source"] = f"SitRep {report_no}/MVB"
        df.loc[idx, "notes"] = "Verified from uploaded SitRep table/card totals; corrected to avoid reading the reporting year or isolated/hospitalised count as cases/deaths."
        if before != (str(cases), str(deaths)):
            notes.append(f"{report_no}: report_summary corrected to confirmed={cases}, deaths={deaths}")
    df["_date"] = pd.to_datetime(df["reporting_date"], errors="coerce")
    df = df.sort_values(["_date", "report_no"]).drop(columns=["_date"])
    df.to_csv(path, index=False)


def repair_response_indicators(notes: list[str]) -> None:
    path = DATA / "response_indicators.csv"
    if not path.exists():
        return
    df = pd.read_csv(path, dtype=str)
    for report_no, fix in RESPONSE_FIXES.items():
        mask = (df.get("report_no", "").astype(str) == report_no) & (df.get("admin_level", "").fillna("").astype(str).str.lower() == "national")
        idx = upsert_by(df, mask)
        df.loc[idx, "reporting_date"] = fix["reporting_date"]
        df.loc[idx, "report_no"] = report_no
        df.loc[idx, "admin_level"] = "national"
        for col in ["province", "health_zone"]:
            if col in df.columns:
                df.loc[idx, col] = ""
        for col, val in fix.items():
            if col != "reporting_date" and col in df.columns:
                df.loc[idx, col] = val
        df.loc[idx, "source"] = "Verified uploaded SitRep tables"
        df.loc[idx, "notes"] = "Corrected from uploaded SitRep surveillance/contact/PoE tables; rates are proportions (0-1)."
    df["_date"] = pd.to_datetime(df["reporting_date"], errors="coerce")
    df = df.sort_values(["_date", "report_no", "admin_level"]).drop(columns=["_date"])
    df.to_csv(path, index=False)
    notes.append("response_indicators.csv updated for N44/N46/N47/N48/N49/N50")


def repair_unventilated(notes: list[str]) -> None:
    path = DATA / "cases_unventilated.csv"
    if not path.exists():
        return
    df = pd.read_csv(path, dtype=str)
    for report_no, (date, count) in UNVENTILATED_FIXES.items():
        mask = df.get("date", "").astype(str) == date
        idx = upsert_by(df, mask)
        values = {
            "date": date,
            "month": date[:7],
            "province": "Ituri",
            "category": "unventilated_unknown_health_zone",
            "confirmed_cases": str(count),
            "confirmed_deaths": "0",
            "source": f"SitRep {report_no}/MVB",
            "source_date": date,
            "notes": "Verified from uploaded SitRep: Autres zones non encore identifiées; not plotted on the map.",
        }
        for col, val in values.items():
            if col in df.columns:
                df.loc[idx, col] = val
    df["_date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.sort_values(["_date", "province"]).drop(columns=["_date"])
    df.to_csv(path, index=False)
    notes.append("cases_unventilated.csv set to 17 unventilated Ituri cases for N44/N46/N47/N48/N49/N50")


def main() -> None:
    notes: list[str] = []
    repair_report_summary(notes)
    repair_response_indicators(notes)
    repair_unventilated(notes)
    body = ["# SitRep repair status", "", f"Updated at: {datetime.now(timezone.utc).isoformat(timespec='seconds')}", "", "## Actions"]
    body.extend([f"- {n}" for n in notes] or ["- No repair needed."])
    STATUS.write_text("\n".join(body) + "\n", encoding="utf-8")
    print("\n".join(notes) if notes else "No SitRep repair needed.")


if __name__ == "__main__":
    main()
