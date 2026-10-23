#!/usr/bin/env python3
"""Regression tests: conservative international snapshots, no fabricated counts."""
import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from update_international_cases import OUT, latest_ecdc_url, parse_ecdc, parse_nicd_table, merge, valid_country

class InternationalTests(unittest.TestCase):
    def test_seeded_verified_snapshot(self):
        data=json.loads(OUT.read_text(encoding='utf-8'))
        for k in ('kenya','nigeria','france'):
            self.assertTrue(valid_country(data['countries'][k]), k)
        self.assertEqual(data['countries']['kenya']['confirmed_cases'], 9)
        self.assertEqual(data['countries']['kenya']['confirmed_deaths'], 1)
        self.assertEqual(data['countries']['nigeria']['confirmed_cases'], 1)
        self.assertEqual(data['countries']['nigeria']['confirmed_deaths'], 0)

    def test_html_country_scope_and_word_numbers(self):
        html='''<main><h2>Overview</h2><h3>Nigeria</h3><p>On 20 October 2026, one case of Ebola disease was reported in Nigeria. Investigation ongoing.</p><h3>Kenya</h3><p>As of 22 October 2026, nine cases have been reported in Kenya, including one death. The last case was reported on 20 October.</p><h3>Democratic Republic of the Congo</h3><p>9 704 confirmed cases including 4 687 deaths.</p></main>'''
        parsed=parse_ecdc(html,'2026-10-22')
        self.assertEqual(parsed['kenya'], (9, 1), parsed)
        self.assertEqual(parsed['nigeria'], (1, None))
        self.assertNotIn('france', parsed)

    def test_no_unanchored_fabricated_counts(self):
        html='<main><h3>Kenya</h3><p>ongoing case investigation</p><h3>DRC</h3><p>confirmed 9704 cases including 4687 deaths</p></main>'
        self.assertNotIn('kenya',parse_ecdc(html,'2026-10-22'))

    def test_index_latest_dated_url(self):
        html='<a href="/en/public-health-events/2026-eip-00026/2026-10-21">21 October</a><a href="/en/public-health-events/2026-eip-00026/2026-10-22">22 October</a>'
        date,url=latest_ecdc_url(html)
        self.assertEqual(date,'2026-10-22')
        self.assertTrue(url.endswith('2026-10-22'))

    def test_nicd_country_table(self):
        html='''<table><tr><th>Country (report date)</th><th>Suspected cases</th><th>Confirmed cases</th><th>Confirmed deaths</th><th>Recovered</th></tr><tr><td>Kenya (20 Oct 2026)</td><td>–</td><td>9</td><td>1</td><td>–</td></tr><tr><td>Nigeria (20 Oct 2026)</td><td>–</td><td>1</td><td>–</td><td>–</td></tr></table>'''
        parsed=parse_nicd_table(html)
        self.assertEqual(parsed['kenya'],(9,1,'2026-10-20'))
        self.assertEqual(parsed['nigeria'],(1,None,'2026-10-20'))

    def test_does_not_overwrite_unknown_or_older(self):
        current={'confirmed_cases':9,'confirmed_deaths':1,'as_of_date':'2026-10-22','source':'ECDC'}
        self.assertFalse(merge(current,{'confirmed_cases':8,'confirmed_deaths':1,'as_of_date':'2026-10-20'}))
        self.assertEqual(current['confirmed_cases'],9)
        self.assertTrue(merge(current,{'confirmed_cases':10,'confirmed_deaths':None,'as_of_date':'2026-10-23'}))
        self.assertEqual(current['confirmed_deaths'],1)

if __name__=='__main__': unittest.main(verbosity=2)
