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
    if not x or str(x).strip().upper() in {'NA','AN','ND','—'}:
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
    # Keep the numeric columns AND form feeds intact for the daily table parser.
    lines=section.splitlines(keepends=True)
    for a,b,name in [('Boma','Mangbetu','Boma Mangbetu'),('Makiso-','Kisangani','Makiso-Kisangani'),('Wanie-','Rukula','Wanie-Rukula')]:
        for i,line in enumerate(lines):
            if line.strip()!=a:continue
            nearby=list(range(i+1,min(len(lines),i+6)))
            suffix=next((j for j in nearby if lines[j].strip()==b),None)
            numeric=next((j for j in nearby if re.match(r'^\s*\d[\d ]*\s+\d[\d ]*\s+\d+[,.]\d+\s*%',lines[j])),None)
            if suffix is not None and numeric is not None:
                start=re.search(r'\d',lines[numeric]).start()
                lines[numeric]=name.ljust(max(start,len(name)+2))+lines[numeric][start:]
                lines[i]='\n';lines[suffix]='\n'
    return ''.join(lines)


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
    dm=re.search(r'Date (?:de rapportage|du rapport)\s*:\s*(\d{1,2})(?:er)?\s+([A-Za-zéûôÀ-ÿ]+)\s+(2026)',text,re.I)
    if not dm:
        d2=re.search(r'_(\d{2})_(\d{2})_(2026)',path.name)
        if not d2:raise ValueError('Missing reporting date')
        ds=f'{d2.group(3)}-{d2.group(2)}-{d2.group(1)}'
    else:
        months={'janvier':'01','février':'02','mars':'03','avril':'04','mai':'05','juin':'06','juillet':'07','septembre':'09','octobre':'10','novembre':'11','décembre':'12','aout':'08','août':'08'}
        if dm.group(2).lower() not in months:raise ValueError(f'Unrecognized reporting month {dm.group(2)}')
        ds=f'{dm.group(3)}-{months[dm.group(2).lower()]}-{int(dm.group(1)):02d}'
    pm=re.search(r'Date de publication\s*:\s*(\d{1,2})(?:er)?\s*([A-Za-zéûôÀ-ÿ]+)\s+(2026)',text,re.I)
    if not pm:raise ValueError('Missing publication date in PDF')
    months={'janvier':'01','février':'02','mars':'03','avril':'04','mai':'05','juin':'06','juillet':'07','août':'08','septembre':'09','octobre':'10','novembre':'11','décembre':'12'}
    pub=f'{pm.group(3)}-{months[pm.group(2).lower()]}-{int(pm.group(1)):02d}'
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
        prev=[line for line in tlines[:totals[-1]] if re.match(r'^\s*\d[\d ]*\s{2,}[\d ]+\s{2,}[\d ]+\s{2,}\d+[,.]\d+\s*%',line)]
        if not prev:raise ValueError('Cannot find total line before Table 1 footer')
        numbers=re.split(r'\s{2,}',prev[-1].strip())
        if len(numbers)<3:raise ValueError(f'Malformed Table 1 TOTAL line: {prev[-1]!r}')
        # Coverage (e.g. 67/178) may follow CFR. The first three
        # columns remain daily cases, cumulative cases, cumulative deaths.
        cases,deaths=as_int(numbers[1]),as_int(numbers[2])
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
    newcases=as_int(numeric[0]) if len(numeric)>=3 else as_int(numbers[0])
    newmatch=re.search(r'(?:Au total,\s*|Au terme[^.]{0,120}?\s*)?(\d{1,4})\s+nouveaux\s+cas\s+confirm[ée]s',text[:3000],re.I)
    if newmatch:newcases=int(newmatch.group(1))
    if newcases is None:
        # Tableau 1 total has new notifications in the first column.
        mt=re.search(r'(?m)^\s*(\d{1,4})\s+([\d ]{2,10})\s+([\d ]{2,10})\s+\d+[,.]\d+\s*%\s*\n\s*Total',t1)
        if mt:newcases=int(mt.group(1))
    first_page=text[:text.find('\f')] if '\f' in text else text[:7000]
    # Rightmost percentage in KPI card is the contact follow-up percentage.
    km=re.search(r'CUMUL\s+DES.*?Provinces\s+touch[ée]es',first_page,re.I|re.S)
    ratios=list(re.finditer(r'(\d{1,3}[,.]\d+)\s*%',km.group(0))) if km else []
    # Contact KPI is the rightmost card, sometimes one text line ABOVE CFR.
    rightmost=max(ratios,key=lambda m:m.start()-km.group(0).rfind('\n',0,m.start())) if ratios else None
    followup=float(rightmost.group(1).replace(',','.'))/100 if len(ratios)>=2 else None
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
    # From N168, a dedicated contact table is inserted before the PoE table.
    ct=re.search(r'Tableau\s+\d+\.\s+Situation du suivi des contacts.*?(?=Tableau\s+\d+\.|\Z)',text,re.S|re.I)
    if ct:
        for line in ct.group().splitlines():
            cells=re.split(r'\s{2,}',line.strip())
            if len(cells)!=4 or not re.fullmatch(r'\d+[,.]\d+%',cells[3]):continue
            try:total,seen=as_int(cells[1]),as_int(cells[2])
            except ValueError:continue
            if total and seen is not None and 0<=seen<=total:
                rate=float(cells[3].rstrip('%').replace(',','.'))/100
                if abs(seen/total-rate)>0.002:raise ValueError('Contact-table ratio mismatch')
                if cells[0]=='Total':contacts_total,contacts_seen=total,seen
                else:province_contacts[norm_prov(cells[0].replace('Sud-Ubangi','Sud Ubangi').replace('Nord-Ubangi','Nord Ubangi'))]=(total,seen,rate)
    samples=None
    sm=re.search(r'(?:total de|sur)\s+([\d ]+)\s+(?:nouveaux\s+)?échantillons',first_page,re.I)
    if sm:samples=as_int(sm.group(1))
    # PoE global rate in table 4 (last percentage from the screening row)
    screening=None
    poe=re.search(r'(?im)^\s*%\s*des\s+voyageurs\s+screen[ée]s\s+(.+)',text)
    if poe:
        percent=re.findall(r'(\d{1,3}[,.]?\d*)\s*%',poe.group(1))
        if percent:screening=float(percent[-1].replace(',','.'))/100
    if screening is None:
        pm=re.search(r'(?m)^\s*Proportion\s+des\s+([^\n]+)\n\s*voyageurs screenés',text)
        if pm:
            rates=re.findall(r'(\d{1,3}[,.]?\d*)\s*%',pm.group(1))
            if rates:screening=float(rates[-1].replace(',','.'))/100
    notifications=extract_notifications(t2, ds, f'N{num}', newcases)
    return {'samples':samples, 'notifications':notifications, 'report_no':f'N{num}','date':ds,'publication_date':pub,'cases':cases,'deaths':deaths,'new_cases':newcases,'followup':followup,
            'contacts_total':contacts_total,'contacts_seen':contacts_seen,'screening':screening,'zones':zone_rows,'residual':residual,'prov_totals':prov_totals,'province_contacts':province_contacts,'text':text,'pdf':path.name}


