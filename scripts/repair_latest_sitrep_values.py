#!/usr/bin/env python3
"""Repair and validate key values extracted from the latest SitRep.

This is a safety net for layouts where the first-page KPI cards are extracted
out of order. For N47, the card value 609 is patients isolated/hospitalized,
not confirmed deaths. The script prefers stable table/total values and writes
corrected dashboard CSVs before downstream projections are regenerated.
"""
from __future__ import annotations

import csv
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import fitz  # PyMuPDF
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RAW = ROOT / "raw" / "sitreps"
STATUS = ROOT / ".sitrep_repair_status.md"


def norm(s: object) -> str:
    txt = unicodedata.normalize("NFKC", "" if s is None else str(s))
    return re.sub(r"\s+", " ", txt.replace("\xa0", " ").replace("\u202f", " ")).strip()


def num(s: object) -> int | None:
    m = re.search(r"\d[\d\s,.]*", norm(s))
    if not m:
        return None
    raw = m.group(0).replace(" ", "").replace(",", "")
    try:
        return int(float(raw))
    except ValueError:
        return None


def latest_report_row(df: pd.DataFrame) -> tuple[int, pd.Series]:
    d = df.copy()
    d["_date"] = pd.to_datetime(d.get("reporting_date"), errors="coerce")
    idx = d.sort_values(["_date", "report_no"]).index[-1]
    return idx, df.loc[idx]


def pdf_text_for(report_no: str) -> str:
    candidates = sorted(RAW.glob(f"*{report_no.replace('N','')}*.pdf")) + sorted(RAW.glob(f"*{report_no}*.pdf"))
    # Common canonical file name.
    canonical = RAW / f"sitrep_{report_no}.pdf"
    if canonical.exists():
        candidates.insert(0, canonical)
    seen = []
    for c in candidates:
        if c in seen:
            continue
        seen.append(c)
        try:
            doc = fitz.open(c)
            return "\n".join(page.get_text("text") for page in doc)
        except Exception:
            continue
    return ""


def extract_totals_from_text(text: str) -> dict[str, int | str] | None:
    t = norm(text)
    if not t:
        return None
    # Best source: Table 1/2 total row: Total 1 406 438 31,2% ... 73
    matches = re.findall(r"(?:TOTAL|Total)\s+(\d[\d\s]*)\s+(\d[\d\s]*)\s+(?:\d+[,.]\d+%)(?:\s+\d+\s+sur\s+\d+[^\d]+)?(?:\s+(\d{1,4}))?", t, flags=re.I)
    candidates = []
    for a, b, c in matches:
        cases, deaths = num(a), num(b)
        new_cases = num(c) if c else None
        if cases and deaths and cases >= deaths and 100 <= cases <= 20000:
            candidates.append((cases, deaths, new_cases))
    if candidates:
        # Prefer the highest cumulative confirmed cases among table totals.
        cases, deaths, new_cases = sorted(candidates, key=lambda x: x[0])[-1]
        return {"confirmed_cases": cases, "confirmed_deaths": deaths, "new_confirmed_cases": new_cases or ""}
    # Fallback: sentence "cumul s’élève à 1 406 cas confirmés et 438 décès".
    m = re.search(r"cumul\s+s[’']?élève\s+à\s+(\d[\d\s]*)\s+cas\s+confirm[ée]s\s+et\s+(\d[\d\s]*)\s+d[ée]c[èe]s", t, flags=re.I)
    if m:
        return {"confirmed_cases": num(m.group(1)), "confirmed_deaths": num(m.group(2)), "new_confirmed_cases": ""}
    return None


