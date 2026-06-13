#!/usr/bin/env python3
"""Update Uganda EVD daily summary for the dashboard.

Source: https://evd-daily.health.go.ug/

This script is designed for GitHub Actions. It fetches the Uganda Ministry of
Health Ebola Updates page and writes:
- data/uganda_evd_summary.csv for the latest Uganda KPI cards;
- data/uganda_evd_history.csv as an as-of-date history;
- data/uganda_evd_daily_cases.csv when a daily confirmation time series can be parsed.

The dashboard and SitRep delta summary use these files to describe whether
Uganda has reported recent increases.
"""

from __future__ import annotations

import csv
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone, date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
STATUS = ROOT / ".uganda_evd_update_status.md"
URL = "https://evd-daily.health.go.ug/"
USER_AGENT = "Mozilla/5.0 (compatible; DRC-Ebola-Dashboard-Uganda-Updater/1.0; +https://github.com/)"
TIMEOUT = 45

MONTHS = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
}


def norm_text(value: object) -> str:
    txt = unicodedata.normalize("NFKC", "" if value is None else str(value))
    txt = txt.replace("\xa0", " ").replace("\u202f", " ")
    txt = re.sub(r"\s+", " ", txt)
    return txt.strip()


def to_int(value: object) -> int | None:
    txt = norm_text(value)
    m = re.search(r"-?\d[\d,.\s]*", txt)
    if not m:
        return None
    raw = m.group(0).replace(" ", "").replace(",", "")
    try:
        return int(float(raw))
    except ValueError:
        return None


def parse_english_date(label: str) -> str | None:
    txt = norm_text(label)
    m = re.search(r"(?:as of\s+)?(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)?,?\s*(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", txt, re.I)
    if not m:
        return None
    day = int(m.group(1))
    mon = MONTHS.get(m.group(2).lower())
    if not mon:
        return None
    return f"{m.group(3)}-{mon}-{day:02d}"


def parse_chart_date(label: str, fallback_year: int | None = None) -> str | None:
    txt = norm_text(label)
    m = re.search(r"(\d{1,2})[-/ ]([A-Za-z]{3,9})[-/ ](\d{2,4})", txt, re.I)
    if not m:
        return None
    day = int(m.group(1))
    mon_txt = m.group(2).lower()
    mon_lookup = {
        "jan": "01", "january": "01", "feb": "02", "february": "02", "mar": "03", "march": "03",
        "apr": "04", "april": "04", "may": "05", "jun": "06", "june": "06", "jul": "07", "july": "07",
        "aug": "08", "august": "08", "sep": "09", "sept": "09", "september": "09", "oct": "10", "october": "10",
        "nov": "11", "november": "11", "dec": "12", "december": "12",
    }
    mon = mon_lookup.get(mon_txt)
    if not mon:
        return None
    year_txt = m.group(3)
    if len(year_txt) == 2:
        year = 2000 + int(year_txt)
    else:
        year = int(year_txt)
    if fallback_year and not year:
        year = fallback_year
    return f"{year:04d}-{mon}-{day:02d}"


def extract_daily_cases_from_html(html: str, as_of_date: str | None) -> list[dict[str, object]]:
    """Parse the Cases by date of confirmation chart if the page embeds labels/data in JS.

    This is intentionally conservative. If parsing fails, the history file still
    permits cumulative comparisons, and the summary falls back to that.
    """
    fallback_year = int(as_of_date[:4]) if as_of_date else None
    candidates: list[tuple[list[str], list[int]]] = []

    # Common Chart.js pattern:
    # labels: ["15-May-26", ...], datasets: [{ data: [1,0,...] }]
    for m in re.finditer(r"labels\s*:\s*\[([^\]]+)\].{0,2500}?data\s*:\s*\[([^\]]+)\]", html, re.I | re.S):
        labels_raw, data_raw = m.group(1), m.group(2)
        labels = [norm_text(x.strip().strip("'\"")) for x in re.split(r",", labels_raw) if norm_text(x.strip().strip("'\""))]
        nums = [to_int(x) for x in re.split(r",", data_raw)]
        vals = [int(x or 0) for x in nums]
        if labels and vals and len(labels) == len(vals) and any(parse_chart_date(x, fallback_year) for x in labels):
            candidates.append((labels, vals))

    # More generic JSON-ish objects: categories: [...], series: [{data:[...]}]
    for m in re.finditer(r"categories\s*:\s*\[([^\]]+)\].{0,2500}?data\s*:\s*\[([^\]]+)\]", html, re.I | re.S):
        labels_raw, data_raw = m.group(1), m.group(2)
        labels = [norm_text(x.strip().strip("'\"")) for x in re.split(r",", labels_raw) if norm_text(x.strip().strip("'\""))]
        nums = [to_int(x) for x in re.split(r",", data_raw)]
        vals = [int(x or 0) for x in nums]
        if labels and vals and len(labels) == len(vals) and any(parse_chart_date(x, fallback_year) for x in labels):
            candidates.append((labels, vals))

    if not candidates:
        return []
    # Prefer the longest date-like series; this should correspond to the cases chart.
    labels, vals = sorted(candidates, key=lambda x: len(x[0]), reverse=True)[0]
    rows = []
    for lab, val in zip(labels, vals):
        d = parse_chart_date(lab, fallback_year)
        if d:
            rows.append({
                "date": d,
                "date_label": lab,
                "confirmed_cases": val,
                "source_url": URL,
                "notes": "Parsed from Uganda MoH Cases by date of confirmation chart."
            })
    rows.sort(key=lambda r: str(r["date"]))
    return rows


def read_csv_rows(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv_rows(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in fields})


