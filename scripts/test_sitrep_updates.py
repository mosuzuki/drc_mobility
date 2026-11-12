#!/usr/bin/env python3
"""Regression fixtures are immutable source PDFs, not today's live counts."""
import json
import unittest
from pathlib import Path
from import_sitrep_archive import extract_report
from sitrep_notifications import overlay_national
from update_international_cases import parse_who_table, parse_ecdc, ecdc_reporting_date
from update_uganda_evd import apply_who_uganda_end_override
ROOT = Path(__file__).resolve().parents[1]

class SitrepRegression(unittest.TestCase):
    def test_n178_to_n180(self):
        for no,c,d,n,nd in [(178,10990,5354,70,31),(179,11042,5387,52,33),(180,11114,5433,72,46)]:
            with self.subTest(report=no):
                r=extract_report(ROOT/'raw'/f'sitrep_N{no}.pdf')
                self.assertEqual((r['cases'],r['deaths'],r['new_cases']),(c,d,n))
                self.assertEqual(r['date'],f'2026-11-{no-170:02d}')
                self.assertEqual(sum(z[2] for z in r['zones']),c)
                daily=r['notifications']
                self.assertEqual(sum(x['new_confirmed_deaths'] for x in daily if x['admin_level']=='health_zone'),nd)
    def test_ministry_backfill_date_and_staggered_kpi(self):
        r=extract_report(ROOT/'raw/sitreps/sitrep_N142.pdf')
        self.assertEqual((r['date'],r['publication_date']),('2026-10-03','2026-10-04'))
        self.assertEqual((r['cases'],r['deaths'],r['new_cases']),(8544,4114,102))
        self.assertEqual(r['followup'],0.738)
    def test_november_layouts_and_unallocated_death(self):
        expected=[(10467,5103,53),(10532,5126,65),(10603,5163,71),(10668,5198,62),(10741,5230,73),(10803,5267,62),(10879,5302,76),(10920,5323,41)]
        for no,(c,d,n) in enumerate(expected,170):
            with self.subTest(report=no):
                r=extract_report(ROOT/'raw'/f'sitrep_N{no}.pdf')
                self.assertEqual((r['cases'],r['deaths'],r['new_cases']),(c,d,n))
                self.assertEqual(sum(z[2] for z in r['zones']),c)
                self.assertEqual(sum(z[3] or 0 for z in r['zones'])+sum(z[2] for z in r['residual']),d)
                daily=r['notifications']; national=next(x for x in daily if x['admin_level']=='national')
                sub=[x for x in daily if x['admin_level'] in ('health_zone','unallocated')]
                self.assertEqual(sum(x['new_confirmed_cases'] for x in sub),n)
                self.assertEqual(sum(x['new_confirmed_deaths'] for x in sub),national['new_confirmed_deaths'])
                if no==177:
                    self.assertEqual([(x['province'],x['new_confirmed_deaths']) for x in sub if x['admin_level']=='unallocated'],[('Nord-Kivu',1)])
    def test_uploaded_ten_report_layouts(self):
        expected=[(9765,4727,71),(9816,4757,51),(9876,4803,60),(9964,4841,88),(10060,4887,96),(10141,4928,81),(10193,4960,52),(10274,4992,78),(10351,5026,77),(10414,5372,63)]
        for no, (c,d,n) in enumerate(expected,160):
            with self.subTest(report=no):
                r=extract_report(ROOT/'raw'/f'sitrep_N{no}.pdf')
                self.assertEqual((r['cases'],r['deaths'],r['new_cases']),(c,d,n))
                self.assertEqual(sum(z[2] for z in r['zones']),c)
                self.assertEqual(sum(z[3] or 0 for z in r['zones'])+sum(z[2] for z in r['residual']),d)
                self.assertEqual(sum(n['new_confirmed_cases'] for n in r['notifications'] if n['admin_level']=='health_zone'),n)
                self.assertEqual(r['date'],f'2026-10-{no-139}')
                self.assertEqual(r['publication_date'],f'2026-10-{no-138}')
    def test_reconciliation_is_not_new_incidence(self):
        rows=[{'date':'2026-10-21','incidence':61,'new_deaths':40},{'date':'2026-10-28','incidence':81,'new_deaths':32},{'date':'2026-10-30','incidence':63,'new_deaths':346}]
        overlay_national(rows)
        self.assertEqual([r['incidence'] for r in rows],[71,78,63])
        self.assertEqual(rows[-1]['new_deaths'],32)
    def test_who_dates_not_publication_or_unknown_zero(self):
        html='<table><tr><td>Country</td><td>Suspected</td><td>Confirmed cases</td><td>Confirmed deaths</td></tr><tr><td colspan="8">Data as of 29 October 2026</td></tr><tr><td>Kenya#</td><td>-</td><td>9</td><td>2</td><td>-</td><td>-</td><td>22%</td><td>-</td></tr><tr><td colspan="8">Data as of 27 October 2026</td></tr><tr><td>Nigeria</td><td>-</td><td>1</td><td>-</td><td>-</td><td>-</td><td>-</td><td>-</td></tr></table>'
        self.assertEqual(parse_who_table(html),{'kenya':(9,2,'2026-10-29'),'nigeria':(1,None,'2026-10-27')})
        html='<h3>Kenya</h3><p>As of 26 October 2026, nine cases have been reported in Kenya, including two deaths.</p>'
        self.assertEqual(parse_ecdc(html,'2026-10-30')['kenya'],(9,2))
        self.assertEqual(ecdc_reporting_date(html,'kenya','2026-10-30'),'2026-10-26')
    def test_future_uganda_report_not_overridden_as_ended(self):
        row={'as_of_date':'2026-10-31','cumulative_confirmed_cases':21,'source_url':'ministry','notes':'new report'}
        new,_=apply_who_uganda_end_override(row,[])
        self.assertEqual(new,row)
    def test_latest_summary_uses_daily_provinces(self):
        d=json.loads((ROOT/'data/latest_situation.json').read_text())
        if d['report_no']=='N169':
            self.assertIn('Ituriで34例',d['drc_summary_ja'])
            self.assertIn('遡及修正314例',d['drc_summary_ja'])
            self.assertNotIn('Ituriで225例',d['drc_summary_ja'])

if __name__=='__main__':unittest.main(verbosity=2)