def extract_notifications(section, reporting_date, report_no, expected_cases):
    """Read explicit 24h table columns; never equate cumulative corrections with incidence.

    Poppler keeps columns aligned on each page but changes spacing across pages.
    A complete province row supplies the first two daily-column coordinates.
    Blank daily case cells are accepted as zero only after all province/national
    sums reconcile against the explicitly printed daily totals.
    """
    result=[]; current=None; seen_provinces=set()
    for page in section.split('\f'):
        lines=page.splitlines(); boundary=None
        for line in lines:
            m=ROW.match(line) or ROW_TIGHT.match(line)
            if not m:continue
            tail=list(re.finditer(r'\d+',line[m.end():]))
            if len(tail)==4 and (norm_prov(m.group('name')) in PROVS or m.group('name').strip()=='Total'):
                boundary=(tail[0].end()+tail[1].start())//2
                break
        if boundary is None:
            # Historical cumulative-only layouts have no 24h columns.
            continue
        for line in lines:
            m=ROW.match(line) or ROW_TIGHT.match(line)
            if not m:continue
            name=norm_prov(m.group('name'))
            tail_numbers=re.findall(r'\d+',line[m.end():])
            # Four explicit daily cells are unambiguous even if a PDF row
            # is horizontally indented (e.g. Kyondo in N176).
            cell=tail_numbers[0] if len(tail_numbers)==4 else line[m.end():m.end()+boundary].strip()
            if cell and not cell.isdigit():raise ValueError(f'Ambiguous daily cell {name}: {cell!r}')
            daily=int(cell or 0)
            daily_deaths=int(tail_numbers[-1]) if tail_numbers else 0
            if name=='Total':
                if daily!=expected_cases:raise ValueError('Daily Table 1/Table 2 totals disagree')
                tail=re.findall(r'\d+',line[m.end():])
                result.append(dict(date=reporting_date,report_no=report_no,admin_level='national',province='',health_zone='',new_confirmed_cases=daily,new_confirmed_deaths=int(tail[-1]),source='INSP Table 2: explicit 24h notifications'))
                continue
            if name in PROVS and name not in seen_provinces:
                current=name;seen_provinces.add(name);level='province';zone=''
            else:
                if current is None:raise ValueError('Daily zone has no province')
                level='health_zone';zone=norm_zone(name)
            result.append(dict(date=reporting_date,report_no=report_no,admin_level=level,province=current,health_zone=zone,new_confirmed_cases=daily,new_confirmed_deaths=daily_deaths,source='INSP Table 2: explicit 24h notifications'))
    if result:
        national=[r for r in result if r['admin_level']=='national']
        if len(national)!=1:raise ValueError('Missing unique national daily total')
        provs=[r for r in result if r['admin_level']=='province']
        if sum(r['new_confirmed_deaths'] for r in provs)!=national[0]['new_confirmed_deaths']:
            raise ValueError('Daily province death sum mismatch')
        if sum(r['new_confirmed_cases'] for r in provs)!=expected_cases:raise ValueError('Daily province sum mismatch')
        for p in provs:
            zones=[r for r in result if r['admin_level']=='health_zone' and r['province']==p['province']]
            death_residual=p['new_confirmed_deaths']-sum(r['new_confirmed_deaths'] for r in zones)
            if death_residual:
                # Verified against N177 PDF page 3: province 9, listed zones 8.
                # Retain both levels; do not assign the missing death to a zone.
                if (report_no, p['province'], death_residual)==('N177','Nord-Kivu',1):
                    result.append(dict(date=reporting_date,report_no=report_no,admin_level='unallocated',province=p['province'],health_zone='',new_confirmed_cases=0,new_confirmed_deaths=death_residual,source='INSP N177 Table 2 p3: province 9 vs listed zones 8; daily allocation unresolved'))
                else:
                    raise ValueError(f"Daily zone death sum mismatch: {p['province']}")
            if sum(r['new_confirmed_cases'] for r in zones)!=p['new_confirmed_cases']:
                raise ValueError(f"Daily zone sum mismatch: {p['province']}")
    return result