def repair_n47_known_layout() -> list[str]:
    """Apply the verified N47 values from the uploaded 30 Jun 2026 SitRep."""
    notes: list[str] = []
    # report_summary
    rs_path = DATA / "report_summary.csv"
    rs = pd.read_csv(rs_path, dtype=str)
    mask = (rs["report_no"].astype(str) == "N47") | (rs["reporting_date"].astype(str) == "2026-06-30")
    if mask.any():
        idx = rs[mask].index[-1]
    else:
        idx = len(rs)
        rs.loc[idx] = {c: "" for c in rs.columns}
    before = (rs.loc[idx].get("drc_confirmed_cases"), rs.loc[idx].get("drc_confirmed_deaths"))
    rs.loc[idx, "report_no"] = "N47"
    rs.loc[idx, "reporting_date"] = "2026-06-30"
    rs.loc[idx, "publication_date"] = "2026-07-01"
    rs.loc[idx, "drc_confirmed_cases"] = "1406"
    rs.loc[idx, "drc_confirmed_deaths"] = "438"
    if "uganda_confirmed_cases" in rs.columns:
        rs.loc[idx, "uganda_confirmed_cases"] = "20"
    if "uganda_confirmed_deaths" in rs.columns:
        rs.loc[idx, "uganda_confirmed_deaths"] = "2"
    rs.loc[idx, "source"] = "SitRep N47/MVB"
    rs.loc[idx, "notes"] = "Corrected from SitRep N47 table totals: 1,406 confirmed cases and 438 confirmed deaths; 609 is isolated/hospitalized, not deaths."
    rs.to_csv(rs_path, index=False)
    if before != ("1406", "438"):
        notes.append(f"report_summary.csv N47 corrected from {before} to confirmed=1406, deaths=438")

    # unventilated cases
    uv_path = DATA / "cases_unventilated.csv"
    if uv_path.exists():
        uv = pd.read_csv(uv_path, dtype=str)
        m = uv["date"].astype(str).eq("2026-06-30")
        row = {
            "date": "2026-06-30", "month": "2026-06", "province": "Ituri",
            "category": "unventilated_unknown_health_zone", "confirmed_cases": "17",
            "confirmed_deaths": "0", "source": "SitRep N47/MVB", "source_date": "2026-06-30",
            "notes": "Corrected from SitRep N47 table 2: Autres zones non encore identifiées, 17 confirmed cases and 0 deaths; not plotted on the map.",
        }
        if m.any():
            for k, v in row.items():
                if k in uv.columns:
                    uv.loc[m, k] = v
        else:
            uv = pd.concat([uv, pd.DataFrame([row])], ignore_index=True)
        uv.to_csv(uv_path, index=False)
        notes.append("cases_unventilated.csv N47 set to 17 unventilated cases")
    return notes


def main() -> None:
    notes: list[str] = []
    rs_path = DATA / "report_summary.csv"
    if not rs_path.exists():
        STATUS.write_text("# SitRep repair status\n\nreport_summary.csv missing\n", encoding="utf-8")
        return
    rs = pd.read_csv(rs_path, dtype=str)
    idx, row = latest_report_row(rs)
    report_no = str(row.get("report_no") or "")
    current_cases = num(row.get("drc_confirmed_cases"))
    current_deaths = num(row.get("drc_confirmed_deaths"))
    # N47 has a known difficult layout and also needs unventilated correction.
    # Apply the verified table values directly before any generic heuristic.
    if report_no == "N47" or str(row.get("reporting_date")) == "2026-06-30":
        notes.extend(repair_n47_known_layout())
    else:
        text = pdf_text_for(report_no)
        extracted = extract_totals_from_text(text)
        if extracted and extracted.get("confirmed_cases") and extracted.get("confirmed_deaths"):
            c, d = int(extracted["confirmed_cases"]), int(extracted["confirmed_deaths"])
            # Repair if the CSV disagrees with table totals or has implausible CFR.
            if (current_cases != c) or (current_deaths != d) or (current_deaths and current_cases and current_deaths / max(current_cases, 1) > 0.55):
                rs.loc[idx, "drc_confirmed_cases"] = str(c)
                rs.loc[idx, "drc_confirmed_deaths"] = str(d)
                rs.loc[idx, "notes"] = norm(str(rs.loc[idx].get("notes", "")) + " Corrected by repair_latest_sitrep_values.py using SitRep table totals.")
                rs.to_csv(rs_path, index=False)
                notes.append(f"{report_no}: report_summary corrected from {current_cases}/{current_deaths} to {c}/{d}")
    body = ["# SitRep repair status", "", f"Updated at: {datetime.now(timezone.utc).isoformat(timespec='seconds')}", ""]
    body.append("## Actions")
    body.extend([f"- {n}" for n in notes] or ["- No repair needed."])
    STATUS.write_text("\n".join(body) + "\n", encoding="utf-8")
    print("\n".join(notes) if notes else "No SitRep repair needed.")


if __name__ == "__main__":
    main()
