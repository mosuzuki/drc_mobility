#!/usr/bin/env python3
"""Leakage-aware pooled regional LightGBM Ebola reported-case forecasts.

Direct forecasts for four disjoint 7-day incidence bins. Backtests train using
labels that had matured by each forecast origin; each source observation uses
only the cumulative snapshots recorded at that reporting date. Intra-gap daily
values are linearly apportioned (not actual onset dates). Probability intervals
are empirical, with limited-outbreak calibration explicitly disclosed.

Chronos-2 is an optional *real* zero-shot candidate (ENABLE_CHRONOS2=1). If
not installed / model not available it is omitted, never imitated with rules.
"""
from __future__ import annotations
import json, math, os, warnings
from datetime import timedelta, datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/short_term_ml_forecast.json'
HORIZONS = (1, 2, 3, 4)
FEATURES = ['log_recent7', 'log_prev7', 'log_recent14', 'log_recent28', 'ratio',
            'slope7', 'zero_days', 'age_days', 'log_cumulative', 'log_national7',
            'log_province7', 'level', 'horizon', 'weekday']
RNG = np.random.default_rng(20261023)


def load_inputs():
    reports = pd.read_csv(ROOT/'data/report_summary.csv')
    reports['date'] = pd.to_datetime(reports['reporting_date'])
    reports = reports.sort_values('date').drop_duplicates('date', keep='last')
    reports = reports[reports['drc_confirmed_cases'].notna()]
    last = reports.iloc[-1]
    start = reports['date'].min(); end = reports['date'].max()
    index = pd.date_range(start, end, freq='D')
    zones = pd.read_csv(ROOT/'data/cases_by_hz.csv')
    zones['date'] = pd.to_datetime(zones['date'])
    zones = zones[zones['date'].le(end)].copy()
    zones['series'] = zones['province'].fillna('unknown') + '||' + zones['health_zone'].fillna('unknown')
    zones = zones.groupby(['series','date'], as_index=False).agg(cumulative=('confirmed_cases','max'), province=('province','first'))
    by_series = {}
    for name, df in zones.groupby('series'):
        d = df.set_index('date')['cumulative'].sort_index().reindex(index).interpolate(method='time', limit_area='inside').ffill().fillna(0)
        # Historical revisions may lower cumulative counts. Retain corrections in totals,
        # but don't manufacture negative incident cases for count forecasts.
        inc = d.diff().fillna(0).clip(lower=0).to_numpy(dtype=float)
        by_series['z:'+name] = {'inc': inc, 'province':str(df['province'].iloc[0]), 'level':0}
    df_nat = reports.set_index('date')['drc_confirmed_cases'].reindex(index).interpolate(method='time',limit_area='inside').ffill().fillna(0)
    nat_inc = df_nat.diff().fillna(0).clip(lower=0).to_numpy(dtype=float)
    by_series['national'] = {'inc': nat_inc, 'province':'National', 'level':2}
    provinces = sorted({r['province'] for r in by_series.values() if r['level']==0})
    for prov in provinces:
        case = [v['inc'] for v in by_series.values() if v['level']==0 and v['province']==prov]
        if case:
            by_series['p:'+prov] = {'inc':np.sum(case,axis=0), 'province':prov, 'level':1}
    return index, by_series, last


def features(series, t, horizon, national, provinces, dates):
    inc = series['inc']
    def window(lo,hi): return float(np.sum(inc[max(0,lo):max(0,hi)]))
    r7=window(t-6,t+1); p7=window(t-13,t-6); r14=r7+p7; r28=window(t-27,t+1)
    province7 = window(t-6,t+1) if series['level']==1 else float(np.sum(provinces.get(series['province'], inc)[max(0,t-6):t+1]))
    nat7 = float(np.sum(national[max(0,t-6):t+1]))
    recent = inc[max(0,t-27):t+1]
    nonzero = np.flatnonzero(recent>0)
    zero_days = min(28, len(recent)-1-int(nonzero[-1])) if len(nonzero) else 28
    seen = np.flatnonzero(inc[:t+1]>0)
    age = t-int(seen[0]) if len(seen) else 0
    return [math.log1p(r7),math.log1p(p7),math.log1p(r14),math.log1p(r28),
            math.log1p(r7)-math.log1p(p7),math.log1p(r7)-math.log1p(r14/2),
            zero_days,age,math.log1p(float(sum(inc[:t+1]))),math.log1p(nat7),
            math.log1p(province7),series['level'],horizon,dates[t].weekday()]


