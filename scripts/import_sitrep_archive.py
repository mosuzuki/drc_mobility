#!/usr/bin/env python3
"""Import verified late-2026 INSP SitRep PDFs into dashboard data.

Supports bulk backfill and single new PDFs, preserving the exact official
reporting date and requiring every province's cumulative zone counts to match.
Uses Poppler pdftotext -layout for complex multi-page tables. Never interpolates
missing reports or treats an ECDC summary as a full spatial report.
"""
from __future__ import annotations
import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
EXTRACTED = ROOT / 'extracted'
PROVS = {'Ituri', 'Nord-Kivu', 'Haut-Uélé', 'Tshopo', 'Sud-Kivu', 'Bas-Uélé', 'Sud Ubangi', 'Nord Ubangi'}
ROW = re.compile(r'^\s*(?P<name>[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s\-\u2011’]+?)\s{2,}(?P<cases>[0-9][0-9 ]*|NA)\s{2,}(?P<deaths>[0-9][0-9 ]*|NA)\s+(?P<pct>\d+[,.]?\d*)\s*%', re.I)
ROW_TIGHT = re.compile(r'^\s*(?P<name>[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s\-\u2011’]+?)\s+(?P<cases>\d{1,6})\s+(?P<deaths>\d{1,6})\s+(?P<pct>\d+[,.]\d+)\s*%', re.I)


def as_int(x):
    if not x or str(x).strip().upper() in {'NA','ND','—'}:
        return None
    return int(re.sub(r'\D','',str(x)))


def norm_prov(n):
    n=' '.join(n.replace('‐','-').replace('–','-').split())
    if re.sub('[^a-z]','',unicodedata.normalize('NFKD',n).encode('ascii','ignore').decode().lower())=='basuele':return 'Bas-Uélé'
    if re.sub('[^a-z]','',unicodedata.normalize('NFKD',n).encode('ascii','ignore').decode().lower())=='hautuele':return 'Haut-Uélé'
    return n


def norm_zone(n):
    n=' '.join(n.strip().replace('‑','-').replace('–','-').split())
    return {'Gethy':'Gety','Nia Nia':'Nia-Nia','Makiso - Kisangani':'Makiso-Kisangani','Wanie - Rukula':'Wanie-Rukula'}.get(n,n)


def read_csv(path):
    if not path.exists():return []
    with path.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))


def write_csv(path,rows,keys=None):
    old=read_csv(path)
    fields=list(old[0]) if old else list(rows[0])
    if not keys:keys=['date']
    m={tuple(r.get(k,'') for k in keys):r for r in old}
    for r in rows:
        key=tuple(str(r.get(k,'')) for k in keys)
        m[key]={**{f:'' for f in fields},**{f:str(r.get(f,'')) for f in fields}}
    result=list(m.values())
    dk='reporting_date' if 'reporting_date' in fields else 'date'
    result.sort(key=lambda r:(r.get(dk,''),str(r.get('report_no','')),str(r.get('province','')),str(r.get('health_zone',''))))
    with path.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(result)


def extract_text(path):
    if shutil.which('pdftotext'):
        p=subprocess.run(['pdftotext','-layout',str(path),'-'],capture_output=True,text=True,timeout=80)
        if p.returncode==0 and len(p.stdout)>1000:return p.stdout
    try:
        import fitz
        with fitz.open(path) as doc:
            return '\n'.join(page.get_text(sort=True) for page in doc)
    except Exception as exc:raise RuntimeError(f'Cannot extract PDF text: {path}: {exc}')