def upsert_rows(path,rows,key,remove_dates=None):
    old=read_csv(path)
    if not old and not rows:return
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
    unique={}
    for r in reports:
        key=(r['report_no'],r['date'])
        if key in unique and r['text']!=unique[key]['text']:
            raise ValueError(f'Conflicting duplicate PDF: {key}')
        unique[key]=r
    reports=list(unique.values())
    dates={r['date'] for r in reports}
    base_zone=read_csv(DATA/'cases_by_hz.csv')
    loc={}
    for r in base_zone:
        p=norm_prov(r['province']);n=norm_zone(r['health_zone'])
        if n and (r.get('zone_id') or r.get('lat')):loc[(p,n)]=(r.get('zone_id',''),r.get('lat',''),r.get('lon',''))
    # Explicitly keep geography unavailable where no approved shape/centroid exists.
    summary=[];zones=[];unvent=[];response=[];prov_contacts=[];audit=[];notifications=[]
    for r in sorted(reports,key=lambda r:r['date']):
        notifications.extend(r.get('notifications',[]))
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
                'samples_received':'','samples_analysed':r.get('samples') or '', 'positive_samples':r['new_cases'] if r.get('samples') else '', 'travellers_total':'','travellers_screened':'',
                'poe_screening_coverage':r['screening'] if r['screening'] is not None else '', 'source':source,
                'notes':'KPI national contact rate and PoE Table 4 (if available). Missing counts left blank, not guessed.'})
        for p,(total,seen,rate) in r.get('province_contacts',{}).items():
            prov_contacts.append({'date':d,'province':p,'contacts_under_followup':total,'contacts_seen':seen,
               'followup_rate':f'{rate:.6f}','target_rate':0.95,'source':source,
               'notes':'Province follow-up extracted from SitRep narrative; fraction independently matches quoted rate.'})
        audit.append({'report_no':no,'date':d,'cases':r['cases'],'deaths':r['deaths'],'health_zones':len(r['zones']),
                      'daily_spatial_warnings':[n['source'] for n in r.get('notifications',[]) if n['admin_level']=='unallocated'],
                      'provinces':len(r['prov_totals']),'contact_followup':r['followup'],
                      'unallocated_deaths':sum(x[2] for x in r['residual']), 'pdf':r['pdf'],'spatial_warning': 'Table 1/Table 2 discrepancy (Haut-Uélé +1); spatial snapshot omitted' if no=='N150' else ''})
    # Record cumulative revision residuals separately from explicit 24h reports.
    all_summary={r['reporting_date']:r for r in read_csv(DATA/'report_summary.csv')}
    all_summary.update({r['reporting_date']:r for r in summary})
    for row in summary:
        unresolved=[n for n in notifications if n['date']==row['reporting_date'] and n['admin_level']=='unallocated']
        for n in unresolved:
            row['notes']+=f" Daily table discrepancy: {n['province']} has {n['new_confirmed_deaths']} death(s) without health-zone allocation; national/province total retained."
        daily=next((n for n in notifications if n['date']==row['reporting_date'] and n['admin_level']=='national'),None)
        earlier=[d for d in all_summary if d<row['reporting_date']]
        if daily and earlier:
            previous=all_summary[max(earlier)]
            from datetime import date
            if (date.fromisoformat(row['reporting_date'])-date.fromisoformat(previous['reporting_date'])).days==1:
                rc=int(row['drc_confirmed_cases'])-int(float(previous['drc_confirmed_cases']))-daily['new_confirmed_cases']
                rd=int(row['drc_confirmed_deaths'])-int(float(previous['drc_confirmed_deaths']))-daily['new_confirmed_deaths']
                row['notes']+=f' Explicit 24h: {daily["new_confirmed_cases"]} cases / {daily["new_confirmed_deaths"]} deaths. Cumulative reconciliation residual: cases {rc:+d}, deaths {rd:+d}; not daily incidence.'
    for fn,rows,keys in [
      ('report_summary.csv',summary,['report_no','reporting_date']),('cases_by_hz.csv',zones,['date','province','health_zone']),
      ('cases_unventilated.csv',unvent,['date','province','category']),
      ('response_indicators.csv',response,['reporting_date','report_no','admin_level','province','health_zone']),
      ('contact_followup_by_province.csv',prov_contacts,['date','province']),
      ('sitrep_daily_notifications.csv',notifications,['date','admin_level','province','health_zone'])]:
        upsert_rows(DATA/fn,rows,keys,remove_dates=dates)
    # Retain each report text for audit/backfill; a late older PDF must not
    # overwrite the pointer to the newest report.
    EXTRACTED.mkdir(parents=True,exist_ok=True)
    for r in reports:
        (EXTRACTED/f'sitrep_{r["report_no"]}.txt').write_text(r['text'],encoding='utf-8')
    # Full qualitative source from newest PDF; do not retain stale warnings.
    latest=max(reports,key=lambda x:x['date'])
    EXTRACTED.mkdir(parents=True,exist_ok=True)
    (EXTRACTED/f'sitrep_{latest["report_no"]}.txt').write_text(latest['text'],encoding='utf-8')
    pointer=EXTRACTED/'latest_sitrep_update.json'
    old_pointer=json.loads(pointer.read_text()) if pointer.exists() else {}
    if latest['date'] >= old_pointer.get('reporting_date',''):
        pointer.write_text(json.dumps({
        'report_no':latest['report_no'],'reporting_date':latest['date'],'pdf':latest['pdf'],'source':'Uploaded INSP original PDF'},indent=2,ensure_ascii=False),encoding='utf-8')
    audit_path=EXTRACTED/'sitrep_archive_import_audit.json'
    old_audit=json.loads(audit_path.read_text()) if audit_path.exists() else []
    merged={r['report_no']:r for r in old_audit}
    merged.update({r['report_no']:r for r in audit})
    audit_path.write_text(json.dumps(sorted(merged.values(),key=lambda r:r['date']),ensure_ascii=False,indent=2),encoding='utf-8')
    report_numbers={int(x['report_no'][1:]) for x in audit}
    missing=[f'N{k}' for k in range(min(report_numbers),max(report_numbers)+1) if k not in report_numbers]
    status=(f'# INSP SitRep archive import\n\n- Imported: {len(reports)} full PDFs, N{min(report_numbers)}–N{max(report_numbers)}\n'
           f'- Missing PDF numbers: {", ".join(missing) if missing else "none"}\n'
           f'- Latest: {latest["report_no"]}, {latest["date"]}, {latest["cases"]} confirmed / {latest["deaths"]} deaths\n'
           '- Official national totals verified and province/zone sums reconciled for all PDFs except N150: its Table 1 reports 9,065 while Table 2 reports 9,066 (+1 Haut-Uélé); spatial snapshot omitted on N150.\n'
           '- Unallocated confirmed deaths tracked separately; no unreported case dates interpolated.\n'
           '- N177 daily Nord-Kivu deaths: province 9 vs listed zones 8; one unresolved daily death retained without a zone. All other daily mismatches fail validation.\n')
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