def samples(dates,by_series,cutoff):
    nat=by_series['national']['inc']
    prov={k[2:]:v['inc'] for k,v in by_series.items() if k.startswith('p:')}
    xx=[];yy=[];tt=[];keys=[]
    for k,s in by_series.items():
        # Weekly non-overlapping origins limit strongly correlated targets.
        for t in range(28,cutoff-27,7):
            for h in HORIZONS:
                finish=t+7*h
                if finish>cutoff:continue
                y=sum(s['inc'][t+7*(h-1)+1:finish+1])
                xx.append(features(s,t,h,nat,prov,dates));yy.append(math.log1p(y));tt.append(t);keys.append(k)
    return np.asarray(xx),np.asarray(yy),np.asarray(tt),keys


def fit_model(x,y):
    if len(y)<180: return None
    mod=LGBMRegressor(n_estimators=115, learning_rate=.04, num_leaves=11,
             min_child_samples=30, reg_lambda=12., verbosity=-1, n_jobs=2, random_state=20261023)
    mod.fit(pd.DataFrame(x,columns=FEATURES),y)
    return mod


def forecast_model(dates,by_series,t,model):
    nat=by_series['national']['inc'];prov={k[2:]:v['inc'] for k,v in by_series.items() if k.startswith('p:')}
    x=np.asarray([features(by_series['national'],t,h,nat,prov,dates) for h in HORIZONS])
    med=np.expm1(model.predict(pd.DataFrame(x,columns=FEATURES))).clip(0,1e7)
    return med


def naive_week(inc,t,h):
    last7=float(sum(inc[max(0,t-6):t+1]))
    prev7=float(sum(inc[max(0,t-13):max(0,t-6)]))
    momentum=float(np.clip((last7+3)/(prev7+3), .65,1.35))
    return last7 * momentum**min(h,3)


def make_record(meds,errs,origin):
    # Approx log-scale calibration: empirical held-out *national* forecast errors.
    # Residual distribution reflects only available retrospective origins, not
    # calibration across independent epidemics.
    sigma = max(.22, min(1.5, float(np.std(errs,ddof=1)) if len(errs)>=4 else .55))
    rng=np.random.default_rng(20261023+int(origin.strftime('%j')))
    common=rng.normal(size=1500)
    weekly=[]; sims=[]
    for h,m in enumerate(meds,1):
        z=.65*common + math.sqrt(1-.65**2)*rng.normal(size=len(common))
        draws=np.clip((float(m)+1)*np.exp(sigma*z)-1,0,1e7)
        sims.append(draws)
        q=np.quantile(draws,[.05,.25,.5,.75,.95]); weekly.append({'week':h,'from':(origin+timedelta(days=7*(h-1)+1)).date().isoformat(),'to':(origin+timedelta(days=7*h)).date().isoformat(),
                'median':round(float(q[2]),1),'q05':round(float(q[0]),1),'q25':round(float(q[1]),1),'q75':round(float(q[3]),1),'q95':round(float(q[4]),1)})
    return {'weeks':weekly,'uncertainty_method':'log-error residual scale estimated from available rolling national origins','log_sigma':round(sigma,3)}


def chronos_candidate(national,dates,origin):
    if os.environ.get('ENABLE_CHRONOS2') != '1':
        return None, 'disabled; set ENABLE_CHRONOS2=1 with chronos-forecasting>=2.0 and cached model weights'
    try:
        from chronos import Chronos2Pipeline
        end=origin; history=pd.DataFrame({'id':'DRC','timestamp':dates[:end+1],'target':national[:end+1]})
        pipeline=Chronos2Pipeline.from_pretrained('amazon/chronos-2',device_map='cpu')
        out=pipeline.predict_df(history,prediction_length=28,quantile_levels=[.05,.25,.5,.75,.95],
            id_column='id',timestamp_column='timestamp',target='target')
        # Forecast 28 daily incident counts; aggregate per-week median only.
        middle=next((c for c in out.columns if str(c) in ('0.5','0.50')),None)
        if middle is None: raise RuntimeError('Chronos-2 output lacks median column')
        values=np.maximum(0,np.asarray(out[middle],dtype=float)[:28])
        if len(values)!=28: raise RuntimeError('Expected 28 daily outputs')
        med=[sum(values[i*7:(i+1)*7]) for i in range(4)]
        return med,None
    except Exception as e:
        return None,str(e)[:300]


