#!/usr/bin/env python3
"""Conservatively update non-DRC country counts from ECDC epidemic intelligence.

Only explicitly stated confirmed counts are accepted. Fallback: South African NICD
country table. Report/publication dates are stored separately; failed extraction
never replaces the last verified snapshot. Do not label publication date as onset.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data' / 'international_cases.json'
STATUS = ROOT / '.international_cases_status.md'
ECDC_INDEX = 'https://www.ecdc.europa.eu/en/public-health-events/2026-EIP-00026'
NICD_URL = 'https://www.nicd.ac.za/bundibugyo-virus-disease-updates/'
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; public-health-dashboard/1.0; data reconciliation)'}
COUNTRIES = ('kenya', 'nigeria', 'france')


def clean(s: str) -> str:
    return re.sub(r'\s+', ' ', s).strip()


def get_page(url: str) -> str:
    r = requests.get(url, timeout=25, headers=HEADERS)
    r.raise_for_status()
    return r.text


def latest_ecdc_url(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, 'html.parser')
    links = []
    for a in soup.find_all('a', href=True):
        href = urljoin(ECDC_INDEX, a['href'])
        m = re.search(r'/2026-eip-00026/(2026-\d\d-\d\d)(?:[/?#]|$)', href, re.I)
        if m and m.group(1) <= date.today().isoformat():
            links.append((m.group(1), href.split('#')[0]))
    if not links:
        raise ValueError('ECDC index has no dated outbreak update links')
    return max(links)


def country_section(html: str, name: str) -> str:
    """Only a country-labelled section, never unanchored text from the entire page."""
    soup = BeautifulSoup(html, 'html.parser')
    main = soup.find('main') or soup
    for h in main.find_all(re.compile(r'^h[2-6]$')):
        if clean(h.get_text(' ')).lower() != name.lower():
            continue
        level = int(h.name[1]); chunks = []
        # ECDC country heading followed by paragraphs until the next heading
        for sib in h.next_siblings:
            if getattr(sib, 'name', None) and re.match(r'^h[2-6]$', sib.name):
                if int(sib.name[1]) <= level: break
            if getattr(sib, 'get_text', None): chunks.append(sib.get_text(' ', strip=True))
            elif isinstance(sib, str): chunks.append(sib)
        txt = clean(' '.join(chunks))
        if txt: return txt[:9000]
    return ''


def parse_ecdc(html: str, published: str) -> dict:
    """Parse explicitly reported confirmed counts; reject ambiguous/free-text guesses."""
    sections = {n: country_section(html, n.title()) for n in COUNTRIES}
    found = {}
    # Exact numerical expressions in the Kenya country section.
    k = sections['kenya']
    if k:
        word_numbers = {'one':'1','two':'2','three':'3','four':'4','five':'5','six':'6','seven':'7','eight':'8','nine':'9','ten':'10','eleven':'11','twelve':'12'}
        # ECDC often uses 'nine cases, including one death' rather than numerals.
        k = re.sub(r'\b(?:' + '|'.join(word_numbers) + r')\b', lambda m: word_numbers[m.group(0).lower()], k, flags=re.I)
        patterns = [
            r'(?:total of|reported|reports|confirmed)\s+(\d{1,3}(?:[ ,]\d{3})*)\s+(?:confirmed\s+)?cases?\s*,?\s+(?:including|with)\s+(\d+)\s+death',
            r'(\d{1,3})\s+confirmed\s+cases?\s*,?\s+(?:including|with)\s+(\d+)\s+death',
            r'(\d{1,3})\s+(?:confirmed\s+)?cases?(?:\s+have\s+been\s+reported\s+in\s+Kenya)?\s*,?\s+(?:including|with)\s+(\d+)\s+death',
        ]
        for p in patterns:
            m = re.search(p, k, re.I)
            if m:
                found['kenya'] = (int(m.group(1).replace(' ', '').replace(',', '')), int(m.group(2)))
                break
    # Nigeria first confirmed case was reported on 20 October (words are common).
    n = sections['nigeria']
    if n and re.search(r'\b(?:one|1)\s+(?:laboratory[- ]confirmed\s+|confirmed\s+)?case\b', n, re.I):
        # An unknown death status must not be converted to zero without corroboration.
        found['nigeria'] = (1, None)
    for key, (cases, deaths) in list(found.items()):
        if cases < 1 or cases > 2000 or (deaths is not None and (deaths < 0 or deaths > cases)):
            found.pop(key)
    return found


def parse_nicd_table(html: str) -> dict:
    soup = BeautifulSoup(html, 'html.parser')
    found = {}
    for table in soup.find_all('table'):
        rows = table.find_all('tr')
        headings = clean(rows[0].get_text(' ', strip=True)).lower() if rows else ''
        if 'confirmed cases' not in headings or 'confirmed deaths' not in headings:
            continue
        for tr in rows[1:]:
            cells = [clean(td.get_text(' ', strip=True)) for td in tr.find_all(['td', 'th'])]
            if len(cells) < 4: continue
            name = cells[0].lower()
            key = next((key for key in COUNTRIES if name.startswith(key)), None)
            if not key: continue
            mc = re.fullmatch(r'\d[\d, ]*', cells[2]); md = re.fullmatch(r'\d[\d, ]*', cells[3])
            if not mc: continue
            cases = int(cells[2].replace(' ', '').replace(',', ''))
            deaths = int(cells[3].replace(' ', '').replace(',', '')) if md else None
            mdate = re.search(r'(\d{1,2})\s+([A-Za-z]{3})\s+2026', cells[0])
            from datetime import datetime as dt
            reporting = dt.strptime(mdate.group(0), '%d %b %Y').date().isoformat() if mdate else None
            if 0 < cases <= 2000 and (deaths is None or 0 <= deaths <= cases):
                found[key] = (cases, deaths, reporting)
        if found: break
    return found


def valid_country(entry: dict) -> bool:
    try:
        c = int(entry['confirmed_cases']); d = entry.get('confirmed_deaths')
        return c >= 0 and (d is None or 0 <= int(d) <= c) and date.fromisoformat(entry['as_of_date']) <= date.today()
    except (TypeError, ValueError, KeyError):
        return False


def merge(current: dict, incoming: dict) -> bool:
    """Incoming must be at least as recent; do not erase known deaths with unknowns."""
    if not valid_country(incoming): return False
    old_date = current.get('as_of_date', '0000-00-00')
    if incoming['as_of_date'] < old_date: return False
    if (incoming['as_of_date'] == old_date and incoming['confirmed_cases'] < current.get('confirmed_cases', 0)):
        return False
    if incoming.get('confirmed_deaths') is None and current.get('confirmed_deaths') is not None:
        incoming['confirmed_deaths'] = current['confirmed_deaths']
        incoming['death_source_note'] = 'Deaths carried forward from prior independently verified source'
    current.update(incoming)
    return True


def update_from_sources(data: dict) -> list[str]:
    log = []
    countries = data.setdefault('countries', {})
    try:
        date_str, url = latest_ecdc_url(get_page(ECDC_INDEX))
        parsed = parse_ecdc(get_page(url), date_str)
        log.append(f'ECDC latest update: {date_str}; parsed countries: {list(parsed)}')
        for country, (cases, deaths) in parsed.items():
            # ECDC Kenya as-of date is the page date; Nigeria case confirmation is 20 Oct 2026.
            asof = '2026-10-20' if country == 'nigeria' and date_str >= '2026-10-20' else date_str
            entry = {'confirmed_cases': cases, 'confirmed_deaths': deaths,
                     'as_of_date': asof, 'publication_date': date_str,
                     'source': 'ECDC', 'source_url': url}
            previous = countries.get(country, {}).copy()
            ok = merge(countries.setdefault(country, {}), entry)
            if ok and (cases != previous.get('confirmed_cases') or asof != previous.get('as_of_date')):
                countries[country]['note_ja'] = '現時点の伝播状況や接触者調査の詳細は出典を参照してください。'
                countries[country]['note_en'] = 'See the source for the latest transmission and contact tracing details.'
                countries[country]['note_fr'] = 'Voir la source pour les détails récents des transmissions et du suivi des contacts.'
            log.append(f'{country}: {"updated" if ok else "retained"} from ECDC')
    except Exception as ex:
        log.append(f'ECDC unavailable / extraction rejected: {ex}')
    try:
        parsed = parse_nicd_table(get_page(NICD_URL))
        log.append(f'NICD comparison: {list(parsed)}')
        for country, (cases, deaths, asof) in parsed.items():
            if not asof: continue
            entry = {'confirmed_cases': cases, 'confirmed_deaths': deaths,
                     'as_of_date': asof, 'publication_date': asof,
                     'source': 'NICD', 'source_url': NICD_URL}
            # NICD provides a corroborated zero-death count for Nigeria only if explicit.
            ok = merge(countries.setdefault(country, {}), entry)
            log.append(f'{country}: {"updated" if ok else "retained"} from NICD')
    except Exception as ex:
        log.append(f'NICD unavailable / extraction rejected: {ex}')
    # Ensure every retained country entry passes validation.
    for country in COUNTRIES:
        if country in countries and not valid_country(countries[country]):
            raise ValueError(f'Invalid retained record for {country}')
    return log


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--offline', action='store_true', help='Validate the bundled source-verified snapshot without networking')
    args = ap.parse_args()
    old_text = OUT.read_text(encoding='utf-8')
    data = json.loads(old_text)
    logs = ['# International country count update', '']
    if not args.offline:
        logs += update_from_sources(data)
    else:
        logs.append('Offline: retained independently verified seeded snapshot')
    data['schema_version'] = 1
    logs.append(f"Checked at {datetime.now(timezone.utc).isoformat(timespec='seconds')} UTC")
    # Avoid claiming freshness when the upstream source could not be contacted.
    for key in COUNTRIES:
        assert valid_country(data['countries'][key]), key
    new_text = json.dumps(data, ensure_ascii=False, indent=2) + '\n'
    if new_text != old_text:
        OUT.write_text(new_text, encoding='utf-8')
    STATUS.write_text('\n'.join(logs) + '\n', encoding='utf-8')
    print('\n'.join(logs))


if __name__ == '__main__':
    main()
