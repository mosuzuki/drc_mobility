#!/usr/bin/env python3
"""Backup national surveillance snapshot when INSP SitRep publication/download fails.
ECDC explicitly reports the INSP data cutoff. Never manufactures health-zone rows.
The ECDC- prefix permits replacement by an official full SitRep for the same date.
"""
from __future__ import annotations
import csv,re,sys
from datetime import date, datetime, timezone
from pathlib import Path
import requests
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data/report_summary.csv'
STATUS=ROOT/'.ecdc_snapshot_status.md'
INDEX='https://www.ecdc.europa.eu/en/public-health-events/2026-EIP-00026'

def log(message):
    STATUS.write_text('# ECDC national fallback\n\n'+message+'\n',encoding='utf-8');print(message)

def main():
    session=requests.Session(); session.headers.update({'User-Agent':'Mozilla/5.0 (public health monitoring)'})
    urls=[]
    try:
        resp=session.get(INDEX,timeout=35);resp.raise_for_status()
        soup=BeautifulSoup(resp.text,'html.parser')
        for a in soup.select('a[href]'):
            href=a.get('href','')
            if re.search(r'/2026-10-\d{2}',href):
                from urllib.parse import urljoin
                urls.append(urljoin(INDEX,href))
    except Exception as exc:
        log('INDEX DISCOVERY FAILED: '+str(exc))
    # Known landing page as fallback: the exact date in ECDC text controls ingestion.
    urls.extend(['https://www.ecdc.europa.eu/en/public-health-events/2026-eip-00026/2026-10-22'])
    urls=list(dict.fromkeys(urls))
    with DATA.open(newline='',encoding='utf-8-sig') as f:
        records=list(csv.DictReader(f));fields=list(records[0])
    known={(r.get('reporting_date'),r.get('drc_confirmed_cases'),r.get('drc_confirmed_deaths')) for r in records}
    today=date.today();inserted=[];last_error=''
    for url in urls[:14]:
        try:
            r=session.get(url,timeout=35);r.raise_for_status()
            text=' '.join(BeautifulSoup(r.text,'html.parser').stripped_strings)
            # ECDC date of observation is not its publication date.
            pat=(r'As of \d{1,2} [A-Za-z]+ 2026.{0,260}?'+
                 r'(?:DRC|Democratic Republic of the Congo).*?'+
                 r'(?:total of )?([\d \,]+) confirmed cases, including ([\d \,]+) (?:related |confirmed )?deaths'+
                 r'.{0,110}?data (?:up )?until (\d{1,2}) ([A-Za-z]+)(?: 2026)?')
            m=re.search(pat,text,re.I)
            if not m:continue
            confirmed=int(re.sub(r'\D','',m.group(1))); deaths=int(re.sub(r'\D','',m.group(2)))
            months={'september':9,'october':10,'november':11,'august':8}
            month=months.get(m.group(4).lower())
            if not month:continue
            d=date(2026,month,int(m.group(3)))
            if d>today or confirmed<1000 or deaths>confirmed:continue
            if (d.isoformat(),str(confirmed),str(deaths)) in known: continue
            if any(x['reporting_date']==d.isoformat() and not str(x['report_no']).startswith('ECDC-') for x in records):continue
            row={f:'' for f in fields}
            row.update({'report_no':'ECDC-'+d.isoformat(),'reporting_date':d.isoformat(),
                'drc_confirmed_cases':str(confirmed),'drc_confirmed_deaths':str(deaths),
                'uganda_confirmed_cases':'20','uganda_confirmed_deaths':'2','source':url,
                'notes':'ECDC verified national INSP-derived snapshot; no health-zone table. Pending full INSP SitRep.'})
            records=[x for x in records if x['report_no']!=row['report_no']]+[row]
            known.add((d.isoformat(),str(confirmed),str(deaths)));inserted.append(d.isoformat())
        except Exception as exc:last_error=str(exc)
    if inserted:
        records.sort(key=lambda x:(x['reporting_date'],x['report_no']))
        with DATA.open('w',newline='',encoding='utf-8') as f:
            writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(records)
    log('New ECDC report dates: '+(', '.join(inserted) if inserted else 'none')+'; '+('Last error: '+last_error if last_error else 'No parsing exceptions.'))
if __name__=='__main__':main()