def repair_split_labels(section):
    for a,b,name in [('Boma','Mangbetu','Boma Mangbetu'),('Makiso-','Kisangani','Makiso-Kisangani'),('Wanie-','Rukula','Wanie-Rukula')]:
        a,b=re.escape(a),re.escape(b)
        pat=rf'(?m)^\s*{a}\s*\n\s*([0-9][0-9 ]+\s+[0-9][0-9 ]+\s+[0-9.,]+\s*%[^\n]*)\n\s*{b}\s*$'
        section=re.sub(pat,lambda m:'  '+name+'     '+m.group(1),section)
        pat=rf'(?m)^\s*{a}\s*\n\s*{b}\s*\n\s*([0-9][0-9 ]+\s+[0-9][0-9 ]+\s+[0-9.,]+\s*%[^\n]*)$'
        section=re.sub(pat,lambda m:'  '+name+'     '+m.group(1),section)
        pat=rf'(?m)^\s*{a}\s*\n\s*{b}\s+(\d+\s+\d+\s+[0-9.,]+\s*%[^\n]*)$'
        section=re.sub(pat,lambda m:'  '+name+'     '+m.group(1),section)
    return section


def table_section(text,number):
    start=re.search(rf'Tableau\s*{number}\s*[.\-]',text,re.I)
    if not start:raise ValueError(f'Tableau {number} not found')
    end=re.search(rf'Tableau\s*{number+1}\s*[.\-]',text[start.end():],re.I)
    if not end:raise ValueError(f'Tableau {number+1} not found after Tableau {number}')
    return text[start.end():start.end()+end.start()]


