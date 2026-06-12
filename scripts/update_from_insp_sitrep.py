#!/usr/bin/env python3
"""Update the static dashboard from the latest INSP DRC SitRep.

This script is designed for GitHub Actions. It:
1) finds the newest INSP SitRep article under https://insp.cd/category/sitrep/;
2) opens the article and tries to obtain the embedded SitRep PDF;
3) extracts key epidemiological and response indicators with deterministic rules;
4) if deterministic extraction fails validation and OPENAI_API_KEY is available,
   asks OpenAI for a structured JSON extraction of the relevant PDF text/tables;
5) appends a new reporting date to the dashboard CSVs if validation passes.

The extractor is deliberately conservative. OpenAI is used only as a fallback
when rule-based extraction fails or extracted values do not validate. If the PDF
cannot be found, OpenAI is unavailable, or OpenAI-assisted extraction still does
not validate, it stops and writes .sitrep_update_status.md so the scheduled
workflow can create a review issue.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote, unquote, urljoin, urlparse, parse_qs

import fitz  # PyMuPDF
import pandas as pd
import pdfplumber
import requests
from bs4 import BeautifulSoup

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
except Exception:  # playwright may not be installed outside CI until requirements are installed
    sync_playwright = None
    PlaywrightTimeoutError = Exception

try:
    from openai import OpenAI
except Exception:  # openai is optional; fallback is skipped when unavailable
    OpenAI = None

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RAW = ROOT / "raw" / "sitreps"
EXTRACTED = ROOT / "extracted"
STATUS = ROOT / ".sitrep_update_status.md"
CATEGORY_URL = "https://insp.cd/category/sitrep/"
USER_AGENT = "Mozilla/5.0 (compatible; DRC-Ebola-Dashboard-Updater/1.0; +https://github.com/)"
TIMEOUT = 45

FRENCH_MONTHS = {
    "janvier": 1, "fevrier": 2, "février": 2, "mars": 3, "avril": 4, "mai": 5,
    "juin": 6, "juillet": 7, "aout": 8, "août": 8, "septembre": 9,
    "octobre": 10, "novembre": 11, "decembre": 12, "décembre": 12,
}

HZ_ALIASES = {
    "Mungbwalu": "Mongbwalu",
    "Mongwalu": "Mongbwalu",
    "Mungwalu": "Mongbwalu",
    "Miti Murhesa": "Miti-Murhesa",
    "Miti-Murhesa": "Miti-Murhesa",
    "Miti Murhesa": "Miti-Murhesa",
    "Nyakunde": "Nyankunde",
    "Gethy": "Gety",
    "Gethy": "Gety",
    "BAMBU": "Bambu",
    "Sans fiche": "unventilated_unknown_health_zone",
    "Echantillons sans fiche": "unventilated_unknown_health_zone",
    "Échantillons sans fiche": "unventilated_unknown_health_zone",
    "ZS non identifiée": "unventilated_unknown_health_zone",
    "Autres ZS": "unventilated_unknown_health_zone",
    # Some affected health zones are missing from population_by_hz.csv but are
    # still valid SitRep rows. They are retained with blank geometry so they
    # contribute to totals while not being mapped as polygons/centroids.
    "Mangala": "Mangala",
}

# These names are used for table parsing. The current dashboard's population file is
# used at runtime to map canonical names to zone_id, lat/lon and province.
KNOWN_NON_ZONE_ROWS = {
    "sous total", "total", "ituri", "nord-kivu", "nord-kivi", "sud-kivu", "provinces", "zones de santé",
}

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})


def write_status(title: str, body: str, *, ok: bool = False) -> None:
    prefix = "✅" if ok else "⚠️"
    STATUS.write_text(f"# {prefix} {title}\n\n{body}\n", encoding="utf-8")


def fail(message: str, detail: str = "") -> None:
    write_status("SitRep auto-update needs review", f"{message}\n\n{detail}".strip())
    raise SystemExit(2)


def log(message: str) -> None:
    print(f"[sitrep-update] {message}", flush=True)


def norm_text(s: Any) -> str:
    if s is None:
        return ""
    s = str(s)
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\xa0", " ").replace("\u202f", " ")
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def to_int(s: Any) -> int | None:
    if s is None:
        return None
    txt = norm_text(s)
    if not txt or txt.upper() in {"ND", "NA", "-"}:
        return None
    m = re.search(r"-?\d[\d\s,.]*", txt)
    if not m:
        return None
    val = m.group(0).replace(" ", "").replace(".", "").replace(",", ".")
    try:
        return int(float(val))
    except ValueError:
        return None


def to_float(s: Any) -> float | None:
    if s is None:
        return None
    txt = norm_text(s)
    if not txt or txt.upper() in {"ND", "NA", "-"}:
        return None
    m = re.search(r"-?\d+(?:[,.]\d+)?", txt.replace(" ", ""))
    if not m:
        return None
    try:
        value = float(m.group(0).replace(",", "."))
        if "%" in txt:
            value /= 100.0
        return value
    except ValueError:
        return None


def parse_fr_date(raw: str | None) -> str | None:
    if not raw:
        return None
    txt = strip_accents(norm_text(raw).lower())
    txt = re.sub(r"\s+", " ", txt)
    # 08/06/2026 or 08-06-2026
    m = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](20\d{2})", txt)
    if m:
        d, mo, y = map(int, m.groups())
        return f"{y:04d}-{mo:02d}-{d:02d}"
    # 8 juin 2026
    m = re.search(r"(\d{1,2})\s+([a-zéûôîèêàùç]+)\s+(20\d{2})", raw.lower())
    if m:
        d = int(m.group(1))
        mon = strip_accents(m.group(2))
        y = int(m.group(3))
        mo = FRENCH_MONTHS.get(mon)
        if mo:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    return None


def report_number_from_text(text: str) -> int | None:
    txt = norm_text(text)
    patterns = [
        r"SitRep\s*(?:MVE\s*)?N\s*[°ºo]?\s*0*(\d{1,3})",
        r"sitrep[-_ ]?n\s*0*(\d{1,3})",
        r"N[°ºo]?\s*0*(\d{1,3})\s*/\s*MVB",
    ]
    for pat in patterns:
        m = re.search(pat, txt, re.I)
        if m:
            return int(m.group(1))
    return None


@dataclass
class SitRepArticle:
    title: str
    url: str
    report_no: int | None
    reporting_date: str | None


def find_latest_article(category_url: str = CATEGORY_URL) -> SitRepArticle:
    html = SESSION.get(category_url, timeout=TIMEOUT).text
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[SitRepArticle] = []
    for a in soup.find_all("a", href=True):
        text = norm_text(a.get_text(" ", strip=True))
        href = urljoin(category_url, a["href"])
        if "sitrep" not in (text + " " + href).lower():
            continue
        no = report_number_from_text(text) or report_number_from_text(href)
        if no is None:
            continue
        date = parse_fr_date(text) or parse_fr_date(href.replace("_", "/").replace("-", "/"))
        title = text or href
        if href not in {c.url for c in candidates}:
            candidates.append(SitRepArticle(title, href, no, date))
    if not candidates:
        fail("No SitRep article links were found on the INSP category page.", f"URL: {category_url}")
    candidates.sort(key=lambda c: (c.report_no or -1, c.reporting_date or ""), reverse=True)
    return candidates[0]


def existing_max_report() -> tuple[int, str | None]:
    path = DATA / "report_summary.csv"
    if not path.exists():
        return (0, None)
    df = pd.read_csv(path, dtype=str)
    max_no = 0
    max_date = None
    for _, row in df.iterrows():
        no = report_number_from_text(str(row.get("report_no", "")))
        if no is not None:
            max_no = max(max_no, no)
        d = str(row.get("reporting_date", "") or "")
        if d and (max_date is None or d > max_date):
            max_date = d
    return max_no, max_date


def _unique_urls(urls: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for u in urls:
        if not u:
            continue
        u = norm_text(str(u)).strip().strip('"').strip("'")
        if not u or u.lower().startswith(("javascript:", "mailto:", "tel:")):
            continue
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _urls_from_text(text: str, base_url: str) -> list[str]:
    """Find direct and encoded PDF/viewer URLs in HTML, scripts and REST-rendered content."""
    urls: list[str] = []
    txt = text or ""
    # Direct absolute URLs.
    urls.extend(re.findall(r"https?://[^\"'<>\s)]+(?:\.pdf|/download/)[^\"'<>\s)]*", txt, flags=re.I))
    # Relative PDF URLs.
    urls.extend(urljoin(base_url, u) for u in re.findall(r"(?:(?:/wp-content/|wp-content/|/download/|download/)[^\"'<>\s)]+\.pdf[^\"'<>\s)]*)", txt, flags=re.I))
    # Query params / encoded values in PDF.js, dFlip, WonderPlugin, etc.
    for val in re.findall(r"(?:file|src|pdf|source|url|href)\s*[:=]\s*[\"']([^\"']+)[\"']", txt, flags=re.I):
        decoded = unquote(val)
        if ".pdf" in decoded.lower() or "/download/" in decoded.lower():
            urls.append(urljoin(base_url, decoded))
    # data-source, data-pdf, data-file attributes are common in WP PDF viewers.
    for val in re.findall(r"data-[a-z0-9_-]*\s*=\s*[\"']([^\"']+)[\"']", txt, flags=re.I):
        decoded = unquote(val)
        if ".pdf" in decoded.lower() or "/download/" in decoded.lower():
            urls.append(urljoin(base_url, decoded))
    return _unique_urls(urls)


def guessed_wp_upload_pdf_candidates(article: SitRepArticle | None, article_url: str) -> list[str]:
    """Try common WordPress upload filenames used by INSP SitRep posts.

    This is a deterministic fallback only. Each candidate is still validated by
    download_url(), so false guesses are harmless.
    """
    no = article.report_no if article else report_number_from_text(article_url)
    date_iso = article.reporting_date if article else (parse_fr_date(article_url.replace("_", "/").replace("-", "/")) or "")
    if no is None or not date_iso:
        return []
    yyyy, mm, dd = date_iso.split("-")
    dmy_us = f"{dd}_{mm}_{yyyy}"
    dmy_dash = f"{dd}-{mm}-{yyyy}"
    yy = yyyy[-2:]
    # Include the exact naming pattern seen in manually uploaded N26 PDFs plus common variants.
    names = [
        f"SitRep_MVE_RDC_N°{no}_{dmy_us}_Final.pdf",
        f"SitRep_MVE_RDC_N°{no}_{dmy_us}-Final.pdf",
        f"SitRep_MVE_RDC_N°{no}_{dmy_us}.pdf",
        f"SitRep_MVE_RDC_N{no}_{dmy_us}_Final.pdf",
        f"SitRep_MVE_RDC_N{no}_{dmy_us}.pdf",
        f"SitRep-MVE-RDC-N°{no}-{dmy_dash}-Final.pdf",
        f"SitRep-MVE-RDC-N{no}-{dmy_dash}-Final.pdf",
        f"SitRep-MVE-RDC-N{no}-{dmy_dash}.pdf",
        f"SitRep_N°{no}_MVB_{dmy_us}.pdf",
        f"SitRep_N{no}_MVB_{dmy_us}.pdf",
        f"SitRep-N{no}-MVB_{dmy_us}.pdf",
        f"SitRep-N°{no}-MVB_{dmy_us}.pdf",
        f"SitRep-MVE-RDC-N{no:03d}-{dmy_dash}.pdf",
        f"SitRep_MVE_RDC_N{no:03d}_{dmy_us}.pdf",
        f"SitRep-MVE-RDC-N{no}_{dd}_{mm}_{yy}.pdf",
        f"SitRep_MVE_RDC_N°{no}_{dd}_{mm}_{yy}.pdf",
    ]
    bases = [
        f"https://insp.cd/wp-content/uploads/{yyyy}/{mm}/",
        f"https://insp.cd/wp-content/uploads/{yyyy}/{int(mm)}/",
        f"https://insp.cd/wp-content/uploads/{yyyy}/",
    ]
    urls = []
    for base in bases:
        for name in names:
            urls.append(urljoin(base, quote(name)))
            urls.append(urljoin(base, name))
    return _unique_urls(urls)


def wp_rest_pdf_candidates(article_url: str, article: SitRepArticle | None = None) -> list[str]:
    """Search WordPress REST content and attachment metadata for PDF candidates."""
    parsed = urlparse(article_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    slug = parsed.path.strip("/").split("/")[-1]
    urls: list[str] = []
    try:
        r = SESSION.get(f"{root}/wp-json/wp/v2/posts", params={"slug": slug, "_embed": "1"}, timeout=TIMEOUT)
        if r.ok:
            posts = r.json()
            if isinstance(posts, list) and posts:
                post = posts[0]
                post_id = post.get("id")
                for key in ("content", "excerpt", "title"):
                    val = post.get(key)
                    if isinstance(val, dict):
                        urls.extend(_urls_from_text(str(val.get("rendered", "")), article_url))
                    elif val:
                        urls.extend(_urls_from_text(str(val), article_url))
                # Embedded media / attachments.
                embedded = post.get("_embedded") or {}
                for group in embedded.values():
                    if isinstance(group, list):
                        for item in group:
                            if isinstance(item, dict):
                                for k in ("source_url", "link"):
                                    if item.get(k):
                                        urls.append(str(item[k]))
                    elif isinstance(group, dict):
                        for k in ("source_url", "link"):
                            if group.get(k):
                                urls.append(str(group[k]))
                if post_id:
                    mr = SESSION.get(f"{root}/wp-json/wp/v2/media", params={"parent": post_id, "per_page": 100}, timeout=TIMEOUT)
                    if mr.ok:
                        for item in mr.json():
                            if not isinstance(item, dict):
                                continue
                            mime = str(item.get("mime_type", "")).lower()
                            for k in ("source_url", "link"):
                                if item.get(k):
                                    u = str(item[k])
                                    if "pdf" in mime or ".pdf" in u.lower():
                                        urls.append(u)
                # Search media by report number and slug; useful when parent is not set.
                searches = [slug]
                if article and article.report_no:
                    searches.extend([f"n{article.report_no}", f"sitrep n{article.report_no}", f"mvb {article.report_no}"])
                for q in searches:
                    mr = SESSION.get(f"{root}/wp-json/wp/v2/media", params={"search": q, "per_page": 100}, timeout=TIMEOUT)
                    if mr.ok:
                        for item in mr.json():
                            if not isinstance(item, dict):
                                continue
                            mime = str(item.get("mime_type", "")).lower()
                            u = str(item.get("source_url", "") or item.get("link", ""))
                            title = json.dumps(item.get("title", ""), ensure_ascii=False)
                            if ("pdf" in mime or ".pdf" in u.lower() or ".pdf" in title.lower()):
                                urls.append(u)
    except Exception:
        pass
    return _unique_urls([u for u in urls if ".pdf" in u.lower() or "/download/" in u.lower() or "application/pdf" in u.lower()])


def html_pdf_candidates(article_url: str, article: SitRepArticle | None = None) -> tuple[str, list[str]]:
    html = SESSION.get(article_url, timeout=TIMEOUT).text
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []

    # Direct tags and all attributes, because many WP PDF viewers hide the file URL
    # in data-* attributes rather than normal href/src.
    for tag in soup.find_all(True):
        for attr, val in tag.attrs.items():
            vals = val if isinstance(val, list) else [val]
            for v in vals:
                if not isinstance(v, str):
                    continue
                if ".pdf" in v.lower() or "/download/" in v.lower() or "viewer" in v.lower():
                    urls.append(urljoin(article_url, unquote(v)))

    urls.extend(_urls_from_text(html, article_url))

    # PDF.js/ViewerJS file= or #../file.pdf patterns from discovered viewer URLs.
    more: list[str] = []
    for u in list(urls):
        parsed = urlparse(u)
        qs = parse_qs(parsed.query)
        for key in ("file", "src", "pdf", "source"):
            for val in qs.get(key, []):
                if ".pdf" in val.lower() or "/download/" in val.lower():
                    more.append(urljoin(article_url, unquote(val)))
        if parsed.fragment and (".pdf" in parsed.fragment.lower() or "/download/" in parsed.fragment.lower()):
            more.append(urljoin(article_url, unquote(parsed.fragment)))
    urls.extend(more)

    # WordPress REST and common uploads filename guesses.
    urls.extend(wp_rest_pdf_candidates(article_url, article))
    urls.extend(guessed_wp_upload_pdf_candidates(article, article_url))

    urls = [u for u in _unique_urls(urls) if ".pdf" in u.lower() or "application/pdf" in u.lower() or "/download/" in u.lower()]
    return html, urls



def download_url(url: str, dest: Path) -> bool:
    try:
        r = SESSION.get(url, timeout=TIMEOUT, allow_redirects=True)
        ct = r.headers.get("content-type", "").lower()
        if r.ok and ("application/pdf" in ct or r.content[:4] == b"%PDF" or url.lower().split("?")[0].endswith(".pdf")):
            dest.write_bytes(r.content)
            return dest.stat().st_size > 1000
    except Exception:
        return False
    return False


def download_pdf_with_playwright(article_url: str, dest: Path) -> bool:
    if sync_playwright is None:
        return False
    pdf_urls: list[str] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(accept_downloads=True, viewport={"width": 1440, "height": 1600})
            page = context.new_page()

            def on_response(resp):
                try:
                    ct = resp.headers.get("content-type", "").lower()
                    u = resp.url
                    if "application/pdf" in ct or u.lower().split("?")[0].endswith(".pdf") or "/download/" in u.lower():
                        pdf_urls.append(u)
                except Exception:
                    pass

            page.on("response", on_response)
            page.goto(article_url, wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(2500)
            # Some viewers lazy-load only after scrolling into view.
            try:
                page.mouse.wheel(0, 1800)
                page.wait_for_timeout(2500)
                page.mouse.wheel(0, -1200)
                page.wait_for_timeout(1000)
            except Exception:
                pass

            # PDF URL discovered in network traffic.
            for u in list(dict.fromkeys(pdf_urls)):
                if download_url(u, dest):
                    browser.close()
                    return True

            # Inspect DOM and frames after dynamic rendering.
            try:
                html = page.content()
                for u in _urls_from_text(html, article_url):
                    if download_url(u, dest):
                        browser.close()
                        return True
            except Exception:
                pass

            try:
                sources = page.evaluate("""
                    () => Array.from(document.querySelectorAll('*')).flatMap(e => {
                        const vals = [];
                        for (const a of e.getAttributeNames ? e.getAttributeNames() : []) {
                            const v = e.getAttribute(a) || '';
                            if (v) vals.push(v);
                        }
                        return vals;
                    }).filter(Boolean)
                """)
            except Exception:
                sources = []
            for src in sources:
                if ".pdf" in str(src).lower() or "/download/" in str(src).lower() or "viewer" in str(src).lower():
                    parsed = urlparse(urljoin(article_url, str(src)))
                    qs = parse_qs(parsed.query)
                    possible = []
                    for key in ("file", "src", "pdf", "source"):
                        possible.extend(qs.get(key, []))
                    if parsed.fragment and (".pdf" in parsed.fragment.lower() or "/download/" in parsed.fragment.lower()):
                        possible.append(parsed.fragment)
                    possible.append(urljoin(article_url, str(src)))
                    for u in possible:
                        u = urljoin(article_url, unquote(u))
                        if download_url(u, dest):
                            browser.close()
                            return True

            # Last resort: click visible or hidden PDF viewer download buttons.
            selectors = [
                "#download", "#secondaryDownload", "#downloadButton",
                "button#download", "a#download",
                "[id*='download' i]", "[class*='download' i]",
                "[title*='Download' i]", "[aria-label*='Download' i]",
                "[title*='Télécharger' i]", "[aria-label*='Télécharger' i]",
                "[title*='download' i]", "[aria-label*='download' i]",
                "a[download]", "button[download]"
            ]

            def try_click_in(frame) -> bool:
                # Try selectors first.
                for sel in selectors:
                    try:
                        loc = frame.locator(sel).first
                        if loc.count() == 0:
                            continue
                        try:
                            loc.scroll_into_view_if_needed(timeout=3000)
                        except Exception:
                            pass
                        with page.expect_download(timeout=10000) as dl_info:
                            loc.click(timeout=5000, force=True)
                        dl = dl_info.value
                        dl.save_as(str(dest))
                        if dest.exists() and dest.stat().st_size > 1000:
                            return True
                    except Exception:
                        continue
                # Try a JS click on matching elements; some PDF viewer buttons are not
                # considered visible by Playwright.
                try:
                    handles = frame.locator("button,a,div[role='button']").element_handles()
                    for h in handles:
                        try:
                            txt = (h.get_attribute("id") or "") + " " + (h.get_attribute("title") or "") + " " + (h.get_attribute("aria-label") or "") + " " + (h.get_attribute("class") or "")
                            if not re.search(r"download|t[ée]l[ée]charger", txt, re.I):
                                continue
                            with page.expect_download(timeout=10000) as dl_info:
                                h.evaluate("(el) => el.click()")
                            dl = dl_info.value
                            dl.save_as(str(dest))
                            if dest.exists() and dest.stat().st_size > 1000:
                                return True
                        except Exception:
                            continue
                except Exception:
                    pass
                return False

            for frame in [page] + list(page.frames):
                if try_click_in(frame):
                    browser.close()
                    return True

            browser.close()
    except Exception:
        return False
    return False



def download_latest_pdf(article: SitRepArticle) -> Path:
    RAW.mkdir(parents=True, exist_ok=True)
    no = article.report_no or 0
    pdf_path = RAW / f"sitrep_N{no:03d}.pdf"
    html, urls = html_pdf_candidates(article.url, article)
    for u in urls:
        if download_url(u, pdf_path):
            return pdf_path
    if download_pdf_with_playwright(article.url, pdf_path):
        return pdf_path
    fail(
        "The latest SitRep article was found, but the embedded PDF could not be downloaded automatically.",
        f"Article: {article.title}\nURL: {article.url}\nFound PDF-like candidates: {urls[:5]}\n\nThe site may have changed its embedded PDF viewer. Upload the PDF manually or inspect the article's viewer URL.",
    )
    return pdf_path


def extract_pdf_text(pdf_path: Path) -> str:
    doc = fitz.open(str(pdf_path))
    parts = []
    for i, page in enumerate(doc):
        parts.append(f"\n--- PAGE {i+1} ---\n")
        parts.append(page.get_text("text") or "")
    return "\n".join(parts)


def extract_tables(pdf_path: Path) -> list[list[list[str]]]:
    out = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            try:
                tables = page.extract_tables() or []
                for t in tables:
                    out.append([[norm_text(c) for c in row] for row in t if row])
            except Exception:
                continue
    return out


def find_date_field(text: str, field: str) -> str | None:
    m = re.search(field + r"\s*[:\-]?\s*([^\n]+)", text, re.I)
    if m:
        return parse_fr_date(m.group(1))
    return None


def extract_total_confirmed(text: str) -> int | None:
    # Prefer explicit province-summary tables where the Total row is followed by
    # confirmed cases and deaths. This avoids accidentally reading the year (2026)
    # from prose such as "Cumul de cas confirmés 08 juin 2026".
    table_total = re.search(r"\bTotal\s+([0-9]{1,5})\s+([0-9]{1,5})\s+[0-9]+[,.]?[0-9]*\s*%", text, re.I)
    if table_total:
        return int(table_total.group(1).replace(" ", ""))
    # Province bullet lines, e.g. Ituri (563 cas), Nord-Kivu (32 cas), Sud-Kivu (3 cas).
    prov = re.search(r"Ituri\s*\((\d+)\s+cas\).*?Nord[- ]Kivu\s*\((\d+)\s+cas\).*?Sud[- ]Kivu\s*\((\d+)\s+cas\)", text, re.I | re.S)
    if prov:
        return sum(int(x) for x in prov.groups())
    patterns = [
        r"cumul\s+des\s+cas\s+confirm[ée]s?\s+s[’']?él[eè]ve\s+[àa]\s+(\d+)\s+cas",
        r"(?:Ainsi\s+)?le\s+cumul\s+des\s+cas\s+confirm[ée]s.*?(\d+)\s+cas",
        r"cumul\s+cas\s+confirm[ée]s?\s*[:\-]?\s*(\d+)",
        r"(\d+)\s+cumul\s+cas\s+confirm[ée]s",
        r"(\d+)\s+cas\s+confirm[ée]s?\s+dont",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I | re.S)
        if m:
            val = int(m.group(1).replace(" ", ""))
            if 0 < val < 10000:
                return val
    return None


def extract_total_deaths(text: str) -> int | None:
    table_total = re.search(r"\bTotal\s+([0-9]{1,5})\s+([0-9]{1,5})\s+[0-9]+[,.]?[0-9]*\s*%", text, re.I)
    if table_total:
        return int(table_total.group(2).replace(" ", ""))
    patterns = [
        r"cumul\s+des?\s+d[ée]c[èe]s\s+parmi\s+les\s+confirm[ée]s?.{0,80}?(\d+)(?:\s|\()",
        r"cumul\s+d[ée]c[èe]s\s+parmi\s+les\s+confirm[ée]s?.{0,80}?(\d+)(?:\s|\()",
        r"(\d+)\s*\([0-9]+[,.]?[0-9]*%\)\s*Taux\s+de\s+suivi",
        r"cumul\s+de\s+d[ée]c[èe]s\s+est\s+(\d+)",
        r"(\d+)\s+d[ée]c[èe]s\s+confirm[ée]s",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I | re.S)
        if m:
            val = int(m.group(1).replace(" ", ""))
            if 0 <= val < 10000:
                return val
    return None


def canonical_zone_name(raw: str, known_names: set[str]) -> str | None:
    s = norm_text(raw)
    if not s:
        return None
    for alias, canonical in HZ_ALIASES.items():
        if strip_accents(alias).lower() == strip_accents(s).lower():
            return canonical
    s_clean = strip_accents(s).lower().replace("zs ", "").strip()
    if s_clean in KNOWN_NON_ZONE_ROWS or len(s_clean) < 2:
        return None
    for name in known_names:
        if strip_accents(name).lower() == s_clean:
            return name
    # exact substring match only when the cell contains little else
    for name in sorted(known_names, key=len, reverse=True):
        n = strip_accents(name).lower()
        if re.fullmatch(rf".*\b{re.escape(n)}\b.*", s_clean) and len(s_clean) <= len(n) + 10:
            return name
    return None


def load_zone_lookup() -> dict[str, dict[str, Any]]:
    pop_path = DATA / "population_by_hz.csv"
    df = pd.read_csv(pop_path, dtype=str)
    # Try common column names from current dashboard.
    name_col = next((c for c in ["zone_name", "health_zone", "name"] if c in df.columns), None)
    id_col = next((c for c in ["zone_id", "id"] if c in df.columns), None)
    if not name_col or not id_col:
        raise ValueError("population_by_hz.csv must contain zone_id and zone_name/health_zone columns")
    lookup: dict[str, dict[str, Any]] = {}
    for _, r in df.iterrows():
        name = norm_text(r.get(name_col, ""))
        if not name:
            continue
        lookup[name] = {
            "zone_id": r.get(id_col, ""),
            "province": r.get("province", ""),
            "lat": r.get("lat", ""),
            "lon": r.get("lon", ""),
        }
    return lookup


def header_indices(table: list[list[str]]) -> tuple[int | None, int | None, int | None]:
    # Return (zone_col, confirmed_col, death_col). Header may be one or two rows.
    best = (None, None, None)
    for h_rows in (1, 2, 3):
        if len(table) < h_rows:
            continue
        max_cols = max(len(r) for r in table[:h_rows])
        headers = []
        for c in range(max_cols):
            headers.append(" ".join(norm_text(table[r][c]) if c < len(table[r]) else "" for r in range(h_rows)))
        zc = None; cc = None; dc = None
        for i, h in enumerate(headers):
            hs = strip_accents(h).lower()
            if "zone" in hs and "sante" in hs:
                zc = i
            if "confirm" in hs and "deces" not in hs and "nouveaux" not in hs and ("cumul" in hs or "nbre" in hs or "nombre" in hs or "cas" in hs):
                cc = i
            if "confirm" in hs and "deces" in hs and "nouveaux" not in hs:
                dc = i
        if zc is not None and cc is not None:
            return zc, cc, dc
        if cc is not None:
            best = (zc, cc, dc)
    return best


def extract_health_zone_rows(pdf_path: Path, known_lookup: dict[str, dict[str, Any]], report_date: str, report_label: str) -> tuple[list[dict[str, Any]], int | None, int | None]:
    """Extract cumulative confirmed cases/deaths by health zone.

    INSP PDFs use at least two layouts: true PDF tables and vertically rendered
    tables where every cell is extracted on a separate line. We therefore first
    try pdfplumber tables and then fall back to a line-based parser around the
    cumulative health-zone table.
    """
    known_names = set(known_lookup.keys())
    tables = extract_tables(pdf_path)
    rows: dict[str, dict[str, Any]] = {}
    unassigned_cases = None
    unassigned_deaths = None

    def add_row(zone: str, cval: int | None, dval: int | None, note_suffix: str = "") -> None:
        nonlocal unassigned_cases, unassigned_deaths
        if cval is None:
            return
        if zone == "unventilated_unknown_health_zone":
            unassigned_cases = cval
            unassigned_deaths = dval
            return
        # Filter out daily-new rows by preferring larger cumulative tables. If the same zone appears multiple times,
        # keep the largest confirmed count as the cumulative value.
        if zone not in rows or cval > int(rows[zone]["confirmed_cases"]):
            meta = known_lookup.get(zone, {})
            rows[zone] = {
                "date": report_date,
                "month": report_date[:7],
                "province": meta.get("province", ""),
                "health_zone": zone,
                "zone_id": meta.get("zone_id", ""),
                "confirmed_cases": cval,
                "confirmed_deaths": dval if dval is not None else "",
                "lat": meta.get("lat", ""),
                "lon": meta.get("lon", ""),
                "source": report_label,
                "source_date": report_date,
                "notes": "Automatically extracted from INSP SitRep PDF; validated against total cumulative cases." + note_suffix,
            }

    # 1) Standard PDF table extraction.
    for table in tables:
        zc, cc, dc = header_indices(table)
        if cc is None:
            continue
        for row in table:
            if not row:
                continue
            zone = None
            candidate_cells = []
            if zc is not None and zc < len(row):
                candidate_cells.append(row[zc])
            candidate_cells.extend(row)
            for cell in candidate_cells:
                zone = canonical_zone_name(cell, known_names)
                if zone:
                    break
            if not zone:
                continue
            cval = to_int(row[cc]) if cc < len(row) else None
            dval = to_int(row[dc]) if dc is not None and dc < len(row) else None
            add_row(zone, cval, dval)

    # 2) Fallback for vertically extracted cumulative health-zone tables.
    #    Example sequence: Bunia / 173 / 15 / 8,7% / Rwampara / 133 / 25 ...
    text = extract_pdf_text(pdf_path)
    start = re.search(r"Tableau\s+1\..{0,300}?(?:zone de sant[ée]|province)", text, re.I | re.S)
    if start:
        # Stop before response sections or after TOTAL.
        section = text[start.start():]
        end_candidates = []
        for pat in [r"\n\s*TOTAL\s*\n", r"\n\s*4\.\s*ACTIONS", r"--- PAGE\s+5", r"4\.\s*ACTIONS"]:
            m = re.search(pat, section, re.I)
            if m:
                end_candidates.append(m.end())
        if end_candidates:
            section = section[: max(end_candidates)]
        lines = [norm_text(x) for x in section.splitlines() if norm_text(x)]
        i = 0
        while i < len(lines):
            raw_line = lines[i]
            zone = canonical_zone_name(raw_line, known_names)
            if zone:
                nums: list[int] = []
                j = i + 1
                while j < len(lines) and len(nums) < 2:
                    # Stop if another known zone/subtotal begins before two numbers.
                    if canonical_zone_name(lines[j], known_names) and nums:
                        break
                    if re.search(r"%", lines[j]):
                        j += 1
                        continue
                    val = to_int(lines[j])
                    if val is not None:
                        nums.append(val)
                    j += 1
                if nums:
                    add_row(zone, nums[0], nums[1] if len(nums) > 1 else None, " Parsed from vertically rendered table.")
                    i = j
                    continue
            # Explicit unassigned labels sometimes are longer than the alias.
            if re.search(r"Autres\s+ZS|sans\s+fiche|non\s+ventil", raw_line, re.I):
                nums=[]; j=i+1
                while j < len(lines) and len(nums) < 2:
                    if re.search(r"%", lines[j]):
                        j += 1; continue
                    val = to_int(lines[j])
                    if val is not None:
                        nums.append(val)
                    j += 1
                if nums:
                    unassigned_cases = nums[0]
                    unassigned_deaths = nums[1] if len(nums) > 1 else None
                    i = j
                    continue
            i += 1

    # 3) Fallback for prose summaries, e.g. "Bunia (173), Rwampara (133)".
    #    Deaths are not available in that prose, but cases can still pass validation
    #    together with an explicit or inferred unassigned count.
    if not rows:
        for name in sorted(known_names, key=len, reverse=True):
            pat = rf"{re.escape(name)}\s*\(\s*(\d{{1,5}})\s*\)"
            m = re.search(pat, text, re.I)
            if m:
                add_row(name, int(m.group(1)), None, " Parsed from prose health-zone summary.")

    return list(rows.values()), unassigned_cases, unassigned_deaths


def extract_response_indicators(text: str, report_date: str, report_no: str) -> dict[str, Any]:
    row = {
        "reporting_date": report_date,
        "report_no": report_no,
        "admin_level": "national",
        "province": "",
        "health_zone": "",
        "contacts_under_followup": "",
        "contacts_seen": "",
        "contact_followup_rate": "",
        "alerts_reported": "",
        "alerts_investigated": "",
        "alert_investigation_rate": "",
        "samples_received": "",
        "samples_analysed": "",
        "positive_samples": "",
        "travellers_total": "",
        "travellers_screened": "",
        "poe_screening_coverage": "",
        "source": "INSP SitRep PDF auto-extract",
        "notes": "Automatically extracted; response indicators may reflect national, provincial, or operational-summary level depending on SitRep reporting.",
    }
    t = norm_text(text)
    # Contact follow-up rate, use the last/latest explicit contact follow-up rate if present.
    contact_patterns = [
        r"Taux\s+de\s+suivi\s+de\s+contacts?\s*[:\-]?\s*(\d+[,.]?\d*)\s*%",
        r"Taux\s+de\s+Suivis?\s*[:\-]?\s*(\d+[,.]?\d*)\s*%",
        r"contacts\s+suivis.*?Taux\s+de\s+Suivis?.{0,80}?(\d+[,.]?\d*)\s*%",
    ]
    vals = []
    for pat in contact_patterns:
        vals.extend([to_float(m) for m in re.findall(pat, t, re.I | re.S)])
    vals = [v for v in vals if v is not None and 0 <= v <= 1]
    if vals:
        row["contact_followup_rate"] = vals[-1]

    m = re.search(r"Pour\s+la\s+journ[ée]e\s+du\s+[^,]+,\s*(\d[\d\s]*)\s+alertes.*?dont\s+(\d[\d\s]*)\s*\((\d+[,.]?\d*)\s*%\)\s+investigu[ée]es", t, re.I | re.S)
    if m:
        ar = to_int(m.group(1)); ai = to_int(m.group(2)); rate = to_float(m.group(3) + "%")
        row["alerts_reported"] = ar if ar is not None else ""
        row["alerts_investigated"] = ai if ai is not None else ""
        row["alert_investigation_rate"] = rate if rate is not None else ""

    # Laboratory: prefer daily sample statement if available.
    lab = re.search(r"(\d[\d\s]*)\s+nouveaux?\s+[ée]chantillons?.{0,80}?analys[ée]s?.{0,80}?(\d[\d\s]*)\s+(?:sont\s+)?revenus?\s+positifs?", t, re.I | re.S)
    if lab:
        row["samples_analysed"] = to_int(lab.group(1)) or ""
        row["positive_samples"] = to_int(lab.group(2)) or ""
    else:
        rec = re.search(r"[ÉE]chantillons\s+re[çc]us\s+(\d[\d\s]*)", t, re.I)
        ana = re.search(r"(?:Nbre\s+)?d[’']?[ée]chantillons\s+analys[ée]s\s+(\d[\d\s]*)", t, re.I)
        pos = re.search(r"(?:Nbre\s+(?:des\s+)?)?cas\s+positifs?\s+(\d[\d\s]*)", t, re.I)
        if rec: row["samples_received"] = to_int(rec.group(1)) or ""
        if ana: row["samples_analysed"] = to_int(ana.group(1)) or ""
        if pos: row["positive_samples"] = to_int(pos.group(1)) or ""

    poe_total = re.search(r"Voyageurs\s+pass[ée]s\s+par\s+le\s+PoE/PoC\s+(\d[\d\s]*)", t, re.I)
    poe_screen = re.search(r"Voyageurs\s+screen[ée]s\s+(\d[\d\s]*)(?:\s*\((\d+[,.]?\d*)\s*%\))?", t, re.I)
    if poe_total: row["travellers_total"] = to_int(poe_total.group(1)) or ""
    if poe_screen:
        row["travellers_screened"] = to_int(poe_screen.group(1)) or ""
        if poe_screen.group(2):
            row["poe_screening_coverage"] = to_float(poe_screen.group(2) + "%") or ""
        elif row["travellers_total"] and row["travellers_screened"]:
            try:
                row["poe_screening_coverage"] = float(row["travellers_screened"]) / float(row["travellers_total"])
            except Exception:
                pass
    return row



def table_preview_for_llm(pdf_path: Path, max_tables: int = 18, max_rows_per_table: int = 45) -> str:
    """Return a compact text representation of extracted tables for OpenAI fallback."""
    chunks: list[str] = []
    for idx, table in enumerate(extract_tables(pdf_path)[:max_tables], start=1):
        chunks.append(f"\n[TABLE {idx}]")
        for row in table[:max_rows_per_table]:
            chunks.append(" | ".join(norm_text(c) for c in row))
    return "\n".join(chunks)


def compact_text_for_llm(text: str, limit: int = 65000) -> str:
    """Keep the most extraction-relevant parts while staying within a modest token budget."""
    if len(text) <= limit:
        return text
    head = text[: int(limit * 0.75)]
    tail = text[-int(limit * 0.25):]
    return head + "\n\n--- TEXT TRUNCATED FOR OPENAI FALLBACK ---\n\n" + tail


def openai_fallback_extract(pdf_path: Path, text: str, known_lookup: dict[str, dict[str, Any]], article: SitRepArticle, reason: str) -> dict[str, Any] | None:
    """Use OpenAI only when deterministic PDF extraction failed validation."""
    key_configured = bool(os.environ.get("OPENAI_API_KEY"))
    log(f"OpenAI fallback check: OPENAI_API_KEY configured = {'yes' if key_configured else 'no'}; openai package available = {'yes' if OpenAI is not None else 'no'}")
    if OpenAI is None:
        EXTRACTED.mkdir(exist_ok=True)
        (EXTRACTED / "openai_fallback_error.txt").write_text("OpenAI Python package is not available in the runner.", encoding="utf-8")
        return None
    if not key_configured:
        return None

    model = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
    known_names = sorted(known_lookup.keys())
    allowed_names = ", ".join(known_names[:700])
    log(f"OpenAI fallback started with model={model}; reason={reason}; pdf={pdf_path.name}; text_chars={len(text)}")
    prompt = f"""