def branching_week_samples(inc, n=800, seed=20261023):
    """Independent branching distribution used for the same four forecast weeks."""
    # For numerical consistency, use the shared generation interval and Rt
    # estimation functions from the final-size simulator.
    from generate_final_size_projection import generation_interval_weights, estimate_rt, DISPERSION_K
    rng=np.random.default_rng(seed)
    gi=generation_interval_weights(12,5,40)
    rt=estimate_rt(list(inc),gi,10)
    rt_draw=np.clip(rng.gamma(rt['shape'],1/max(rt['rate'],1e-10),size=n),.05,4)
    arr=np.repeat(np.asarray(inc,dtype=float)[None,:],n,axis=0)
    weeks=np.zeros((4,n))
    for h in range(1,29):
        lag=min(len(gi)-1,arr.shape[1])
        infectious=arr[:,-lag:]@gi[1:lag+1][::-1]
        # Same short-term Rt as in the browser forecast: constant across 28 d
        mean=np.maximum(0,rt_draw*infectious)
        new=rng.poisson(rng.gamma(DISPERSION_K,mean/DISPERSION_K)).astype(float)
        weeks[(h-1)//7]+=new
        arr=np.column_stack([arr,new])
    return weeks


def calibrated_draws(meds,sigma,n=800,seed=20261023):
    rng=np.random.default_rng(seed)
    common=rng.normal(size=n)
    draws=[]
    for m in meds:
        z=.65*common+np.sqrt(1-.65**2)*rng.normal(size=n)
        draws.append(np.maximum(0,(float(m)+1)*np.exp(sigma*z)-1))
    return np.asarray(draws)


def summarise_samples(draws,origin):
    weeks=[]
    for idx,arr in enumerate(draws):
        q=np.quantile(arr,[.05,.25,.5,.75,.95]);h=idx+1
        weeks.append({'week':h,'from':(origin+timedelta(days=7*(h-1)+1)).date().isoformat(),
             'to':(origin+timedelta(days=7*h)).date().isoformat(),
             'q05':round(float(q[0]),1),'q25':round(float(q[1]),1),
             'median':round(float(q[2]),1),'q75':round(float(q[3]),1),'q95':round(float(q[4]),1)})
    return {'weeks':weeks, 'uncertainty_method':'Monte Carlo weekly forecast distribution (uncalibrated on independent Ebola outbreaks)'}


def main():
    dates,series,last=load_inputs();t=len(dates)-1; origin=dates[t]
    hz=pd.read_csv(ROOT/'data/cases_by_hz.csv', usecols=['date'])
    hz_latest=pd.to_datetime(hz['date']).max()
    if hz_latest != origin:
        # Keep a marked unavailable record instead of mixing newer national
        # counts with stale regional features and publishing a fake AI forecast.
        OUT.write_text(json.dumps({'status':'withheld_stale_regional_data',
            'report_date':origin.date().isoformat(), 'health_zone_report_date':hz_latest.date().isoformat(),
            'models':{}, 'recommended_lightgbm_weight':0},indent=2)+'\n',encoding='utf8')
        print(f'Forecast withheld: national {origin.date()} / regional {hz_latest.date()}')
        return
    xx,yy,tt,keys=samples(dates,series,t)
    mod=fit_model(xx,yy)
    if mod is None:raise RuntimeError('Insufficient time/region training samples for LightGBM')
    # Rolling-origin forecasts: re-fit using only labels fully observed at each origin.
    origins=list(range(max(55,t-77),t-27,7))
    errors=[]; folds=[]
    for c in origins:
        xf,yf,tf,_=samples(dates,series,c)
        fitted=fit_model(xf,yf)
        if fitted is None:continue
        pred=forecast_model(dates,series,c,fitted)
        actual=[sum(series['national']['inc'][c+7*(h-1)+1:c+7*h+1]) for h in HORIZONS]
        baseline=[naive_week(series['national']['inc'],c,h) for h in HORIZONS]
        bp_draws=branching_week_samples(series['national']['inc'][:c+1],n=260,seed=20261023+c)
        bp_median=np.median(bp_draws,axis=1).tolist()
        for h in HORIZONS:
            if c+7*h>t:continue
            errors.append(math.log1p(actual[h-1])-math.log1p(pred[h-1]))
        folds.append({'origin':dates[c].date().isoformat(), 'actual':[round(v,1) for v in actual],
             'lightgbm':[round(float(v),1) for v in pred], 'persistence':[round(float(v),1) for v in baseline],
             'branching':[round(float(v),1) for v in bp_median]})
    med=forecast_model(dates,series,t,mod)
    ml=make_record(med,errors,origin)
    chrono,issue=chronos_candidate(series['national']['inc'],dates,t)
    models={'lightgbm':ml}
    if chrono is not None:models['chronos2']=make_record(chrono,errors,origin)
    def mae(model):
        a=[abs(o['actual'][h]-o[model][h]) for o in folds for h in range(4)]
        return round(float(np.mean(a)),2) if a else None
    metrics={'lightgbm_mae_weekly':mae('lightgbm'),'persistence_mae_weekly':mae('persistence'),
       'branching_mae_weekly':mae('branching'),'n_origins':len(folds),
      'evaluation':'Time-forward LightGBM refits and independent branching forecasts; disjoint-week national MAE on matured labels. SitRep revisions and interpolation remain limitations.'}
    enabled = len(folds)>=4 and metrics['lightgbm_mae_weekly'] is not None and metrics['branching_mae_weekly'] is not None
    # Use real branching-process backtest as the ensemble gate, not a proxy.
    # No independently demonstrated benefit means no added ML weight; the ML
    # forecast remains available as an explicitly experimental comparator.
    ml_weight = .40 if enabled and metrics['lightgbm_mae_weekly'] < metrics['branching_mae_weekly'] else 0.0
    # The user can still compare an *experimental* linked forecast. A small
    # 20% weight is permitted only if ML beats the elementary persistence
    # baseline; it is not labelled better than branching nor recommended.
    experiment_weight = ml_weight if ml_weight>0 else (.20 if enabled and metrics['lightgbm_mae_weekly'] < metrics['persistence_mae_weekly'] else 0.0)
    # Construct weekly predictive distributions rather than averaging quantiles.
    # Ensemble here is a weighted *blend of sample paths*, not a linear opinion
    # pool. It assumes independent model errors conditional on shared data.
    branch_samples=branching_week_samples(series['national']['inc'],n=800)
    ai_samples=calibrated_draws(med,ml['log_sigma'],n=800)
    ensemble=(1-experiment_weight)*branch_samples+experiment_weight*ai_samples
    models['branching']=summarise_samples(branch_samples,origin)
    models['ensemble']=summarise_samples(ensemble,origin)
    if chrono is not None:
        # Chronos has not been backtested in this job, so it remains an optional
        # comparison; it never gets a positive automatic ensemble weight.
        models['chronos2']=summarise_samples(calibrated_draws(chrono,ml['log_sigma'],n=800,seed=20261025),origin)
    payload={'updated_at':datetime.now(timezone.utc).isoformat(),'source_sitrep':str(last['report_no']),
       'report_date':origin.date().isoformat(),'method':'pooled_regional_lightgbm_direct_weekly_incidence',
       'horizons_days':[7,14,21,28],'models':models,'backtest':metrics,'folds':folds,
       'recommended_lightgbm_weight':ml_weight,'experimental_lightgbm_weight':experiment_weight,'ensemble_method':'weighted sample-path combination; model errors assumed independent','chronos2_status': 'available' if chrono is not None else 'not_available',
       'chronos2_error':issue,
       'caveats':['This model was trained using one evolving outbreak, NOT many independent epidemics.',
         'Incidence is approximated from cumulative report dates, with interpolation over missed days.',
         'Historical SitRep revisions may cause retrospective information leakage in backtests.',
         'Uncertainty intervals are exploratory and are not independently calibrated.']}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(f'LightGBM: {len(xx)} training observations; {len(folds)} validation origins; latest {origin.date()}; W={ml_weight}; experimental W={experiment_weight}; Chronos={payload["chronos2_status"]}')

if __name__=='__main__':main()