def extract_report(path):
    text=extract_text(path)
    m=re.search(r'SitRep\s+N[°o]?\s*(\d{3})\s*/',text,re.I)
    if not m:m=re.search(r'[_\-](\d{3})[_\-]',path.name)
    if not m:raise ValueError('Missing report number')
    num=int(m.group(1))
    dm=re.search(r'Date de rapportage\s*:\s*(\d{1,2})\s+([A-Za-zéûôÀ-ÿ]+)\s+(2026)',text,re.I)
    if not dm:
        d2=re.search(r'_(\d{2})_(\d{2})_(2026)',path.name)
        if not d2:raise ValueError('Missing reporting date')
        ds=f'{d2.group(3)}-{d2.group(2)}-{d2.group(1)}'
    else:
        months={'septembre':'09','octobre':'10','novembre':'11','aout':'08','août':'08'}
        if dm.group(2).lower() not in months:raise ValueError(f'Unrecognized reporting month {dm.group(2)}')
        ds=f'{dm.group(3)}-{months[dm.group(2).lower()]}-{int(dm.group(1)):02d}'
    try:
        from datetime import date,timedelta
        pub=(date.fromisoformat(ds)+timedelta(days=1)).isoformat()
    except Exception:pub=''
    t1=table_section(text,1)
    # Read the final numeric line preceding "Total". Column order is
    # new cases (sometimes on a separate footer line), cumulative cases,
    # cumulative deaths, CFR. Avoid greedily eating the leading digit of 9 144.
    tlines=t1.splitlines()
    totals=[i for i,line in enumerate(tlines) if re.match(r'^\s*Total\b',line,re.I)]
    if not totals:raise ValueError('Cannot find official Table 1 national TOTAL')
    curr=tlines[totals[-1]]
    cells=re.split(r'\s{2,}',curr.strip())
    if len(cells)>=5 and re.fullmatch(r'\d+[,.]\d+%',cells[-2] or ''):
        # "Total  99  6 942  3 349  48,2%  61/151..."
        pass
    numeric=[v for v in cells if re.fullmatch(r'[\d ]+',v)]
    if len(numeric)>=3:
        cases,deaths=as_int(numeric[1]),as_int(numeric[2])
    else:
        prev=[line for line in tlines[:totals[-1]] if re.search(r'\d+[,.]\d+\s*%\s*$',line)]
        if not prev:raise ValueError('Cannot find total line before Table 1 footer')
        numbers=re.split(r'\s{2,}',prev[-1].strip())
        if len(numbers)<3:raise ValueError(f'Malformed Table 1 TOTAL line: {prev[-1]!r}')
        cases,deaths=as_int(numbers[-3]),as_int(numbers[-2])
    if not (1000<cases<50000 and 100<deaths<cases):raise ValueError(f'Implausible national total {cases}/{deaths}')
    t2=repair_split_labels(table_section(text,2))
    zone_rows=[];prov_totals={};current=None;unvent=[]
    for line in t2.splitlines():
        match=ROW.match(line) or ROW_TIGHT.match(line)
        if not match:continue
        name=norm_prov(match.group('name'));cc=as_int(match.group('cases'));dd=as_int(match.group('deaths'))
        if name=='Total':
            if dd!=deaths or (cc!=cases and not (num==150 and cc==cases+1)):
                raise ValueError(f'Table 2 total {cc}/{dd} != Table 1 {cases}/{deaths}')
            break
        if name.lower()=='a ventiler':
            if current:unvent.append((current,cc or 0,dd or 0))
            continue
        if name in PROVS and name not in prov_totals:
            current=name;prov_totals[name]=(cc,dd);continue
        if current is not None and cc is not None:
            zone_rows.append((current,norm_zone(name),cc,dd))
    if not zone_rows or len(prov_totals)<5:raise ValueError(f'Table 2 parsed too few zones: {len(zone_rows)}, provinces: {list(prov_totals)}')
    provsum=defaultdict(lambda:[0,0]);zoneset=set()
    for p,n,c,d in zone_rows:
        if (p,n) in zoneset:raise ValueError(f'Duplicate health zone {p}/{n}')
        zoneset.add((p,n));provsum[p][0]+=c;provsum[p][1]+=d or 0
    if sum(v[0] for v in prov_totals.values())!=cases or sum(v[1] for v in prov_totals.values())!=deaths:
        if num == 150 and sum(v[0] for v in prov_totals.values()) == cases+1 and sum(v[1] for v in prov_totals.values())==deaths:
            # N150 official Table 1 and KPI: 9,065 cases. Table 2: 9,066,
            # with Haut-Uélé 383 vs Table 1 382. Preserve official national
            # series; omit only that report's inconsistent spatial snapshot.
            zone_rows=[]
            prov_totals={}
        else:
            raise ValueError(f'Province totals do not reconcile nationally N{num}')
    for p,(c,d) in prov_totals.items():
        if provsum[p][0]!=c:raise ValueError(f'{p} case mismatch: zone sum {provsum[p][0]} vs province {c}')
        if provsum[p][1]>d:raise ValueError(f'{p} death mismatch: zones {provsum[p][1]} > province {d}')
    residual=[]
    for p,(c,d) in prov_totals.items():
        if d-provsum[p][1]>0:
            residual.append((p,0,d-provsum[p][1]))
    # Cumulative totals are primary. New daily data are taken only if explicitly
    # described; never infer from cumulative differences after reconciliation.
    newcases=None
    newmatch=re.search(r'(?:Au total,\s*|Au terme[^.]{0,120}?\s*)?(\d{1,4})\s+nouveaux\s+cas\s+confirm[ée]s',text[:3000],re.I)
    if newmatch:newcases=int(newmatch.group(1))
    if newcases is None:
        # Tableau 1 total has new notifications in the first column.
        mt=re.search(r'(?m)^\s*(\d{1,4})\s+([\d ]{2,10})\s+([\d ]{2,10})\s+\d+[,.]\d+\s*%\s*\n\s*Total',t1)
        if mt:newcases=int(mt.group(1))
    first_page=text[:text.find('\f')] if '\f' in text else text[:7000]
    # Rightmost percentage in KPI card is the contact follow-up percentage.
    km=re.search(r'CUMUL\s+DES.*?Provinces\s+touch[ée]es',first_page,re.I|re.S)
    ratios=re.findall(r'(\d{1,3}[,.]\d+)\s*%',km.group(0)) if km else []
    followup=float(ratios[-1].replace(',','.'))/100 if len(ratios)>=2 else None
    if followup is None:raise ValueError(f'N{num} national contact follow-up not found')
    # Short national contact follow-up paragraph.
    contacts_total=contacts_seen=None
    patterns=[r'(?:Parmi les|Des|Au total,?)\s+([\d ]{2,9})\s+contacts\s+(?:sous suivi|à suivre).{0,135}?([\d ]{2,9})\s+(?:d[’\']entre eux\s+)?(?:ont été\s+)?vus',
              r'([\d ]{2,9})\s+contacts\s+(?:sous suivi|à suivre).{0,100}?([\d ]{2,9})\s+(?:d[’\']entre eux\s+)?(?:ont été\s+)?vus']
    for patt in patterns:
        co=re.search(patt,text,re.I|re.S)
        if co:
            total,seen=as_int(co.group(1)),as_int(co.group(2))
            if total and seen and 0<=seen<=total and abs(seen/total-followup)<0.035:
                contacts_total,contacts_seen=total,seen;break
    # Province contact follow-up: accept a ratio ONLY if it reproduces the
    # percentage printed alongside it. This prevents accidentally accepting
    # unrelated numerator/denominator pairs in a narrative paragraph.
    province_contacts={}
    prov_names=r'(Ituri|Nord[- ]Kivu|Haut[- ]U[ée]l[ée]|Bas[- ]U[ée]l[ée]|Tshopo|Sud[- ]Kivu|Sud Ubangi|Nord Ubangi)'
    rx1=re.compile(r'(\d{1,3}[,.]\d)\s*%\s*(?:en|au|à la|à|dans)?\s*'+prov_names+r'\s*\(\s*([\d ]+)\s*/\s*([\d ]+)\s*\)',re.I)
    rx2=re.compile(prov_names+r'\s*\(\s*(\d{1,3}[,.]\d)\s*%\s*[;:,]\s*([\d ]+)\s*/\s*([\d ]+)\s*\)',re.I)
    for mm in rx1.finditer(text):
        pct,p,n,d=mm.groups(); p=norm_prov(p);seen,total=as_int(n),as_int(d)
        if seen is not None and total and seen<=total and abs(seen/total-float(pct.replace(',','.'))/100)<0.012:
            province_contacts[p]=(total,seen,seen/total)
    for mm in rx2.finditer(text):
        p,pct,n,d=mm.groups();p=norm_prov(p);seen,total=as_int(n),as_int(d)
        if seen is not None and total and seen<=total and abs(seen/total-float(pct.replace(',','.'))/100)<0.012:
            province_contacts[p]=(total,seen,seen/total)
    # PoE global rate in table 4 (last percentage from the screening row)
    screening=None
    poe=re.search(r'(?im)^\s*%\s*des\s+voyageurs\s+screen[ée]s\s+(.+)',text)
    if poe:
        percent=re.findall(r'(\d{1,3}[,.]?\d*)\s*%',poe.group(1))
        if percent:screening=float(percent[-1].replace(',','.'))/100
    return {'report_no':f'N{num}','date':ds,'publication_date':pub,'cases':cases,'deaths':deaths,'new_cases':newcases,'followup':followup,
            'contacts_total':contacts_total,'contacts_seen':contacts_seen,'screening':screening,'zones':zone_rows,'residual':residual,'prov_totals':prov_totals,'province_contacts':province_contacts,'text':text,'pdf':path.name}


