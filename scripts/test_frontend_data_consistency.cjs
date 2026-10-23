const fs=require('fs'), path=require('path'); const vm=require('vm'); const assert=require('assert');
const src=fs.readFileSync(path.join(__dirname,'..','assets','app.js'),'utf8');
function extract(name) {
 const i=src.indexOf('function '+name+'('); assert(i>=0,name+' missing');
 const k=src.indexOf('{',i); let depth=0, quote=null, escape=false;
 for(let j=k;j<src.length;j++) {let c=src[j];if(quote){if(escape){escape=false;continue;}if(c==='\\'){escape=true;continue;}if(c===quote)quote=null;continue;}if(c==="'"||c==='"'||c==='`'){quote=c;continue;}if(c==='{')depth++;if(c==='}'&&--depth===0){return src.slice(i,j+1);}}
 throw Error('unclosed '+name)
}
// Avoid extractor complexity by using from function until next top-level function for simple functions.
const funcs=['latestNationalSummary','healthZoneFreshness','verifiedCountryData','setInternationalKpi','formatInternationalSummary','updateInternationalSituationLines','setEpiKpis','updateAssessmentPanel'];
const els={};for(const id of ['kpiTotal','kpiKinshasa','kpiTotalMeta','kpiKinshasaShare','kpiBorder','kpiBorderShare','kpiKenya','kpiKenyaMeta','kpiNigeria','kpiNigeriaMeta','latestSituationKenya','latestSituationNigeria','latestSituationOther','assessmentUpdated','publicHealthAssessmentMeta'])els[id]={textContent:'',style:{}};
const cards={};
const data={reportSummary:[{report_no:'N116',reporting_date:'2026-09-07',drc_confirmed_cases:6757,drc_confirmed_deaths:3267},{report_no:'ECDC-2026-10-20',reporting_date:'2026-10-20',drc_confirmed_cases:9704,drc_confirmed_deaths:4687}], cases:[{date:'2026-09-07',confirmed_cases:1}], ugandaEvdSummary:[{as_of_date:'2026-08-27',cumulative_confirmed_cases:20,cumulative_deaths:2,imported_cases:15,local_cases:5}],};
data.internationalCasesData=JSON.parse(fs.readFileSync(path.join(__dirname,'..','data','international_cases.json'),'utf8'));
const code=funcs.map(extract).join('\n')+'\n'+Object.entries(data).map(([k,v])=>`let ${k} = ${JSON.stringify(v)};`).join('\n')+`\nconst currentLang='ja';\n const fmt=new Intl.NumberFormat('en-US');\n function textByLang(a,b,c){return a};\n function displayDateLabel(d){return d};\n function pct(v){return (v*100).toFixed(1)+'%'};\nfunction toNumber(v){return Number(v)||0};\nfunction daysBetweenIso(a,b){return Math.round((new Date(b)-new Date(a))/86400000)};\nfunction latestUgandaEvdSummary(){return ugandaEvdSummary[0]};\nfunction ugandaEvdSourceLabel(){return 'WHO'};\n function setAssessmentCard(id,v){cards[id]=v};\nfunction selectedCaseDate(){return '2026-09-07'};\nsetEpiKpis();\nupdateInternationalSituationLines();\nupdateAssessmentPanel();\n`;
const context=vm.createContext({document:{getElementById:id=>els[id]??null},cards,console});
try{ vm.runInContext(code,context) } catch(e) {console.error(e);process.exit(1)}
assert.equal(els.kpiTotal.textContent,'9,704'); assert.equal(els.kpiKinshasa.textContent,'4,687');
assert.equal(els.kpiBorder.textContent,'20');
assert.equal(els.kpiKenya.textContent,'9');
assert.equal(els.kpiNigeria.textContent,'1');
assert.match(els.kpiKenyaMeta.textContent,/死亡 1例/);
assert.match(els.kpiNigeriaMeta.textContent,/死亡 0例/);
assert.match(els.latestSituationKenya.textContent,/累積確定例9例/);
assert.match(els.latestSituationNigeria.textContent,/累積確定例1例/);
assert.match(els.latestSituationOther.textContent,/フランス/);
assert.equal(cards.assessmentLocal.level,'uncertain');assert.equal(cards.assessmentCapital.level,'uncertain');assert.equal(cards.assessmentCrossBorder.level,'uncertain');
assert.match(els.assessmentUpdated.textContent,/評価保留/);
console.log('PASS: national KPI independent from historical map selection; stale risk cards withheld.');