You are extracting structured data from a Democratic Republic of Congo Ebola SitRep PDF for a public-health dashboard.
Use only the supplied PDF text and extracted tables. Do not infer values that are not stated, except that unassigned/unventilated cases may be calculated as total_confirmed minus the sum of health-zone rows if the PDF clearly reports a total.

Reason deterministic extraction failed: {reason}
Article URL: {article.url}
Article title: {article.title}

Return JSON only, with this schema:
{{
  "report_no": integer or null,
  "reporting_date": "YYYY-MM-DD" or null,
  "publication_date": "YYYY-MM-DD" or null,
  "total_confirmed": integer or null,
  "total_deaths": integer or null,
  "health_zone_rows": [
    {{"province":"Ituri|Nord-Kivu|Sud-Kivu|other/unknown", "health_zone":"canonical health-zone name", "confirmed_cases": integer, "confirmed_deaths": integer or null}}
  ],
  "unassigned_cases": integer or null,
  "unassigned_deaths": integer or null,
  "response_indicators": {{
    "contact_followup_rate": number between 0 and 1 or null,
    "contacts_under_followup": integer or null,
    "contacts_seen": integer or null,
    "alerts_reported": integer or null,
    "alerts_investigated": integer or null,
    "alert_investigation_rate": number between 0 and 1 or null,
    "samples_received": integer or null,
    "samples_analysed": integer or null,
    "positive_samples": integer or null,
    "travellers_total": integer or null,
    "travellers_screened": integer or null,
    "poe_screening_coverage": number between 0 and 1 or null
  }},
  "notes": "short extraction note"
}}

