#!/usr/bin/env python3
"""Generate health-zone activity status from cumulative SitRep case rows.

The output supports an operational table that shows, for each affected health zone,
the latest cumulative confirmed cases and the most recent reporting date on which
that health zone's cumulative confirmed cases increased.

Important: this is a reporting-date proxy, not onset date or exposure date.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
STATUS = ROOT / ".health_zone_activity_status.md"
OUT = DATA / "health_zone_activity_status.csv"


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def to_int(x, default=0) -> int:
    try:
        if x is None or str(x).strip() == "":
            return default
        return int(round(float(str(x).replace(",", "").strip())))
    except Exception:
        return default


def parse_date(x: str) -> date | None:
    try:
        return date.fromisoformat(str(x)[:10])
    except Exception:
        return None


def norm_name(x: str) -> str:
    # Harmonize common accent / dash variants so a zone is not duplicated after
    # SitRep formatting changes (e.g. Haut-Uele vs Haut-Uélé).
    txt = str(x or '').strip()
    txt = txt.replace('Haut-Uele', 'Haut-Uélé').replace('Haut Uele', 'Haut-Uélé')
    txt = txt.replace('Nord Kivu', 'Nord-Kivu')
    txt = txt.replace('Nia Nia', 'Nia-Nia')
    return txt


def status_for_days(days: int) -> tuple[str, str, str, str, int]:
    if days <= 21:
        return "active_0_21", "継続警戒", "Active vigilance", "Vigilance active", 1
    if days <= 41:
        return "watch_22_41", "観察継続", "Continue observation", "Observation continue", 2
    return "cooldown_42_plus", "警戒低下候補", "Candidate for lower alert", "Candidat à la baisse d’alerte", 3


def main() -> None:
    summary = read_csv(DATA / "report_summary.csv")
    cases = read_csv(DATA / "cases_by_hz.csv")
    if not summary or not cases:
        raise SystemExit("report_summary.csv and cases_by_hz.csv are required")

    summary = sorted([r for r in summary if r.get("reporting_date")], key=lambda r: r["reporting_date"])
    latest = summary[-1]
    ref_date_s = latest.get("reporting_date", "")
    ref_date = parse_date(ref_date_s)
    if not ref_date:
        raise SystemExit(f"Invalid latest reporting_date: {ref_date_s}")
    ref_no = latest.get("report_no", "")

    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in cases:
        hz = norm_name(r.get("health_zone") or "")
        prov = norm_name(r.get("province") or "")
        d = parse_date(str(r.get("date") or r.get("source_date") or ""))
        cc = to_int(r.get("confirmed_cases"), 0)
        if not hz or not prov or not d or cc <= 0:
            continue
        # Exclude aggregate/unventilated buckets that are not actual health zones.
        hz_norm = hz.lower()
        if "autres" in hz_norm or "non ventil" in hz_norm or "unventil" in hz_norm:
            continue
        grouped[(prov, hz)].append(r)

    out = []
    for (province, hz), rows in grouped.items():
        rows = sorted(rows, key=lambda r: str(r.get("date") or r.get("source_date") or ""))
        prev_cc = None
        current_cc = 0
        last_increase = None
        latest_row = None
        for r in rows:
            d = parse_date(str(r.get("date") or r.get("source_date") or ""))
            if not d or d > ref_date:
                continue
            cc = to_int(r.get("confirmed_cases"), 0)
            # Use the reported cumulative value in the latest SitRep as the current
            # value. Do not carry forward a previous maximum, because DHIS2
            # reconciliation can move or de-duplicate cases between zones.
            if prev_cc is None or cc > prev_cc:
                last_increase = d
            prev_cc = cc
            current_cc = cc
            latest_row = r
        if not latest_row or current_cc <= 0 or not last_increase:
            continue
        days = (ref_date - last_increase).days
        code, ja, en, fr, sort = status_for_days(days)
        deaths = to_int(latest_row.get("confirmed_deaths"), 0)
        out.append({
            "reference_report_no": ref_no,
            "reference_date": ref_date_s,
            "province": province,
            "health_zone": hz,
            "cumulative_confirmed": current_cc,
            "cumulative_deaths": deaths,
            "last_increase_report_date": last_increase.isoformat(),
            "days_since_last_increase": days,
            "activity_status": code,
            "status_label_ja": ja,
            "status_label_en": en,
            "status_label_fr": fr,
            "status_sort": sort,
            "source": latest_row.get("source") or latest.get("source") or "SitRep",
            "notes": "Reporting-date proxy based on the last SitRep date where cumulative confirmed cases increased for this health zone; not onset or exposure date.",
        })

    out.sort(key=lambda r: (int(r["days_since_last_increase"]), -int(r["cumulative_confirmed"]), r["province"], r["health_zone"]))
    fields = [
        "reference_report_no", "reference_date", "province", "health_zone",
        "cumulative_confirmed", "cumulative_deaths", "last_increase_report_date",
        "days_since_last_increase", "activity_status", "status_label_ja", "status_label_en", "status_label_fr",
        "status_sort", "source", "notes"
    ]
    write_csv(OUT, out, fields)
    counts = {"active_0_21": 0, "watch_22_41": 0, "cooldown_42_plus": 0}
    for r in out:
        counts[r["activity_status"]] = counts.get(r["activity_status"], 0) + 1
    STATUS.write_text(
        "\n".join([
            "# Health-zone activity status",
            "",
            f"Updated at: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
            f"Reference SitRep: {ref_no} / {ref_date_s}",
            f"Affected health zones: {len(out)}",
            f"0-21 days: {counts.get('active_0_21',0)}",
            f"22-41 days: {counts.get('watch_22_41',0)}",
            f"42+ days: {counts.get('cooldown_42_plus',0)}",
            "",
        ]),
        encoding="utf-8",
    )
    print(STATUS.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
