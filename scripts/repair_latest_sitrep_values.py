#!/usr/bin/env python3
"""Repair recent SitRep totals before dashboard projections.

The SitRep first-page KPI cards can be misread by PDF text extraction: the year
2026 may be captured as cumulative cases and the isolated/hospitalized count
(e.g. 628) as deaths. This safety net applies verified table-total corrections
for recent problematic SitReps and keeps zone/unventilated totals consistent.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
STATUS = ROOT / ".sitrep_repair_status.md"

VERIFIED = {
    "N47": dict(reporting_date="2026-06-30", publication_date="2026-07-01", cases=1406, deaths=438, new_cases=73, hospitalised=609),
    "N48": dict(reporting_date="2026-07-01", publication_date="2026-07-02", cases=1460, deaths=452, new_cases=54, hospitalised=641),
    "N49": dict(reporting_date="2026-07-02", publication_date="2026-07-03", cases=1502, deaths=473, new_cases=42, hospitalised=628),
    "N50": dict(reporting_date="2026-07-03", publication_date="2026-07-04", cases=1528, deaths=492, new_cases=26, hospitalised=628),
}

PROVINCE_FIX = {"Mangala": "Ituri", "Miti-Murhesa": "Sud-Kivu", "Vuhovi": "Nord-Kivu", "Nia-Nia": "Ituri"}

N48_ROWS = {
    ("Ituri", "Bunia"): (416, 103), ("Ituri", "Rwampara"): (308, 63), ("Ituri", "Mongbwalu"): (270, 127),
    ("Ituri", "Nyankunde"): (95, 16), ("Ituri", "Nizi"): (65, 19), ("Ituri", "Lita"): (32, 11),
    ("Ituri", "Mangala"): (24, 12), ("Ituri", "Komanda"): (19, 5), ("Ituri", "Bambu"): (16, 5),
    ("Ituri", "Tchomia"): (14, 2), ("Ituri", "Nia-Nia"): (12, 5), ("Ituri", "Kilo"): (9, 1),
    ("Ituri", "Logo"): (7, 0), ("Ituri", "Aungba"): (6, 2), ("Ituri", "Damas"): (5, 0),
    ("Ituri", "Rimba"): (3, 0), ("Ituri", "Aru"): (3, 1), ("Ituri", "Drodro"): (3, 3),
    ("Ituri", "Kambala"): (2, 2), ("Ituri", "Mambasa"): (2, 1), ("Ituri", "Mandima"): (2, 1),
    ("Ituri", "Gety"): (1, 0), ("Ituri", "Fataki"): (1, 0), ("Ituri", "Lolwa"): (1, 1),
    ("Nord-Kivu", "Katwa"): (49, 35), ("Nord-Kivu", "Butembo"): (34, 15), ("Nord-Kivu", "Beni"): (26, 15),
    ("Nord-Kivu", "Oicha"): (3, 2), ("Nord-Kivu", "Kyondo"): (3, 2), ("Nord-Kivu", "Kalunguta"): (2, 1),
    ("Nord-Kivu", "Musienene"): (3, 2), ("Nord-Kivu", "Goma"): (1, 0), ("Nord-Kivu", "Masereka"): (1, 0),
    ("Nord-Kivu", "Vuhovi"): (1, 1), ("Nord-Kivu", "Mabalako"): (1, 0),
    ("Sud-Kivu", "Miti-Murhesa"): (3, 1),
}


def report_no_int(s: object) -> int:
    m = re.search(r"(\d+)", str(s or ""))
    return int(m.group(1)) if m else -1


def upsert_reports(notes: list[str]) -> None:
    path = DATA / "report_summary.csv"
    rs = pd.read_csv(path, dtype=str)
    for rep, vals in VERIFIED.items():
        mask = rs["report_no"].astype(str).eq(rep) | rs["reporting_date"].astype(str).eq(vals["reporting_date"])
        idx = rs[mask].index[-1] if mask.any() else len(rs)
        if not mask.any():
            rs.loc[idx] = {c: "" for c in rs.columns}
        before = (str(rs.loc[idx].get("drc_confirmed_cases", "")), str(rs.loc[idx].get("drc_confirmed_deaths", "")))
        rs.loc[idx, "report_no"] = rep
        rs.loc[idx, "reporting_date"] = vals["reporting_date"]
        rs.loc[idx, "publication_date"] = vals["publication_date"]
        rs.loc[idx, "drc_confirmed_cases"] = str(vals["cases"])
        rs.loc[idx, "drc_confirmed_deaths"] = str(vals["deaths"])
        if "uganda_confirmed_cases" in rs.columns:
            rs.loc[idx, "uganda_confirmed_cases"] = "20"
        if "uganda_confirmed_deaths" in rs.columns:
            rs.loc[idx, "uganda_confirmed_deaths"] = "2"
        rs.loc[idx, "source"] = f"SitRep {rep}/MVB"
        rs.loc[idx, "notes"] = f"Corrected from SitRep {rep} Table 1/2 totals: {vals['cases']:,} confirmed cases and {vals['deaths']:,} confirmed deaths. The card value {vals['hospitalised']:,} is isolated/hospitalized patients, not confirmed deaths."
        after = (str(vals["cases"]), str(vals["deaths"]))
        if before != after:
            notes.append(f"{rep} report_summary corrected from {before} to confirmed={after[0]}, deaths={after[1]}")
    rs["_date"] = pd.to_datetime(rs["reporting_date"], errors="coerce")
    rs["_no"] = rs["report_no"].map(report_no_int)
    rs = rs.sort_values(["_date", "_no"]).drop(columns=["_date", "_no"])
    rs.to_csv(path, index=False)


def coord_lookup(cb: pd.DataFrame, hz: str):
    rows = cb[(cb["health_zone"].astype(str) == hz) & cb.get("zone_id", pd.Series(index=cb.index)).notna()]
    if not rows.empty:
        r = rows.iloc[-1]
        return r.get("zone_id", ""), r.get("lat", ""), r.get("lon", "")
    return "", "", ""


def upsert_case_row(cb: pd.DataFrame, date: str, province: str, hz: str, cases: int, deaths: int, source: str) -> pd.DataFrame:
    m = cb["date"].astype(str).eq(date) & cb["health_zone"].astype(str).eq(hz)
    zid, lat, lon = coord_lookup(cb, hz)
    row = {"date": date, "month": date[:7], "province": province, "health_zone": hz, "zone_id": zid,
           "confirmed_cases": cases, "confirmed_deaths": deaths, "lat": lat, "lon": lon,
           "source": source, "source_date": date,
           "notes": "Corrected from SitRep province/health-zone table; rows without dashboard geometry are retained in totals but hidden on the case map."}
    if m.any():
        for k, v in row.items():
            if k in cb.columns:
                cb.loc[m, k] = v
    else:
        cb = pd.concat([cb, pd.DataFrame([row])], ignore_index=True)
    return cb


def repair_cases(notes: list[str]) -> None:
    cb_path = DATA / "cases_by_hz.csv"
    cb = pd.read_csv(cb_path)
    for hz, prov in PROVINCE_FIX.items():
        mask = cb["health_zone"].astype(str).eq(hz) & (cb["province"].isna() | cb["province"].astype(str).eq(""))
        if mask.any():
            cb.loc[mask, "province"] = prov
            notes.append(f"province set to {prov} for {hz} rows")
    for (prov, hz), (cases, deaths) in N48_ROWS.items():
        cb = upsert_case_row(cb, "2026-07-01", prov, hz, cases, deaths, "SitRep N48/MVB")
    for date, source in [("2026-07-02", "SitRep N49/MVB"), ("2026-07-03", "SitRep N50/MVB")]:
        cb = upsert_case_row(cb, date, "Ituri", "Nia-Nia", 12, 5, source)
    for hz, prov in PROVINCE_FIX.items():
        mask = cb["health_zone"].astype(str).eq(hz) & (cb["province"].isna() | cb["province"].astype(str).eq(""))
        cb.loc[mask, "province"] = prov
    cb["_date"] = pd.to_datetime(cb["date"], errors="coerce")
    cb = cb.sort_values(["_date", "province", "health_zone"], na_position="last").drop(columns=["_date"])
    cb.to_csv(cb_path, index=False)
    notes.append("cases_by_hz.csv repaired for N48-N50 consistency")


def repair_unventilated(notes: list[str]) -> None:
    uv_path = DATA / "cases_unventilated.csv"
    uv = pd.read_csv(uv_path, dtype=str)
    for rep, vals in VERIFIED.items():
        date = vals["reporting_date"]
        row = {"date": date, "month": date[:7], "province": "Ituri", "category": "unventilated_unknown_health_zone",
               "confirmed_cases": "17", "confirmed_deaths": "0", "source": f"SitRep {rep}/MVB", "source_date": date,
               "notes": f"Corrected from SitRep {rep} table row: Autres zones non encore identifiées, 17 confirmed cases and 0 deaths; not plotted on the map."}
        mask = uv["date"].astype(str).eq(date) & uv["category"].astype(str).eq("unventilated_unknown_health_zone")
        if mask.any():
            for k, v in row.items():
                if k in uv.columns:
                    uv.loc[mask, k] = v
        else:
            uv = pd.concat([uv, pd.DataFrame([row])], ignore_index=True)
    uv["_date"] = pd.to_datetime(uv["date"], errors="coerce")
    uv = uv.sort_values(["_date", "source"]).drop(columns=["_date"])
    uv.to_csv(uv_path, index=False)
    notes.append("cases_unventilated.csv set to 17 cases for N47-N50")


def repair_ai_summary(notes: list[str]) -> None:
    path = DATA / "ai_sitrep_summary.csv"
    if not path.exists():
        return
    ai = pd.read_csv(path, dtype=str)
    rows = {
        "N48": ("2026-07-01", "N47", "2026-06-30", "DRCでは、7月1日に新規確定例54例、うち死亡例9例が報告され、累積確定例は1,460例、確定死亡例は452例となりました。影響を受けた保健区は3州36保健区で、前日から新たな保健区の追加はありません。"),
        "N49": ("2026-07-02", "N48", "2026-07-01", "DRCでは、7月2日に新規確定例42例が報告され、累積確定例は1,502例、確定死亡例は473例となりました。Ituriで33例、Nord-Kivuで9例が報告されています。"),
        "N50": ("2026-07-03", "N49", "2026-07-02", "DRCでは、7月3日に新規確定例26例、うち死亡例9例が報告され、累積確定例は1,528例、確定死亡例は492例となりました。Ituriで21例、Nord-Kivuで5例が報告されています。"),
    }
    for rep, (date, prev, prev_date, drc) in rows.items():
        mask = ai["report_no"].astype(str).eq(rep)
        idx = ai[mask].index[-1] if mask.any() else len(ai)
        if not mask.any():
            ai.loc[idx] = {c: "" for c in ai.columns}
        ai.loc[idx, "report_no"] = rep
        ai.loc[idx, "reporting_date"] = date
        ai.loc[idx, "previous_report_no"] = prev
        ai.loc[idx, "previous_reporting_date"] = prev_date
        ai.loc[idx, "drc_summary_ja"] = drc
        ai.loc[idx, "uganda_summary_ja"] = "ウガンダでは、累積確定例20例、死亡例2例です。輸入例15例、国内感染例5例で、直近の新規確定例は報告されていません。"
        ai.loc[idx, "summary_ja"] = drc + "\nウガンダでは、累積確定例20例、死亡例2例です。"
        ai.loc[idx, "generated_by"] = "manual_correction"
        ai.loc[idx, "openai_model"] = "none"
        ai.loc[idx, "source"] = f"Corrected SitRep {rep} table totals + Uganda MoH EVD daily page"
        ai.loc[idx, "updated_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        ai.loc[idx, "notes"] = "Corrected after detecting extraction errors that confused year 2026 or isolated/hospitalized count with cumulative cases/deaths."
    ai["_date"] = pd.to_datetime(ai["reporting_date"], errors="coerce")
    ai["_no"] = ai["report_no"].map(report_no_int)
    ai = ai.sort_values(["_date", "_no"]).drop(columns=["_date", "_no"])
    ai.to_csv(path, index=False)
    notes.append("ai_sitrep_summary.csv corrected for N48-N50")


def main() -> None:
    notes: list[str] = []
    upsert_reports(notes)
    repair_cases(notes)
    repair_unventilated(notes)
    repair_ai_summary(notes)
    body = ["# SitRep repair status", "", f"Updated at: {datetime.now(timezone.utc).isoformat(timespec='seconds')}", "", "## Actions"]
    body.extend([f"- {n}" for n in notes] or ["- No repair needed."])
    STATUS.write_text("\n".join(body) + "\n", encoding="utf-8")
    print("\n".join(notes) if notes else "No SitRep repair needed.")


if __name__ == "__main__":
    main()