Canonical health-zone names should match this dashboard list when possible. Important aliases: Mungbwalu/Mongwalu/Mungwalu = Mongbwalu; Nyakunde = Nyankunde; Miti Murhesa = Miti-Murhesa; Gethy = Gety. If a named health zone is present but not in the dashboard list, keep it as a health-zone row with its name and province; do not add it to unassigned. Only Sans fiche/Echantillons sans fiche/ZS non identifiée/Autres ZS/données non ventilées are unassigned, not map health zones.

Known dashboard health-zone names include:
{allowed_names}

PDF TEXT:
{compact_text_for_llm(text)}

EXTRACTED TABLES:
{table_preview_for_llm(pdf_path)}
""".strip()

    try:
        client = OpenAI()
        raw = None
        try:
            log("Calling OpenAI Responses API for SitRep extraction.")
            resp = client.responses.create(
                model=model,
                input=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0,
            )
            raw = getattr(resp, "output_text", None)
            if not raw:
                raw = resp.output[0].content[0].text  # type: ignore[attr-defined]
            log("OpenAI Responses API returned output.")
        except Exception as e1:
            log(f"OpenAI Responses API failed; trying Chat Completions fallback. Error: {type(e1).__name__}: {e1}")
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0,
            )
            raw = resp.choices[0].message.content
            log("OpenAI Chat Completions fallback returned output.")
        if not raw:
            log("OpenAI fallback returned empty output.")
            return None
        EXTRACTED.mkdir(exist_ok=True)
        (EXTRACTED / "openai_fallback_raw.json").write_text(raw, encoding="utf-8")
        data = json.loads(raw)
        data["_openai_model"] = model
        log(f"OpenAI fallback parsed JSON: health_zone_rows={len(data.get('health_zone_rows') or [])}, total_confirmed={data.get('total_confirmed')}, unassigned_cases={data.get('unassigned_cases')}")
        return data
    except Exception as e:
        EXTRACTED.mkdir(exist_ok=True)
        err = f"{type(e).__name__}: {e}"
        log(f"OpenAI fallback failed: {err}")
        (EXTRACTED / "openai_fallback_error.txt").write_text(err, encoding="utf-8")
        return None


def rows_from_openai_payload(payload: dict[str, Any], known_lookup: dict[str, dict[str, Any]], report_date: str, report_label: str) -> tuple[list[dict[str, Any]], int | None, int | None]:
    known_names = set(known_lookup.keys())
    rows: list[dict[str, Any]] = []
    unassigned_cases = payload.get("unassigned_cases")
    unassigned_deaths = payload.get("unassigned_deaths")
    for item in payload.get("health_zone_rows", []) or []:
        zone_raw = norm_text(item.get("health_zone", ""))
        zone = canonical_zone_name(zone_raw, known_names) or zone_raw
        if not zone:
            continue
        cval = to_int(item.get("confirmed_cases"))
        if cval is None:
            continue
        dval = to_int(item.get("confirmed_deaths"))
        if zone == "unventilated_unknown_health_zone" or strip_accents(zone).lower() in {"sans fiche", "echantillons sans fiche", "zs non identifiee", "autres zs", "donnees non ventilees", "donnees non ventile"}:
            unassigned_cases = cval
            unassigned_deaths = dval
            continue
        meta = known_lookup.get(zone, {})
        # If a new named health zone is not yet in the dashboard geography, keep it
        # as a valid health-zone row with blank geometry. The map code hides rows
        # without reliable coordinates, but totals and trends still remain correct.
        rows.append({
            "date": report_date,
            "month": report_date[:7],
            "province": item.get("province") or meta.get("province", ""),
            "health_zone": zone,
            "zone_id": meta.get("zone_id", ""),
            "confirmed_cases": cval,
            "confirmed_deaths": dval if dval is not None else "",
            "lat": meta.get("lat", ""),
            "lon": meta.get("lon", ""),
            "source": report_label,
            "source_date": report_date,
            "notes": "OpenAI-assisted fallback extraction from INSP SitRep PDF; used only after deterministic extraction failed validation. Rows without dashboard geometry are retained in totals but hidden on the case map.",
        })
    return rows, to_int(unassigned_cases), to_int(unassigned_deaths)


def response_row_from_openai_payload(payload: dict[str, Any], report_date: str, report_no: str, existing_row: dict[str, Any]) -> dict[str, Any]:
    out = dict(existing_row)
    resp = payload.get("response_indicators") or {}
    mapping = {
        "contact_followup_rate": to_float,
        "contacts_under_followup": to_int,
        "contacts_seen": to_int,
        "alerts_reported": to_int,
        "alerts_investigated": to_int,
        "alert_investigation_rate": to_float,
        "samples_received": to_int,
        "samples_analysed": to_int,
        "positive_samples": to_int,
        "travellers_total": to_int,
        "travellers_screened": to_int,
        "poe_screening_coverage": to_float,
    }
    for key, fn in mapping.items():
        val = fn(resp.get(key)) if key in resp else None
        if val is not None:
            out[key] = val
    out["reporting_date"] = report_date
    out["report_no"] = report_no
    out["notes"] = (out.get("notes", "") + " OpenAI fallback was used where deterministic extraction was incomplete.").strip()
    return out

def append_or_replace_csv(path: Path, new_rows: list[dict[str, Any]], key_cols: list[str]) -> None:
    if not new_rows:
        return
    new_df = pd.DataFrame(new_rows)
    if path.exists():
        df = pd.read_csv(path, dtype=str)
        # Ensure all columns exist in both frames.
        for c in df.columns:
            if c not in new_df.columns:
                new_df[c] = ""
        for c in new_df.columns:
            if c not in df.columns:
                df[c] = ""
        new_df = new_df[df.columns]
        key_new = set(tuple(str(r.get(c, "")) for c in key_cols) for _, r in new_df.iterrows())
        keep = []
        for _, r in df.iterrows():
            keep.append(tuple(str(r.get(c, "")) for c in key_cols) not in key_new)
        out = pd.concat([df.loc[keep], new_df], ignore_index=True)
    else:
        out = new_df
    sort_cols = [c for c in ["reporting_date", "date", "province", "health_zone"] if c in out.columns]
    if sort_cols:
        out = out.sort_values(sort_cols)
    out.to_csv(path, index=False)


def update_dashboard(pdf_path: Path, article: SitRepArticle, *, force: bool = False) -> bool:
    text = extract_pdf_text(pdf_path)
    EXTRACTED.mkdir(exist_ok=True)
    report_no = article.report_no or report_number_from_text(text)
    if report_no is None:
        fail("Could not extract SitRep report number.", f"Article: {article.url}")
    report_label = f"SitRep N{report_no}/MVB"
    report_date = find_date_field(text, "Date de rapportage") or article.reporting_date
    publication_date = find_date_field(text, "Date de publication")
    if not report_date:
        fail("Could not extract reporting date from the PDF.", f"Article: {article.url}\nReport number: N{report_no}")
    if not publication_date:
        publication_date = datetime.utcnow().date().isoformat()

    max_no, max_date = existing_max_report()
    if not force and report_no <= max_no and (max_date is None or report_date <= max_date):
        write_status("No newer SitRep found", f"Latest article was N{report_no} ({report_date}), but dashboard already has N{max_no} ({max_date}).", ok=True)
        return False

    total_cases = extract_total_confirmed(text)
    total_deaths = extract_total_deaths(text)

    lookup = load_zone_lookup()
    hz_rows, unassigned_cases, unassigned_deaths = extract_health_zone_rows(pdf_path, lookup, report_date, report_label)

    openai_payload: dict[str, Any] | None = None
    openai_used = False

    def validation_detail(reason: str) -> dict[str, Any]:
        hz_sum_now = sum(int(r["confirmed_cases"]) for r in hz_rows if str(r["confirmed_cases"]).isdigit())
        return {
            "reason": reason,
            "article": article.url,
            "report_no": report_no,
            "reporting_date": report_date,
            "total_confirmed": total_cases,
            "health_zone_sum": hz_sum_now,
            "unassigned_cases": unassigned_cases,
            "health_zone_rows_extracted": len(hz_rows),
            "openai_api_key_configured": bool(os.environ.get("OPENAI_API_KEY")),
        }

    def try_openai(reason: str) -> bool:
        nonlocal total_cases, total_deaths, report_no, report_label, report_date, publication_date
        nonlocal hz_rows, unassigned_cases, unassigned_deaths, openai_payload, openai_used
        openai_payload = openai_fallback_extract(pdf_path, text, lookup, article, reason)
        if not openai_payload:
            return False
        if openai_payload.get("report_no"):
            report_no = int(openai_payload["report_no"])
            report_label = f"SitRep N{report_no}/MVB"
        report_date = openai_payload.get("reporting_date") or report_date
        publication_date = openai_payload.get("publication_date") or publication_date
        total_cases = to_int(openai_payload.get("total_confirmed")) or total_cases
        total_deaths = to_int(openai_payload.get("total_deaths")) if openai_payload.get("total_deaths") is not None else total_deaths
        hz_rows, unassigned_cases, unassigned_deaths = rows_from_openai_payload(openai_payload, lookup, report_date, report_label)
        openai_used = True
        EXTRACTED.mkdir(exist_ok=True)
        (EXTRACTED / f"sitrep_N{report_no:03d}_openai_fallback.json").write_text(json.dumps(openai_payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return True

    log(f"Parsed SitRep: report_no=N{report_no}, reporting_date={report_date}, publication_date={publication_date}, total_cases={total_cases}, total_deaths={total_deaths}")
    if total_cases is None:
        log("Total confirmed cases missing after deterministic extraction; attempting OpenAI fallback.")
        if not try_openai("Could not extract total confirmed cases with deterministic rules.") or total_cases is None:
            fail("Could not extract total confirmed cases from the SitRep PDF, and OpenAI fallback was unavailable or unsuccessful.", f"Article: {article.url}\nPDF: {pdf_path}")

    hz_sum = sum(int(r["confirmed_cases"]) for r in hz_rows if str(r["confirmed_cases"]).isdigit())
    if unassigned_cases is None and total_cases is not None:
        diff = total_cases - hz_sum
        unassigned_cases = diff if diff > 0 else 0

    log(f"Deterministic validation: health_zone_sum={hz_sum}, unassigned_cases={unassigned_cases}, total_cases={total_cases}, rows={len(hz_rows)}")
    if total_cases is not None and (hz_sum <= 0 or hz_sum + int(unassigned_cases or 0) != total_cases):
        if not openai_used:
            log("Deterministic validation failed; attempting OpenAI fallback.")
            try_openai("Health-zone counts did not validate against the total confirmed cases.")
            hz_sum = sum(int(r["confirmed_cases"]) for r in hz_rows if str(r["confirmed_cases"]).isdigit())
            if unassigned_cases is None and total_cases is not None:
                diff = total_cases - hz_sum
                unassigned_cases = diff if diff > 0 else 0
            log(f"Post-OpenAI validation: health_zone_sum={hz_sum}, unassigned_cases={unassigned_cases}, total_cases={total_cases}, rows={len(hz_rows)}, openai_used={openai_used}")

    if total_cases is None or hz_sum <= 0 or hz_sum + int(unassigned_cases or 0) != total_cases:
        detail = validation_detail("Extracted health-zone counts did not validate after deterministic extraction and optional OpenAI fallback.")
        (EXTRACTED / f"sitrep_N{report_no:03d}_review.json").write_text(json.dumps(detail, indent=2), encoding="utf-8")
        fail(
            "Extracted health-zone counts did not validate against the total confirmed cases.",
            "The update was stopped to avoid publishing incorrect values. Review extracted/sitrep_N%03d_review.json and, if present, extracted/openai_fallback_error.txt or extracted/openai_fallback_raw.json. OPENAI_API_KEY configured in runner: %s; OpenAI fallback used: %s." % (report_no, bool(os.environ.get("OPENAI_API_KEY")), openai_used),
        )

    # Write text for audit/debugging.
    (EXTRACTED / f"sitrep_N{report_no:03d}.txt").write_text(text, encoding="utf-8")

    report_row = {
        "report_no": f"N{report_no}",
        "reporting_date": report_date,
        "publication_date": publication_date,
        "drc_confirmed_cases": total_cases,
        "drc_confirmed_deaths": total_deaths if total_deaths is not None else "",
        "uganda_confirmed_cases": "7",
        "uganda_confirmed_deaths": "1",
        "source": report_label,
        "notes": "Automatically updated from INSP SitRep PDF. Uganda figures remain latest available DTM EVD snapshot values unless separately updated.",
    }
    append_or_replace_csv(DATA / "report_summary.csv", [report_row], ["report_no", "reporting_date"])
    append_or_replace_csv(DATA / "cases_by_hz.csv", hz_rows, ["date", "health_zone"])
    if unassigned_cases and int(unassigned_cases) > 0:
        unassigned_row = {
            "date": report_date,
            "month": report_date[:7],
            "province": "Ituri",
            "category": "unventilated_unknown_health_zone",
            "confirmed_cases": int(unassigned_cases),
            "confirmed_deaths": int(unassigned_deaths) if unassigned_deaths is not None else "",
            "source": report_label,
            "source_date": report_date,
            "notes": "Cases reported as unassigned / non-ventilated / no case form; not plotted on the map. Automatically extracted or inferred as total minus mapped health-zone counts.",
        }
        append_or_replace_csv(DATA / "cases_unventilated.csv", [unassigned_row], ["date", "category"])
    resp_row = extract_response_indicators(text, report_date, f"N{report_no}")
    if openai_payload:
        resp_row = response_row_from_openai_payload(openai_payload, report_date, f"N{report_no}", resp_row)
    append_or_replace_csv(DATA / "response_indicators.csv", [resp_row], ["reporting_date", "report_no", "admin_level", "province", "health_zone"])

    meta = {
        "updated_at_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "article_url": article.url,
        "pdf_path": str(pdf_path.relative_to(ROOT)) if pdf_path.is_relative_to(ROOT) else str(pdf_path),
        "report_no": report_no,
        "reporting_date": report_date,
        "publication_date": publication_date,
        "total_confirmed": total_cases,
        "total_deaths": total_deaths,
        "mapped_health_zone_count": len(hz_rows),
        "mapped_cases_sum": hz_sum,
        "unassigned_cases": unassigned_cases,
        "openai_fallback_used": openai_used,
        "openai_model": (openai_payload or {}).get("_openai_model", "") if openai_used else "",
    }
    (EXTRACTED / "latest_sitrep_update.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    write_status("SitRep auto-update completed", json.dumps(meta, indent=2, ensure_ascii=False), ok=True)
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--category-url", default=CATEGORY_URL)
    ap.add_argument("--article-url", default=None, help="Override article URL for testing")
    ap.add_argument("--pdf", default=None, help="Use a local PDF instead of downloading")
    ap.add_argument("--force", action="store_true", help="Replace current report even if report number already exists")
    args = ap.parse_args()

    if args.pdf:
        article = SitRepArticle(title=Path(args.pdf).name, url=args.article_url or "local", report_no=report_number_from_text(Path(args.pdf).name), reporting_date=None)
        # Local test will extract report no/date from PDF.
        update_dashboard(Path(args.pdf), article, force=args.force)
        return

    if args.article_url:
        article = SitRepArticle(title=args.article_url, url=args.article_url, report_no=report_number_from_text(args.article_url), reporting_date=parse_fr_date(args.article_url.replace("_", "/").replace("-", "/")))
    else:
        article = find_latest_article(args.category_url)

    max_no, max_date = existing_max_report()
    if not args.force and article.report_no is not None and article.report_no <= max_no:
        write_status("No newer SitRep found", f"Latest article is N{article.report_no}; dashboard already has N{max_no} ({max_date}).", ok=True)
        return
    pdf_path = download_latest_pdf(article)
    update_dashboard(pdf_path, article, force=args.force)


if __name__ == "__main__":
    main()
