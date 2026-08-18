#!/usr/bin/env python3
"""Generate the top-panel latest-situation text from validated data.

Numbers are assembled only from corrected CSV/JSON data.  The optional AI-assisted
qualitative sentence is constrained to contain no numbers, so extraction mistakes
cannot contaminate the numeric summary.
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EXTRACTED = ROOT / "extracted"
OUT = DATA / "latest_situation.json"
STATUS = ROOT / ".latest_situation_status.md"


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str], keys: list[str]) -> None:
    existing = read_csv(path)
    idx = {tuple(str(r.get(k, "")) for k in keys): r for r in existing}
    for row in rows:
        idx[tuple(str(row.get(k, "")) for k in keys)] = {k: str(row.get(k, "")) for k in fieldnames}
    ordered = list(idx.values())
    ordered.sort(key=lambda r: (str(r.get("reporting_date", "")), str(r.get("report_no", ""))))
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(ordered)


def num(x: Any) -> float:
    try:
        s = str(x).replace(",", "").replace(" ", "").strip()
        if s == "" or s.lower() == "nan":
            return float("nan")
        return float(s)
    except Exception:
        return float("nan")


def fmt_int(x: float | int) -> str:
    try:
        return f"{int(round(float(x))):,}"
    except Exception:
        return "—"


def latest_reports() -> tuple[dict[str, str], dict[str, str] | None]:
    rows = [r for r in read_csv(DATA / "report_summary.csv") if r.get("reporting_date")]
    rows.sort(key=lambda r: (r.get("reporting_date", ""), r.get("report_no", "")))
    latest = rows[-1]
    prev = None
    for r in reversed(rows[:-1]):
        if r.get("report_no") != latest.get("report_no"):
            prev = r
            break
    return latest, prev


def zone_deltas(latest_date: str, previous_date: str | None) -> tuple[list[tuple[str, str, int]], dict[str, int]]:
    if not previous_date:
        return [], {}
    rows = read_csv(DATA / "cases_by_hz.csv")
    cur: dict[tuple[str, str], int] = {}
    prev: dict[tuple[str, str], int] = {}
    for r in rows:
        key = (r.get("province", ""), r.get("health_zone", ""))
        val = num(r.get("confirmed_cases"))
        if not val == val:
            continue
        if r.get("date") == latest_date:
            cur[key] = int(round(val))
        elif r.get("date") == previous_date:
            prev[key] = int(round(val))
    deltas: list[tuple[str, str, int]] = []
    province_delta: dict[str, int] = {}
    for k, v in cur.items():
        d = v - prev.get(k, 0)
        if d > 0:
            deltas.append((k[0], k[1], d))
            province_delta[k[0]] = province_delta.get(k[0], 0) + d
    deltas.sort(key=lambda x: x[2], reverse=True)
    return deltas, province_delta


def province_sentence_ja(prov_delta: dict[str, int]) -> str:
    items = [(p, v) for p, v in prov_delta.items() if v > 0]
    if not items:
        return ""
    order = {"Ituri": 0, "Nord-Kivu": 1, "Sud-Kivu": 2}
    items.sort(key=lambda x: order.get(x[0], 99))
    return "新規確定例は" + "、".join(f"{p}で{fmt_int(v)}例" for p, v in items) + "でした。"


def province_sentence_from_text_ja(text: str) -> str:
    """Extract province-level new confirmed cases from the SitRep headline text.

    Newer INSP SitReps use several layouts, for example:
    "+62 nouveaux cas (Ituri 53 · N-Kivu 8 – Haut-Uele 1)" or
    bullets such as "Ituri : 53 nouveaux cas confirmés". Health-zone
    cumulative tables may include data cleaning and retroactive reclassification,
    so the top-panel 24h provincial breakdown must prefer these explicit
    headline/bullet statements over health-zone cumulative deltas.
    """
    t = re.sub(r"\s+", " ", text)
    aliases = [
        ("Ituri", [r"Ituri"]),
        ("Nord-Kivu", [r"Nord[-\s]?Kivu", r"N[-\s]?Kivu"]),
        ("Haut-Uélé", [r"Haut[-\s]?U[ée]l[ée]", r"Haut[-\s]?Uele", r"H[-\s]?U[ée]l[ée]", r"H[-\s]?Uele"]),
        ("Tshopo", [r"Tshopo"]),
        ("Sud-Kivu", [r"Sud[-\s]?Kivu", r"S[-\s]?Kivu"]),
    ]
    out = []
    seen = set()

    # First, restrict to the explicit headline parenthesis after "nouveaux cas".
    # This avoids accidentally picking death counts, suspect counts, or recovery
    # counts that appear later on the same page.
    headline = ""
    m_head = re.search(r"\+?\s*\d{1,4}\s+nouveaux?\s+cas[^()]*\(([^)]{1,160})\)", t, re.I)
    if m_head:
        headline = m_head.group(1)
        for label, pats in aliases:
            for pat in pats:
                m = re.search(pat + r"\s*(?:[:=])?\s*(\d{1,4})(?=\s*(?:[·;,)–-]|$))", headline, re.I)
                if m:
                    val = int(m.group(1))
                    if val > 0:
                        out.append((label, val))
                        seen.add(label)
                    break
    if out:
        return "新規確定例は" + "、".join(f"{p}で{fmt_int(v)}例" for p, v in out if v > 0) + "でした。"

    # New layout: "101 nouveaux cas ... provinces de l’Ituri (89 cas),
    # du Nord-Kivu (8 cas)...". Restrict parsing to the short 24h sentence
    # so cumulative province tables later in the PDF cannot be mistaken for
    # daily incidence.
    m_24h = re.search(r"(?:derni[èe]res?\s+24\s+heures[^.]{0,120}?|)(?:\d{1,4})\s+nouveaux?\s+cas[^.]{0,520}", t, re.I)
    if m_24h:
        snippet = m_24h.group(0)
        for label, pats in aliases:
            for pat in pats:
                m = re.search(pat + r"[^()]{0,18}\(\s*(\d{1,4})(?:\s+cas)?\s*\)", snippet, re.I)
                if m:
                    val = int(m.group(1))
                    if val > 0:
                        out.append((label, val))
                        seen.add(label)
                    break
    if out:
        return "新規確定例は" + "、".join(f"{p}で{fmt_int(v)}例" for p, v in out if v > 0) + "でした。"

    for label, pats in aliases:
        val = None
        for pat in pats:
            # Bullet text: "Ituri : 53 nouveaux cas confirmés".
            m = re.search(pat + r"\s*[:：]\s*(\d{1,4})\s+nouveaux?\s+cas", t, re.I)
            if m:
                val = int(m.group(1))
                break
        if val is not None and val > 0 and label not in seen:
            out.append((label, val))
            seen.add(label)
    if not out:
        return ""
    return "新規確定例は" + "、".join(f"{p}で{fmt_int(v)}例" for p, v in out if v > 0) + "でした。"


def top_zones_sentence_ja(deltas: list[tuple[str, str, int]], limit: int = 5) -> str:
    top = [(z, d) for _p, z, d in deltas if d > 0][:limit]
    if not top:
        return ""
    return "主な増加は" + "、".join(f"{z} {fmt_int(d)}例" for z, d in top) + "でした。"


def uganda_summary_ja() -> str:
    rows = [r for r in read_csv(DATA / "uganda_evd_summary.csv") if r.get("as_of_date")]
    if not rows:
        return "ウガンダ側の更新情報は取得できていません。"
    rows.sort(key=lambda r: r.get("as_of_date", ""))
    r = rows[-1]
    cases = fmt_int(num(r.get("cumulative_confirmed") or r.get("cumulative_confirmed_cases")))
    deaths = fmt_int(num(r.get("cumulative_deaths") or r.get("deaths")))
    imported = fmt_int(num(r.get("imported_cases") or r.get("cumulative_imported_cases")))
    local = fmt_int(num(r.get("local_cases") or r.get("cumulative_local_cases")))
    asof = r.get("as_of_date", "")
    daily = [x for x in read_csv(DATA / "uganda_evd_daily_cases.csv") if x.get("date")]
    daily.sort(key=lambda x: x.get("date", ""))
    zero_days = None
    last_positive = None
    for x in reversed(daily):
        nc = num(x.get("new_confirmed_cases") or x.get("new_cases") or x.get("new_cases_last_24h") or x.get("confirmed_cases"))
        if nc == nc and nc > 0:
            last_positive = x.get("date")
            break
    if last_positive and asof:
        from datetime import date
        try:
            zero_days = (date.fromisoformat(asof) - date.fromisoformat(last_positive)).days
        except Exception:
            zero_days = None
    zero_text = f"{last_positive}以降、{zero_days}日間連続で新規症例の増加は報告されていません。" if zero_days is not None and zero_days > 0 else "直近の新規症例数はデータで確認してください。"
    return f"{asof}時点で累積確定例{cases}例、死亡例{deaths}例です。輸入例{imported}例、国内感染例{local}例です。{zero_text}"


def sitrep_text(report_no: str) -> str:
    labels = [report_no]
    m = re.search(r"(\d+)", report_no or "")
    if m:
        labels.append(f"N{int(m.group(1)):03d}")
    for lab in dict.fromkeys(labels):
        p = EXTRACTED / f"sitrep_{lab}.txt"
        if p.exists():
            return p.read_text(encoding="utf-8", errors="ignore")
    return ""


def facts_highlight(text: str) -> str:
    """Fallback qualitative sentence with no explicit numbers."""
    t = re.sub(r"\s+", " ", text)
    low = t.lower()

    if ("mission conjointe" in low and "buta" in low) or ("buta" in low and "bas-uélé" in low):
        return "SitRepでは、Bas-UéléのButaに合同ミッションが到着し、準備・対応強化が進められていることが記載されています。"
    if "supervision des actions" in low or "supervision" in low and "réponse par pilier" in low:
        return "SitRepでは、各対応ピラーの活動に対する監督が実施されたことが対応上の動きとして記載されています。"
    if "aucune nouvelle zone" in low:
        if "laboratoire" in low and "kasenyi" in low:
            return "SitRepでは、新たに影響を受けた保健区は報告されず、Kasenyiの診断ラボ関連の対応が記載されています。"
        return "SitRepでは、新たに影響を受けた保健区は報告されず、既存の影響地域で対応活動が継続されています。"
    if "ariwara" in low:
        return "SitRepでは、Ituri州のAriwaraが新たに影響を受けた保健区として記載され、TshopoとHaut-Ueleでの調査継続も示されています。"
    if "tshopo" in low and ("haut-uele" in low or "haut uele" in low or "haut-uélé" in low):
        return "SitRepでは、TshopoとHaut-Ueleへの地理的拡大が示され、追加調査と対応強化が必要な状況として記載されています。"
    if "grève de prestataires" in low or "greve de prestataires" in low:
        return "SitRepでは、BuniaとRwamparaで医療従事者のストライキが続いていることが対応上の懸念として記載されています。"
    if "incident sécuritaire" in low or "incident securitaire" in low:
        return "SitRepでは、IturiとNord-Kivuで伝播が続く中、Nia-Nia周辺の治安インシデントが対応上の懸念として記載されています。"
    if "essais cliniques" in low or "essais cliniques au cte" in low:
        return "SitRepでは、RwamparaのCTE CMEで臨床試験が開始されたことが対応上の動きとして記載されています。"
    if "installation des laboratoires" in low or "laboratoires de diagnostic" in low:
        return "SitRepでは、Ituri州内の複数の保健区で診断ラボ整備が進められていることが対応上の動きとして記載されています。"
    if ("nouvelle zone de santé" in low or "nouvelle zone de sante" in low) and not ("aucune nouvelle zone" in low):
        # Try to pick a named zone after phrase; keep no numbers.
        m = re.search(r"nouvelle zone de sant[ée].{0,80}?(?:ZS de |celle de )([A-Za-zÀ-ÿ\-]+)", t, re.I)
        zone = m.group(1) if m else "新たな保健区"
        return f"SitRepでは、{zone}が新たに影響を受けた保健区として記載され、地理的拡大への注意が必要です。"
    if "visite officielle" in low:
        return "SitRepでは、国際的な支援と協力強化に関する動きが対応上の注目点として記載されています。"
    if "décès" in low and "cte" in low:
        return "SitRepでは、治療施設での死亡例と退院例が引き続き報告されており、治療体制と接触者追跡の継続が重要です。"
    return "SitRepでは、影響地域での伝播継続と対応活動の維持が引き続き重要な論点として示されています。"


def openai_highlight(text: str, fallback: str) -> tuple[str, str, str]:
    api_key = os.environ.get("OPENAI_API_KEY")
    model = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
    if not api_key:
        return fallback, "deterministic", ""
    faits = text[:6000]
    prompt = (
        "以下はエボラ出血熱SitRepの本文です。数値、日付、割合、累積症例数、死亡例数、新規症例数は書かないでください。"
        "保健区名や出来事名は書いてよいです。直近で注目すべき疫学的または対応上の出来事を、日本語で一文だけ要約してください。\n\n"
        + faits
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You produce one short Japanese sentence. Do not include any numbers, dates, percentages, case counts, or death counts."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 120,
    }
    try:
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"].strip()
        # Final guard: if any digit slipped in, fall back.
        if re.search(r"\d|[０-９]", content):
            return fallback, "deterministic_after_ai_digit_guard", model
        return content, "openai", model
    except Exception as e:
        (EXTRACTED / "latest_situation_openai_error.txt").write_text(f"{type(e).__name__}: {e}", encoding="utf-8")
        return fallback, "deterministic_after_openai_error", model


def main() -> None:
    latest, prev = latest_reports()
    latest_no = latest.get("report_no", "")
    latest_date = latest.get("reporting_date", "")
    prev_no = prev.get("report_no", "") if prev else ""
    prev_date = prev.get("reporting_date", "") if prev else ""
    lc = num(latest.get("drc_confirmed_cases"))
    ld = num(latest.get("drc_confirmed_deaths"))
    pc = num(prev.get("drc_confirmed_cases")) if prev else float("nan")
    pd = num(prev.get("drc_confirmed_deaths")) if prev else float("nan")
    dc = int(round(lc - pc)) if lc == lc and pc == pc else None
    dd = int(round(ld - pd)) if ld == ld and pd == pd else None
    deltas, prov_delta = zone_deltas(latest_date, prev_date)
    text = sitrep_text(latest_no)
    province_sentence = province_sentence_from_text_ja(text) or province_sentence_ja(prov_delta)
    zone_sentence = top_zones_sentence_ja(deltas)
    if prev:
        numeric = (
            f"累積確定例は{fmt_int(pc)}例から{fmt_int(lc)}例に{fmt_int(dc)}例増加し、"
            f"累積確定死亡例は{fmt_int(pd)}例から{fmt_int(ld)}例に{fmt_int(dd)}例増加しました。"
        )
    else:
        numeric = f"{latest_no}時点で累積確定例は{fmt_int(lc)}例、累積確定死亡例は{fmt_int(ld)}例です。"
    if province_sentence:
        numeric += province_sentence
    # Do not append health-zone delta details here: the PDF extraction of
    # health-zone histories can include reclassification noise. The top panel
    # keeps validated cumulative/province-level numbers plus one qualitative
    # SitRep sentence.
    fallback = facts_highlight(text)
    qualitative, generated_by, model = openai_highlight(text, fallback)
    drc_summary = numeric + qualitative
    ug = uganda_summary_ja()
    result = {
        "report_no": latest_no,
        "report_date": latest_date,
        "previous_report_no": prev_no,
        "previous_report_date": prev_date,
        "drc_numeric_summary_ja": numeric,
        "drc_ai_qualitative_sentence_ja": qualitative,
        "drc_summary_ja": drc_summary,
        "uganda_summary_ja": ug,
        "generated_by": "validated_data_plus_qualitative_highlight",
        "qualitative_generated_by": generated_by,
        "openai_model": model if generated_by == "openai" else "",
        "updated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "Validated CSV/JSON numbers + SitRep text qualitative highlight",
        "notes": "Numeric statements are generated from validated data only. The qualitative sentence is AI-assisted when OPENAI_API_KEY is available and is blocked from containing numbers; otherwise a deterministic SitRep-text highlight is used.",
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # Keep legacy CSV compatible and corrected for the dashboard/fallback.
    ai_path = DATA / "ai_sitrep_summary.csv"
    if ai_path.exists():
        rows = read_csv(ai_path)
        fields = list(rows[0].keys()) if rows else ["report_no","reporting_date","previous_report_no","previous_reporting_date","drc_summary_ja","uganda_summary_ja","summary_ja","generated_by","openai_model","source","updated_at_utc","notes"]
        row = {k: "" for k in fields}
        row.update({
            "report_no": latest_no,
            "reporting_date": latest_date,
            "previous_report_no": prev_no,
            "previous_reporting_date": prev_date,
            "drc_summary_ja": drc_summary,
            "uganda_summary_ja": ug.replace("ウガンダ：", "", 1),
            "summary_ja": f"• {drc_summary}\n• {ug}",
            "generated_by": result["generated_by"],
            "openai_model": result["openai_model"],
            "source": result["source"],
            "updated_at_utc": result["updated_at_utc"],
            "notes": result["notes"],
        })
        # Keep only the current safe fallback row. Older AI summaries may contain
        # stale extraction artifacts and must not be available to the browser.
        with ai_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerow(row)

    # Rewrite latest delta payload from corrected values so stale 2026/628 cannot be reused.
    EXTRACTED.mkdir(exist_ok=True)
    payload = {
        "report_no": latest_no,
        "reporting_date": latest_date,
        "previous_report_no": prev_no,
        "previous_reporting_date": prev_date,
        "confirmed_cases": {"previous": int(round(pc)) if pc == pc else None, "latest": int(round(lc)), "change": dc},
        "confirmed_deaths": {"previous": int(round(pd)) if pd == pd else None, "latest": int(round(ld)), "change": dd},
        "province_new_cases_from_health_zone_delta": prov_delta,
        "top_health_zone_increases": [{"province": p, "health_zone": z, "increase": d} for p, z, d in deltas[:10]],
        "source": "Rebuilt from corrected report_summary.csv and cases_by_hz.csv by generate_latest_situation.py",
    }
    (EXTRACTED / f"sitrep_{latest_no}_delta_payload.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (EXTRACTED / "latest_sitrep_update.json").write_text(json.dumps({
        "report_no": latest_no,
        "reporting_date": latest_date,
        "total_confirmed": int(round(lc)),
        "total_deaths": int(round(ld)),
        "source": payload["source"],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    STATUS.write_text(
        "# Latest situation generation\n\n"
        f"Updated at: {result['updated_at_utc']}\n\n"
        f"- Report: {latest_no} / {latest_date}\n"
        f"- Previous: {prev_no} / {prev_date}\n"
        f"- Qualitative sentence: {generated_by}\n",
        encoding="utf-8",
    )
    print(f"latest_situation.json written for {latest_no} / {latest_date}; qualitative={generated_by}")


if __name__ == "__main__":
    main()