def upsert_by_key(existing: list[dict[str, object]], new_rows: list[dict[str, object]], keys: list[str]) -> list[dict[str, object]]:
    out = {tuple(str(r.get(k, "")) for k in keys): dict(r) for r in existing}
    for r in new_rows:
        out[tuple(str(r.get(k, "")) for k in keys)] = dict(r)
    return list(out.values())


def value_before_label(text: str, label: str) -> int | None:
    # The Uganda page generally renders as "19 Cumulative confirmed cases".
    pattern = rf"(\d[\d,.\s]*)\s+{label}"
    m = re.search(pattern, text, re.I)
    return to_int(m.group(1)) if m else None


def value_after_label(text: str, label: str) -> int | None:
    pattern = rf"{label}\s+(\d[\d,.\s]*)"
    m = re.search(pattern, text, re.I)
    return to_int(m.group(1)) if m else None


def pair_imported_local(text: str) -> tuple[int | None, int | None]:
    m = re.search(r"(\d[\d,.\s]*)\s+Imported Cases\s+(\d[\d,.\s]*)\s+Local cases", text, re.I)
    if not m:
        return None, None
    return to_int(m.group(1)), to_int(m.group(2))


def scrape() -> tuple[dict[str, object], list[dict[str, object]]]:
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    r = requests.get(URL, headers=headers, timeout=TIMEOUT)
    r.raise_for_status()
    html = r.text
    soup = BeautifulSoup(html, "html.parser")
    text = norm_text(soup.get_text(" ", strip=True))

    date_label = None
    m = re.search(r"As of\s+((?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+\d{1,2}\s+[A-Za-z]+\s+\d{4})", text, re.I)
    if m:
        date_label = norm_text(m.group(1))
    as_of_date = parse_english_date(date_label or "")
    daily_cases = extract_daily_cases_from_html(html, as_of_date)

    imported, local = pair_imported_local(text)
    row = {
        "as_of_date": as_of_date or "",
        "as_of_label": date_label or "",
        "cumulative_confirmed_cases": value_before_label(text, r"Cumulative confirmed cases"),
        "imported_cases": imported,
        "local_cases": local,
        "new_cases_last_24h": value_before_label(text, r"new cases\s*\(last 24 hrs\)"),
        "current_admissions": value_before_label(text, r"Current admissions"),
        "recoveries": value_before_label(text, r"Recoveries"),
        "cumulative_deaths": value_before_label(text, r"Cumulative deaths"),
        "total_persons_tested": value_before_label(text, r"Total persons tested"),
        "all_time_contacts_listed": value_before_label(text, r"All-time contacts listed"),
        "active_contacts_under_followup": value_before_label(text, r"Active contacts\s*\(under follow-up\)"),
        "completed_21day_followup": value_before_label(text, r"Completed 21-day follow-up"),
        "total_alerts": value_before_label(text, r"Total alerts"),
        "alerts_verified": value_before_label(text, r"Alerts verified"),
        "poe_screened_last_24h": value_before_label(text, r"Screened\s*\(last 24 hrs\)"),
        "poe_inbound_last_24h": value_before_label(text, r"Inbound\s*\(last 24 hrs\)"),
        "poe_outbound_last_24h": value_before_label(text, r"Outbound\s*\(last 24 hrs\)"),
        "source_url": URL,
        "notes": "Updated from Uganda Ministry of Health Ebola Updates dashboard.",
    }

    required = ["as_of_date", "cumulative_confirmed_cases", "cumulative_deaths"]
    missing = [k for k in required if row.get(k) in (None, "")]
    if missing:
        raise RuntimeError(f"Could not parse required Uganda EVD fields: {missing}. Text preview: {text[:500]}")

    return row, daily_cases


def main() -> None:
    DATA.mkdir(exist_ok=True)
    try:
        row, daily_cases = scrape()
    except Exception as exc:
        STATUS.write_text(
            "# ⚠️ Uganda EVD daily update needs review\n\n"
            f"The Uganda EVD daily page could not be parsed automatically.\n\n"
            f"Source: {URL}\n\nError: {exc}\n",
            encoding="utf-8",
        )
        raise SystemExit(2)

    out = DATA / "uganda_evd_summary.csv"
    fields = [
        "as_of_date", "as_of_label", "cumulative_confirmed_cases",
        "imported_cases", "local_cases", "new_cases_last_24h",
        "current_admissions", "recoveries", "cumulative_deaths",
        "total_persons_tested", "all_time_contacts_listed",
        "active_contacts_under_followup", "completed_21day_followup",
        "total_alerts", "alerts_verified", "poe_screened_last_24h",
        "poe_inbound_last_24h", "poe_outbound_last_24h", "source_url", "notes",
    ]
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerow(row)

    # Preserve as-of-date history so downstream summaries can say whether
    # Uganda has reported no increase for X days.
    history_out = DATA / "uganda_evd_history.csv"
    history_fields = fields + ["fetched_at_utc"]
    hist_row = dict(row)
    hist_row["fetched_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    history_rows = upsert_by_key(read_csv_rows(history_out), [hist_row], ["as_of_date"])
    history_rows.sort(key=lambda r: str(r.get("as_of_date", "")))
    write_csv_rows(history_out, history_fields, history_rows)

    daily_out = DATA / "uganda_evd_daily_cases.csv"
    daily_fields = ["date", "date_label", "confirmed_cases", "source_url", "notes"]
    if daily_cases:
        daily_rows = upsert_by_key(read_csv_rows(daily_out), daily_cases, ["date"])
        daily_rows.sort(key=lambda r: str(r.get("date", "")))
        write_csv_rows(daily_out, daily_fields, daily_rows)

    meta = {
        "updated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_url": URL,
        "parsed": row,
        "daily_cases_rows_parsed": len(daily_cases),
    }
    STATUS.write_text("# ✅ Uganda EVD daily update completed\n\n" + json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
