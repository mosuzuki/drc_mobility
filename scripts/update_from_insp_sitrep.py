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
from datetime import datetime, date
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
    "Kambala": "Kambala",
    "Vuhovi": "Vuhovi",
    "Masereka": "Masereka",
    "Nia Nia": "Nia-Nia",
    "Gethy": "Gety",
    "Boma Mangbetu": "Boma Mangbetu",
    "Rungu": "Rungu",
    "Mahagi": "Mahagi",
    "Adja": "Adja",
    "Makiso-Kisangani": "Makiso-Kisangani",
    # Some affected health zones are missing from population_by_hz.csv but are
    # still valid SitRep rows. They are retained with blank geometry so they
    # contribute to totals while not being mapped as polygons/centroids.
    "Mangala": "Mangala",
}

# These names are used for table parsing. The current dashboard's population file is
# used at runtime to map canonical names to zone_id, lat/lon and province.
KNOWN_NON_ZONE_ROWS = {
    "sous total", "total", "ituri", "nord-kivu", "nord-kivi", "sud-kivu",
    "haut-uele", "haut uele", "hautu ele", "tshopo", "provinces", "zones de santé",
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
    val = re.sub(r"\s+", "", m.group(0)).replace(".", "").replace(",", ".")
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
        r"SitRep\s*(?:MVE\s*)?N\s*[°ºo_ -]?\s*0*(\d{1,3})",
        r"sitrep[-_ ]?n[-_ ]?0*(\d{1,3})",
        r"MVEBDB[-_ ]?0*(\d{1,3})(?:\D|$)",
        r"MVE[_ -]*RDC[_ -]*N[°ºo_ -]*0*(\d{1,3})(?:\D|$)",
        r"N[°ºo]?\s*0*(\d{1,3})\s*/\s*MVB",
        r"^N[°ºo]?\s*0*(\d{1,3})$",
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


def _wp_rest_sitrep_candidates(site_root: str) -> list[SitRepArticle]:
    """Discover recent SitReps from WordPress posts and media attachments.

    INSP has changed how recent SitReps are published; some PDFs are uploaded to
    the media library without a category-page link. The old category-only scraper
    therefore stalled at N81. REST discovery is additive and safely falls back to
    the category page when the endpoints are unavailable.
    """
    parsed = urlparse(site_root)
    base = f"{parsed.scheme or 'https'}://{parsed.netloc or 'insp.cd'}"
    out: list[SitRepArticle] = []
    endpoints = [
        ("posts", f"{base}/wp-json/wp/v2/posts"),
        ("media", f"{base}/wp-json/wp/v2/media"),
    ]
    for kind, endpoint in endpoints:
        for search_term in ("SitRep", "MVEBDB", "Ebola"):
            try:
                r = SESSION.get(endpoint, params={
                    "search": search_term, "per_page": 100,
                    "orderby": "date", "order": "desc",
                }, timeout=TIMEOUT)
                if r.status_code >= 400:
                    continue
                items = r.json()
                if not isinstance(items, list):
                    continue
            except Exception as exc:
                log(f"WordPress REST discovery failed for {kind}/{search_term}: {exc}")
                continue
            for item in items:
                title_obj = item.get("title") or {}
                title = title_obj.get("rendered", "") if isinstance(title_obj, dict) else str(title_obj)
                title = BeautifulSoup(title, "html.parser").get_text(" ", strip=True)
                slug = str(item.get("slug") or "")
                url = str(item.get("source_url") or item.get("link") or "")
                hay = " ".join([title, slug, url])
                no = report_number_from_text(hay)
                if no is None or not url:
                    continue
                d = parse_fr_date(hay.replace("_", "/").replace("-", "/"))
                if not d:
                    # WordPress publication date is only a fallback; the PDF itself
                    # remains authoritative for Date de rapportage.
                    wpdate = str(item.get("date") or "")[:10]
                    d = wpdate if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", wpdate) else None
                out.append(SitRepArticle(title or slug or url, url, no, d))
    # De-duplicate by report number and URL.
    uniq: dict[tuple[int | None, str], SitRepArticle] = {}
    for c in out:
        uniq[(c.report_no, c.url)] = c
    return list(uniq.values())


def find_latest_article(category_url: str = CATEGORY_URL) -> SitRepArticle:
    candidates: list[SitRepArticle] = []
    # 1) Legacy category-page discovery.
    try:
        resp = SESSION.get(category_url, timeout=TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", href=True):
            text = norm_text(a.get_text(" ", strip=True))
            href = urljoin(category_url, a["href"])
            if "sitrep" not in (text + " " + href).lower():
                continue
            no = report_number_from_text(text) or report_number_from_text(href)
            if no is None:
                continue
            d = parse_fr_date(text) or parse_fr_date(href.replace("_", "/").replace("-", "/"))
            candidates.append(SitRepArticle(text or href, href, no, d))
    except Exception as exc:
        log(f"Category-page discovery failed: {exc}")

    # 2) WordPress REST posts/media discovery. This is essential for the current
    # publication pattern, where newer PDFs may not appear on /category/sitrep/.
    candidates.extend(_wp_rest_sitrep_candidates(category_url))

    if not candidates:
        fail("No SitRep article or media links were found on the INSP site.", f"URL: {category_url}")
    # Keep one copy of each URL and prefer the highest report number.
    by_url: dict[str, SitRepArticle] = {}
    for c in candidates:
        by_url[c.url] = c
    candidates = list(by_url.values())
    candidates.sort(key=lambda c: (c.report_no or -1, c.reporting_date or ""), reverse=True)
    latest = candidates[0]
    log(f"Latest discovered SitRep candidate: N{latest.report_no} {latest.url}")
    return latest


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
    # REST media discovery may return the PDF itself rather than an article page.
    if download_url(article.url, pdf_path):
        return pdf_path
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
    # Newer SitReps expose an explicit KPI card "CUMUL DES CAS". Prefer it
    # over generic "Cas confirmés" text, which may span province-table cells.
    m = re.search(r"CUMUL\s+DES\s+CAS\s*\n\s*([0-9][0-9\s\u00a0\u202f]{2,10})\b", text, re.I)
    if m:
        val = int(re.sub(r"\D", "", m.group(1)))
        if 0 < val < 20000 and val != 2026:
            return val
    m = re.search(r"cumule\s+([0-9][0-9\s\u00a0\u202f]{2,10})\s+cas\s+confirm", text, re.I)
    if m:
        val = int(re.sub(r"\D", "", m.group(1)))
        if 0 < val < 20000:
            return val
    # Prefer explicit KPI cards and province-summary tables. New SitReps use
    # vertically rendered cards such as:
    #   CAS CONFIRMES — 5 PROVINCES
    #   3 200
    # This avoids accidentally reading the reporting year (2026).
    kpi = re.search(r"CAS\s+CONFIRM[ÉE]S?\s*[—-].{0,80}?\n\s*([0-9][0-9\s]{2,8})\b", text, re.I | re.S)
    if kpi:
        val = int(re.sub(r"\D", "", kpi.group(1)))
        if 0 < val < 20000 and val != 2026:
            return val
    table_total = re.search(r"\bTotal\s+([0-9][0-9\s]{0,8})\s+([0-9][0-9\s]{0,8})\s+[0-9]+[,.]?[0-9]*\s*%", text, re.I)
    if table_total:
        return int(re.sub(r"\D", "", table_total.group(1)))
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
            val = int(re.sub(r"\D", "", m.group(1)))
            if 0 < val < 10000:
                return val
    return None


def extract_total_deaths(text: str) -> int | None:
    # Prefer the explicit cumulative-deaths KPI card in the redesigned SitReps.
    m = re.search(r"CUMUL\s+DES\s+\n?D[ÉE]C[ÈE]S(?:\s+CONFIRM[ÉE]S?\*{0,2})?\s*\n\s*([0-9][0-9\s\u00a0\u202f]{1,10})\b", text, re.I)
    if m:
        val = int(re.sub(r"\D", "", m.group(1)))
        if 0 <= val < 20000 and val != 2026:
            return val
    m = re.search(r"cas\s+confirm[ée]s\s+dont\s+([0-9][0-9\s\u00a0\u202f]{1,10})\s+d[ée]c", text, re.I)
    if m:
        val = int(re.sub(r"\D", "", m.group(1)))
        if 0 <= val < 20000:
            return val
    kpi = re.search(r"D[ÉE]C[ÈE]S\s+CONFIRM[ÉE]S?.{0,80}?\n\s*([0-9][0-9\s]{1,8})\s*(?:[·(]|\n)", text, re.I | re.S)
    if kpi:
        val = int(re.sub(r"\D", "", kpi.group(1)))
        if 0 <= val < 20000 and val != 2026:
            return val
    table_total = re.search(r"\bTotal\s+([0-9][0-9\s]{0,8})\s+([0-9][0-9\s]{0,8})\s+[0-9]+[,.]?[0-9]*\s*%", text, re.I)
    if table_total:
        return int(re.sub(r"\D", "", table_total.group(2)))
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
            val = int(re.sub(r"\D", "", m.group(1)))
            if 0 <= val < 10000:
                return val
    return None



def reconcile_totals_with_health_zone_rows(total_cases: int | None, total_deaths: int | None, hz_rows: list[dict[str, Any]], unassigned_cases: int | None, unassigned_deaths: int | None, report_date: str) -> tuple[int | None, int | None]:
    """Guard against PDF extraction errors such as reading the reporting year
    (2026) as cumulative cases or the isolated/hospitalised count as deaths.

    When the sum of health-zone rows plus unassigned cases is internally
    consistent and differs from the headline totals, prefer the internally
    consistent table-derived total.  This is safer than card-text extraction for
    the current SitRep layout.
    """
    def sint(x):
        try:
            if x is None or str(x).strip() == "":
                return 0
            return int(float(str(x).replace(',', '').replace(' ', '')))
        except Exception:
            return 0
    hz_cases = sum(sint(r.get("confirmed_cases")) for r in hz_rows)
    hz_deaths = sum(sint(r.get("confirmed_deaths")) for r in hz_rows)
    uv_cases = sint(unassigned_cases)
    uv_deaths = sint(unassigned_deaths)
    table_cases = hz_cases + uv_cases
    table_deaths = hz_deaths + uv_deaths
    # Use table-derived totals if headline values are missing, implausible, or
    # disagree by more than one case/death.  This prevents 2026/628 errors.
    if table_cases > 0 and (total_cases is None or abs(int(total_cases) - table_cases) > 1):
        log(f"Reconciled total confirmed from {total_cases} to table-derived {table_cases} for {report_date}")
        total_cases = table_cases
    if table_deaths >= 0 and table_cases > 0 and (total_deaths is None or abs(int(total_deaths) - table_deaths) > 1):
        log(f"Reconciled total deaths from {total_deaths} to table-derived {table_deaths} for {report_date}")
        total_deaths = table_deaths
    return total_cases, total_deaths

def canonical_zone_name(raw: str, known_names: set[str]) -> str | None:
    s = norm_text(raw)
    if not s:
        return None
    # PDF cells sometimes split one health-zone name across lines or insert
    # non-standard separator glyphs. Compare a separator-insensitive key first.
    def zone_key(x: str) -> str:
        x = strip_accents(norm_text(x)).lower().replace("zs ", "")
        return re.sub(r"[^a-z0-9]+", " ", x).strip()
    key = zone_key(s)
    for alias, canonical in HZ_ALIASES.items():
        if zone_key(alias) == key:
            return canonical
    s_clean = key
    if s_clean in KNOWN_NON_ZONE_ROWS or len(s_clean) < 2:
        return None
    for name in known_names:
        if zone_key(name) == s_clean:
            return name
    # exact substring match only when the cell contains little else
    for name in sorted(known_names, key=len, reverse=True):
        n = zone_key(name)
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
    # Valid affected health zones that may be absent from population_by_hz.csv
    # or boundary metadata are retained with blank geometry so totals still validate.
    extra_zone_province = {
        "Boma Mangbetu": "Haut-Uele", "Rungu": "Haut-Uele", "Wamba": "Haut-Uele",
        "Isiro": "Haut-Uele", "Pawa": "Haut-Uele", "Makiso-Kisangani": "Tshopo",
        "Lubunga": "Tshopo", "Mangobo": "Tshopo", "Kabondo": "Tshopo", "Wanie-Rukula": "Tshopo",
        "Miti-Murhesa": "Sud-Kivu",
        "Adja": "Ituri", "Mahagi": "Ituri", "Ariwara": "Ituri", "Mangala": "Ituri", "Nia-Nia": "Ituri",
        "Masereka": "Nord-Kivu", "Vuhovi": "Nord-Kivu", "Lubero": "Nord-Kivu",
    }
    for name, province in extra_zone_province.items():
        rec = lookup.setdefault(name, {"zone_id": "", "province": province, "lat": "", "lon": ""})
        # Some library rows exist without province/geometry; keep the row but
        # fill the province so downstream panels and deltas do not lose it.
        if not norm_text(rec.get("province", "")):
            rec["province"] = province
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
                "notes": "Automatically extracted from INSP SitRep PDF; validated against total cumulative cases. Rows without dashboard geometry are retained in totals but hidden on the case map." + note_suffix,
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
    start = re.search(r"TABLEAU\s+2\s*[—-].{0,500}?(?:ZONE DE SANTE|Zone de sant[ée]|Province / Zone)", text, re.I | re.S)
    if not start:
        start = re.search(r"Tableau\s+1\..{0,300}?(?:zone de sant[ée]|province)", text, re.I | re.S)
    if not start:
        # Newer SitRep layout (N88+) drops the "Tableau" label and starts
        # directly with "Province / Zone de santé - Nombre cumulatif".
        start = re.search(r"Province\s*/?\s*Zone\s+de\s+sant[ée].{0,300}?Nombre\s+cumulatif", text, re.I | re.S)
    if start:
        # Stop before response sections or after TOTAL.
        section = text[start.start():]
        end_candidates = []
        for pat in [r"\n\s*Total\s*\n", r"\n\s*TOTAL\s*\n", r"\n\s*2\.3\s", r"\n\s*4\.\s*ACTIONS", r"--- PAGE\s+5", r"4\.\s*ACTIONS"]:
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
            if re.search(r"Autres\s+ZS|sans\s+fiche|non\s+ventil|[ÀA]\s+ventiler", raw_line, re.I):
                vals=[]; j=i+1
                while j < len(lines) and len(vals) < 2:
                    if re.search(r"%", lines[j]):
                        j += 1; continue
                    token = norm_text(lines[j])
                    if token.upper() in {"NA", "ND", "-"}:
                        vals.append(None)
                    else:
                        val = to_int(token)
                        if val is not None:
                            vals.append(val)
                    j += 1
                if vals:
                    # "A ventiler NA 59" means no unassigned case count but 59 deaths
                    # pending health-zone attribution; it must not inflate case totals.
                    unassigned_cases = vals[0] if vals[0] is not None else 0
                    unassigned_deaths = vals[1] if len(vals) > 1 else None
                    i = j
                    continue
            i += 1

    # 2b) Repair multi-line / ambiguous rows seen in the redesigned cumulative table.
    # Makiso-Kisangani and Boma Mangbetu are sometimes split across PDF text lines.
    for raw_name, canonical in [("Makiso-Kisangani", "Makiso-Kisangani"), ("Boma Mangbetu", "Boma Mangbetu")]:
        parts = re.split(r"[- ]+", raw_name)
        sep = r"(?:\s|[-‐‑–—]|[^\w%])+"
        pat = sep.join(re.escape(x) for x in parts) + r"\s+(\d{1,5})\s+(\d{1,5})\s+\d+[,.]?\d*%"
        m = re.search(pat, section if start else text, re.I)
        if m and canonical in known_lookup:
            add_row(canonical, int(m.group(1)), int(m.group(2)), " Repaired from a split health-zone name in PDF text extraction.")
            if canonical == "Boma Mangbetu":
                # A split cell can otherwise be misclassified as the unrelated
                # health zone Boma (Kongo Central).
                rows.pop("Boma", None)

    # Tshopo is both a province name and, from later SitReps, a health-zone name.
    # Only add it when the cumulative table contains at least two Tshopo rows;
    # the smaller positive case count is the health-zone row, not the province subtotal.
    if start and "Tshopo" in known_lookup:
        sec = section
        tm = [(int(a), int(b)) for a, b in re.findall(r"(?:^|\n)\s*Tshopo\s*\n?\s*(\d{1,5})\s*\n?\s*(\d{1,5})\s*\n?\s*\d+[,.]?\d*%", sec, re.I)]
        if len(tm) >= 2:
            cval, dval = min(tm, key=lambda x: x[0])
            add_row("Tshopo", cval, dval, " Disambiguated from the Tshopo province subtotal.")

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

    # Robust parser for the newer vertically rendered SitRep tables.  It keeps
    # extraction inside the named response sections so the Table 1 CFR does not
    # get mistaken for the contact follow-up rate.
    lines = [norm_text(x) for x in text.splitlines() if norm_text(x)]

    def values_after(row_pat: str, start_pat: str | None = None, end_pat: str | None = None, max_values: int = 12) -> list[float]:
        start_i = 0
        if start_pat:
            for k, line in enumerate(lines):
                if re.search(start_pat, line, re.I):
                    start_i = k
                    break
        end_i = len(lines)
        if end_pat:
            for k in range(start_i + 1, len(lines)):
                if re.search(end_pat, lines[k], re.I):
                    end_i = k
                    break
        for k in range(start_i, end_i):
            if re.search(row_pat, lines[k], re.I):
                vals = []
                for cell in lines[k+1:end_i]:
                    if len(vals) >= max_values:
                        break
                    if re.search(r"^(?:ND|NA|—|-)$", cell, re.I):
                        continue
                    v = to_float(cell if '%' in cell else cell)
                    if v is not None:
                        vals.append(v)
                return vals
        return []

    contact_tot = values_after(r"^Total$", r"TABLEAU\s+4", r"3\.4|Points\s+d['’]entr", 3) if re.search(r"TABLEAU\s+4", t, re.I) else []
    if len(contact_tot) >= 3:
        cu = int(contact_tot[0]); cs = int(contact_tot[1]); rate = contact_tot[2] if contact_tot[2] <= 1 else contact_tot[2] / 100.0
        if 0 < cs <= cu and 0 <= rate <= 1:
            row["contacts_under_followup"] = cu
            row["contacts_seen"] = cs
            row["contact_followup_rate"] = rate

    alert_tot = values_after(r"^Total\s+alertes\s+du\s+jour$", r"TABLEAU\s+3", r"3\.2|Laboratoire", 6)
    alert_inv = values_after(r"^Alertes\s+investigu", r"TABLEAU\s+3", r"3\.2|Laboratoire", 6)
    alert_rate = values_after(r"^Taux\s+d[’']investigation", r"TABLEAU\s+3", r"3\.2|Laboratoire", 6)
    if alert_tot:
        row["alerts_reported"] = int(alert_tot[-1])
    if alert_inv:
        row["alerts_investigated"] = int(alert_inv[-1])
    if alert_rate:
        ar = alert_rate[-1] if alert_rate[-1] <= 1 else alert_rate[-1] / 100.0
        if 0 <= ar <= 1:
            row["alert_investigation_rate"] = ar

    poe_total = values_after(r"Nombre\s+de\s+personnes\s+pass", r"TABLEAU\s+5", r"4\s+Sant", 6)
    poe_screen = values_after(r"%\s+des\s+voyageurs\s+screen", r"TABLEAU\s+5", r"4\s+Sant", 6)
    if poe_total:
        # ND columns are skipped by values_after; the global total is generally
        # the largest count in this row, while some provinces may legitimately be zero.
        row["travellers_total"] = int(max(poe_total))
    if poe_screen:
        pr = poe_screen[-1] if poe_screen[-1] <= 1 else poe_screen[-1] / 100.0
        if 0 <= pr <= 1:
            row["poe_screening_coverage"] = pr
            if row.get("travellers_total"):
                try:
                    row["travellers_screened"] = int(round(float(row["travellers_total"]) * pr))
                except Exception:
                    pass

    # Contact follow-up.  From SitRep N084 onward the page layout changed and
    # broad cross-page regexes can accidentally pair the cumulative case/death
    # cards with the CFR.  Prefer explicit surveillance prose with counts, then
    # tightly bounded KPI patterns.
    contact_prose_patterns = [
        r"(?:La\s+proportion\s+des\s+contacts\s+suivis|Le\s+suivi\s+des\s+contacts)[^%]{0,120}?(\d{1,3}[,.]\d+)\s*%\s*\(\s*(\d[\d\s\u202f]*)\s+vus?\s+sur\s+(\d[\d\s\u202f]*)\s+[àa]\s+suivre",
        r"(?:La\s+proportion\s+des\s+contacts\s+suivis|Le\s+suivi\s+des\s+contacts)[^%]{0,120}?(\d{1,3}[,.]\d+)\s*%",
    ]
    cm = re.search(contact_prose_patterns[0], t, re.I | re.S)
    if cm:
        rate = to_float(cm.group(1) + "%")
        seen = to_int(cm.group(2)); follow = to_int(cm.group(3))
        if rate is not None and seen is not None and follow is not None and 0 <= seen <= follow:
            row["contacts_under_followup"] = follow
            row["contacts_seen"] = seen
            row["contact_followup_rate"] = rate
    elif row.get("contact_followup_rate") in ("", None):
        cm = re.search(contact_prose_patterns[1], t, re.I | re.S)
        if cm:
            row["contact_followup_rate"] = to_float(cm.group(1) + "%") or ""

    # Older N082/N083 first-page KPI: rate followed by seen / to-follow counts.
    if row.get("contacts_under_followup") in ("", None):
        cm = re.search(r"SUIVI\s+DES\s+CONTACTS\s+NATIONAL\s+(\d{1,3}[,.]\d+)\s*%\s+(\d[\d\s\u202f]*)\s*/\s*(\d[\d\s\u202f]*)\s+vus", t, re.I)
        if cm:
            row["contact_followup_rate"] = to_float(cm.group(1) + "%") or ""
            row["contacts_seen"] = to_int(cm.group(2)) or ""
            row["contacts_under_followup"] = to_int(cm.group(3)) or ""

    if row.get("contact_followup_rate") in ("", None):
        contact_patterns = [
            r"Taux\s+de\s+suivi\s+des?\s+contacts?[^%]{0,50}?(\d{1,3}[,.]\d+)\s*%",
            r"(\d{1,3}[,.]\d+)\s*%\s*.{0,50}?Taux\s+de\s+suivi\s+des?\s+contacts?",
        ]
        for pat in contact_patterns:
            m = re.search(pat, t, re.I | re.S)
            if m:
                rate = to_float(m.group(1) + "%")
                if rate is not None:
                    row["contact_followup_rate"] = rate
                    break

    # Contact follow-up table: Total / contacts under follow-up / contacts seen / rate.
    cm = re.search(r"Tableau\s+4[^\n]{0,120}.*?Suivi\s+des\s+contacts.*?Total\s+(\d[\d\s]*)\s+(\d[\d\s]*)\s+(\d{1,3}[,.]\d+)\s*%", t, re.I | re.S)
    if cm and row.get("contacts_under_followup") in ("", None):
        row["contacts_under_followup"] = to_int(cm.group(1)) or ""
        row["contacts_seen"] = to_int(cm.group(2)) or ""
        rate = to_float(cm.group(3) + "%")
        if rate is not None:
            row["contact_followup_rate"] = rate

    # Newer surveillance prose: total alerts, then validated suspects, then
    # investigated suspects with an explicit investigation percentage.
    modern_alert = re.search(
        r"(?:Au\s+total,?\s*|Au\s+terme\s+de\s+la\s+journ[ée]e[^,]*,?\s*)(\d[\d\s\u202f]*)\s+alertes\s+ont\s+[ée]t[ée]\s+enregistr[ée]es.*?(\d[\d\s\u202f]*)\s*\((\d{1,3}[,.]\d+)\s*%\)\s+ont\s+[ée]t[ée]\s+investigu[ée]es?",
        t, re.I | re.S)
    if modern_alert:
        row["alerts_reported"] = to_int(modern_alert.group(1)) or ""
        row["alerts_investigated"] = to_int(modern_alert.group(2)) or ""
        row["alert_investigation_rate"] = to_float(modern_alert.group(3) + "%") or ""

    m = re.search(r"Pour\s+la\s+journ[ée]e\s+du\s+[^,]+,\s*(\d[\d\s]*)\s+alertes.*?dont\s+(\d[\d\s]*)\s*\((\d+[,.]?\d*)\s*%\)\s+investigu[ée]es", t, re.I | re.S)
    if not m:
        m = re.search(r"Au\s+total,\s*(\d[\d\s]*)\s+alertes.*?(\d[\d\s]*)\s+investigu[ée]s?.*?\((\d+[,.]?\d*)\s*%\)", t, re.I | re.S)
    if m and row.get("alerts_reported") in ("", None):
        ar = to_int(m.group(1)); ai = to_int(m.group(2)); rate = to_float(m.group(3) + "%")
        row["alerts_reported"] = ar if ar is not None else ""
        row["alerts_investigated"] = ai if ai is not None else ""
        row["alert_investigation_rate"] = rate if rate is not None else ""

    # Alert-management table: Total Alertes du jour / Alertes investiguées / Taux d'investigation.
    am = re.search(r"Total\s+Alertes\s+du\s+jour\s+.*?(\d[\d\s]*)\s+Alertes\s+investigu[ée]es\s+.*?(\d[\d\s]*)\s+Taux\s+d[’']investigation\s+.*?(\d{1,3}[,.]\d+)\s*%", t, re.I | re.S)
    if am and row.get("alerts_reported") in ("", None):
        ar = to_int(am.group(1)); ai = to_int(am.group(2)); rate = to_float(am.group(3) + "%")
        row["alerts_reported"] = ar if ar is not None else row["alerts_reported"]
        row["alerts_investigated"] = ai if ai is not None else row["alerts_investigated"]
        row["alert_investigation_rate"] = rate if rate is not None else row["alert_investigation_rate"]

    # PoE/PoC screening table: global travellers and screening percentage.
    poe = re.search(r"Nombre\s+(?:de|des)\s+personnes\s+pass[ée]es\s+aux\s+PoE/PoC\s+(?:\d[\d\s]*\s+){2}(\d[\d\s]*)\s+%\s+des\s+voyageurs\s+screen[ée]s\s+(?:\d{1,3}[,.]\d+%\s+){2}(\d{1,3}[,.]\d+)\s*%", t, re.I | re.S)
    if poe and row.get("travellers_total") in ("", None):
        row["travellers_total"] = to_int(poe.group(1)) or ""
        rate = to_float(poe.group(2) + "%")
        if rate is not None:
            row["poe_screening_coverage"] = rate

    # Laboratory: prefer daily sample statement if available.
    lab_modern = re.search(r"(\d[\d\s\u202f]*)\s+[ée]chantillons\s+ont\s+[ée]t[ée]\s+analys[ée]s,?\s+confirmant\s+(\d[\d\s\u202f]*)\s+nouveaux?\s+cas", t, re.I | re.S)
    if lab_modern:
        row["samples_analysed"] = to_int(lab_modern.group(1)) or ""
        row["positive_samples"] = to_int(lab_modern.group(2)) or ""
    lab = None if lab_modern else re.search(r"(\d[\d\s]*)\s+nouveaux?\s+[ée]chantillons?.{0,80}?analys[ée]s?.{0,80}?(\d[\d\s]*)\s+(?:sont\s+)?revenus?\s+positifs?", t, re.I | re.S)
    if lab:
        row["samples_analysed"] = to_int(lab.group(1)) or ""
        row["positive_samples"] = to_int(lab.group(2)) or ""
    elif not lab_modern:
        rec = re.search(r"[ÉE]chantillons\s+re[çc]us\s+(\d[\d\s]*)", t, re.I)
        ana = re.search(r"(?:Nbre\s+)?d[’']?[ée]chantillons\s+analys[ée]s\s+(\d[\d\s]*)", t, re.I)
        pos = re.search(r"(?:Nbre\s+(?:des\s+)?)?cas\s+positifs?\s+(\d[\d\s]*)", t, re.I)
        if rec: row["samples_received"] = to_int(rec.group(1)) or ""
        if ana: row["samples_analysed"] = to_int(ana.group(1)) or ""
        if pos: row["positive_samples"] = to_int(pos.group(1)) or ""

    poe_total = re.search(r"Voyageurs\s+pass[ée]s\s+par\s+le\s+PoE/PoC\s+(\d[\d\s]*)", t, re.I)
    poe_screen = re.search(r"Voyageurs\s+screen[ée]s\s+(\d[\d\s]*)(?:\s*\((\d+[,.]?\d*)\s*%\))?", t, re.I)
    if poe_total and row.get("travellers_total") in ("", None): row["travellers_total"] = to_int(poe_total.group(1)) or ""
    if poe_screen and row.get("travellers_screened") in ("", None):
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
        def key_value(v: Any) -> str:
            if v is None or (isinstance(v, float) and pd.isna(v)):
                return ""
            txt = str(v)
            return "" if txt.lower() == "nan" else txt
        key_new = set(tuple(key_value(r.get(c, "")) for c in key_cols) for _, r in new_df.iterrows())
        keep = []
        for _, r in df.iterrows():
            keep.append(tuple(key_value(r.get(c, "")) for c in key_cols) not in key_new)
        out = pd.concat([df.loc[keep], new_df], ignore_index=True)
    else:
        out = new_df
    sort_cols = [c for c in ["reporting_date", "date", "province", "health_zone"] if c in out.columns]
    if sort_cols:
        out = out.sort_values(sort_cols)
    out.to_csv(path, index=False)



def safe_int(value: Any, default: int = 0) -> int:
    """Parse integers from already-validated CSV values without treating 29.0 as 290."""
    if value is None:
        return default
    txt = norm_text(value)
    if not txt:
        return default
    try:
        return int(float(txt.replace(",", "")))
    except Exception:
        v = to_int(value)
        return int(v) if v is not None else default


def safe_float(value: Any, default: float | None = None) -> float | None:
    v = to_float(value)
    return float(v) if v is not None else default


def report_sort_key(row: dict[str, Any]) -> tuple[int, str]:
    no = report_number_from_text(str(row.get("report_no", ""))) or 0
    return (no, str(row.get("reporting_date", "")))


def load_csv_dicts(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return pd.read_csv(path, dtype=str).fillna("").to_dict(orient="records")


def previous_report_row(report_no: int, report_date: str) -> dict[str, Any] | None:
    rows = load_csv_dicts(DATA / "report_summary.csv")
    prior = []
    for r in rows:
        no = report_number_from_text(str(r.get("report_no", ""))) or 0
        d = str(r.get("reporting_date", ""))
        # Use the previous SitRep number as the comparator. Do not compare against
        # an existing row with the same report number, which can happen when a
        # report is re-run with --force or reprocessed after manual correction.
        if no < report_no:
            prior.append(r)
    if not prior:
        return None
    prior.sort(key=report_sort_key)
    return prior[-1]


def rows_for_date(path: Path, date: str) -> list[dict[str, Any]]:
    return [r for r in load_csv_dicts(path) if str(r.get("date") or r.get("reporting_date")) == str(date)]


def health_zone_case_map(date: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows_for_date(DATA / "cases_by_hz.csv", date):
        hz = norm_text(r.get("health_zone", ""))
        if hz:
            out[hz] = safe_int(r.get("confirmed_cases"))
    return out


def response_national_row(date: str) -> dict[str, Any] | None:
    rows = [r for r in rows_for_date(DATA / "response_indicators.csv", date) if str(r.get("admin_level", "")).lower() == "national"]
    return rows[-1] if rows else None


def latest_uganda_evd_row() -> dict[str, Any] | None:
    path = DATA / "uganda_evd_summary.csv"
    rows = load_csv_dicts(path)
    if not rows:
        return None
    rows.sort(key=lambda r: str(r.get("as_of_date", "")))
    return rows[-1]


def parse_iso_date(value: Any) -> date | None:
    txt = norm_text(value)
    if not txt:
        return None
    try:
        return date.fromisoformat(txt[:10])
    except Exception:
        return None


def uganda_history_rows() -> list[dict[str, Any]]:
    rows = load_csv_dicts(DATA / "uganda_evd_history.csv")
    if not rows:
        rows = load_csv_dicts(DATA / "uganda_evd_summary.csv")
    rows = [r for r in rows if r.get("as_of_date")]
    rows.sort(key=lambda r: str(r.get("as_of_date", "")))
    return rows


def uganda_daily_case_rows() -> list[dict[str, Any]]:
    rows = load_csv_dicts(DATA / "uganda_evd_daily_cases.csv")
    rows = [r for r in rows if r.get("date")]
    rows.sort(key=lambda r: str(r.get("date", "")))
    return rows


def uganda_no_reported_increase_info(latest: dict[str, Any] | None) -> dict[str, Any]:
    """Return Uganda trend info, preferring daily confirmation chart over cumulative history.

    no_increase_days counts calendar days from the day after the most recent
    positive daily-confirmation date to the latest Uganda as-of date.
    """
    if not latest:
        return {}
    latest_date = parse_iso_date(latest.get("as_of_date"))
    latest_cases = safe_int(latest.get("cumulative_confirmed_cases"))

    daily = uganda_daily_case_rows()
    if daily and latest_date:
        positive_dates = []
        for r in daily:
            d = parse_iso_date(r.get("date"))
            v = safe_int(r.get("confirmed_cases"))
            if d and d <= latest_date and v > 0:
                positive_dates.append(d)
        if positive_dates:
            last_positive = max(positive_dates)
            return {
                "basis": "daily_confirmation_chart",
                "last_increase_date": last_positive.isoformat(),
                "no_increase_days": max((latest_date - last_positive).days, 0),
                "latest_as_of_date": latest_date.isoformat(),
                "latest_confirmed_cases": latest_cases,
                "status": "no_recent_increase" if (latest_date - last_positive).days > 0 else "increase_on_latest_date",
            }

    hist = uganda_history_rows()
    if len(hist) >= 2 and latest_date:
        last_increase_date = None
        prev_cases = None
        for r in hist:
            d = parse_iso_date(r.get("as_of_date"))
            c = safe_int(r.get("cumulative_confirmed_cases"))
            if d is None:
                continue
            if prev_cases is not None and c > prev_cases:
                last_increase_date = d
            prev_cases = c
        if last_increase_date:
            return {
                "basis": "cumulative_history",
                "last_increase_date": last_increase_date.isoformat(),
                "no_increase_days": max((latest_date - last_increase_date).days, 0),
                "latest_as_of_date": latest_date.isoformat(),
                "latest_confirmed_cases": latest_cases,
                "status": "no_recent_increase" if (latest_date - last_increase_date).days > 0 else "increase_on_latest_date",
            }
        # If no increase is seen within the available history, report the span
        # conservatively as since the first available history date.
        first = parse_iso_date(hist[0].get("as_of_date"))
        if first:
            return {
                "basis": "cumulative_history_no_increase_within_available_history",
                "last_increase_date": None,
                "no_increase_days": max((latest_date - first).days, 0),
                "latest_as_of_date": latest_date.isoformat(),
                "latest_confirmed_cases": latest_cases,
                "status": "no_increase_within_available_history",
            }
    return {
        "basis": "latest_snapshot_only",
        "last_increase_date": None,
        "no_increase_days": None,
        "latest_as_of_date": latest.get("as_of_date"),
        "latest_confirmed_cases": latest_cases,
        "status": "unknown",
    }


def pct_text(value: float | None) -> str:
    if value is None:
        return "不明"
    return f"{value * 100:.1f}%"


def fmt_count(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except Exception:
        return str(value)


def build_sitrep_delta_payload(
    report_no: int,
    report_date: str,
    current_report: dict[str, Any],
    previous_report: dict[str, Any] | None,
    resp_row: dict[str, Any] | None,
) -> dict[str, Any]:
    previous_date = str(previous_report.get("reporting_date", "")) if previous_report else ""
    cur_hz = health_zone_case_map(report_date)
    prev_hz = health_zone_case_map(previous_date) if previous_date else {}
    new_hz = sorted([hz for hz, v in cur_hz.items() if v > 0 and prev_hz.get(hz, 0) <= 0])
    increased_hz = sorted(
        [{"health_zone": hz, "change": cur_hz[hz] - prev_hz.get(hz, 0), "latest": cur_hz[hz]}
         for hz in cur_hz if cur_hz[hz] - prev_hz.get(hz, 0) > 0],
        key=lambda x: x["change"],
        reverse=True,
    )[:8]

    current_resp = response_national_row(report_date) or resp_row or {}
    previous_resp = response_national_row(previous_date) if previous_date else None
    ug = latest_uganda_evd_row()
    ug_trend = uganda_no_reported_increase_info(ug)

    payload = {
        "from_report": previous_report.get("report_no") if previous_report else None,
        "to_report": f"N{report_no}",
        "from_reporting_date": previous_date or None,
        "to_reporting_date": report_date,
        "confirmed_cases": {
            "previous": safe_int(previous_report.get("drc_confirmed_cases")) if previous_report else None,
            "latest": safe_int(current_report.get("drc_confirmed_cases")),
        },
        "confirmed_deaths": {
            "previous": safe_int(previous_report.get("drc_confirmed_deaths")) if previous_report else None,
            "latest": safe_int(current_report.get("drc_confirmed_deaths")),
        },
        "health_zones": {
            "previous_count": len([v for v in prev_hz.values() if v > 0]) if previous_report else None,
            "latest_count": len([v for v in cur_hz.values() if v > 0]),
            "new_health_zones": new_hz,
            "largest_increases": increased_hz,
        },
        "response": {
            "contact_followup_rate_previous": safe_float(previous_resp.get("contact_followup_rate")) if previous_resp else None,
            "contact_followup_rate_latest": safe_float(current_resp.get("contact_followup_rate")) if current_resp else None,
            "contacts_under_followup_latest": safe_int(current_resp.get("contacts_under_followup")) if current_resp else None,
            "contacts_seen_latest": safe_int(current_resp.get("contacts_seen")) if current_resp else None,
            "alerts_reported_latest": safe_int(current_resp.get("alerts_reported")) if current_resp else None,
            "alerts_investigated_latest": safe_int(current_resp.get("alerts_investigated")) if current_resp else None,
            "alert_investigation_rate_latest": safe_float(current_resp.get("alert_investigation_rate")) if current_resp else None,
            "samples_analysed_latest": safe_int(current_resp.get("samples_analysed")) if current_resp else None,
            "positive_samples_latest": safe_int(current_resp.get("positive_samples")) if current_resp else None,
            "travellers_total_latest": safe_int(current_resp.get("travellers_total")) if current_resp else None,
            "travellers_screened_latest": safe_int(current_resp.get("travellers_screened")) if current_resp else None,
            "poe_screening_coverage_latest": safe_float(current_resp.get("poe_screening_coverage")) if current_resp else None,
        },
        "uganda": {
            "as_of_date": ug.get("as_of_date") if ug else None,
            "confirmed_cases": safe_int(ug.get("cumulative_confirmed_cases")) if ug else None,
            "confirmed_deaths": safe_int(ug.get("cumulative_deaths")) if ug else None,
            "imported_cases": safe_int(ug.get("imported_cases")) if ug else None,
            "local_cases": safe_int(ug.get("local_cases")) if ug else None,
            "new_cases_last_24h": safe_int(ug.get("new_cases_last_24h")) if ug else None,
            "no_reported_increase_days": ug_trend.get("no_increase_days"),
            "last_reported_increase_date": ug_trend.get("last_increase_date"),
            "trend_basis": ug_trend.get("basis"),
            "trend_status": ug_trend.get("status"),
            "source_url": ug.get("source_url") if ug else "https://evd-daily.health.go.ug/",
        },
    }

    for key in ("confirmed_cases", "confirmed_deaths"):
        prev = payload[key]["previous"]
        latest = payload[key]["latest"]
        payload[key]["change"] = latest - prev if prev is not None else None

    return payload


def deterministic_summary_parts_ja(payload: dict[str, Any]) -> tuple[str, str]:
    from_report = payload.get("from_report") or "前回"
    to_report = payload.get("to_report") or "今回"
    cases = payload.get("confirmed_cases", {})
    deaths = payload.get("confirmed_deaths", {})
    hz = payload.get("health_zones", {})
    resp = payload.get("response", {})
    ug = payload.get("uganda", {})

    drc_parts = []
    if cases.get("change") is not None and deaths.get("change") is not None:
        drc_parts.append(
            f"{from_report}から{to_report}への更新では、DRCの累積確定例は{cases.get('previous')}例から{cases.get('latest')}例へ{cases.get('change')}例増加し、死亡例は{deaths.get('previous')}例から{deaths.get('latest')}例へ{deaths.get('change')}例増加した。"
        )
    else:
        drc_parts.append(f"{to_report}では、DRCの累積確定例は{cases.get('latest')}例、死亡例は{deaths.get('latest')}例である。")

    new_hz = hz.get("new_health_zones") or []
    if new_hz:
        drc_parts.append(
            f"新たに{', '.join(new_hz)}の{len(new_hz)} health zoneが報告され、影響を受けたhealth zoneは{hz.get('latest_count')}であった。"
        )
    else:
        drc_parts.append("新規に報告されたhealth zoneは確認されていない。")

    inc = hz.get("largest_increases") or []
    if inc:
        top = ", ".join([f"{x['health_zone']}+{x['change']}" for x in inc[:4]])
        drc_parts.append(f"health zone別では、{top}などで増加が報告された。")

    prev_rate = resp.get("contact_followup_rate_previous")
    latest_rate = resp.get("contact_followup_rate_latest")
    if latest_rate is not None:
        if prev_rate is not None:
            drc_parts.append(f"接触者追跡率は{pct_text(prev_rate)}から{pct_text(latest_rate)}へ変化した。")
        else:
            drc_parts.append(f"接触者追跡率は{pct_text(latest_rate)}であった。")
    if resp.get("alert_investigation_rate_latest") is not None:
        drc_parts.append(f"アラート調査率は{pct_text(resp.get('alert_investigation_rate_latest'))}、検査陽性は{resp.get('positive_samples_latest')}件であった。")

    # PoE/PoC screening is shown only when a concrete updated value is available
    # from the latest SitRep response indicators.
    poe_cov = resp.get("poe_screening_coverage_latest")
    travellers_total = resp.get("travellers_total_latest")
    travellers_screened = resp.get("travellers_screened_latest")
    if poe_cov is not None or travellers_total or travellers_screened:
        if travellers_total and poe_cov is not None:
            drc_parts.append(f"PoC/PoEでは{fmt_count(travellers_total)}人の通過が記録され、スクリーニング率は{pct_text(poe_cov)}であった。")
        elif poe_cov is not None:
            drc_parts.append(f"PoC/PoEスクリーニング率は{pct_text(poe_cov)}であった。")

    ug_parts = []
    if ug.get("confirmed_cases") is not None:
        ug_parts.append(
            f"ウガンダ側はMoH daily pageに基づき累積確定例{ug.get('confirmed_cases')}例、死亡{ug.get('confirmed_deaths')}例（輸入例{ug.get('imported_cases')}例、国内例{ug.get('local_cases')}例）である。"
        )
        if ug.get("new_cases_last_24h") is not None:
            ug_parts.append(f"過去24時間の新規確定例は{ug.get('new_cases_last_24h')}例であった。")
        no_inc_days = ug.get("no_reported_increase_days")
        trend_status = ug.get("trend_status")
        last_inc = ug.get("last_reported_increase_date")
        if no_inc_days is not None and trend_status in {"no_recent_increase", "no_increase_within_available_history"}:
            if last_inc:
                ug_parts.append(f"日別確定例データでは{last_inc}を最後に増加が記録され、最新更新日時点で過去{no_inc_days}日間、新たな確定例の増加は報告されていない。")
            else:
                ug_parts.append(f"累積履歴に基づき、過去{no_inc_days}日間、新たな確定例の増加は報告されていない。")
    return "".join(drc_parts), "".join(ug_parts)


def deterministic_delta_summary_ja(payload: dict[str, Any]) -> str:
    drc, uganda = deterministic_summary_parts_ja(payload)
    if uganda:
        return f"• DRC：{drc}\n• ウガンダ：{uganda}"
    return f"• DRC：{drc}"


def openai_delta_summary_ja(payload: dict[str, Any]) -> tuple[dict[str, str] | None, str]:
    if OpenAI is None or not os.environ.get("OPENAI_API_KEY"):
        return None, ""
    model = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
    prompt = f"""
You are writing concise Japanese factual updates for a public health dashboard.
Use only the validated structured delta JSON below. Do not invent numbers.

Return JSON only with:
{{
  "drc_summary_ja": "Japanese factual summary for DRC only",
  "uganda_summary_ja": "Japanese factual summary for Uganda only",
  "summary_ja": "combined summary using two bullets: • DRC：...\\n• ウガンダ：..."
}}

Rules:
- Separate DRC and ウガンダ.
- Use "ウガンダ", not "Uganda", in Japanese text.
- Describe only observed/reported changes and reported indicators.
- Do not include recommendations, risk judgments, or value judgments.
- Do not use phrases such as 「重要である」「必要である」「警戒が必要」「懸念される」「監視が必要」.
- Include PoE/PoC screening only if concrete screening values are present in the JSON.
- Mention whether ウガンダ has reported no increase for X days when that field is present.
- Keep each country summary to 2-4 sentences.

Validated delta JSON:
{json.dumps(payload, ensure_ascii=False, indent=2)}
""".strip()
    try:
        client = OpenAI()
        raw = None
        try:
            resp = client.responses.create(
                model=model,
                input=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.1,
            )
            raw = getattr(resp, "output_text", None)
            if not raw:
                raw = resp.output[0].content[0].text  # type: ignore[attr-defined]
        except Exception:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.1,
            )
            raw = resp.choices[0].message.content
        if not raw:
            return None, model
        data = json.loads(raw)
        drc = norm_text(data.get("drc_summary_ja", ""))
        uganda = norm_text(data.get("uganda_summary_ja", ""))
        summary = norm_text(data.get("summary_ja", ""))
        if not summary:
            summary = f"• DRC：{drc}\n• ウガンダ：{uganda}" if uganda else f"• DRC：{drc}"
        if not drc and not uganda:
            return None, model
        return {"drc_summary_ja": drc, "uganda_summary_ja": uganda, "summary_ja": summary}, model
    except Exception as e:
        EXTRACTED.mkdir(exist_ok=True)
        (EXTRACTED / "openai_delta_summary_error.txt").write_text(f"{type(e).__name__}: {e}", encoding="utf-8")
        return None, model


def write_ai_sitrep_summary(
    report_no: int,
    report_date: str,
    current_report: dict[str, Any],
    resp_row: dict[str, Any] | None,
) -> None:
    previous = previous_report_row(report_no, report_date)
    payload = build_sitrep_delta_payload(report_no, report_date, current_report, previous, resp_row)
    det_drc, det_uganda = deterministic_summary_parts_ja(payload)
    det_summary = f"• DRC：{det_drc}\n• ウガンダ：{det_uganda}" if det_uganda else f"• DRC：{det_drc}"

    ai_parts, model = openai_delta_summary_ja(payload)
    generated_by = "openai" if ai_parts else "deterministic"
    drc_summary = (ai_parts or {}).get("drc_summary_ja") or det_drc
    uganda_summary = (ai_parts or {}).get("uganda_summary_ja") or det_uganda
    summary = (ai_parts or {}).get("summary_ja") or det_summary

    EXTRACTED.mkdir(exist_ok=True)
    (EXTRACTED / f"sitrep_N{report_no:03d}_delta_payload.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    row = {
        "report_no": f"N{report_no}",
        "reporting_date": report_date,
        "previous_report_no": previous.get("report_no") if previous else "",
        "previous_reporting_date": previous.get("reporting_date") if previous else "",
        "drc_summary_ja": drc_summary,
        "uganda_summary_ja": uganda_summary,
        "summary_ja": summary,
        "generated_by": generated_by,
        "openai_model": model if generated_by == "openai" else "",
        "source": "validated SitRep delta + Uganda MoH EVD daily page",
        "updated_at_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "notes": "Generated at SitRep update time. The dashboard displays this saved text and does not call OpenAI from the browser. Latest-situation text is limited to observed/reported changes; value judgments are left to the public-health assessment cards.",
    }
    append_or_replace_csv(DATA / "ai_sitrep_summary.csv", [row], ["report_no", "reporting_date"])
    log(f"AI situation summary written: report=N{report_no}, generated_by={generated_by}, chars={len(summary)}")



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
        # Do not silently infer huge "unassigned" counts. In this SitRep layout,
        # large gaps usually mean the reporting year (2026) was misread as the
        # cumulative case count. Small gaps are legitimate "Autres zones non
        # encore identifiées" rows.
        unassigned_cases = diff if 0 <= diff <= 100 else None

    has_hz_cumulative_table = bool(re.search(r"Nombre\s+cumulatif", text, re.I))
    hz_discrepancy = None if total_cases is None or hz_sum <= 0 else hz_sum + int(unassigned_cases or 0) - total_cases
    summary_only_hz = hz_sum <= 0 and not has_hz_cumulative_table

    log(f"Deterministic validation: health_zone_sum={hz_sum}, unassigned_cases={unassigned_cases}, total_cases={total_cases}, rows={len(hz_rows)}, cumulative_hz_table={has_hz_cumulative_table}")
    if summary_only_hz:
        # N84-N86 (and potentially future redesigned reports) provide national/provincial
        # cumulative totals but no cumulative health-zone table. Do not invent a spatial
        # distribution and do not block the whole dashboard update; update the summary
        # and response indicators and leave that date absent from cases_by_hz.csv.
        log("No cumulative health-zone table is present in this SitRep; proceeding with a summary-only spatial update.")
        unassigned_cases = 0
    elif total_cases is not None and (hz_sum <= 0 or abs(int(hz_discrepancy or 0)) > 1):
        if not openai_used:
            log("Deterministic validation failed; attempting OpenAI fallback.")
            try_openai("Health-zone counts did not validate against the total confirmed cases.")
            hz_sum = sum(int(r["confirmed_cases"]) for r in hz_rows if str(r["confirmed_cases"]).isdigit())
            if unassigned_cases is None and total_cases is not None:
                diff = total_cases - hz_sum
                unassigned_cases = diff if 0 <= diff <= 100 else None
            hz_discrepancy = None if total_cases is None or hz_sum <= 0 else hz_sum + int(unassigned_cases or 0) - total_cases
            log(f"Post-OpenAI validation: health_zone_sum={hz_sum}, unassigned_cases={unassigned_cases}, total_cases={total_cases}, rows={len(hz_rows)}, discrepancy={hz_discrepancy}, openai_used={openai_used}")

    if not summary_only_hz and (total_cases is None or hz_sum <= 0 or unassigned_cases is None or abs(int(hz_discrepancy or 0)) > 1):
        detail = validation_detail("Extracted health-zone counts did not validate after deterministic extraction and optional OpenAI fallback.")
        detail["health_zone_discrepancy"] = hz_discrepancy
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
        "uganda_confirmed_cases": "20",
        "uganda_confirmed_deaths": "2",
        "source": report_label,
        "notes": "Automatically updated from INSP SitRep PDF. Uganda figures remain latest available DTM EVD snapshot values unless separately updated.",
    }
    append_or_replace_csv(DATA / "report_summary.csv", [report_row], ["report_no", "reporting_date"])
    if hz_rows and not summary_only_hz:
        append_or_replace_csv(DATA / "cases_by_hz.csv", hz_rows, ["date", "health_zone"])
    if int(unassigned_cases or 0) > 0 or int(unassigned_deaths or 0) > 0:
        unassigned_row = {
            "date": report_date,
            "month": report_date[:7],
            "province": "Ituri",
            "category": "unventilated_unknown_health_zone",
            "confirmed_cases": int(unassigned_cases or 0),
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

    # Generate a saved Japanese SitRep-to-SitRep delta summary. This may use
    # OpenAI when available, but the browser only reads the saved CSV.
    write_ai_sitrep_summary(report_no, report_date, report_row, resp_row)

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
        "health_zone_discrepancy": hz_discrepancy,
        "spatial_detail_available": bool(hz_rows),
        "summary_only_spatial_update": summary_only_hz,
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
