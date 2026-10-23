#!/usr/bin/env python3
"""Fail CI rather than silently display incoherent epidemiological forecasts."""
import json, math
from pathlib import Path
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
D=ROOT/'data'
reports=pd.read_csv(D/'report_summary.csv').sort_values('reporting_date')
latest=reports.iloc[-1]
date=str(latest['reporting_date']);cum=float(latest['drc_confirmed_cases'])
hz_date=str(pd.read_csv(D/'cases_by_hz.csv',usecols=['date'])['date'].max())
short=json.loads((D/'short_term_ml_forecast.json').read_text())
final=json.loads((D/'final_size_projection.json').read_text())
assert short.get('report_date') == date
assert final['report_date'] == date
assert final['source_sitrep'] == latest['report_no']
if date != hz_date:
    assert short.get('status')=='withheld_stale_regional_data'
    assert not short.get('models')
else:
    assert 'lightgbm' in short['models'] and 'ensemble' in short['models']
    assert short['backtest']['n_origins'] >= 4
    assert 0<=short['recommended_lightgbm_weight']<=.65
    assert 0<=short['experimental_lightgbm_weight']<=.65
    if short['backtest']['lightgbm_mae_weekly'] >= short['backtest']['branching_mae_weekly']:
        assert short['recommended_lightgbm_weight']==0

    for key,model in short['models'].items():
        rows=model['weeks']
        assert len(rows)==4,(key,len(rows))
        for h,row in enumerate(rows,1):
            assert row['week']==h
            v=[row[q] for q in ('q05','q25','median','q75','q95')]
            assert all(math.isfinite(x) and x>=0 for x in v),(key,h,v)
            assert v==sorted(v),(key,h,'disordered quantiles')
    models=final['dates'][date]['models']
    assert {'branching','ai_linked','ai_improved','ai_delayed'}<=models.keys()
    assert models['ai_linked']['weights']['lightgbm']==short['experimental_lightgbm_weight']
    # The scenarios should diverge only after the identical 28-day AI phase.
    for scen in ('ai_improved','ai_delayed'):
        assert models['ai_linked']['trajectory'][20]==models[scen]['trajectory'][20]
    for key,model in models.items():
        assert model is not None,key
        f=model['final_size']
        assert f['pi90'][0]<=f['pi50'][0]<=f['median']<=f['pi50'][1]<=f['pi90'][1]
        assert f['pi90'][0]>=cum,(key,'implausible final total below already observed')
        first=cum
        for point in model['trajectory']:
            assert point['q05']>=cum,(key,'future curve below observed')
            assert point['q05']<=point['q25']<=point['median']<=point['q75']<=point['q95']
            assert point['median']>=first,(key,'declining cumulative curve')
            first=point['median']
        if model['end_date'].get('median') is None:
            assert 'not_reached_probability' in model['end_date']
print('PASS: forecast national/regional dates, model availability, 4 horizons, backtest maturity, forecast quantiles, baseline and control scenarios')
