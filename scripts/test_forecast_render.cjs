#!/usr/bin/env node
// Regression: exercise both forecast render functions with their real JSON files.
// Catches truncated plot functions even when JavaScript passes syntax checks.
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const root = path.join(__dirname, '..');
const js = fs.readFileSync(path.join(root, 'assets/app.js'), 'utf8');
const short = JSON.parse(fs.readFileSync(path.join(root, 'data/short_term_ml_forecast.json')));
const final = JSON.parse(fs.readFileSync(path.join(root, 'data/final_size_projection.json')));
const date = short.report_date;
if (!final.dates?.[date]) throw new Error(`Missing final-size forecast for ${date}`);
function snippet(from, to) {
  const a = js.indexOf(from);
  const b = js.indexOf(to, a);
  if (a < 0 || b <= a) throw new Error('Cannot find forecast UI function: ' + from);
  return js.slice(a, b);
}
const elems = Object.fromEntries(['forecastHorizonSelect','forecastStats','forecastChart','finalSizeChart','finalSizeSummary','finalSizeStats','finalSizeScenarioSelect'].map(id=>[id, {value:'',textContent:'',innerHTML:''}]));
elems.forecastHorizonSelect.value = '7';
elems.finalSizeScenarioSelect.value = 'branching';
let lastPlots = {};
const context = {
  console, Date, Math, Number, String, Array, Object, Boolean,
  currentLang:'ja', fmt:new Intl.NumberFormat('en-US'),
  document: {
    getElementById:(id)=>elems[id]||null,
    querySelector:(s)=>s.includes('finalSizeBasisToggle') ? {dataset:{finalSizeBasis:'reported'}} : null
  },
  Plotly:{newPlot:(element,traces,layout)=>{
    const id=typeof element==='string'?element:element===elems.finalSizeChart?'finalSizeChart':'forecastChart';
    lastPlots[id]={traces,layout};
  }},
  textByLang:(ja,en,fr)=>ja,
  addDaysIso:(date,n)=>{const d=new Date(`${date}T00:00:00Z`);d.setUTCDate(d.getUTCDate()+n);return d.toISOString().slice(0,10)},
  dailyObservedSeriesUntil:(date)=>Array.from({length:70},(_,i)=>{
    const d=new Date(`${date}T00:00:00Z`);d.setUTCDate(d.getUTCDate()-(69-i));
    return {date:d.toISOString().slice(0,10),incidence:65+(i%6),cumulative:9704-69*65+i*65};
  }),
  healthZoneFreshness:()=>({stale:false}),
  getFinalSizeProjectionRecord:(date)=>final.dates[date],
  selectedCaseDate:()=>date,
  refreshFinalSizeScenarioOptions:()=>{},
  finalProjectionScenarioData:(record,scenario)=>record.models?.[scenario],
  updateFinalSizeBasisToggleAvailability:()=>{},
  getAdjustedMultiplierForRecord:()=>null,
  getSelectedFinalSizeBasis:()=> 'reported',
  latestObservedCumulative:()=>Number(final.dates[date].current_cumulative_cases),
  finalSizeScenarioLabel:(name)=>name,
  formatDateLong:(v)=>v||'—',
  displayDateLabel:(v)=>v,
  trueInfectionEstimateData:null
};
vm.createContext(context);
vm.runInContext(`const shortMlForecastData=${JSON.stringify(short)}; const finalSizeProjectionData=${JSON.stringify(final)};\n`+
  snippet('function updateMlForecastChart(data, model) {','\nfunction updateForecastChart()')+'\n'+
  snippet('function updateFinalSizeProjectionChart() {','\nfunction syncRightPanelHeight()'),context);
const assert = (cond,msg)=>{if(!cond)throw new Error(msg)};
for(const horizon of [7,14,21,28]) {
  elems.forecastHorizonSelect.value=String(horizon);
  for(const model of Object.keys(short.models||{})) {
    context.updateMlForecastChart(short,model);
    const plot=lastPlots.forecastChart;
    assert(plot?.traces?.length>=2,`No short-term chart for ${horizon} ${model}`);
    const med=plot.traces.find(t=>t.name==='予測中央値');
    assert(med && med.x.length===horizon/7,`Missing median at ${horizon} ${model}`);
    assert(med.error_y?.visible,`No 90% interval for ${horizon} ${model}`);
    assert(elems.forecastStats.textContent.length>10,'Missing short-term caption');
  }
}
for(const scenario of ['branching','ai_linked','ai_improved','ai_delayed']) {
  elems.finalSizeScenarioSelect.value=scenario;
  context.updateFinalSizeProjectionChart();
  const plot=lastPlots.finalSizeChart;
  assert(plot?.traces?.length>=6,`Final-size plot blank for ${scenario}`);
  assert(plot.traces.some(t=>t.y?.length>25),`Missing trajectory for ${scenario}`);
  assert(elems.finalSizeSummary.innerHTML.includes('260日後'),`Summary missing for ${scenario}`);
  assert(elems.finalSizeStats.textContent.includes('260日'),`Caption missing for ${scenario}`);
}
console.log('PASS: short-term 7/14/21/28-day forecast render with 90% PI; all four final-size scenarios plot curves, cards and captions');