def upsert_rows(path,rows,key,remove_dates=None):
    old=read_csv(path)
    fields=list(old[0]) if old else list(rows[0])
    if remove_dates:
        datekey='reporting_date' if 'reporting_date' in fields else 'date'
        old=[r for r in old if r.get(datekey) not in remove_dates]
    mapping={tuple(str(r.get(k,'')) for k in key):r for r in old}
    for r in rows:
        keyval=tuple(str(r.get(k,'')) for k in key)
        mapping[keyval]={f:str(r.get(f,'')) for f in fields}
    data=list(mapping.values())
    datekey='reporting_date' if 'reporting_date' in fields else 'date'
    data.sort(key=lambda r:(r.get(datekey,''),r.get('report_no',''),r.get('province',''),r.get('health_zone','')))
    path.parent.mkdir(exist_ok=True,parents=True)
    with path.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(data)


def apply_reports(reports):
    if not reports:raise ValueError('No reports')
    dates={r['date'] for r in reports}
    base_zone=read_csv(DATA/'cases_by_hz.csv')
    loc={}
    for r in base_zone:
        p=norm_prov(r['province']);n=norm_zone(r['health_zone'])
        if n and (r.get('zone_id') or r.get('lat')):loc[(p,n)]=(r.get('zone_id',''),r.get('lat',''),r.get('lon',''))
    # Explicitly keep geography unavailable where no approved shape/centroid exists.
    summary=[];zones=[];unvent=[];response=[];prov_contacts=[];audit=[]
    for r in sorted(reports,key=lambda r:r['date']):
        d=r['date'];no=r['report_no'];source=f'INSP SitRep {no} / MVE-BDBV (uploaded original PDF)'
        summary.append({'report_no':no,'reporting_date':d,'publication_date':r['publication_date'], 'drc_confirmed_cases':r['cases'],
            'drc_confirmed_deaths':r['deaths'],'uganda_confirmed_cases':20,'uganda_confirmed_deaths':2,'source':source,
            'notes':('Official Table 1 cumulative total used. Warning: Table 2 and Haut-Uélé provincial row differ by +1; spatial snapshot for N150 withheld.' if no=='N150' else 'Official Table 1 and Table 2 verified, including province vs health-zone checks; no interpolation.')})
        for p,z,c,dd in r['zones']:
            ident,lat,lon=loc.get((p,z),('','',''))
            zones.append({'date':d,'month':d[:7],'province':p,'health_zone':z,'zone_id':ident,
                'confirmed_cases':c,'confirmed_deaths':dd if dd is not None else '', 'lat':lat,'lon':lon,'source':source,'source_date':d,
                'notes':'Cumulative count extracted from official Table 2; no inferred geographical coordinates.'})
        for p,cc,dd in r['residual']:
            unvent.append({'date':d,'month':d[:7],'province':p,'category':'unventilated_unknown_health_zone',
                'confirmed_cases':cc,'confirmed_deaths':dd,'source':source,'source_date':d,
                'notes':'Confirmed deaths not yet assigned to health zones; official province total minus itemized health-zone deaths.'})
        response.append({'reporting_date':d,'report_no':no,'admin_level':'national','province':'','health_zone':'',
                'contacts_under_followup':r['contacts_total'] if r['contacts_total'] is not None else '',
                'contacts_seen':r['contacts_seen'] if r['contacts_seen'] is not None else '',
                'contact_followup_rate':r['followup'], 'alerts_reported':'','alerts_investigated':'','alert_investigation_rate':'',
                'samples_received':'','samples_analysed':'','positive_samples':'','travellers_total':'','travellers_screened':'',
                'poe_screening_coverage':r['screening'] if r['screening'] is not None else '', 'source':source,
                'notes':'KPI national contact rate and PoE Table 4 (if available). Missing counts left blank, not guessed.'})
        for p,(total,seen,rate) in r.get('province_contacts',{}).items():
            prov_contacts.append({'date':d,'province':p,'contacts_under_followup':total,'contacts_seen':seen,
               'followup_rate':f'{rate:.6f}','target_rate':0.95,'source':source,
               'notes':'Province follow-up extracted from SitRep narrative; fraction independently matches quoted rate.'})
        audit.append({'report_no':no,'date':d,'cases':r['cases'],'deaths':r['deaths'],'health_zones':len(r['zones']),
                      'provinces':len(r['prov_totals']),'contact_followup':r['followup'],
                      'unallocated_deaths':sum(x[2] for x in r['residual']), 'pdf':r['pdf'],'spatial_warning': 'Table 1/Table 2 discrepancy (Haut-Uélé +1); spatial snapshot omitted' if no=='N150' else ''})
    for fn,rows,keys in [
      ('report_summary.csv',summary,['report_no','reporting_date']),('cases_by_hz.csv',zones,['date','province','health_zone']),
      ('cases_unventilated.csv',unvent,['date','province','category']),
      ('response_indicators.csv',response,['reporting_date','report_no','admin_level','province','health_zone']),
      ('contact_followup_by_province.csv',prov_contacts,['date','province'])]:
        upsert_rows(DATA/fn,rows,keys,remove_dates=dates)
    # Full qualitative source from newest PDF; do not retain the stale ECDC-only warning.
    latest=max(reports,key=lambda x:x['date'])
    EXTRACTED.mkdir(parents=True,exist_ok=True)
    (EXTRACTED/f'sitrep_{latest["report_no"]}.txt').write_text(latest['text'],encoding='utf-8')
    (EXTRACTED/f'sitrep_{latest["report_no"][1:]:0>3}.txt').write_text(latest['text'],encoding='utf-8')
    (EXTRACTED/'latest_sitrep_update.json').write_text(json.dumps({
        'report_no':latest['report_no'],'reporting_date':latest['date'],'pdf':latest['pdf'],'source':'Uploaded INSP original PDF'},indent=2,ensure_ascii=False),encoding='utf-8')
    (EXTRACTED/'sitrep_archive_import_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    report_numbers={int(x['report_no'][1:]) for x in audit}
    missing=[f'N{k}' for k in range(min(report_numbers),max(report_numbers)+1) if k not in report_numbers]
    status=(f'# INSP SitRep archive import\n\n- Imported: {len(reports)} full PDFs, N{min(report_numbers)}–N{max(report_numbers)}\n'
           f'- Missing PDF numbers: {", ".join(missing) if missing else "none"}\n'
           f'- Latest: {latest["report_no"]}, {latest["date"]}, {latest["cases"]} confirmed / {latest["deaths"]} deaths\n'
           '- Official national totals verified and province/zone sums reconciled for all PDFs except N150: its Table 1 reports 9,065 while Table 2 reports 9,066 (+1 Haut-Uélé); spatial snapshot omitted on N150.\n'
           '- Unallocated confirmed deaths tracked separately; no unreported case dates interpolated.\n')
    (ROOT/'.sitrep_archive_import_status.md').write_text(status,encoding='utf-8')
    print(status)


def main():
    ap=argparse.ArgumentParser()
    grp=ap.add_mutually_exclusive_group(required=True)
    grp.add_argument('--archive',help='ZIP of SitRep PDF files')
    grp.add_argument('--pdf',help='Single SitRep PDF')
    args=ap.parse_args()
    if args.archive:
        with tempfile.TemporaryDirectory() as temp:
            with zipfile.ZipFile(args.archive) as z:
                names=[x for x in z.namelist() if x.lower().endswith('.pdf')]
                if not names:raise SystemExit('Archive contains no PDFs')
                for n in names:
                    if '..' in Path(n).parts or Path(n).is_absolute():raise ValueError('Unsafe ZIP path')
                paths=[]
                for n in names:
                    f=Path(temp)/n;f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(z.read(n));paths.append(f)
                reports=[];failure=[]
                for path in sorted(paths):
                    try:
                        report=extract_report(path);reports.append(report)
                        print(f'{report["report_no"]} {report["date"]}: {report["cases"]}/{report["deaths"]}, {len(report["zones"])} health zones')
                    except Exception as exc:
                        failure.append(f'{path.name}: {exc}')
                if failure:
                    raise SystemExit('Rejected PDFs; NO data was written:\n'+'\n'.join(failure))
                apply_reports(reports)
    else:
        report=extract_report(Path(args.pdf));apply_reports([report])

if __name__=='__main__':main()
