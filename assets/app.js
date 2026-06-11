const files = {
  origins: 'data/outbreak_zones.csv',
  destinations: 'data/destinations.csv',
  flows: 'data/monthly_flows.csv',
  scenarios: 'data/scenarios.csv',
  population: 'data/population_by_hz.csv',
  boundaries: 'data/health_zones.geojson',
  ugandaProfile: 'data/uganda_projection_profile.csv',
  cases: 'data/cases_by_hz.csv',
  airAdjustment: 'data/air_adjustment.csv',
  contactFollowup: 'data/contact_followup_by_province.csv',
  ugandaFmpFlows: 'data/uganda_fmp_flows_2026.csv',
  ugandaDistrictFlows: 'data/uganda_district_flows_2026.csv',
  reportSummary: 'data/report_summary.csv',
  rwi: 'data/health_zone_rwi.csv',
  response: 'data/response_indicators.csv'
};

let origins = [], destinations = [], flows = [], scenarios = [], population = [], ugandaProfile = [], cases = [], airAdjustment = [], contactFollowup = [], ugandaFmpFlows = [], ugandaDistrictFlows = [], reportSummary = [], healthZoneRwi = [], responseIndicators = [];
let healthZoneBoundaries = null;
let mapMode = 'cases';
let map, layerGroup;
let choroLegend = null;
let monthsCache = [];
let reportDatesCache = [];
let caseDisplayMode = 'cumulative';

// Performance caches: built once after loading data, then reused for all filters/layers.
// This avoids repeatedly scanning all OD rows, rebuilding case lookups, and recomputing
// boundary centroids while the user changes origin sets or months.
let destinationById = new Map();
let destinationByName = new Map();
let flowsByMonthOrigin = new Map();
let latestCasesCache = null;
let caseLookupCache = null;
let affectedOriginsCache = null;
let boundaryCentroidCache = null;
let populationLookupCache = new Map();
let rwiLookupCache = null;
let rwiPercentileCache = null;
let updateTimer = null;


const fmt = new Intl.NumberFormat('en-US');
const pct = (x) => `${(x * 100).toFixed(1)}%`;

function toNumber(x) {
  const n = Number(x);
  return Number.isFinite(n) ? n : 0;
}

function bearingDegrees(from, to) {
  const lat1 = from[0] * Math.PI / 180;
  const lat2 = to[0] * Math.PI / 180;
  const dLon = (to[1] - from[1]) * Math.PI / 180;
  const y = Math.sin(dLon) * Math.cos(lat2);
  const x = Math.cos(lat1) * Math.sin(lat2) - Math.sin(lat1) * Math.cos(lat2) * Math.cos(dLon);
  return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
}

function midpoint(a, b) {
  return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
}

function pointAlong(a, b, t) {
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
}

function bentLinePoints(from, to, bend = 0.16) {
  // Leaflet does not need a plugin here: a 3-point line gives the long-distance corridor
  // a visible arc-like bend, making direction easier to read on a country-scale map.
  const mid = midpoint(from, to);
  const dx = to[1] - from[1];
  const dy = to[0] - from[0];
  const control = [mid[0] - dx * bend, mid[1] + dy * bend];
  return [from, control, to];
}

function addArrow(from, to, color, movement, options = {}) {
  const pos = pointAlong(from, to, options.at ?? 0.72);
  const angle = bearingDegrees(from, to);
  const size = options.size || Math.max(20, Math.min(42, 20 + Math.sqrt(Math.max(movement, 1)) / 8));
  const icon = L.divIcon({
    className: 'flow-arrow-icon',
    html: `<div style="transform: rotate(${angle}deg); color:${color}; font-size:${size}px;">➤</div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2]
  });
  L.marker(pos, { icon, interactive: false }).addTo(layerGroup);
}

function addFlowLabel(latlng, html, className = 'flow-label') {
  L.marker(latlng, {
    icon: L.divIcon({
      className,
      html: `<span>${html}</span>`,
      iconSize: [180, 28],
      iconAnchor: [90, 14]
    }),
    interactive: false
  }).addTo(layerGroup);
}

function destinationColor(d) {
  if (d?.is_kinshasa === 1) return '#1f5d8c';
  if (d?.is_uganda_border === 1) return '#b54708';
  return '#475467';
}


function destById(id) {
  return destinationById.get(String(id || '')) || null;
}

function destByName(name) {
  return destinationByName.get(normalizedString(name || '')) || null;
}

function buildIndexes() {
  destinationById = new Map(destinations.map(d => [String(d.zone_id), d]));
  destinationByName = new Map(destinations.map(d => [normalizedString(d.zone_name), d]));

  flowsByMonthOrigin = new Map();
  for (const r of flows) {
    const key = `${r.month}|${String(r.origin_id)}`;
    if (!flowsByMonthOrigin.has(key)) flowsByMonthOrigin.set(key, []);
    flowsByMonthOrigin.get(key).push(r);
  }

  latestCasesCache = null;
  caseLookupCache = null;
  affectedOriginsCache = null;
  boundaryCentroidCache = null;
  populationLookupCache = new Map();
  rwiLookupCache = null;
  rwiPercentileCache = null;
}

function flowsForMonthAndOrigins(month, originIds) {
  const out = [];
  for (const id of originIds) {
    const rows = flowsByMonthOrigin.get(`${month}|${String(id)}`);
    if (rows && rows.length) out.push(...rows);
  }
  return out;
}

function requestDashboardUpdate() {
  if (updateTimer) clearTimeout(updateTimer);
  updateTimer = setTimeout(() => {
    updateTimer = null;
    updateDashboard();
  }, 70);
}


async function loadCsv(path) {
  const res = await fetch(path);
  const text = await res.text();
  return Papa.parse(text, { header: true, dynamicTyping: true, skipEmptyLines: true }).data;
}

async function loadCsvOptional(path) {
  try {
    const res = await fetch(path, { cache: 'no-store' });
    if (!res.ok) return [];
    const text = await res.text();
    return Papa.parse(text, { header: true, dynamicTyping: true, skipEmptyLines: true }).data;
  } catch (e) {
    console.warn(`Optional file not loaded: ${path}`, e);
    return [];
  }
}

async function loadGeoJsonOptional(path) {
  try {
    const res = await fetch(path, { cache: 'no-store' });
    if (!res.ok) return null;
    const json = await res.json();
    if (!json || !Array.isArray(json.features) || !json.features.length) return null;
    return json;
  } catch (e) {
    console.warn(`Optional GeoJSON not loaded: ${path}`, e);
    return null;
  }
}

function hasBoundaries() {
  return !!(healthZoneBoundaries && Array.isArray(healthZoneBoundaries.features) && healthZoneBoundaries.features.length);
}

function normalizedString(x) {
  return String(x ?? '').trim().toLowerCase().replace(/\s+/g, ' ');
}

function pickProp(props, names) {
  for (const n of names) {
    if (props && props[n] !== undefined && props[n] !== null && String(props[n]).trim() !== '') return props[n];
  }
  return '';
}

function featureZoneId(feature) {
  const p = feature.properties || {};
  return String(pickProp(p, ['zone_id', 'hz_id', 'HZ_ID', 'health_zone_id', 'healthzone_id', 'id', 'ID', 'dhis2_id', 'DHIS2_ID']));
}

function featureZoneName(feature) {
  const p = feature.properties || {};
  return String(pickProp(p, ['zone_name', 'hz_name', 'hz_name_short', 'HZ_NAME', 'health_zone', 'health_zone_name', 'name', 'NAME', 'nom', 'NOM']));
}

function featureProvince(feature) {
  const p = feature.properties || {};
  return String(pickProp(p, ['province', 'province_name', 'province_name_short', 'PROVINCE', 'prov_name', 'name_province']));
}

function getFeatureAreaKm2(feature) {
  const p = feature.properties || {};
  const fromProp = toNumber(p.area_km2 || p.AREA_KM2 || p.area_sqkm || p.Shape_Area_KM2);
  if (fromProp > 0) return fromProp;
  if (typeof turf !== 'undefined' && turf.area) return turf.area(feature) / 1e6;
  return 0;
}

function populationLookupForMonth(month) {
  if (populationLookupCache.has(month)) return populationLookupCache.get(month);
  const byId = new Map();
  const byName = new Map();
  for (const r of population) {
    if (r.month !== month) continue;
    byId.set(String(r.zone_id), r);
    byName.set(normalizedString(r.zone_name), r);
  }
  const lookup = { byId, byName };
  populationLookupCache.set(month, lookup);
  return lookup;
}

function populationRowForFeature(feature, month) {
  const lookup = populationLookupForMonth(month);
  const id = featureZoneId(feature);
  const name = featureZoneName(feature);
  return lookup.byId.get(id) || lookup.byName.get(normalizedString(name)) || null;
}

function choroplethColor(value, breaks) {
  if (!Number.isFinite(value) || value <= 0) return '#f2f4f7';
  if (value <= breaks[0]) return '#d1e9ff';
  if (value <= breaks[1]) return '#84caff';
  if (value <= breaks[2]) return '#2e90fa';
  if (value <= breaks[3]) return '#175cd3';
  return '#102a56';
}

function riskColor(value, breaks) {
  if (!Number.isFinite(value) || value <= 0) return '#fff5f5';
  if (value <= breaks[0]) return '#fee4e2';
  if (value <= breaks[1]) return '#fecdca';
  if (value <= breaks[2]) return '#f97066';
  if (value <= breaks[3]) return '#d92d20';
  return '#7a271a';
}

function addRiskLegend(breaks) {
  const labels = ['No/very low', `≤ ${fmt.format(Math.round(breaks[0]))}`, `≤ ${fmt.format(Math.round(breaks[1]))}`, `≤ ${fmt.format(Math.round(breaks[2]))}`, `≤ ${fmt.format(Math.round(breaks[3]))}`, `> ${fmt.format(Math.round(breaks[3]))}`];
  const colors = ['#fff5f5', '#fee4e2', '#fecdca', '#f97066', '#d92d20', '#7a271a'];
  if (choroLegend) { map.removeControl(choroLegend); choroLegend = null; }
  choroLegend = L.control({ position: 'bottomright' });
  choroLegend.onAdd = function() {
    const div = L.DomUtil.create('div', 'choro-legend');
    div.innerHTML = `<strong>Spread risk<br><small>estimated arrivals from outbreak zones</small></strong>` + colors.map((c, i) => `<div><i style="background:${c}"></i>${labels[i]}</div>`).join('');
    return div;
  };
  choroLegend.addTo(map);
}

function riskRowsForMonth(month) {
  const f = currentFilters();
  const originIds = selectedOriginIds();
  const rows = flowsForMonthAndOrigins(month, originIds);
  const byDest = new Map();
  for (const r of rows) byDest.set(String(r.destination_id), (byDest.get(String(r.destination_id)) || 0) + toNumber(r.movement));
  const popById = new Map(population.filter(r => r.month === month).map(r => [String(r.zone_id), r]));
  const destIds = new Set([...byDest.keys(), ...destinations.map(d => String(d.zone_id))]);
  return [...destIds].map(id => {
    const d = destById(id) || {};
    const pop = popById.get(String(id));
    const incoming = byDest.get(String(id)) || 0;
    const populationValue = pop ? toNumber(pop.population) : getZonePopulation(id, month);
    const risk = incoming;
    return {
      ...d,
      zone_id: id,
      zone_name: d.zone_name || pop?.zone_name || id,
      province: d.province || pop?.province || '',
      lat: toNumber(d.lat) || toNumber(pop?.lat),
      lon: toNumber(d.lon) || toNumber(pop?.lon),
      incoming,
      population: populationValue,
      risk,
      is_outbreak: origins.some(o => String(o.zone_id) === String(id) || normalizedString(o.zone_name) === normalizedString(d.zone_name || pop?.zone_name))
    };
  }).filter(r => r.incoming > 0 || r.population > 0 || r.zone_name);
}

function quantile(values, q) {
  const arr = values.filter(v => Number.isFinite(v) && v > 0).sort((a, b) => a - b);
  if (!arr.length) return 0;
  const pos = (arr.length - 1) * q;
  const base = Math.floor(pos);
  const rest = pos - base;
  return arr[base + 1] !== undefined ? arr[base] + rest * (arr[base + 1] - arr[base]) : arr[base];
}

function boundaryMetricRows(month, metric) {
  if (!hasBoundaries() || !hasPopulationData()) return [];
  return healthZoneBoundaries.features.map(feature => {
    const pop = populationRowForFeature(feature, month);
    const populationValue = pop ? toNumber(pop.population) : 0;
    const areaKm2 = getFeatureAreaKm2(feature);
    const density = areaKm2 > 0 ? populationValue / areaKm2 : 0;
    const id = featureZoneId(feature) || (pop ? pop.zone_id : '');
    const name = featureZoneName(feature) || (pop ? pop.zone_name : 'Unknown health zone');
    const destination = destById(id) || destByName(name) || {};
    return {
      feature, zone_id: id, zone_name: name, province: featureProvince(feature) || pop?.province || destination.province || '',
      population: populationValue, area_km2: areaKm2, density, value: metric === 'density' ? density : populationValue,
      is_outbreak: origins.some(o => String(o.zone_id) === String(id) || normalizedString(o.zone_name) === normalizedString(name)),
      category: destination.category || '', is_kinshasa: destination.is_kinshasa === 1, is_uganda_border: destination.is_uganda_border === 1
    };
  }).filter(r => r.population > 0 || r.value > 0);
}

function quantileAll(values, q) {
  const arr = values.filter(v => Number.isFinite(v)).sort((a, b) => a - b);
  if (!arr.length) return 0;
  const pos = (arr.length - 1) * q;
  const base = Math.floor(pos);
  const rest = pos - base;
  return arr[base + 1] !== undefined ? arr[base] + rest * (arr[base + 1] - arr[base]) : arr[base];
}


function metricNumber(row, key) {
  const v = toNumber(row?.[key]);
  if (!Number.isFinite(v) || v === 0 && (row?.[key] === '' || row?.[key] === null || row?.[key] === undefined)) return NaN;
  return v;
}

function rowsUpToSelected(rows, date = selectedCaseDate()) {
  return rows.filter(r => String(r.reporting_date || '') && String(r.reporting_date) <= String(date))
    .sort((a, b) => String(a.reporting_date).localeCompare(String(b.reporting_date)));
}

function latestResponseRow(metric, opts = {}) {
  const date = opts.date || selectedCaseDate();
  const province = opts.province || '';
  const admin = opts.admin_level || '';
  let rows = responseIndicators.filter(r => String(r.reporting_date || '') <= String(date));
  if (admin) rows = rows.filter(r => String(r.admin_level || '') === admin);
  if (province) rows = rows.filter(r => normalizedString(r.province || '') === normalizedString(province));
  rows = rows.filter(r => Number.isFinite(metricNumber(r, metric)));
  rows.sort((a, b) => String(a.reporting_date).localeCompare(String(b.reporting_date)));
  return rows[rows.length - 1] || null;
}

function responseMetricValue(metric, province = '') {
  if (metric === 'contact_gap') {
    const r = latestResponseRow('contact_followup_rate', { province, admin_level: province ? 'province' : '' }) || latestResponseRow('contact_followup_rate', { province: '' });
    const rate = metricNumber(r, 'contact_followup_rate');
    return Number.isFinite(rate) ? Math.max(0, 0.95 - rate) : NaN;
  }
  if (metric === 'response_intensity') {
    const contact = responseMetricValue('contact_followup_rate', province);
    const alert = responseMetricValue('alert_investigation_rate', province);
    const poe = responseMetricValue('poe_screening_coverage', province);
    const vals = [contact, alert, poe].filter(Number.isFinite).map(v => Math.max(0, Math.min(1, v)));
    return vals.length ? vals.reduce((a,b)=>a+b,0) / vals.length : NaN;
  }
  const row = latestResponseRow(metric, { province, admin_level: province ? 'province' : '' }) || latestResponseRow(metric, { province: '' });
  return metricNumber(row, metric);
}

function responseMetricLabel(metric) {
  return {
    contact_followup_rate: 'Contact follow-up rate',
    contact_gap: 'Contact tracing gap',
    alert_investigation_rate: 'Alert investigation coverage',
    poe_screening_coverage: 'PoE/PoC screening coverage',
    samples_analysed: 'Samples analysed',
    travellers_screened: 'Travellers screened',
    response_intensity: 'Response intensity score'
  }[metric] || metric;
}

function responseMetricFormat(metric, value) {
  if (!Number.isFinite(value)) return 'No data';
  if (['contact_followup_rate','contact_gap','alert_investigation_rate','poe_screening_coverage','response_intensity'].includes(metric)) return pct(value);
  return fmt.format(Math.round(value));
}

function responseColor(metric, value) {
  if (!Number.isFinite(value)) return '#f2f4f7';
  const v = Math.max(0, Math.min(1, value));
  if (metric === 'contact_gap') {
    if (v < 0.10) return '#166534';
    if (v < 0.25) return '#65a30d';
    if (v < 0.45) return '#facc15';
    if (v < 0.65) return '#ea580c';
    return '#7f1d1d';
  }
  if (v < 0.25) return '#7f1d1d';
  if (v < 0.50) return '#ea580c';
  if (v < 0.70) return '#facc15';
  if (v < 0.90) return '#65a30d';
  return '#166534';
}

function addResponseLegend(metric) {
  if (choroLegend) { map.removeControl(choroLegend); choroLegend = null; }
  const labels = metric === 'contact_gap' ? ['<10%', '10–25%', '25–45%', '45–65%', '>65%'] : ['<25%', '25–50%', '50–70%', '70–90%', '>90%'];
  const colors = metric === 'contact_gap' ? ['#166534','#65a30d','#facc15','#ea580c','#7f1d1d'] : ['#7f1d1d','#ea580c','#facc15','#65a30d','#166534'];
  choroLegend = L.control({ position: 'bottomright' });
  choroLegend.onAdd = function() {
    const div = L.DomUtil.create('div', 'choro-legend response-legend');
    div.innerHTML = `<strong>${responseMetricLabel(metric)}<br><small>${metric === 'contact_gap' ? 'Higher gap = weaker follow-up' : 'Higher = stronger reported coverage'}</small></strong>` + colors.map((c, i) => `<div><i style="background:${c}"></i>${labels[i]}</div>`).join('');
    return div;
  };
  choroLegend.addTo(map);
}

function responseLayerMetricForMode(mode = mapMode) {
  if (mode === 'response_contact_gap') return 'contact_gap';
  if (mode === 'response_alert') return 'alert_investigation_rate';
  if (mode === 'response_poe') return 'poe_screening_coverage';
  if (mode === 'response_intensity') return 'response_intensity';
  return 'response_intensity';
}

function updateResponseMap() {
  if (choroLegend) { map.removeControl(choroLegend); choroLegend = null; }
  layerGroup.clearLayers();
  const metric = responseLayerMetricForMode();
  const notice = document.getElementById('populationNotice');
  document.getElementById('mapTitle').textContent = responseMetricLabel(metric);
  document.getElementById('mapDescription').textContent = 'Response indicators are extracted from SitRep response sections. Where health-zone values are unavailable, the latest province or national value available by the selected reporting date is shown.';
  document.getElementById('rankingTitle').textContent = responseMetricLabel(metric);
  document.getElementById('rankingDescription').textContent = 'Latest available response indicator by province or national summary at the selected SitRep date.';
  notice.style.display = 'block';
  notice.className = 'population-notice';
  notice.innerHTML = `Response layer: <strong>${responseMetricLabel(metric)}</strong> for ${displayDateLabel(selectedCaseDate())}. Values may reflect province-level or national/operational summaries depending on SitRep availability; absence of data should not be interpreted as absence of activity.`;
  if (hasBoundaries()) {
    L.geoJSON(healthZoneBoundaries, {
      style: feature => {
        const province = featureProvince(feature) || '';
        const v = responseMetricValue(metric, province);
        const isOutbreak = origins.some(o => String(o.zone_id) === String(featureZoneId(feature)) || normalizedString(o.zone_name) === normalizedString(featureZoneName(feature)));
        return { color: isOutbreak ? '#1d2939' : '#ffffff', weight: isOutbreak ? 2.2 : 0.5, fillColor: responseColor(metric, v), fillOpacity: Number.isFinite(v) ? 0.70 : 0.10, opacity: 1 };
      },
      onEachFeature: (feature, layer) => {
        const province = featureProvince(feature) || '';
        const v = responseMetricValue(metric, province);
        const src = latestResponseRow(metric === 'contact_gap' ? 'contact_followup_rate' : (metric === 'response_intensity' ? 'contact_followup_rate' : metric), { province, admin_level: province ? 'province' : '' }) || latestResponseRow(metric === 'contact_gap' ? 'contact_followup_rate' : metric, { province: '' });
        layer.bindPopup(`<strong>${featureZoneName(feature) || 'Health zone'}</strong><br>${province}<br>${responseMetricLabel(metric)}: ${responseMetricFormat(metric, v)}<br>Latest source date: ${src?.reporting_date || '—'}<br><small>${src?.notes || 'Response data from SitRep summaries.'}</small>`);
      }
    }).addTo(layerGroup);
    addResponseLegend(metric);
  }
}

function responseTimelineRows(metric) {
  if (metric === 'contact_gap') {
    return responseTimelineRows('contact_followup_rate').map(r => ({ ...r, value: Math.max(0, 0.95 - r.value), label: 'Contact tracing gap' }));
  }
  const rows = responseIndicators
    .filter(r => String(r.admin_level || 'national') === 'national' && Number.isFinite(metricNumber(r, metric)))
    .map(r => ({ date: String(r.reporting_date), value: metricNumber(r, metric), report_no: r.report_no || '', label: responseMetricLabel(metric), source: r.source || 'SitRep' }));
  // If no national value is available, aggregate province values by simple mean for rates and sum for counts.
  if (!rows.length) {
    const byDate = new Map();
    for (const r of responseIndicators.filter(r => Number.isFinite(metricNumber(r, metric)))) {
      const d = String(r.reporting_date);
      if (!byDate.has(d)) byDate.set(d, []);
      byDate.get(d).push(metricNumber(r, metric));
    }
    for (const [date, vals] of byDate.entries()) {
      const isRate = ['contact_followup_rate','alert_investigation_rate','poe_screening_coverage'].includes(metric);
      rows.push({ date, value: isRate ? vals.reduce((a,b)=>a+b,0)/vals.length : vals.reduce((a,b)=>a+b,0), report_no:'', label: responseMetricLabel(metric), source:'SitRep province summaries' });
    }
  }
  return rows.sort((a,b)=>a.date.localeCompare(b.date));
}

function updateResponseTimelineChart() {
  const el = document.getElementById('responseTimelineChart');
  if (!el) return;
  const metric = document.getElementById('responseMetricSelect')?.value || 'contact_followup_rate';
  const rows = responseTimelineRows(metric);
  const selected = selectedCaseDate();
  const isRate = ['contact_followup_rate','contact_gap','alert_investigation_rate','poe_screening_coverage'].includes(metric);
  const yTitle = isRate ? 'Percent' : 'Count';
  const yVals = rows.map(r => isRate ? r.value * 100 : r.value);
  const selectedRow = rows.filter(r => r.date <= selected).slice(-1)[0];
  const traces = [{
    type: 'scatter', mode: 'lines+markers', name: responseMetricLabel(metric),
    x: rows.map(r => r.date), y: yVals,
    customdata: rows.map(r => [r.report_no, r.source]),
    hovertemplate: isRate ? '%{x}<br>%{y:.1f}%<br>%{customdata[0]}<extra></extra>' : '%{x}<br>%{y:,.0f}<br>%{customdata[0]}<extra></extra>',
    line: { width: 2.4 }, marker: { size: 7 }
  }];
  if (selectedRow) {
    traces.push({ type:'scatter', mode:'markers+text', name:'Selected / latest available', x:[selectedRow.date], y:[isRate ? selectedRow.value*100 : selectedRow.value], text:[selectedRow.report_no || 'selected'], textposition:'top center', marker:{ size:12, symbol:'diamond' }, hoverinfo:'skip' });
  }
  Plotly.newPlot('responseTimelineChart', traces, {
    margin: { l: 52, r: 18, t: 18, b: 58 },
    xaxis: { title: 'Reporting date', gridcolor: '#e7eef7' },
    yaxis: { title: yTitle, gridcolor: '#e7eef7', rangemode: 'tozero' },
    shapes: selected ? [{ type:'line', x0:selected, x1:selected, y0:0, y1:1, xref:'x', yref:'paper', line:{ color:'#667085', width:1.5, dash:'dot' } }] : [],
    legend: { orientation:'h', y:-0.32 },
    paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)'
  }, { responsive:true, displayModeBar:false });
  const stat = document.getElementById('responseStats');
  if (stat) {
    stat.innerHTML = selectedRow ? `${responseMetricLabel(metric)} latest available by selected date: <strong>${responseMetricFormat(metric, selectedRow.value)}</strong> (${displayDateLabel(selectedRow.date)}${selectedRow.report_no ? ', ' + selectedRow.report_no : ''}). Response data are heterogeneous across SitReps and should be interpreted as operational indicators, not direct measures of intervention effectiveness.` : 'No response data available for this metric.';
  }
}


function rwiMetric(row) {
  if (!row) return NaN;
  const med = toNumber(row.rwi_median);
  if (Number.isFinite(med) && med !== 0) return med;
  return toNumber(row.rwi_mean);
}

function rwiPercentileLookup() {
  if (rwiPercentileCache) return rwiPercentileCache;
  const rows = healthZoneRwi
    .map((r, idx) => ({ r, idx, value: rwiMetric(r), id: String(r.zone_id || ''), name: String(r.zone_name || '') }))
    .filter(d => Number.isFinite(d.value) && toNumber(d.r.n_rwi_points) > 0)
    .sort((a, b) => a.value - b.value);
  const byId = new Map();
  const byName = new Map();
  const byIndex = new Map();
  const n = rows.length;
  let i = 0;
  while (i < n) {
    let j = i;
    while (j + 1 < n && rows[j + 1].value === rows[i].value) j++;
    const pct = n > 1 ? ((i + j) / 2) / (n - 1) * 100 : 50;
    for (let k = i; k <= j; k++) {
      const d = rows[k];
      byIndex.set(d.idx, pct);
      if (d.id) byId.set(d.id, pct);
      if (d.name) byName.set(normalizedString(d.name), pct);
    }
    i = j + 1;
  }
  rwiPercentileCache = { byId, byName, byIndex, count: n };
  return rwiPercentileCache;
}

function rwiPercentileForRow(row, idx = null) {
  const lookup = rwiPercentileLookup();
  const id = String(row?.zone_id || '');
  const name = String(row?.zone_name || '');
  if (id && lookup.byId.has(id)) return lookup.byId.get(id);
  if (name && lookup.byName.has(normalizedString(name))) return lookup.byName.get(normalizedString(name));
  if (idx !== null && lookup.byIndex.has(idx)) return lookup.byIndex.get(idx);
  return NaN;
}

function rwiPercentileForFeature(feature) {
  const lookup = rwiPercentileLookup();
  const id = String(featureZoneId(feature) || '');
  const name = featureZoneName(feature) || '';
  return (id && lookup.byId.has(id)) ? lookup.byId.get(id) : lookup.byName.get(normalizedString(name));
}

function rwiQuintile(pct) {
  if (!Number.isFinite(pct)) return 'No data';
  if (pct < 20) return 'Q1 lowest relative wealth';
  if (pct < 40) return 'Q2';
  if (pct < 60) return 'Q3';
  if (pct < 80) return 'Q4';
  return 'Q5 highest relative wealth';
}

function rwiLookup() {
  if (rwiLookupCache) return rwiLookupCache;
  const byId = new Map();
  const byName = new Map();
  for (const r of healthZoneRwi) {
    const row = {
      ...r,
      zone_id: String(r.zone_id || ''),
      zone_name: String(r.zone_name || ''),
      province: String(r.province || ''),
      rwi_mean: toNumber(r.rwi_mean),
      rwi_median: toNumber(r.rwi_median),
      rwi_min: toNumber(r.rwi_min),
      rwi_max: toNumber(r.rwi_max),
      rwi_p25: toNumber(r.rwi_p25),
      rwi_p75: toNumber(r.rwi_p75),
      rwi_error_mean: toNumber(r.rwi_error_mean),
      n_rwi_points: toNumber(r.n_rwi_points),
      lat: toNumber(r.lat),
      lon: toNumber(r.lon),
      area_km2: toNumber(r.area_km2)
    };
    if (row.zone_id) byId.set(row.zone_id, row);
    if (row.zone_name) byName.set(normalizedString(row.zone_name), row);
  }
  rwiLookupCache = { byId, byName };
  return rwiLookupCache;
}

function rwiRowForFeature(feature) {
  const lookup = rwiLookup();
  const id = featureZoneId(feature);
  const name = featureZoneName(feature);
  return lookup.byId.get(String(id || '')) || lookup.byName.get(normalizedString(name || '')) || null;
}


function rwiRowsForChart() {
  const f = currentFilters();
  const caseById = new Map();
  const caseByName = new Map();
  for (const c of caseRowsLatest()) {
    if (c.zone_id) caseById.set(String(c.zone_id), c);
    if (c.health_zone) caseByName.set(normalizedString(c.health_zone), c);
  }
  const popLookup = populationLookupForMonth(f.month);
  return healthZoneRwi.map((r, idx) => {
    const id = String(r.zone_id || '');
    const name = String(r.zone_name || '');
    const c = caseById.get(id) || caseByName.get(normalizedString(name)) || { confirmed_cases: 0, confirmed_deaths: 0 };
    const pop = popLookup.byId.get(id) || popLookup.byName.get(normalizedString(name)) || {};
    const populationValue = toNumber(pop.population);
    const casesValue = toNumber(c.confirmed_cases);
    const originalRwi = rwiMetric(r);
    const percentile = rwiPercentileForRow(r, idx);
    return {
      zone_id: id,
      zone_name: name,
      province: String(r.province || pop.province || c.province || ''),
      rwi: originalRwi,
      rwi_percentile: percentile,
      rwi_quintile: rwiQuintile(percentile),
      rwi_mean: toNumber(r.rwi_mean),
      rwi_median: toNumber(r.rwi_median),
      rwi_p25: toNumber(r.rwi_p25),
      rwi_p75: toNumber(r.rwi_p75),
      n_rwi_points: toNumber(r.n_rwi_points),
      population: populationValue,
      cases: casesValue,
      deaths: toNumber(c.confirmed_deaths),
      cases_per100k: populationValue > 0 ? casesValue / populationValue * 100000 : 0,
      lat: toNumber(r.lat),
      lon: toNumber(r.lon)
    };
  }).filter(r => Number.isFinite(r.rwi_percentile) && r.n_rwi_points > 0);
}

function rwiValue(row) {
  if (!row) return NaN;
  return rwiPercentileForRow(row);
}

function rwiColor(value, breaks) {
  if (!Number.isFinite(value)) return '#f2f4f7';
  if (value <= breaks[0]) return '#7f1d1d';
  if (value <= breaks[1]) return '#ea580c';
  if (value <= breaks[2]) return '#facc15';
  if (value <= breaks[3]) return '#65a30d';
  return '#166534';
}

function addRwiLegend(breaks) {
  const labels = [`≤ ${breaks[0].toFixed(2)}`, `≤ ${breaks[1].toFixed(2)}`, `≤ ${breaks[2].toFixed(2)}`, `≤ ${breaks[3].toFixed(2)}`, `> ${breaks[3].toFixed(2)}`];
  const colors = ['#7f1d1d', '#ea580c', '#facc15', '#65a30d', '#166534'];
  if (choroLegend) { map.removeControl(choroLegend); choroLegend = null; }
  choroLegend = L.control({ position: 'bottomright' });
  choroLegend.onAdd = function() {
    const div = L.DomUtil.create('div', 'choro-legend');
    div.innerHTML = `<strong>Relative wealth percentile<br><small>0 = lowest, 100 = highest within DRC</small></strong>` + colors.map((c, i) => `<div><i style="background:${c}"></i>${labels[i]}</div>`).join('');
    return div;
  };
  choroLegend.addTo(map);
}

function updateRwiMap() {
  if (choroLegend) { map.removeControl(choroLegend); choroLegend = null; }
  layerGroup.clearLayers();
  const notice = document.getElementById('populationNotice');
  document.getElementById('mapTitle').textContent = 'Relative wealth percentile by health zone';
  document.getElementById('mapDescription').textContent = 'Health-zone polygons are colored by Relative Wealth Index percentile within DRC, derived from median RWI values aggregated to each health zone. Higher percentiles indicate relatively wealthier areas within DRC.';
  document.getElementById('rankingTitle').textContent = 'Relative wealth percentile ranking';
  document.getElementById('rankingDescription').textContent = 'Top health zones by Relative Wealth Index percentile within DRC.';
  notice.style.display = 'block';
  notice.className = 'population-notice';
  notice.innerHTML = "Relative Wealth Index percentiles are derived from standardized RWI values. They show a health zone's relative position within DRC, not income, poverty rate, or individual-level socioeconomic status.";
  const breaks = [20, 40, 60, 80];
  if (hasBoundaries()) {
    L.geoJSON(healthZoneBoundaries, {
      style: feature => {
        const r = rwiRowForFeature(feature);
        const v = rwiValue(r);
        const isOutbreak = origins.some(o => String(o.zone_id) === String(featureZoneId(feature)) || normalizedString(o.zone_name) === normalizedString(featureZoneName(feature)));
        return { color: isOutbreak ? '#7a271a' : '#ffffff', weight: isOutbreak ? 2.5 : 0.6, fillColor: rwiColor(v, breaks), fillOpacity: Number.isFinite(v) ? 0.74 : 0.12, opacity: 1 };
      },
      onEachFeature: (feature, layer) => {
        const r = rwiRowForFeature(feature) || {};
        const v = rwiValue(r);
        layer.bindPopup(`<strong>${r.zone_name || featureZoneName(feature) || 'Health zone'}</strong><br>${r.province || featureProvince(feature) || ''}<br>RWI percentile: ${Number.isFinite(v) ? v.toFixed(0) : 'No data'}<br>${rwiQuintile(v)}<br>Original median RWI: ${Number.isFinite(rwiMetric(r)) ? rwiMetric(r).toFixed(3) : '—'}<br>Original RWI IQR: ${Number.isFinite(toNumber(r.rwi_p25)) ? toNumber(r.rwi_p25).toFixed(3) : '—'} to ${Number.isFinite(toNumber(r.rwi_p75)) ? toNumber(r.rwi_p75).toFixed(3) : '—'}<br>RWI grid points: ${fmt.format(Math.round(toNumber(r.n_rwi_points)))}`);
      }
    }).addTo(layerGroup);
    addRwiLegend(breaks);
  }
}

function rank(values) {
  const arr = values.map((v, i) => ({ v, i })).sort((a, b) => a.v - b.v);
  const ranks = new Array(values.length);
  let i = 0;
  while (i < arr.length) {
    let j = i;
    while (j + 1 < arr.length && arr[j + 1].v === arr[i].v) j++;
    const avg = (i + j + 2) / 2;
    for (let k = i; k <= j; k++) ranks[arr[k].i] = avg;
    i = j + 1;
  }
  return ranks;
}

function pearson(xs, ys) {
  const n = Math.min(xs.length, ys.length);
  if (n < 3) return NaN;
  const mx = xs.reduce((a,b)=>a+b,0)/n;
  const my = ys.reduce((a,b)=>a+b,0)/n;
  let num=0, dx=0, dy=0;
  for (let i=0;i<n;i++) { const x=xs[i]-mx; const y=ys[i]-my; num += x*y; dx += x*x; dy += y*y; }
  return dx > 0 && dy > 0 ? num / Math.sqrt(dx*dy) : NaN;
}

function spearman(xs, ys) {
  if (xs.length < 3) return NaN;
  return pearson(rank(xs), rank(ys));
}


function updateRwiScatterChart() {
  const el = document.getElementById('rwiScatterChart');
  if (!el) return;
  const outcome = document.getElementById('rwiOutcomeSelect')?.value || 'per100k';
  const display = document.getElementById('rwiDisplaySelect')?.value || 'affected';
  const scale = document.getElementById('rwiScaleSelect')?.value || 'linear';
  const allRows = rwiRowsForChart().filter(r => r.population > 0 || outcome === 'cases' || outcome === 'recent_cases');
  let yKey = 'cases_per100k';
  let yTitle = caseDisplayMode === 'recent' ? 'Recent increase per 100,000 population' : 'Confirmed cases per 100,000 population';
  if (outcome === 'cases') { yKey = 'cases'; yTitle = caseDisplayMode === 'recent' ? 'Recent increase in confirmed cases' : 'Confirmed cases'; }
  if (outcome === 'per100k') { yKey = 'cases_per100k'; }
  let analysisRows = allRows.slice();
  if (display === 'affected') analysisRows = allRows.filter(r => r.cases > 0);
  if (display === 'top25') analysisRows = allRows.filter(r => r.cases > 0).sort((a,b)=>toNumber(b[yKey])-toNumber(a[yKey])).slice(0, 25);
  const transformY = v => scale === 'log1p' ? Math.log1p(Math.max(0, toNumber(v))) : toNumber(v);
  const plotYTitle = scale === 'log1p' ? `log1p(${yTitle})` : yTitle;
  const zeroRows = display === 'all' ? allRows.filter(r => r.cases <= 0) : [];
  const affectedRows = analysisRows.filter(r => r.cases > 0);
  const traces = [];
  if (zeroRows.length) {
    traces.push({
      type: 'scatter', mode: 'markers', name: 'No reported cases',
      x: zeroRows.map(r => r.rwi_percentile), y: zeroRows.map(r => transformY(r[yKey])),
      customdata: zeroRows.map(r => [r.zone_name, r.province, r.cases, r.cases_per100k, r.population, r.rwi, r.rwi_percentile, r.rwi_quintile, r.n_rwi_points]),
      marker: { size: 5, color: '#98a2b3', opacity: 0.22, line: { width: 0 } },
      hovertemplate: '<b>%{customdata[0]}</b><br>%{customdata[1]}<br>RWI percentile: %{customdata[6]:.0f}<br>%{customdata[7]}<br>Original median RWI: %{customdata[5]:.3f}<br>Cases: %{customdata[2]:,.0f}<br>Cases per 100,000: %{customdata[3]:.2f}<br>Population: %{customdata[4]:,.0f}<extra></extra>'
    });
  }
  const provinces = [...new Set(affectedRows.map(r => r.province || 'Unknown'))].sort();
  const maxPop = Math.max(...affectedRows.map(r => toNumber(r.population)), 1);
  for (const prov of provinces) {
    const rr = affectedRows.filter(r => (r.province || 'Unknown') === prov);
    traces.push({
      type: 'scatter', mode: 'markers', name: prov,
      x: rr.map(r => r.rwi_percentile), y: rr.map(r => transformY(r[yKey])),
      customdata: rr.map(r => [r.zone_name, r.province, r.cases, r.cases_per100k, r.population, r.rwi, r.rwi_percentile, r.rwi_quintile, r.n_rwi_points, r.deaths]),
      marker: { size: rr.map(r => 8 + 24 * Math.sqrt(Math.max(toNumber(r.population), 0) / maxPop)), opacity: 0.76, line: { width: 1.4, color: '#344054' } },
      hovertemplate: '<b>%{customdata[0]}</b><br>%{customdata[1]}<br>RWI percentile: %{customdata[6]:.0f}<br>%{customdata[7]}<br>Original median RWI: %{customdata[5]:.3f}<br>Cases: %{customdata[2]:,.0f}<br>Deaths: %{customdata[9]:,.0f}<br>Cases per 100,000: %{customdata[3]:.2f}<br>Population: %{customdata[4]:,.0f}<br>RWI points: %{customdata[8]:,.0f}<extra></extra>'
    });
  }
  const corrRows = analysisRows.filter(r => Number.isFinite(r.rwi_percentile) && Number.isFinite(transformY(r[yKey])));
  const xs = corrRows.map(r => r.rwi_percentile);
  const ys = corrRows.map(r => transformY(r[yKey]));
  const rho = spearman(xs, ys);
  const r = pearson(xs, ys);
  const selected = displayDateLabel(selectedCaseDate());
  const displayLabel = display === 'all' ? 'all health zones' : (display === 'top25' ? 'top 25 affected health zones' : 'affected health zones only');
  Plotly.newPlot('rwiScatterChart', traces, {
    margin: { l: 74, r: 18, t: 14, b: 78 },
    xaxis: { title: 'Relative wealth percentile within DRC', range: [-2, 102], ticksuffix: '', gridcolor: '#eef3f8', zeroline: false },
    yaxis: { title: plotYTitle, gridcolor: '#e7eef7', rangemode: 'tozero' },
    legend: { orientation: 'h', y: -0.34 },
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(0,0,0,0)',
    annotations: analysisRows.length ? [] : [{ text:'No health zones match the selected display mode', x:0.5, y:0.5, xref:'paper', yref:'paper', showarrow:false }]
  }, { responsive: true, displayModeBar: false });
  const stat = document.getElementById('rwiStats');
  if (stat) stat.innerHTML = `Selected reporting date: <strong>${selected}</strong>; display: <strong>${displayLabel}</strong>; outcome: <strong>${plotYTitle}</strong>. Points used for correlation: <strong>${fmt.format(corrRows.length)}</strong>. Spearman ρ: <strong>${Number.isFinite(rho) ? rho.toFixed(2) : '—'}</strong>; Pearson r: <strong>${Number.isFinite(r) ? r.toFixed(2) : '—'}</strong>. Zero-case zones are hidden by default; in all-zone mode they are shown as small, transparent grey points. This is exploratory and ecological, not causal.`;
}

function addBoundaryLegend(metric, breaks) {
  const title = metric === 'density' ? 'Population density (people/km²)' : 'Population';
  const labels = ['No data', `≤ ${fmt.format(Math.round(breaks[0]))}`, `≤ ${fmt.format(Math.round(breaks[1]))}`, `≤ ${fmt.format(Math.round(breaks[2]))}`, `≤ ${fmt.format(Math.round(breaks[3]))}`, `> ${fmt.format(Math.round(breaks[3]))}`];
  const colors = ['#f2f4f7', '#d1e9ff', '#84caff', '#2e90fa', '#175cd3', '#102a56'];
  if (choroLegend) { map.removeControl(choroLegend); choroLegend = null; }
  choroLegend = L.control({ position: 'bottomright' });
  choroLegend.onAdd = function() {
    const div = L.DomUtil.create('div', 'choro-legend');
    div.innerHTML = `<strong>${title}</strong>` + colors.map((c, i) => `<div><i style="background:${c}"></i>${labels[i]}</div>`).join('');
    return div;
  };
  choroLegend.addTo(map);
}

function initMap() {
  map = L.map('map', { zoomControl: true }).setView([0.7, 29.6], 6);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 18,
    attribution: '&copy; OpenStreetMap contributors'
  }).addTo(map);
  layerGroup = L.layerGroup().addTo(map);
}


function populateReportDateControls() {
  reportDatesCache = availableCaseDates();
  const select = document.getElementById('reportDateSelect');
  const slider = document.getElementById('reportDateSlider');
  if (!select || !slider || !reportDatesCache.length) return;
  select.innerHTML = reportDatesCache.map(d => {
    const meta = reportSummaryForDate(d);
    const label = `${displayDateLabel(d)}${meta?.report_no ? ' (' + meta.report_no + ')' : ''}`;
    return `<option value="${d}">${label}</option>`;
  }).join('');
  const latestIdx = reportDatesCache.length - 1;
  select.value = reportDatesCache[latestIdx];
  slider.min = 0;
  slider.max = latestIdx;
  slider.value = latestIdx;
  const start = document.getElementById('reportDateStartLabel');
  const end = document.getElementById('reportDateEndLabel');
  if (start) start.textContent = displayDateLabel(reportDatesCache[0]);
  if (end) end.textContent = displayDateLabel(reportDatesCache[latestIdx]);
  const datalist = document.getElementById('reportDateTicks');
  if (datalist) datalist.innerHTML = reportDatesCache.map((d, i) => `<option value="${i}" label="${d.slice(5)}"></option>`).join('');
  const syncLabels = () => {
    const d = selectedCaseDate();
    const meta = reportSummaryForDate(d);
    const lab = document.getElementById('reportDateSliderLabel');
    const modeLab = document.getElementById('caseModeLabel');
    if (lab) lab.textContent = `${displayDateLabel(d)}${meta?.report_no ? ' / ' + meta.report_no : ''}`;
    if (modeLab) modeLab.textContent = caseDisplayMode === 'recent' ? `Recent increase since ${displayDateLabel(comparisonCaseDate(d))}` : 'Cumulative cases';
  };
  syncLabels();
  slider.addEventListener('input', () => {
    const d = reportDatesCache[Number(slider.value)];
    if (d) select.value = d;
    invalidateCaseCaches();
    syncLabels();
    refreshCustomOriginOptions();
    requestDashboardUpdate();
  });
  select.addEventListener('change', () => {
    const idx = reportDatesCache.indexOf(select.value);
    if (idx >= 0) slider.value = idx;
    invalidateCaseCaches();
    syncLabels();
    refreshCustomOriginOptions();
    requestDashboardUpdate();
  });
  document.getElementById('modeCumulativeCases')?.addEventListener('click', () => {
    caseDisplayMode = 'cumulative';
    document.getElementById('modeCumulativeCases')?.classList.add('active');
    document.getElementById('modeRecentIncrease')?.classList.remove('active');
    invalidateCaseCaches();
    syncLabels();
    refreshCustomOriginOptions();
    requestDashboardUpdate();
  });
  document.getElementById('modeRecentIncrease')?.addEventListener('click', () => {
    caseDisplayMode = 'recent';
    document.getElementById('modeRecentIncrease')?.classList.add('active');
    document.getElementById('modeCumulativeCases')?.classList.remove('active');
    invalidateCaseCaches();
    syncLabels();
    refreshCustomOriginOptions();
    requestDashboardUpdate();
  });
}

function refreshCustomOriginOptions() {
  const customOriginSelect = document.getElementById('customOriginSelect');
  if (!customOriginSelect) return;
  const prev = new Set(Array.from(customOriginSelect.selectedOptions || []).map(o => String(o.value)));
  const rows = affectedOriginRows().sort((a, b) => a.province.localeCompare(b.province) || a.zone_name.localeCompare(b.zone_name));
  customOriginSelect.innerHTML = rows.map(r => `<option value="${r.zone_id}">${r.zone_name} (${r.province}; ${fmt.format(Math.round(r.confirmed_cases))} ${caseDisplayMode === 'recent' ? 'new' : 'cases'})</option>`).join('');
  const majors = new Set(majorOriginIds().map(String));
  for (const opt of customOriginSelect.options) {
    if (prev.size ? prev.has(String(opt.value)) : majors.has(String(opt.value))) opt.selected = true;
  }
}

function populateControls() {
  const originSelect = document.getElementById('originSelect');
  originSelect.innerHTML = `
    <option value="major">Major outbreak zones only</option>
    <option value="all_affected">All affected health zones</option>
    <option value="ituri">Ituri only</option>
    <option value="north_kivu">North Kivu only</option>
    <option value="south_kivu">South Kivu only</option>
    <option value="custom">Custom selection</option>`;
  originSelect.value = 'all_affected';

  refreshCustomOriginOptions();

  monthsCache = [...new Set(flows.map(d => d.month))].sort();
  const monthSelect = document.getElementById('monthSelect');
  monthSelect.innerHTML = monthsCache.map(m => `<option value="${m}">${m}</option>`).join('');
  monthSelect.value = monthsCache[monthsCache.length - 1];

  populateReportDateControls();

  const monthSlider = document.getElementById('monthSlider');
  if (monthSlider) {
    monthSlider.min = 0;
    monthSlider.max = monthsCache.length - 1;
    monthSlider.value = monthsCache.length - 1;
    document.getElementById('monthSliderLabel').textContent = monthsCache[monthsCache.length - 1];
    document.getElementById('monthStartLabel').textContent = monthsCache[0] || '—';
    document.getElementById('monthEndLabel').textContent = monthsCache[monthsCache.length - 1] || '—';

    const tickStep = Math.max(1, Math.floor(monthsCache.length / 8));
    document.getElementById('monthTicks').innerHTML = monthsCache
      .map((m, i) => (i % tickStep === 0 || i === monthsCache.length - 1) ? `<option value="${i}" label="${m}"></option>` : '')
      .join('');
  }

  document.getElementById('scenarioSelect').innerHTML = scenarios.map(s => `<option value="${s.scenario_id}">${s.scenario_name}</option>`).join('');
  document.getElementById('scenarioSelect').value = 'medium';

  for (const id of ['originSelect', 'monthSelect', 'scenarioSelect']) {
    document.getElementById(id).addEventListener('change', () => { toggleCustomOriginControl(); requestDashboardUpdate(); });
  }
  document.getElementById('topN').addEventListener('input', requestDashboardUpdate);
  document.getElementById('customOriginSelect')?.addEventListener('change', requestDashboardUpdate);
  toggleCustomOriginControl();

  document.getElementById('modeMovement').addEventListener('click', () => setMapMode('movement'));
  document.getElementById('modeCases').addEventListener('click', () => setMapMode('cases'));
  document.getElementById('modeRisk').addEventListener('click', () => setMapMode('risk'));
  document.getElementById('modeWeighted').addEventListener('click', () => setMapMode('weighted'));
  document.getElementById('modeContact')?.addEventListener('click', () => setMapMode('contact'));
  document.getElementById('modeAir').addEventListener('click', () => setMapMode('air'));
  document.getElementById('modeUganda').addEventListener('click', () => setMapMode('uganda'));
  document.getElementById('modeUgandaBorder')?.addEventListener('click', () => setMapMode('uganda_border'));
  document.getElementById('modeUgandaImport')?.addEventListener('click', () => setMapMode('uganda_import'));
  document.getElementById('modePopulation').addEventListener('click', () => setMapMode('population'));
  document.getElementById('modeDensity').addEventListener('click', () => setMapMode('density'));
  document.getElementById('modeRwi')?.addEventListener('click', () => setMapMode('rwi'));
  document.getElementById('modeContactGap')?.addEventListener('click', () => setMapMode('response_contact_gap'));
  document.getElementById('modeResponseIntensity')?.addEventListener('click', () => setMapMode('response_intensity'));
  document.getElementById('responseMetricSelect')?.addEventListener('change', () => updateResponseTimelineChart());
  document.getElementById('rwiOutcomeSelect')?.addEventListener('change', () => { updateRwiScatterChart(); });
  document.getElementById('rwiDisplaySelect')?.addEventListener('change', () => { updateRwiScatterChart(); });
  document.getElementById('rwiScaleSelect')?.addEventListener('change', () => { updateRwiScatterChart(); });
  document.getElementById('forecastHorizonSelect')?.addEventListener('change', () => { updateForecastChart(); });
  document.getElementById('forecastSiSelect')?.addEventListener('change', () => { updateForecastChart(); });

  if (monthSlider) {
    monthSlider.addEventListener('input', () => {
      const m = monthsCache[Number(monthSlider.value)];
      if (m) monthSelect.value = m;
      requestDashboardUpdate();
    });
  }
  monthSelect.addEventListener('change', () => {
    if (monthSlider) {
      const idx = monthsCache.indexOf(monthSelect.value);
      if (idx >= 0) monthSlider.value = idx;
    }
    requestDashboardUpdate();
  });

  document.getElementById('fitMap').addEventListener('click', () => fitMapToData());
}


function toggleCustomOriginControl() {
  const container = document.getElementById('customOriginContainer');
  const select = document.getElementById('originSelect');
  if (container && select) container.style.display = select.value === 'custom' ? 'block' : 'none';
}

function setEpiKpis() {
  const kpiTotal = document.getElementById('kpiTotal');
  const kpiKinshasa = document.getElementById('kpiKinshasa');
  const kpiKinshasaShare = document.getElementById('kpiKinshasaShare');
  const kpiBorder = document.getElementById('kpiBorder');
  const kpiBorderShare = document.getElementById('kpiBorderShare');
  const kpiUganda = document.getElementById('kpiUganda');
  const kpiScenario = document.getElementById('kpiScenario');
  if (!kpiTotal) return;
  const d = selectedCaseDate();
  const meta = reportSummaryForDate(d);
  const rows = caseRowsLatest();
  const mappedCases = rows.reduce((a,b)=>a+toNumber(b.confirmed_cases),0);
  const mappedDeaths = rows.reduce((a,b)=>a+toNumber(b.confirmed_deaths),0);
  if (caseDisplayMode === 'recent') {
    kpiTotal.textContent = fmt.format(Math.round(mappedCases));
    kpiKinshasa.textContent = fmt.format(Math.round(mappedDeaths));
    kpiKinshasaShare.textContent = `Mappable health-zone increase; ${displayDateLabel(comparisonCaseDate(d))} to ${displayDateLabel(d)}`;
  } else {
    kpiTotal.textContent = fmt.format(toNumber(meta?.drc_confirmed_cases) || Math.round(mappedCases));
    kpiKinshasa.textContent = fmt.format(toNumber(meta?.drc_confirmed_deaths) || Math.round(mappedDeaths));
    const cfr = (toNumber(meta?.drc_confirmed_deaths) || mappedDeaths) / Math.max((toNumber(meta?.drc_confirmed_cases) || mappedCases), 1);
    kpiKinshasaShare.textContent = `${pct(cfr)} CFR among confirmed; ${meta?.report_no || ''}`;
  }
  kpiBorder.textContent = fmt.format(toNumber(meta?.uganda_confirmed_cases) || 7);
  kpiBorderShare.textContent = 'Uganda confirmed cases; latest available IOM DTM EVD snapshot';
  kpiUganda.textContent = fmt.format(toNumber(meta?.uganda_confirmed_deaths) || 1);
  kpiScenario.textContent = `Uganda confirmed death; DRC data ${caseDisplayLabel()}`;
}


function setMapMode(mode) {
  mapMode = mode;
  const ids = ['movement','cases','risk','weighted','contact','air','uganda','uganda_border','uganda_import','population','density','rwi','response_contact_gap','response_intensity'];
  ids.forEach(m => {
    const modeIdMap = { uganda:'Uganda', uganda_border:'UgandaBorder', uganda_import:'UgandaImport', air:'Air', contact:'Contact', rwi:'Rwi', response_contact_gap:'ContactGap', response_intensity:'ResponseIntensity' };
    const el = document.getElementById('mode' + (modeIdMap[m] || (m.charAt(0).toUpperCase() + m.slice(1))));
    if (el) el.classList.toggle('active', mode === m);
  });
  const labels = { movement:'Movement', cases:'Cases', risk:'Spread risk', weighted:'Weighted risk', contact:'Contact-adjusted risk', air:'Air-adjusted risk', uganda:'Uganda projection', uganda_border:'Uganda border flow', uganda_import:'Uganda importation pressure', population:'Population', density:'Density', rwi:'Relative wealth percentile', response_contact_gap:'Contact gap', response_intensity:'Response intensity' };
  const activeLayerLabel = document.getElementById('activeLayerLabel');
  if (activeLayerLabel) activeLayerLabel.textContent = labels[mode] || 'Movement';
  updateDashboard();
}

function selectedPopulationRows() {
  const f = currentFilters();
  return population.filter(r => r.month === f.month);
}

function getZonePopulation(zoneId, month) {
  const lookup = populationLookupForMonth(month);
  const row = lookup.byId.get(String(zoneId));
  return row ? toNumber(row.population) : 0;
}

function hasPopulationData() {
  return population.some(r => toNumber(r.population) > 0);
}

function enrichPopulationRows(rows) {
  return rows.map(r => {
    const d = destById(r.zone_id) || {};
    return {
      ...d,
      ...r,
      lat: toNumber(r.lat) || toNumber(d.lat),
      lon: toNumber(r.lon) || toNumber(d.lon),
      population: toNumber(r.population)
    };
  }).filter(r => r.zone_id && Number.isFinite(r.lat) && Number.isFinite(r.lon));
}

function resolveCaseOrigin(row) {
  if (!row) return null;
  const zoneName = String(row.health_zone || row.zone_name || '');
  const province = String(row.province || '');
  const matched = destById(row.zone_id) || destByName(zoneName) || {};
  const zoneId = String(row.zone_id || matched.zone_id || '');
  if (!zoneId) return null;
  return {
    zone_id: zoneId,
    zone_name: zoneName || matched.zone_name || zoneId,
    province: province || matched.province || '',
    confirmed_cases: toNumber(row.confirmed_cases),
    confirmed_deaths: toNumber(row.confirmed_deaths),
    lat: toNumber(row.lat) || toNumber(matched.lat),
    lon: toNumber(row.lon) || toNumber(matched.lon)
  };
}

function affectedOriginRows() {
  if (affectedOriginsCache) return affectedOriginsCache;
  const seen = new Set();
  affectedOriginsCache = caseRowsLatest()
    .filter(r => toNumber(r.confirmed_cases) > 0)
    .map(resolveCaseOrigin)
    .filter(Boolean)
    .filter(r => {
      if (seen.has(r.zone_id)) return false;
      seen.add(r.zone_id);
      return true;
    });
  return affectedOriginsCache;
}

function majorOriginIds() {
  const majorNames = new Set(['bunia', 'rwampara', 'mongbwalu', 'nyankunde']);
  const ids = affectedOriginRows().filter(r => majorNames.has(normalizedString(r.zone_name))).map(r => r.zone_id);
  if (ids.length) return ids;
  return origins.map(o => String(o.zone_id));
}

function selectedOriginIds() {
  const f = currentFilters();
  const affected = affectedOriginRows();
  if (f.originSet === 'major') return new Set(majorOriginIds().map(String));
  if (f.originSet === 'all_affected') return new Set(affected.map(r => String(r.zone_id)));
  if (f.originSet === 'ituri') return new Set(affected.filter(r => normalizedString(r.province) === 'ituri').map(r => String(r.zone_id)));
  if (f.originSet === 'north_kivu') return new Set(affected.filter(r => ['nord-kivu','north kivu','north-kivu'].includes(normalizedString(r.province))).map(r => String(r.zone_id)));
  if (f.originSet === 'south_kivu') return new Set(affected.filter(r => ['sud-kivu','south kivu','south-kivu'].includes(normalizedString(r.province))).map(r => String(r.zone_id)));
  if (f.originSet === 'custom') {
    const selected = Array.from(document.getElementById('customOriginSelect')?.selectedOptions || []).map(o => String(o.value));
    return new Set(selected.length ? selected : majorOriginIds().map(String));
  }
  return new Set(majorOriginIds().map(String));
}

function originSetLabel() {
  const f = currentFilters();
  const labels = {
    major: 'Major outbreak zones only',
    all_affected: 'All affected health zones',
    ituri: 'Ituri only',
    north_kivu: 'North Kivu only',
    south_kivu: 'South Kivu only',
    custom: 'Custom selection'
  };
  return labels[f.originSet] || 'Major outbreak zones only';
}

function originFilterText() {
  const ids = selectedOriginIds();
  const affected = affectedOriginRows();
  const names = affected.filter(r => ids.has(String(r.zone_id))).map(r => r.zone_name).filter(Boolean);
  if (names.length <= 5) return names.join(', ') || originSetLabel();
  return `${originSetLabel()} (${names.length} origins)`;
}

function contactFollowupRowForProvince(province) {
  const key = normalizedString(province).replace('north kivu', 'nord-kivu').replace('south kivu', 'sud-kivu');
  const selected = selectedCaseDate();
  const rows = contactFollowup
    .filter(r => normalizedString(r.province).replace('north kivu', 'nord-kivu').replace('south kivu', 'sud-kivu') === key)
    .sort((a,b)=>String(a.date || '').localeCompare(String(b.date || '')));
  if (!rows.length) return null;
  return rows.filter(r => String(r.date || '') <= String(selected)).slice(-1)[0] || rows[0];
}

function contactGapMultiplierForProvince(province) {
  const row = contactFollowupRowForProvince(province);
  if (!row) return 1;
  const follow = toNumber(row.followup_rate);
  const target = toNumber(row.target_rate) || 0.95;
  return 1 + Math.max(0, target - follow);
}

function currentFilters() {
  return {
    originSet: document.getElementById('originSelect').value,
    month: document.getElementById('monthSelect').value,
    scenario: scenarios.find(s => s.scenario_id === document.getElementById('scenarioSelect').value),
    topN: Number(document.getElementById('topN').value)
  };
}

function selectedFlows() {
  const f = currentFilters();
  return flowsForMonthAndOrigins(f.month, selectedOriginIds());
}

function groupByDestination(rows) {
  const out = new Map();
  for (const r of rows) out.set(r.destination_id, (out.get(r.destination_id) || 0) + Number(r.movement || 0));
  return [...out.entries()].map(([destination_id, movement]) => {
    const d = destById(destination_id);
    return { ...d, movement };
  }).sort((a, b) => b.movement - a.movement);
}

function groupByMonth(origin) {
  const months = [...new Set(flows.map(d => d.month))].sort();
  return months.map(month => {
    const originIds = selectedOriginIds();
    const rows = flowsForMonthAndOrigins(month, originIds);
    let kinshasa = 0, border = 0, total = 0;
    for (const r of rows) {
      const d = destById(r.destination_id);
      const m = Number(r.movement || 0);
      total += m;
      if (d?.is_kinshasa === 1) kinshasa += m;
      if (d?.is_uganda_border === 1) border += m;
    }
    return { month, total, kinshasa, border };
  });
}

function aggregateCategoryRows(rows, category) {
  const byOrigin = new Map();
  for (const r of rows) {
    const d = destById(r.destination_id);
    if (!d || d.category !== category) continue;
    const o = origins.find(x => x.zone_id === r.origin_id);
    if (!o) continue;
    const movement = toNumber(r.movement);
    if (!byOrigin.has(o.zone_id)) {
      byOrigin.set(o.zone_id, { origin: o, movement: 0, weightedLat: 0, weightedLon: 0, n: 0 });
    }
    const item = byOrigin.get(o.zone_id);
    item.movement += movement;
    item.weightedLat += toNumber(d.lat) * movement;
    item.weightedLon += toNumber(d.lon) * movement;
    item.n += 1;
  }
  return [...byOrigin.values()].filter(x => x.movement > 0).map(x => ({
    ...x,
    targetLat: x.weightedLat / x.movement,
    targetLon: x.weightedLon / x.movement
  }));
}

function drawStrategicCorridors(rows) {
  const kinColor = '#155eef';
  const borderColor = '#dc6803';
  const kinHub = { zone_name: 'Kinshasa', lat: -4.325, lon: 15.322, province: 'Kinshasa' };
  const kinRows = aggregateCategoryRows(rows, 'kinshasa');
  const borderRows = aggregateCategoryRows(rows, 'uganda_border');
  const maxStrategic = Math.max(
    ...kinRows.map(x => x.movement),
    ...borderRows.map(x => x.movement),
    1
  );

  // Kinshasa is a long-distance corridor. Aggregate all Kinshasa health zones into one hub
  // so small flows do not disappear from the top-N local ranking.
  kinRows.forEach(x => {
    const from = [toNumber(x.origin.lat), toNumber(x.origin.lon)];
    const to = [kinHub.lat, kinHub.lon];
    const points = bentLinePoints(from, to, 0.12);
    const weight = 3 + 10 * Math.sqrt(x.movement / maxStrategic);
    L.polyline(points, { color: kinColor, weight, opacity: 0.78, dashArray: '12 8' })
      .bindPopup(`${x.origin.zone_name} → Kinshasa<br>${fmt.format(Math.round(x.movement))} estimated movements`)
      .addTo(layerGroup);
    addArrow(from, to, kinColor, x.movement, { at: 0.78 });
    addFlowLabel(pointAlong(from, to, 0.55), `Kinshasa ${fmt.format(Math.round(x.movement))}`, 'flow-label kinshasa-label');
  });

  // Uganda-border proxy corridors are shown as aggregated movement pressure toward the
  // weighted centroid of Uganda-border health-zone destinations.
  borderRows.forEach(x => {
    const from = [toNumber(x.origin.lat), toNumber(x.origin.lon)];
    const to = [x.targetLat, x.targetLon];
    const points = bentLinePoints(from, to, 0.10);
    const weight = 3 + 10 * Math.sqrt(x.movement / maxStrategic);
    L.polyline(points, { color: borderColor, weight, opacity: 0.84 })
      .bindPopup(`${x.origin.zone_name} → Uganda-border proxy zones<br>${fmt.format(Math.round(x.movement))} estimated movements`)
      .addTo(layerGroup);
    addArrow(from, to, borderColor, x.movement, { at: 0.72 });
    addFlowLabel(pointAlong(from, to, 0.62), `Uganda-border ${fmt.format(Math.round(x.movement))}`, 'flow-label border-label');
  });

  // Destination hubs, always shown even when their component health zones are not in top-N.
  const kinTotal = kinRows.reduce((a, b) => a + b.movement, 0);
  if (kinTotal > 0) {
    L.circleMarker([kinHub.lat, kinHub.lon], {
      radius: 14, color: '#0b4a6f', weight: 3, fillColor: kinColor, fillOpacity: 0.80
    }).bindPopup(`<strong>Kinshasa hub</strong><br>All Kinshasa health zones<br>${fmt.format(Math.round(kinTotal))} estimated movements`).addTo(layerGroup);
    addFlowLabel([kinHub.lat + 0.55, kinHub.lon], 'Kinshasa', 'hub-label kinshasa-label');
  }

  const borderTotal = borderRows.reduce((a, b) => a + b.movement, 0);
  if (borderTotal > 0) {
    const lat = borderRows.reduce((a, b) => a + b.targetLat * b.movement, 0) / borderTotal;
    const lon = borderRows.reduce((a, b) => a + b.targetLon * b.movement, 0) / borderTotal;
    L.circleMarker([lat, lon], {
      radius: 14, color: '#93370d', weight: 3, fillColor: borderColor, fillOpacity: 0.82
    }).bindPopup(`<strong>Uganda-border proxy hub</strong><br>Weighted centroid of Uganda-border destination health zones<br>${fmt.format(Math.round(borderTotal))} estimated movements`).addTo(layerGroup);
    addFlowLabel([lat + 0.18, lon + 0.12], 'Uganda-border proxy', 'hub-label border-label');
  }
}


function ugandaProfileRows() {
  const rows = ugandaProfile
    .map(r => ({
      uganda_id: String(r.uganda_id || ''),
      uganda_name: String(r.uganda_name || ''),
      type: String(r.type || ''),
      district: String(r.district || ''),
      lat: toNumber(r.lat),
      lon: toNumber(r.lon),
      weight: toNumber(r.weight),
      source_basis: String(r.source_basis || '')
    }))
    .filter(r => r.uganda_name && r.weight > 0 && Number.isFinite(r.lat) && Number.isFinite(r.lon));
  const totalWeight = rows.reduce((a, b) => a + b.weight, 0) || 1;
  return rows.map(r => ({ ...r, weight: r.weight / totalWeight }));
}

function borderPressureForMonth(month, origin = null) {
  const rows = flows.filter(r => r.month === month && (!origin || origin === 'ALL' || r.origin_id === origin));
  let border = 0;
  let weightedLat = 0, weightedLon = 0;
  for (const r of rows) {
    const d = destById(r.destination_id);
    if (!d || !(d.is_uganda_border === 1 || d.category === 'uganda_border')) continue;
    const m = toNumber(r.movement);
    border += m;
    weightedLat += toNumber(d.lat) * m;
    weightedLon += toNumber(d.lon) * m;
  }
  const hub = border > 0 ? { lat: weightedLat / border, lon: weightedLon / border } : { lat: 0.55, lon: 30.15 };
  return { border, hub };
}

function ugandaProjectionRows(month = null) {
  const f = currentFilters();
  const m = month || f.month;
  const scenarioFraction = Number(f.scenario?.cross_border_fraction || 0);
  const { border, hub } = borderPressureForMonth(m, f.origin);
  const totalProjected = border * scenarioFraction;
  return ugandaProfileRows().map(r => ({
    ...r,
    month: m,
    border_pressure: border,
    scenario_fraction: scenarioFraction,
    projected: totalProjected * r.weight,
    hubLat: hub.lat,
    hubLon: hub.lon
  })).sort((a, b) => b.projected - a.projected);
}

function addUgandaProjectionLegend() {
  if (choroLegend) { map.removeControl(choroLegend); choroLegend = null; }
  choroLegend = L.control({ position: 'bottomright' });
  choroLegend.onAdd = function() {
    const div = L.DomUtil.create('div', 'choro-legend');
    div.innerHTML = `<strong>Uganda projection<br><small>scenario-based, not observed</small></strong>` +
      `<div><i style="background:#dc6803"></i>DRC border proxy hub</div>` +
      `<div><i style="background:#7c3aed"></i>Projected Uganda-side destination</div>` +
      `<div><i style="background:#155eef"></i>Kampala share from historical FMP profile</div>`;
    return div;
  };
  choroLegend.addTo(map);
}

function updateUgandaProjectionMap() {
  if (choroLegend) { map.removeControl(choroLegend); choroLegend = null; }
  layerGroup.clearLayers();
  const f = currentFilters();
  const rows = ugandaProjectionRows(f.month);
  const notice = document.getElementById('populationNotice');
  const selectedOrigins = affectedOriginRows().filter(o => selectedOriginIds().has(String(o.zone_id)));
  const { border, hub } = borderPressureForMonth(f.month, f.origin);
  const scenarioFraction = Number(f.scenario?.cross_border_fraction || 0);
  const totalProjected = border * scenarioFraction;

  document.getElementById('mapTitle').textContent = 'Projected Uganda-side movement pressure';
  document.getElementById('mapDescription').textContent = 'Scenario-based projection of potential Uganda-side destinations. It combines DRC-side movement toward Uganda-border proxy health zones with historical IOM DTM Uganda–DRC border FMP destination profiles from Jan–Mar 2020.';
  document.getElementById('rankingTitle').textContent = 'Uganda-side projected destination ranking';
  document.getElementById('rankingDescription').textContent = 'Projected Uganda-side destinations. These are not observed 2026 cross-border movements.';
  notice.style.display = 'block';
  notice.className = 'uganda-warning';
  notice.innerHTML = `Uganda projection is a <strong>scenario-based estimate</strong>: Flowminder DRC border-proxy movement (${fmt.format(Math.round(border))}) × selected crossing fraction (${pct(scenarioFraction)}) × historical IOM DTM Jan–Mar 2020 destination profile. It is not observed cross-border movement and not a transmission probability.`;

  // DRC outbreak origins and corridors toward the DRC border proxy hub.
  selectedOrigins.forEach(o => {
    const from = [toNumber(o.lat), toNumber(o.lon)];
    if (!Number.isFinite(from[0]) || !Number.isFinite(from[1])) return;
    L.circleMarker(from, { radius: 18, color: '#7a271a', weight: 2, fillColor: '#f04438', fillOpacity: 0.18 }).addTo(layerGroup);
    L.circleMarker(from, { radius: 8, color: '#7a271a', weight: 2, fillColor: '#d92d20', fillOpacity: 0.95 })
      .bindPopup(`<strong>${o.zone_name}</strong><br>${o.province}<br>Current outbreak health zone`).addTo(layerGroup);
    L.marker(from, { icon: L.divIcon({ className: 'origin-label', html: `<span>${o.zone_name}</span>`, iconSize: [100, 22], iconAnchor: [-8, 28] }), interactive: false }).addTo(layerGroup);
  });

  if (border > 0) {
    L.circleMarker([hub.lat, hub.lon], { radius: 17, color: '#9a3412', weight: 3, fillColor: '#dc6803', fillOpacity: 0.85 })
      .bindPopup(`<strong>DRC Uganda-border proxy hub</strong><br>Movement toward border-proxy health zones: ${fmt.format(Math.round(border))}<br>Scenario-estimated onward Uganda movement: ${fmt.format(Math.round(totalProjected))}`).addTo(layerGroup);
    addFlowLabel([hub.lat + 0.15, hub.lon + 0.08], 'DRC border proxy', 'hub-label border-label');
  }

  // Draw projected onward corridors within Uganda.
  const maxProjected = Math.max(...rows.map(r => toNumber(r.projected)), 1);
  rows.forEach(r => {
    if (r.projected <= 0) return;
    const from = [r.hubLat, r.hubLon];
    const to = [r.lat, r.lon];
    const color = r.uganda_id === 'UGA_KAMPALA' ? '#155eef' : '#7c3aed';
    const points = bentLinePoints(from, to, 0.10);
    const weight = 2 + 10 * Math.sqrt(r.projected / maxProjected);
    L.polyline(points, { color, weight, opacity: 0.78, dashArray: '9 7' })
      .bindPopup(`<strong>Projected Uganda-side movement</strong><br>DRC border proxy → ${r.uganda_name}<br>${fmt.format(Math.round(r.projected))} projected movements<br>Allocation weight: ${pct(r.weight)}<br><em>Scenario-based estimate, not observed movement</em>`)
      .addTo(layerGroup);
    addArrow(from, to, color, r.projected, { at: 0.70, size: 24 });
  });

  // Destination circles.
  rows.forEach(r => {
    if (r.projected <= 0) return;
    const color = r.uganda_id === 'UGA_KAMPALA' ? '#155eef' : '#7c3aed';
    const radius = 6 + 23 * Math.sqrt(r.projected / maxProjected);
    L.circleMarker([r.lat, r.lon], { radius, color: '#3b0764', weight: 2, fillColor: color, fillOpacity: 0.62 })
      .bindPopup(`<strong>${r.uganda_name}</strong><br>${r.type}; ${r.district}<br>Projected movements: ${fmt.format(Math.round(r.projected))}<br>Share of projection: ${pct(r.weight)}<br><small>${r.source_basis}</small>`).addTo(layerGroup);
    addFlowLabel([r.lat + 0.08, r.lon + 0.04], `${r.uganda_name} ${fmt.format(Math.round(r.projected))}`, 'flow-label uganda-label');
  });

  addUgandaProjectionLegend();
}



function availableCaseDates() {
  return [...new Set(cases.map(r => String(r.date || '')).filter(Boolean))].sort();
}

function selectedCaseDate() {
  const select = document.getElementById('reportDateSelect');
  if (select && select.value) return select.value;
  const dates = availableCaseDates();
  return dates[dates.length - 1] || '';
}

function comparisonCaseDate(selectedDate = selectedCaseDate()) {
  const dates = availableCaseDates();
  if (!selectedDate || !dates.length) return '';
  const target = new Date(selectedDate + 'T00:00:00Z');
  target.setUTCDate(target.getUTCDate() - 7);
  const targetStr = target.toISOString().slice(0, 10);
  const candidates = dates.filter(d => d <= targetStr);
  return candidates[candidates.length - 1] || dates[0];
}

function reportSummaryForDate(date = selectedCaseDate()) {
  return reportSummary.find(r => String(r.reporting_date) === String(date)) || reportSummary.slice().sort((a,b)=>String(a.reporting_date).localeCompare(String(b.reporting_date))).slice(-1)[0] || null;
}

function displayDateLabel(dateStr) {
  if (!dateStr) return '—';
  const d = new Date(dateStr + 'T00:00:00Z');
  if (Number.isNaN(d.getTime())) return dateStr;
  return d.toLocaleDateString('en-GB', { day:'2-digit', month:'short', year:'numeric', timeZone:'UTC' });
}

function caseDisplayLabel() {
  const date = selectedCaseDate();
  if (caseDisplayMode === 'recent') return `recent increase (${displayDateLabel(comparisonCaseDate(date))} to ${displayDateLabel(date)})`;
  return `cumulative as of ${displayDateLabel(date)}`;
}

function invalidateCaseCaches() {
  latestCasesCache = null;
  caseLookupCache = null;
  affectedOriginsCache = null;
}

function rawCaseRowsForDate(date) {
  return cases.filter(r => String(r.date || '') === String(date)).map(r => ({
    ...r,
    zone_id: String(r.zone_id || ''),
    health_zone: String(r.health_zone || r.zone_name || ''),
    province: String(r.province || ''),
    confirmed_cases: toNumber(r.confirmed_cases),
    confirmed_deaths: toNumber(r.confirmed_deaths),
    lat: toNumber(r.lat),
    lon: toNumber(r.lon)
  }));
}

function caseRowKey(r) {
  return String(r.zone_id || '') || `name:${normalizedString(r.health_zone)}|${normalizedString(r.province)}`;
}

function latestCaseDate() {
  return selectedCaseDate();
}

function caseRowsLatest() {
  if (latestCasesCache) return latestCasesCache;
  const selected = selectedCaseDate();
  const current = rawCaseRowsForDate(selected);
  if (caseDisplayMode !== 'recent') {
    latestCasesCache = current;
    return latestCasesCache;
  }
  const baseDate = comparisonCaseDate(selected);
  const base = rawCaseRowsForDate(baseDate);
  const baseByKey = new Map(base.map(r => [caseRowKey(r), r]));
  latestCasesCache = current.map(r => {
    const b = baseByKey.get(caseRowKey(r)) || { confirmed_cases: 0, confirmed_deaths: 0 };
    return {
      ...r,
      confirmed_cases: Math.max(0, toNumber(r.confirmed_cases) - toNumber(b.confirmed_cases)),
      confirmed_deaths: Math.max(0, toNumber(r.confirmed_deaths) - toNumber(b.confirmed_deaths)),
      baseline_date: baseDate,
      cumulative_confirmed_cases: toNumber(r.confirmed_cases),
      cumulative_confirmed_deaths: toNumber(r.confirmed_deaths)
    };
  });
  return latestCasesCache;
}

function casesLookup() {
  if (caseLookupCache) return caseLookupCache;
  const byId = new Map();
  const byName = new Map();
  for (const r of caseRowsLatest()) {
    if (r.zone_id) byId.set(String(r.zone_id), r);
    byName.set(normalizedString(r.health_zone), r);
  }
  caseLookupCache = { byId, byName };
  return caseLookupCache;
}

function caseForZone(zoneId, zoneName) {
  const lookup = casesLookup();
  return lookup.byId.get(String(zoneId || '')) || lookup.byName.get(normalizedString(zoneName || '')) || null;
}

function caseRowsForMap() {
  return caseRowsLatest().map(r => {
    const d = destById(r.zone_id) || destByName(r.health_zone) || {};
    return {
      ...d,
      ...r,
      zone_name: r.health_zone || d.zone_name,
      lat: toNumber(r.lat) || toNumber(d.lat),
      lon: toNumber(r.lon) || toNumber(d.lon),
      cases: toNumber(r.confirmed_cases),
      deaths: toNumber(r.confirmed_deaths)
    };
  });
}


function featureCentroidLatLon(feature) {
  try {
    if (typeof turf !== 'undefined' && turf.centroid) {
      const c = turf.centroid(feature);
      if (c && c.geometry && Array.isArray(c.geometry.coordinates)) {
        return { lat: c.geometry.coordinates[1], lon: c.geometry.coordinates[0] };
      }
    }
  } catch (e) {}
  return { lat: NaN, lon: NaN };
}

function boundaryCentroidLookup() {
  if (boundaryCentroidCache) return boundaryCentroidCache;
  const byId = new Map();
  const byName = new Map();
  if (!hasBoundaries()) return { byId, byName };
  for (const f of healthZoneBoundaries.features) {
    const id = featureZoneId(f);
    const name = featureZoneName(f);
    const province = featureProvince(f);
    const c = featureCentroidLatLon(f);
    const row = { zone_id: id, zone_name: name, province, lat: c.lat, lon: c.lon, feature: f };
    if (id) byId.set(String(id), row);
    if (name) byName.set(normalizedString(name), row);
  }
  boundaryCentroidCache = { byId, byName };
  return boundaryCentroidCache;
}

function addCaseBubbleLegend(maxCases) {
  if (choroLegend) { map.removeControl(choroLegend); choroLegend = null; }
  const large = Math.max(1, Math.round(maxCases));
  const mid = Math.max(1, Math.round(maxCases / 2));
  const small = Math.max(1, Math.round(maxCases / 10));
  const items = [
    { label: fmt.format(large), size: 28 },
    { label: fmt.format(mid), size: 20 },
    { label: fmt.format(small), size: 11 }
  ];
  choroLegend = L.control({ position: 'bottomright' });
  choroLegend.onAdd = function() {
    const div = L.DomUtil.create('div', 'choro-legend case-bubble-legend');
    div.innerHTML = `<strong>${caseDisplayMode === 'recent' ? 'Recent cases' : 'Confirmed cases'}<br><small>bubble size</small></strong>` + items.map(d => `<div class="bubble-row"><span class="bubble-symbol" style="width:${d.size}px;height:${d.size}px"></span>${d.label} ${caseDisplayMode === 'recent' ? 'new' : 'cases'}</div>`).join('');
    return div;
  };
  choroLegend.addTo(map);
}

function updateCasesMap() {
  if (choroLegend) { map.removeControl(choroLegend); choroLegend = null; }
  layerGroup.clearLayers();
  const notice = document.getElementById('populationNotice');
  const centroidLookup = boundaryCentroidLookup();
  const rows = caseRowsForMap().map(r => {
    const hasDirectCoords = Number.isFinite(toNumber(r.lat)) && Number.isFinite(toNumber(r.lon)) && toNumber(r.lat) !== 0 && toNumber(r.lon) !== 0;
    const boundaryById = r.zone_id ? centroidLookup.byId.get(String(r.zone_id)) : null;
    const boundaryByName = !boundaryById && r.zone_name ? centroidLookup.byName.get(normalizedString(r.zone_name)) : null;
    const sameProvinceNameMatch = boundaryByName && r.province && boundaryByName.province && normalizedString(boundaryByName.province) === normalizedString(r.province);
    const safeFallback = boundaryById || sameProvinceNameMatch ? (boundaryById || boundaryByName) : null;
    return {
      ...r,
      lat: hasDirectCoords ? toNumber(r.lat) : toNumber(safeFallback?.lat),
      lon: hasDirectCoords ? toNumber(r.lon) : toNumber(safeFallback?.lon),
      province: r.province || safeFallback?.province || '',
      map_location_known: hasDirectCoords || !!safeFallback
    };
  });
  const mappedRows = rows.filter(r => Number.isFinite(r.lat) && Number.isFinite(r.lon) && toNumber(r.cases) > 0);
  const maxCases = Math.max(...rows.map(r => toNumber(r.cases)), 1);

  document.getElementById('mapTitle').textContent = 'Confirmed Ebola cases by health zone';
  document.getElementById('mapDescription').textContent = caseDisplayMode === 'recent' ? 'Recent increase in confirmed cases is shown as proportional red bubbles at health-zone centroids. Boundary outlines are hidden in this layer to keep the case bubbles readable.' : 'Cumulative confirmed cases are shown as proportional red bubbles at health-zone centroids. Boundary outlines are hidden in this layer to keep the case bubbles readable.';
  document.getElementById('rankingTitle').textContent = 'Case-count ranking';
  document.getElementById('rankingDescription').textContent = caseDisplayMode === 'recent' ? 'Recent increase in confirmed cases by health zone.' : 'Cumulative confirmed cases and deaths by health zone from the selected SitRep.';
  notice.style.display = 'block';
  notice.className = 'population-notice';
  const hiddenUnmapped = rows.filter(r => toNumber(r.cases) > 0 && !r.map_location_known).reduce((sum, r) => sum + toNumber(r.cases), 0);
  notice.innerHTML = `Case layer: bubble size represents ${caseDisplayMode === 'recent' ? 'recent increase in confirmed cases' : 'cumulative confirmed cases'} by health zone for ${caseDisplayLabel()}. Unventilated / unknown-health-zone cases are not shown because they cannot be assigned to a specific health zone.${hiddenUnmapped > 0 ? ` An additional ${fmt.format(Math.round(hiddenUnmapped))} case${hiddenUnmapped === 1 ? '' : 's'} from mapped health-zone records are retained in the totals but hidden on the map because no reliable geographic match is available.` : ''}`;

  // Do not draw health-zone polygon outlines in the Cases layer.
  // The proportional case bubbles are the primary visual encoding here;
  // polygon outlines made the map visually busy and could be mistaken for a choropleth.

  mappedRows.forEach(r => {
    const radius = 5 + 31 * Math.sqrt(toNumber(r.cases) / maxCases);
    L.circleMarker([r.lat, r.lon], {
      radius,
      color: '#7a271a',
      weight: 2,
      fillColor: '#d92d20',
      fillOpacity: 0.54,
      opacity: 0.95
    })
      .bindPopup(`<strong>${r.zone_name}</strong><br>${r.province}<br>Confirmed cases: ${fmt.format(Math.round(r.cases))}<br>Confirmed deaths: ${fmt.format(Math.round(r.deaths))}<br>Source date: ${latestCaseDate() || '—'}`)
      .addTo(layerGroup);

    if (toNumber(r.cases) >= Math.max(10, maxCases * 0.12)) {
      addFlowLabel([r.lat, r.lon], `${r.zone_name}: ${fmt.format(Math.round(r.cases))}`, 'case-bubble-label');
    }
  });

  addCaseBubbleLegend(maxCases);
}

function forecastMobilityMonths(selectedMonth) {
  const months = [...new Set(flows.map(d => d.month))].sort();
  const idx = months.indexOf(selectedMonth);
  if (idx >= 0 && idx < months.length - 1) return { months: [months[idx + 1]], label: `next available month: ${months[idx + 1]}` };
  const last3 = months.slice(Math.max(0, months.length - 3));
  return { months: last3, label: `average of latest ${last3.length} mobility months (${last3.join(', ')})` };
}

function weightedRiskRowsForMonth(month, forecast = false) {
  const f = currentFilters();
  const basis = forecast ? forecastMobilityMonths(month) : { months: [month], label: month };
  const originIds = selectedOriginIds();
  const affectedOriginById = new Map(affectedOriginRows().map(x => [String(x.zone_id), x]));
  const rows = basis.months.flatMap(m => flowsForMonthAndOrigins(m, originIds));
  const denom = Math.max(basis.months.length, 1);
  const byDest = new Map();
  const byDestMove = new Map();
  for (const r of rows) {
    const o = affectedOriginById.get(String(r.origin_id)) || destById(r.origin_id) || {};
    const c = caseForZone(r.origin_id, o.zone_name);
    const caseWeight = c ? toNumber(c.confirmed_cases) : 0;
    const movement = toNumber(r.movement) / denom;
    if (caseWeight <= 0 || movement <= 0) continue;
    const id = String(r.destination_id);
    byDest.set(id, (byDest.get(id) || 0) + caseWeight * movement);
    byDestMove.set(id, (byDestMove.get(id) || 0) + movement);
  }
  const destIds = new Set([...byDest.keys(), ...destinations.map(d => String(d.zone_id))]);
  const popById = new Map(population.filter(r => r.month === month).map(r => [String(r.zone_id), r]));
  return [...destIds].map(id => {
    const d = destById(id) || {};
    const pop = popById.get(String(id));
    return { ...d, zone_id: id, zone_name: d.zone_name || pop?.zone_name || id, province: d.province || pop?.province || '', lat: toNumber(d.lat) || toNumber(pop?.lat), lon: toNumber(d.lon) || toNumber(pop?.lon), weighted: byDest.get(id) || 0, incoming: byDestMove.get(id) || 0, forecast_basis: basis.label };
  }).filter(r => r.weighted > 0 || r.zone_name);
}


function contactAdjustedRiskRowsForMonth(month) {
  const f = currentFilters();
  const originIds = selectedOriginIds();
  const affectedOriginById = new Map(affectedOriginRows().map(x => [String(x.zone_id), x]));
  const rows = flowsForMonthAndOrigins(month, originIds);
  const byDest = new Map();
  const byDestMove = new Map();
  const byDestBase = new Map();
  for (const r of rows) {
    const o = affectedOriginById.get(String(r.origin_id)) || destById(r.origin_id) || {};
    const c = caseForZone(r.origin_id, o.zone_name);
    const caseWeight = c ? toNumber(c.confirmed_cases) : 0;
    const movement = toNumber(r.movement);
    const mult = contactGapMultiplierForProvince(c?.province || o.province || '');
    if (caseWeight <= 0 || movement <= 0) continue;
    const id = String(r.destination_id);
    byDestBase.set(id, (byDestBase.get(id) || 0) + caseWeight * movement);
    byDest.set(id, (byDest.get(id) || 0) + caseWeight * movement * mult);
    byDestMove.set(id, (byDestMove.get(id) || 0) + movement);
  }
  const destIds = new Set([...byDest.keys(), ...destinations.map(d => String(d.zone_id))]);
  const popById = new Map(population.filter(r => r.month === month).map(r => [String(r.zone_id), r]));
  return [...destIds].map(id => {
    const d = destById(id) || {};
    const pop = popById.get(String(id));
    return { ...d, zone_id: id, zone_name: d.zone_name || pop?.zone_name || id, province: d.province || pop?.province || '', lat: toNumber(d.lat) || toNumber(pop?.lat), lon: toNumber(d.lon) || toNumber(pop?.lon), contact_adjusted: byDest.get(id) || 0, weighted: byDestBase.get(id) || 0, incoming: byDestMove.get(id) || 0 };
  }).filter(r => r.contact_adjusted > 0 || r.zone_name);
}


function airAdjustmentFactorForDestination(dest) {
  if (!dest) return 1;
  let factor = 1;
  const destName = normalizedString(dest.zone_name || dest.health_zone || '');
  const destCategory = normalizedString(dest.category || '');
  for (const row of airAdjustment) {
    const mt = normalizedString(row.match_type);
    const mv = normalizedString(row.match_value);
    const f = toNumber(row.air_factor);
    if (!f && f !== 0) continue;
    if (mt === 'category' && destCategory === mv) factor = Math.min(factor, f);
    if (mt === 'zone_name' && destName === mv) factor = Math.min(factor, f);
  }
  return factor;
}

function isAirAdjustedDestination(dest) {
  return airAdjustmentFactorForDestination(dest) < 0.999;
}

function airAdjustedRiskRowsForMonth(month, forecast = false) {
  return weightedRiskRowsForMonth(month, forecast).map(r => {
    const factor = airAdjustmentFactorForDestination(r);
    return {
      ...r,
      air_factor: factor,
      air_adjusted: toNumber(r.weighted) * factor,
      suppressed_amount: toNumber(r.weighted) * (1 - factor),
      is_air_adjusted: factor < 0.999
    };
  });
}

function weightedRiskBreaksForMonth(month) {
  const weightedRows = weightedRiskRowsForMonth(month, false).filter(r => toNumber(r.weighted) > 0);
  const weightedValues = weightedRows.map(r => toNumber(r.weighted)).filter(v => v > 0);
  if (!weightedValues.length) return [0, 0, 0, 0];
  return [0.2, 0.4, 0.6, 0.8].map(q => quantile(weightedValues, q));
}

function addAirRiskLegend(breaks) {
  const labels = ['No/very low', `≤ ${fmt.format(Math.round(breaks[0]))}`, `≤ ${fmt.format(Math.round(breaks[1]))}`, `≤ ${fmt.format(Math.round(breaks[2]))}`, `≤ ${fmt.format(Math.round(breaks[3]))}`, `> ${fmt.format(Math.round(breaks[3]))}`];
  const colors = ['#fff5f5', '#fee4e2', '#fecdca', '#f97066', '#d92d20', '#7a271a'];
  if (choroLegend) { map.removeControl(choroLegend); choroLegend = null; }
  choroLegend = L.control({ position: 'bottomright' });
  choroLegend.onAdd = function() {
    const div = L.DomUtil.create('div', 'choro-legend risk-scale-legend air-risk-legend');
    div.innerHTML = `<strong><span class="legend-title-nowrap">Flight suppression-adjusted score</span></strong>` + colors.map((c, i) => `<div><i style="background:${c}"></i>${labels[i]}</div>`).join('');
    return div;
  };
  choroLegend.addTo(map);
}

function airRiskColor(value, breaks) {
  return riskColor(value, breaks);
}

function addContactRiskLegend(breaks) {
  addCaseLegend(breaks, 'Contact-adjusted risk score', '');
}

function updateContactAdjustedRiskMap() {
  if (choroLegend) { map.removeControl(choroLegend); choroLegend = null; }
  layerGroup.clearLayers();
  const f = currentFilters();
  const notice = document.getElementById('populationNotice');
  const rows = contactAdjustedRiskRowsForMonth(f.month).filter(r => r.contact_adjusted > 0);
  // Use the same color breaks as Case-weighted risk so the effect of contact-follow-up adjustment is visible.
  // If we recalculated quantile breaks independently, the map could look almost identical after a uniform multiplier.
  const breaks = weightedRiskBreaksForMonth(f.month);

  document.getElementById('mapTitle').textContent = 'Contact-adjusted case-weighted risk';
  document.getElementById('mapDescription').textContent = 'Health zones are colored by case-weighted incoming movement adjusted upward when contact follow-up in the origin province is below the 95% target.';
  document.getElementById('rankingTitle').textContent = 'Contact-adjusted risk ranking';
  document.getElementById('rankingDescription').textContent = `Top health zones by contact-follow-up adjusted score. Origins: ${originFilterText()}.`;
  notice.style.display = 'block';
  notice.className = 'population-notice';
  notice.innerHTML = 'Contact-adjusted risk is a <strong>prioritization indicator</strong>, not a probability of transmission. Formula: confirmed cases at selected origins × movement to destination × contact follow-up gap multiplier. Multipliers use SitRep N24 province-level contact follow-up rates and a 95% target: Ituri 60.1%, Nord-Kivu 79.5%, Sud-Kivu 99.1%. Color breaks use the Case-weighted risk scale for direct comparison.';

  const byId = new Map(rows.map(r => [String(r.zone_id), r]));
  const byName = new Map(rows.map(r => [normalizedString(r.zone_name), r]));
  if (hasBoundaries()) {
    L.geoJSON(healthZoneBoundaries, {
      style: feature => {
        const id = featureZoneId(feature);
        const name = featureZoneName(feature);
        const r = byId.get(String(id)) || byName.get(normalizedString(name)) || { contact_adjusted: 0 };
        return {
          color: '#ffffff',
          weight: 0.6,
          fillColor: riskColor(toNumber(r.contact_adjusted), breaks),
          fillOpacity: toNumber(r.contact_adjusted) > 0 ? 0.72 : 0.10,
          opacity: 1
        };
      },
      onEachFeature: (feature, layer) => {
        const id = featureZoneId(feature);
        const name = featureZoneName(feature);
        const r = byId.get(String(id)) || byName.get(normalizedString(name)) || {};
        layer.bindPopup(`<strong>${r.zone_name || name || 'Health zone'}</strong><br>${r.province || featureProvince(feature) || ''}<br>Case-weighted score: ${fmt.format(Math.round(toNumber(r.weighted)))}<br>Contact-adjusted score: ${fmt.format(Math.round(toNumber(r.contact_adjusted)))}<br>${f.month}`);
      }
    }).addTo(layerGroup);
    addContactRiskLegend(breaks);
  } else {
    rows.slice().sort((a,b)=>toNumber(b.contact_adjusted)-toNumber(a.contact_adjusted)).slice(0, 30).forEach(r => {
      if (!Number.isFinite(toNumber(r.lat)) || !Number.isFinite(toNumber(r.lon))) return;
      L.circleMarker([r.lat, r.lon], { radius: Math.max(6, Math.min(34, Math.sqrt(toNumber(r.contact_adjusted))/60)), color:'#7a271a', weight:1.5, fillColor:riskColor(toNumber(r.contact_adjusted), breaks), fillOpacity:0.72 }).bindPopup(`<strong>${r.zone_name}</strong><br>Contact-adjusted score: ${fmt.format(Math.round(toNumber(r.contact_adjusted)))}`).addTo(layerGroup);
    });
    addContactRiskLegend(breaks);
  }
}

function updateAirAdjustedRiskMap() {
  if (choroLegend) { map.removeControl(choroLegend); choroLegend = null; }
  layerGroup.clearLayers();
  const f = currentFilters();
  const notice = document.getElementById('populationNotice');
  const rows = airAdjustedRiskRowsForMonth(f.month, false).filter(r => toNumber(r.weighted) > 0 || toNumber(r.air_adjusted) > 0);
  // Use exactly the same class breaks as the Weighted risk layer for this month.
  // This makes Air-adjusted risk directly comparable to Weighted risk: colors show
  // the adjusted score against the original weighted-risk scale, not a rescaled
  // distribution of adjusted values.
  const breaks = weightedRiskBreaksForMonth(f.month);

  document.getElementById('mapTitle').textContent = 'Air-adjusted case-weighted risk';
  document.getElementById('mapDescription').textContent = 'Scenario-based layer: long-distance air-plausible destinations are down-weighted to reflect suspension/reopening of Bunia passenger flights under screening measures. Local/road-dominant movement is not reduced.';
  document.getElementById('rankingTitle').textContent = 'Air-adjusted risk ranking';
  document.getElementById('rankingDescription').textContent = 'Top health zones by case-weighted movement score after applying the air-travel suppression scenario. Color classes use the same breaks as Weighted risk.';
  notice.style.display = 'block';
  notice.className = 'population-notice';
  notice.innerHTML = 'Air-adjusted risk is a <strong>scenario indicator</strong>, not observed passenger OD and not transmission probability. It reduces long-distance, air-plausible risk using <code>data/air_adjustment.csv</code>. Color breaks are intentionally kept identical to the Weighted risk layer for direct comparison. Case data are from SitRep N24/MVB_07/06/2026; unventilated Ituri cases are excluded from map-based layers because they cannot be assigned to a specific health zone.';

  const byId = new Map(rows.map(r => [String(r.zone_id), r]));
  const byName = new Map(rows.map(r => [normalizedString(r.zone_name), r]));
  if (hasBoundaries()) {
    L.geoJSON(healthZoneBoundaries, {
      style: feature => {
        const id = featureZoneId(feature);
        const name = featureZoneName(feature);
        const r = byId.get(String(id)) || byName.get(normalizedString(name)) || { air_adjusted: 0, is_air_adjusted: false };
        return {
          color: '#ffffff',
          weight: 0.6,
          fillColor: airRiskColor(toNumber(r.air_adjusted), breaks),
          fillOpacity: toNumber(r.air_adjusted) > 0 ? 0.70 : 0.10,
          opacity: 1
        };
      },
      onEachFeature: (feature, layer) => {
        const id = featureZoneId(feature);
        const name = featureZoneName(feature);
        const r = byId.get(String(id)) || byName.get(normalizedString(name)) || {};
        layer.bindPopup(`<strong>${r.zone_name || name || 'Health zone'}</strong><br>${r.province || featureProvince(feature) || ''}<br>Weighted risk: ${fmt.format(Math.round(toNumber(r.weighted)))}<br>Air-adjusted risk: ${fmt.format(Math.round(toNumber(r.air_adjusted)))}<br>Suppression factor: ${r.air_factor !== undefined ? r.air_factor : 1}<br>${f.month}`);
      }
    }).addTo(layerGroup);
    addAirRiskLegend(breaks);
  }

  // Air-adjusted risk is shown as a health-zone risk surface only.
  // Do not overlay case bubbles, outbreak-origin circles, or flight-route lines in this layer.
}


function addCaseLegend(breaks, title, subtitle) {
  const labels = ['No/very low', `≤ ${fmt.format(Math.round(breaks[0]))}`, `≤ ${fmt.format(Math.round(breaks[1]))}`, `≤ ${fmt.format(Math.round(breaks[2]))}`, `≤ ${fmt.format(Math.round(breaks[3]))}`, `> ${fmt.format(Math.round(breaks[3]))}`];
  const colors = ['#fff5f5', '#fee4e2', '#fecdca', '#f97066', '#d92d20', '#7a271a'];
  if (choroLegend) { map.removeControl(choroLegend); choroLegend = null; }
  choroLegend = L.control({ position: 'bottomright' });
  choroLegend.onAdd = function() {
    const div = L.DomUtil.create('div', 'choro-legend risk-scale-legend weighted-risk-legend');
    div.innerHTML = `<strong><span class="legend-title-nowrap">${title}</span>${subtitle ? `<br><small>${subtitle}</small>` : ''}</strong>` + colors.map((c, i) => `<div><i style="background:${c}"></i>${labels[i]}</div>`).join('');
    return div;
  };
  choroLegend.addTo(map);
}

function updateWeightedRiskMap(forecast = false) {
  if (choroLegend) { map.removeControl(choroLegend); choroLegend = null; }
  layerGroup.clearLayers();
  const f = currentFilters();
  const notice = document.getElementById('populationNotice');
  const rows = weightedRiskRowsForMonth(f.month, forecast).filter(r => r.weighted > 0);
  const values = rows.map(r => toNumber(r.weighted)).filter(v => v > 0);
  const breaks = [0.2, 0.4, 0.6, 0.8].map(q => quantile(values, q));
  const title = forecast ? 'Forecast case-weighted spread risk' : 'Case-weighted spread risk';
  document.getElementById('mapTitle').textContent = title;
  document.getElementById('mapDescription').textContent = forecast ? 'Forecast layer uses latest health-zone case counts and the next available or latest-average mobility pattern to estimate future case-weighted movement pressure.' : 'Health zones are colored by case-weighted incoming movement from the selected origin set: confirmed cases × estimated movement to the destination. Changing the origin set recalculates the score using the selected affected health zones.';
  document.getElementById('rankingTitle').textContent = forecast ? 'Forecast-risk ranking' : 'Weighted-risk ranking';
  document.getElementById('rankingDescription').textContent = forecast ? 'Top health zones by forecast case-weighted movement pressure.' : 'Top health zones by confirmed-case-weighted movement pressure.';
  const basis = rows[0]?.forecast_basis || (forecast ? forecastMobilityMonths(f.month).label : f.month);
  notice.style.display = 'block';
  notice.className = 'population-notice';
  notice.innerHTML = `${title}: score = Σ confirmed_cases(origin) × estimated movement(origin→destination). ${forecast ? 'Forecast mobility basis: ' + basis + '.' : 'Mobility basis: selected month ' + f.month + '.'} This is a relative prioritization score, not a transmission probability.`;
  const byId = new Map(rows.map(r => [String(r.zone_id), r]));
  const byName = new Map(rows.map(r => [normalizedString(r.zone_name), r]));
  if (hasBoundaries()) {
    L.geoJSON(healthZoneBoundaries, {
      style: feature => {
        const id = featureZoneId(feature); const name = featureZoneName(feature);
        const r = byId.get(String(id)) || byName.get(normalizedString(name)) || { weighted: 0 };
        return { color: '#ffffff', weight: 0.6, fillColor: riskColor(toNumber(r.weighted), breaks), fillOpacity: toNumber(r.weighted) > 0 ? 0.74 : 0.10, opacity: 1 };
      },
      onEachFeature: (feature, layer) => {
        const id = featureZoneId(feature); const name = featureZoneName(feature);
        const r = byId.get(String(id)) || byName.get(normalizedString(name)) || {};
        layer.bindPopup(`<strong>${r.zone_name || name || 'Health zone'}</strong><br>${r.province || featureProvince(feature) || ''}<br>${title} score: ${fmt.format(Math.round(toNumber(r.weighted)))}<br>Estimated incoming movement basis: ${fmt.format(Math.round(toNumber(r.incoming)))}<br>${forecast ? 'Forecast basis: ' + basis : 'Mobility month: ' + f.month}`);
      }
    }).addTo(layerGroup);
  } else {
    const maxScore = Math.max(...rows.map(r => toNumber(r.weighted)), 1);
    rows.filter(r => Number.isFinite(r.lat) && Number.isFinite(r.lon) && toNumber(r.weighted) > 0).forEach(r => {
      const radius = 5 + 24 * Math.sqrt(toNumber(r.weighted) / maxScore);
      L.circleMarker([r.lat, r.lon], { radius, color: '#7a271a', weight: 1.5, fillColor: '#d92d20', fillOpacity: 0.56 })
        .bindPopup(`<strong>${r.zone_name}</strong><br>${r.province}<br>${title} score: ${fmt.format(Math.round(r.weighted))}<br>Incoming movement basis: ${fmt.format(Math.round(r.incoming))}`).addTo(layerGroup);
    });
  }
  addCaseLegend(breaks, forecast ? 'Forecast risk' : 'Case-weighted risk score', forecast ? 'case × movement score' : '');
}


function ugandaObservedDistrictRows() {
  return ugandaDistrictFlows
    .map(r => ({
      ...r,
      observed_movements: toNumber(r.observed_movements),
      share: toNumber(r.share),
      lat: toNumber(r.lat),
      lon: toNumber(r.lon)
    }))
    .filter(r => r.observed_movements > 0)
    .sort((a, b) => b.observed_movements - a.observed_movements);
}

function ugandaObservedFmpRows() {
  return ugandaFmpFlows
    .map(r => ({
      ...r,
      observed_movements: toNumber(r.observed_movements),
      share: toNumber(r.share),
      lat: toNumber(r.lat),
      lon: toNumber(r.lon)
    }))
    .filter(r => r.observed_movements > 0)
    .sort((a, b) => b.observed_movements - a.observed_movements);
}

function selectedCaseWeightedBorderPressure(month) {
  const originIds = selectedOriginIds();
  const affectedOriginById = new Map(affectedOriginRows().map(x => [String(x.zone_id), x]));
  const rows = flowsForMonthAndOrigins(month, originIds);
  let pressure = 0;
  let movement = 0;
  for (const r of rows) {
    const d = destById(r.destination_id) || {};
    if (!(d.is_uganda_border === 1 || d.category === 'uganda_border')) continue;
    const o = affectedOriginById.get(String(r.origin_id)) || destById(r.origin_id) || {};
    const c = caseForZone(r.origin_id, o.zone_name);
    const cases = c ? toNumber(c.confirmed_cases) : 0;
    const m = toNumber(r.movement);
    pressure += cases * m;
    movement += m;
  }
  return { pressure, movement };
}

function ugandaImportationRowsForMonth(month) {
  const base = selectedCaseWeightedBorderPressure(month);
  const districts = ugandaObservedDistrictRows();
  const totalObserved = districts.reduce((a, b) => a + toNumber(b.observed_movements), 0) || 1;
  return districts.map(r => {
    const share = toNumber(r.share) || toNumber(r.observed_movements) / totalObserved;
    return {
      ...r,
      importation_pressure: base.pressure * share,
      movement_pressure: base.movement * share,
      allocation_share: share,
      base_border_pressure: base.pressure,
      base_border_movement: base.movement
    };
  }).sort((a, b) => b.importation_pressure - a.importation_pressure);
}

function addUgandaObservedLegend(title = 'Observed DTM flow') {
  if (choroLegend) { map.removeControl(choroLegend); choroLegend = null; }
  choroLegend = L.control({ position: 'bottomright' });
  choroLegend.onAdd = function() {
    const div = L.DomUtil.create('div', 'choro-legend');
    div.innerHTML = `<strong>${title}</strong>` +
      `<div><i style="background:#175cd3"></i>Flow monitoring point</div>` +
      `<div><i style="background:#7a271a"></i>Uganda destination district</div>` +
      `<small>15–24 May 2026; indicative, not exhaustive</small>`;
    return div;
  };
  choroLegend.addTo(map);
}

function updateUgandaBorderFlowMap() {
  if (choroLegend) { map.removeControl(choroLegend); choroLegend = null; }
  layerGroup.clearLayers();
  const notice = document.getElementById('populationNotice');
  const fmpRows = ugandaObservedFmpRows();
  const districtRows = ugandaObservedDistrictRows();
  const total = fmpRows.reduce((a, b) => a + b.observed_movements, 0);
  const maxFmp = Math.max(...fmpRows.map(r => r.observed_movements), 1);
  const maxDistrict = Math.max(...districtRows.map(r => r.observed_movements), 1);

  document.getElementById('mapTitle').textContent = 'Uganda border flow — observed DTM FMP data';
  document.getElementById('mapDescription').textContent = 'Observed cross-border movements at selected Uganda–DRC flow monitoring points during 15–24 May 2026, with Uganda destination districts shown as brown bubbles.';
  document.getElementById('rankingTitle').textContent = 'Uganda destination ranking';
  document.getElementById('rankingDescription').textContent = 'Observed movements from Ituri and Nord-Kivu to Uganda districts in the IOM DTM EVD flow monitoring snapshot.';
  notice.style.display = 'block';
  notice.className = 'population-notice';
  notice.innerHTML = 'Uganda border flow uses IOM DTM Uganda Flow Monitoring — Ebola Virus Disease Outbreak, 15–24 May 2026. FMP data are indicative of selected key flows and are not a complete or statistically representative count of all cross-border movement.';

  districtRows.filter(r => Number.isFinite(r.lat) && Number.isFinite(r.lon)).forEach(r => {
    const radius = 5 + 26 * Math.sqrt(r.observed_movements / maxDistrict);
    L.circleMarker([r.lat, r.lon], { radius, color:'#7a271a', weight:1.7, fillColor:'#b42318', fillOpacity:0.48 })
      .bindPopup(`<strong>${r.uganda_district}</strong><br>Observed movements: ${fmt.format(Math.round(r.observed_movements))}<br>Share: ${pct(r.share)}<br>Period: ${r.period}`)
      .addTo(layerGroup);
  });

  fmpRows.filter(r => Number.isFinite(r.lat) && Number.isFinite(r.lon)).forEach(r => {
    const radius = 5 + 18 * Math.sqrt(r.observed_movements / maxFmp);
    L.circleMarker([r.lat, r.lon], { radius, color:'#102a56', weight:2, fillColor:'#175cd3', fillOpacity:0.72 })
      .bindPopup(`<strong>${r.fmp}</strong><br>Flow monitoring point<br>Observed movements: ${fmt.format(Math.round(r.observed_movements))}<br>Share: ${pct(r.share)}<br>Period: ${r.period}`)
      .addTo(layerGroup);
    if (r.observed_movements >= maxFmp * 0.55) addFlowLabel([r.lat, r.lon], `${r.fmp}: ${fmt.format(Math.round(r.observed_movements))}`, 'hub-label');
  });
  addUgandaObservedLegend('Observed Uganda flows');
}

function updateUgandaImportationMap() {
  if (choroLegend) { map.removeControl(choroLegend); choroLegend = null; }
  layerGroup.clearLayers();
  const f = currentFilters();
  const notice = document.getElementById('populationNotice');
  const rows = ugandaImportationRowsForMonth(f.month).filter(r => r.importation_pressure > 0);
  const maxScore = Math.max(...rows.map(r => r.importation_pressure), 1);
  const base = selectedCaseWeightedBorderPressure(f.month);

  document.getElementById('mapTitle').textContent = 'Uganda importation pressure';
  document.getElementById('mapDescription').textContent = 'Scenario indicator combining DRC case-weighted movement toward Uganda-border proxy zones with the 2026 IOM DTM Uganda destination profile.';
  document.getElementById('rankingTitle').textContent = 'Uganda importation-pressure ranking';
  document.getElementById('rankingDescription').textContent = `Uganda districts ranked by case-weighted border-pressure allocation. Origins: ${originFilterText()}.`;
  notice.style.display = 'block';
  notice.className = 'population-notice';
  notice.innerHTML = `Uganda importation pressure = DRC case-weighted movement toward Uganda-border proxy health zones × observed Uganda district allocation from IOM DTM 15–24 May 2026. Base border movement: <strong>${fmt.format(Math.round(base.movement))}</strong>; base case-weighted border pressure: <strong>${fmt.format(Math.round(base.pressure))}</strong>. This is a prioritization score, not observed infected travel and not a transmission probability.`;

  rows.filter(r => Number.isFinite(r.lat) && Number.isFinite(r.lon)).forEach(r => {
    const radius = 5 + 31 * Math.sqrt(r.importation_pressure / maxScore);
    L.circleMarker([r.lat, r.lon], { radius, color:'#7a271a', weight:2, fillColor:'#d92d20', fillOpacity:0.58 })
      .bindPopup(`<strong>${r.uganda_district}</strong><br>Importation-pressure score: ${fmt.format(Math.round(r.importation_pressure))}<br>Allocated movement pressure: ${fmt.format(Math.round(r.movement_pressure))}<br>DTM allocation share: ${pct(r.allocation_share)}<br>Observed DTM movements: ${fmt.format(Math.round(r.observed_movements))}`)
      .addTo(layerGroup);
    if (r.importation_pressure >= maxScore * 0.35) addFlowLabel([r.lat, r.lon], `${r.uganda_district}: ${fmt.format(Math.round(r.importation_pressure))}`, 'origin-label');
  });
  addUgandaObservedLegend('Importation pressure');
}

function updateKpis(destRows) {
  const f = currentFilters();

  if (mapMode === 'uganda_border') {
    const fmpRows = ugandaObservedFmpRows();
    const districtRows = ugandaObservedDistrictRows();
    const total = fmpRows.reduce((a,b)=>a+toNumber(b.observed_movements),0);
    const topFmp = fmpRows[0];
    const topDistrict = districtRows[0];
    document.getElementById('kpiTotal').textContent = fmt.format(Math.round(total));
    document.getElementById('kpiKinshasa').textContent = topFmp ? topFmp.fmp : '—';
    document.getElementById('kpiKinshasaShare').textContent = topFmp ? `${fmt.format(Math.round(topFmp.observed_movements))} observed; top FMP` : '—';
    document.getElementById('kpiBorder').textContent = topDistrict ? topDistrict.uganda_district : '—';
    document.getElementById('kpiBorderShare').textContent = topDistrict ? `${fmt.format(Math.round(topDistrict.observed_movements))} observed; top Uganda district` : '—';
    document.getElementById('kpiUganda').textContent = '15–24 May 2026';
    document.getElementById('kpiScenario').textContent = 'IOM DTM EVD FMP snapshot';
    document.getElementById('scenarioText').innerHTML = '<strong>Uganda border flow</strong><br>Observed movements at selected Uganda–DRC flow monitoring points during 15–24 May 2026. These data are indicative of key flows and do not represent all cross-border movement.';
    return;
  }

  if (mapMode === 'uganda_import') {
    const rows = ugandaImportationRowsForMonth(f.month);
    const base = selectedCaseWeightedBorderPressure(f.month);
    const top = rows[0];
    document.getElementById('kpiTotal').textContent = fmt.format(Math.round(base.pressure));
    document.getElementById('kpiKinshasa').textContent = top ? top.uganda_district : '—';
    document.getElementById('kpiKinshasaShare').textContent = top ? `${fmt.format(Math.round(top.importation_pressure))} score; top district` : '—';
    document.getElementById('kpiBorder').textContent = fmt.format(Math.round(base.movement));
    document.getElementById('kpiBorderShare').textContent = 'DRC border-proxy movement basis';
    document.getElementById('kpiUganda').textContent = originFilterText();
    document.getElementById('kpiScenario').textContent = 'DTM 2026 allocation × DRC case-weighted border pressure';
    document.getElementById('scenarioText').innerHTML = `<strong>Uganda importation pressure</strong><br>For the selected DRC origin set and month, case-weighted movement toward Uganda-border proxy zones is allocated to Uganda districts using the IOM DTM EVD flow monitoring destination profile from 15–24 May 2026. This is a prioritization score, not an observed infected-traveller count.`;
    return;
  }


  if (mapMode === 'rwi') {
    const rows = rwiRowsForChart();
    const vals = rows.map(r => r.rwi_percentile).filter(v => Number.isFinite(v));
    const affected = rows.filter(r => toNumber(r.cases) > 0);
    const median = vals.length ? quantileAll(vals, 0.5) : NaN;
    const top = affected.slice().sort((a,b)=>toNumber(b.cases)-toNumber(a.cases))[0];
    document.getElementById('kpiTotal').textContent = fmt.format(rows.length);
    document.getElementById('kpiKinshasa').textContent = Number.isFinite(median) ? median.toFixed(0) : '—';
    document.getElementById('kpiKinshasaShare').textContent = 'Median RWI percentile among health zones with RWI data';
    document.getElementById('kpiBorder').textContent = fmt.format(affected.length);
    document.getElementById('kpiBorderShare').textContent = `Health zones with reported cases on ${displayDateLabel(selectedCaseDate())}`;
    document.getElementById('kpiUganda').textContent = top ? top.zone_name : '—';
    document.getElementById('kpiScenario').textContent = top ? `${fmt.format(Math.round(top.cases))} cases; RWI percentile ${top.rwi_percentile.toFixed(0)}` : 'No affected health zone in selected date';
    document.getElementById('scenarioText').innerHTML = '<strong>Relative wealth percentile layer</strong><br>Original standardized RWI values are converted to within-DRC percentiles for display. The scatter plot is ecological and exploratory; it does not imply causality and is not adjusted for population mobility, surveillance intensity, healthcare access, or distance from outbreak origin.';
    return;
  }

  if (mapMode === 'cases') {
    const rows = caseRowsLatest();
    const totalCases = rows.reduce((a,b)=>a+toNumber(b.confirmed_cases),0);
    const totalDeaths = rows.reduce((a,b)=>a+toNumber(b.confirmed_deaths),0);
    const mapped = rows.filter(r => r.zone_id).length;
    const top = rows.slice().sort((a,b)=>toNumber(b.confirmed_cases)-toNumber(a.confirmed_cases))[0];
    document.getElementById('kpiTotal').textContent = fmt.format(Math.round(totalCases));
    document.getElementById('kpiKinshasa').textContent = fmt.format(Math.round(totalDeaths));
    document.getElementById('kpiKinshasaShare').textContent = `${pct(totalDeaths / Math.max(totalCases,1))} CFR among confirmed`;
    document.getElementById('kpiBorder').textContent = fmt.format(rows.filter(r => toNumber(r.confirmed_cases)>0 && r.zone_id).length);
    document.getElementById('kpiBorderShare').textContent = `${mapped} rows with mappable zone ID`;
    document.getElementById('kpiUganda').textContent = top ? top.health_zone : '—';
    document.getElementById('kpiScenario').textContent = top ? `${fmt.format(Math.round(top.confirmed_cases))} confirmed cases; source ${latestCaseDate()}` : 'No case data';
    document.getElementById('scenarioText').innerHTML = `<strong>Case-count layer</strong><br>Confirmed cases and deaths are taken from the selected SitRep reporting date. In recent-increase mode, values are differences from the closest available SitRep at least seven days earlier. Unventilated / unknown-health-zone cases are intentionally not shown on the case-bubble map because they cannot be assigned to a specific health zone.`;
    return;
  }

  if (mapMode === 'weighted' || mapMode === 'forecast' || mapMode === 'air' || mapMode === 'contact') {
    const forecast = mapMode === 'forecast';
    const isAir = mapMode === 'air';
    const isContact = mapMode === 'contact';
    const sourceRows = isAir ? airAdjustedRiskRowsForMonth(f.month, false) : (isContact ? contactAdjustedRiskRowsForMonth(f.month) : weightedRiskRowsForMonth(f.month, forecast));
    const metricKey = isAir ? 'air_adjusted' : (isContact ? 'contact_adjusted' : 'weighted');
    const rows = sourceRows.filter(r => toNumber(r[metricKey]) > 0);
    const totalScore = rows.reduce((a,b)=>a+toNumber(b[metricKey]),0);
    const totalIncoming = rows.reduce((a,b)=>a+toNumber(b.incoming),0);
    const top = rows.slice().sort((a,b)=>toNumber(b[metricKey])-toNumber(a[metricKey]))[0];
    const basis = top?.forecast_basis || (forecast ? forecastMobilityMonths(f.month).label : f.month);
    const suppressed = isAir ? rows.reduce((a,b)=>a+toNumber(b.suppressed_amount),0) : 0;
    document.getElementById('kpiTotal').textContent = fmt.format(Math.round(totalScore));
    document.getElementById('kpiKinshasa').textContent = isAir ? fmt.format(Math.round(suppressed)) : fmt.format(Math.round(totalIncoming));
    document.getElementById('kpiKinshasaShare').textContent = isAir ? 'Suppressed case-weighted score from air-plausible destinations' : (isContact ? 'Underlying monthly movement from selected origins' : (forecast ? `Forecast mobility basis: ${basis}` : `Movement basis: ${f.month}`));
    document.getElementById('kpiBorder').textContent = top ? top.zone_name : '—';
    document.getElementById('kpiBorderShare').textContent = top ? `${fmt.format(Math.round(top[metricKey]))} top score` : 'No risk score';
    document.getElementById('kpiUganda').textContent = latestCaseDate() || '—';
    document.getElementById('kpiScenario').textContent = isAir ? 'Case source date; air suppression scenario' : (isContact ? 'Case source date; contact follow-up adjustment' : 'Case source date');
    document.getElementById('scenarioText').innerHTML = isAir
      ? `<strong>Air-adjusted case-weighted risk</strong><br>Air-adjusted risk = case-weighted risk × air-travel suppression factor for long-distance, air-plausible destinations. The default scenario down-weights Kinshasa-bound risk to 25% of the pre-outbreak baseline, reflecting Bunia passenger-flight suspension and subsequent reopening under screening measures. This is a scenario-based prioritization indicator, not observed airline passenger OD and not transmission probability.`
      : (isContact
        ? `<strong>Contact-adjusted risk</strong><br>Origins: <strong>${originFilterText()}</strong>. Score = confirmed cases at selected origins × movement to destination × contact follow-up gap multiplier. Province-level multipliers use a 95% target and the contact-follow-up rates available for the selected SitRep date, falling back to the closest previous available SitRep when necessary. This is a prioritization indicator, not a transmission probability.`
        : `<strong>${forecast ? 'Forecast case-weighted risk' : 'Case-weighted spread risk'}</strong><br>Origins: <strong>${originFilterText()}</strong>. Score = Σ confirmed_cases(origin health zone) × estimated movement(origin→destination). ${forecast ? 'Forecast uses ' + basis + ' as the mobility basis.' : 'Weighted risk uses the selected month mobility matrix.'} This score is for prioritization and should not be interpreted as a probability of transmission.`);
    return;
  }

  if (mapMode === 'uganda_border') {
    const rows = ugandaObservedDistrictRows().slice(0, f.topN).reverse();
    Plotly.newPlot('barChart', [{ type:'bar', orientation:'h', x: rows.map(d=>d.observed_movements), y: rows.map(d=>d.uganda_district), hovertemplate:'%{y}<br>Observed movements: %{x:,.0f}<extra></extra>' }], { margin:{l:145,r:20,t:18,b:40}, xaxis:{title:'Observed movements, 15–24 May 2026', gridcolor:'#e7eef7'}, yaxis:{automargin:true}, paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)'}, {responsive:true, displayModeBar:false});
    return;
  }

  if (mapMode === 'uganda_import') {
    const rows = ugandaImportationRowsForMonth(f.month).filter(r=>r.importation_pressure>0).slice(0, f.topN).reverse();
    Plotly.newPlot('barChart', [{ type:'bar', orientation:'h', x: rows.map(d=>d.importation_pressure), y: rows.map(d=>d.uganda_district), hovertemplate:'%{y}<br>Importation-pressure score: %{x:,.0f}<extra></extra>' }], { margin:{l:145,r:20,t:18,b:40}, xaxis:{title:'Uganda importation-pressure score', gridcolor:'#e7eef7'}, yaxis:{automargin:true}, paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)'}, {responsive:true, displayModeBar:false});
    return;
  }

  if (mapMode === 'uganda') {
    const rows = ugandaProjectionRows(f.month);
    const border = rows[0]?.border_pressure || 0;
    const totalProjected = rows.reduce((a,b)=>a+toNumber(b.projected),0);
    const kampala = rows.find(r => r.uganda_id === 'UGA_KAMPALA')?.projected || 0;
    const top = rows[0];
    document.getElementById('kpiTotal').textContent = fmt.format(Math.round(border));
    document.getElementById('kpiKinshasa').textContent = top ? top.uganda_name : '—';
    document.getElementById('kpiKinshasaShare').textContent = top ? `${fmt.format(Math.round(top.projected))} projected; top Uganda destination` : 'No projection';
    document.getElementById('kpiBorder').textContent = fmt.format(Math.round(totalProjected));
    document.getElementById('kpiBorderShare').textContent = `${f.scenario.scenario_name}; ${pct(Number(f.scenario.cross_border_fraction || 0))} crossing assumption`;
    document.getElementById('kpiUganda').textContent = fmt.format(Math.round(kampala));
    document.getElementById('kpiScenario').textContent = 'Projected Kampala component';
    document.getElementById('scenarioText').innerHTML = `
      <strong>Uganda projection: scenario-based estimate</strong><br>
      This layer estimates possible Uganda-side destinations by combining DRC-side movement toward Uganda-border proxy health zones with a historical IOM DTM Uganda–DRC border FMP destination profile from Jan–Mar 2020. It should be interpreted as <strong>projected movement pressure</strong>, not observed 2026 cross-border movement and not Ebola transmission probability.<br><br>
      For the selected month, DRC-side border-proxy movement is <strong>${fmt.format(Math.round(border))}</strong>. Under <strong>${f.scenario.scenario_name}</strong>, the projected Uganda-side total is <strong>${fmt.format(Math.round(totalProjected))}</strong>.
    `;
    return;
  }

  if (mapMode === 'risk') {
    const rows = riskRowsForMonth(f.month).filter(r => r.incoming > 0);
    const totalIncoming = rows.reduce((a, b) => a + toNumber(b.incoming), 0);
    const kin = rows.filter(r => r.is_kinshasa === 1 || r.category === 'kinshasa').reduce((a, b) => a + toNumber(b.incoming), 0);
    const border = rows.filter(r => r.is_uganda_border === 1 || r.category === 'uganda_border').reduce((a, b) => a + toNumber(b.incoming), 0);
    const top = rows.slice().sort((a,b)=>toNumber(b.risk)-toNumber(a.risk))[0];
    const uganda = Math.round(border * Number(f.scenario.cross_border_fraction || 0));
    document.getElementById('kpiTotal').textContent = fmt.format(Math.round(totalIncoming));
    document.getElementById('kpiKinshasa').textContent = fmt.format(Math.round(kin));
    document.getElementById('kpiKinshasaShare').textContent = `${pct(kin / Math.max(totalIncoming, 1))} of mobility pressure`;
    document.getElementById('kpiBorder').textContent = fmt.format(Math.round(border));
    document.getElementById('kpiBorderShare').textContent = `${pct(border / Math.max(totalIncoming, 1))} of mobility pressure`;
    document.getElementById('kpiUganda').textContent = top ? `${top.zone_name}` : '—';
    document.getElementById('kpiScenario').textContent = top ? `${fmt.format(Math.round(top.incoming))} arrivals; highest inflow` : 'No incoming movement';
    document.getElementById('scenarioText').innerHTML = `
      <strong>Mobility-based spread-risk layer</strong><br>
      Risk index = estimated monthly arrivals from selected outbreak health zone(s) to each destination health zone. The index is not divided by destination population. This is a relative mobility-pressure indicator for surveillance and preparedness, not an Ebola transmission probability. Uganda crossing remains scenario-based: ${fmt.format(uganda)} onward movements under the selected scenario.
    `;
    return;
  }

  if (mapMode === 'population' || mapMode === 'density') {
    const popRows = enrichPopulationRows(selectedPopulationRows()).filter(r => r.population > 0);
    const totalPop = popRows.reduce((a, b) => a + b.population, 0);
    const outbreakIds = new Set(origins.map(o => o.zone_id));
    const outbreakPop = popRows.filter(r => outbreakIds.has(r.zone_id)).reduce((a, b) => a + b.population, 0);
    const kinPop = popRows.filter(r => r.is_kinshasa === 1 || r.category === 'kinshasa').reduce((a, b) => a + b.population, 0);
    const borderPop = popRows.filter(r => r.is_uganda_border === 1 || r.category === 'uganda_border').reduce((a, b) => a + b.population, 0);

    document.getElementById('kpiTotal').textContent = totalPop ? fmt.format(Math.round(totalPop)) : '—';
    document.getElementById('kpiKinshasa').textContent = kinPop ? fmt.format(Math.round(kinPop)) : '—';
    document.getElementById('kpiKinshasaShare').textContent = totalPop ? `${pct(kinPop / Math.max(totalPop, 1))} of displayed population` : 'Population file not loaded';
    document.getElementById('kpiBorder').textContent = borderPop ? fmt.format(Math.round(borderPop)) : '—';
    document.getElementById('kpiBorderShare').textContent = totalPop ? `${pct(borderPop / Math.max(totalPop, 1))} of displayed population` : 'Population file not loaded';
    document.getElementById('kpiUganda').textContent = outbreakPop ? fmt.format(Math.round(outbreakPop)) : '—';
    document.getElementById('kpiScenario').textContent = totalPop ? (mapMode === 'density' ? 'Outbreak-zone population; density map' : 'Population in outbreak zones') : 'Add data/population_by_hz.csv';
    document.getElementById('scenarioText').innerHTML = totalPop ? `
      <strong>${mapMode === 'density' ? 'Population density layer' : 'Population layer'}</strong><br>
      ${mapMode === 'density'
        ? 'This layer colors health-zone polygons by estimated population density. It requires data/health_zones.geojson so that polygon area can be calculated. If boundaries are absent, the dashboard falls back to population bubbles.'
        : 'This layer displays estimated health-zone population for the selected month. With data/health_zones.geojson, health zones are colored as polygons; otherwise the dashboard uses proportional bubbles.'}
    ` : `
      <strong>Population data not loaded</strong><br>
      The uploaded relocation file contains health-zone-to-health-zone movement estimates, but not resident population estimates. Add a Flowminder population extract as <code>data/population_by_hz.csv</code> with columns <code>month, zone_id, zone_name, province, lat, lon, population</code> to activate this layer.
    `;
    return;
  }

  const total = destRows.reduce((a, b) => a + b.movement, 0);
  const kinshasa = destRows.filter(d => d.is_kinshasa === 1).reduce((a, b) => a + b.movement, 0);
  const border = destRows.filter(d => d.is_uganda_border === 1).reduce((a, b) => a + b.movement, 0);
  const uganda = Math.round(border * Number(f.scenario.cross_border_fraction || 0));

  document.getElementById('kpiTotal').textContent = fmt.format(total);
  document.getElementById('kpiKinshasa').textContent = fmt.format(kinshasa);
  document.getElementById('kpiKinshasaShare').textContent = `${pct(kinshasa / Math.max(total, 1))} of outbound movement`;
  document.getElementById('kpiBorder').textContent = fmt.format(border);
  document.getElementById('kpiBorderShare').textContent = `${pct(border / Math.max(total, 1))} of outbound movement`;
  document.getElementById('kpiUganda').textContent = fmt.format(uganda);
  document.getElementById('kpiScenario').textContent = `${f.scenario.scenario_name}; proxy estimate`;

  document.getElementById('scenarioText').innerHTML = `
    <strong>${f.scenario.scenario_name}</strong><br>
    ${f.scenario.description}<br><br>
    For the selected month, movement toward Uganda-border proxy zones is <strong>${fmt.format(border)}</strong>. Under this scenario, estimated onward movement into Uganda is <strong>${fmt.format(uganda)}</strong>. This is not observed cross-border movement; it is a scenario-based proxy until UNHCR, IOM DTM, or border-monitoring data are added.
  `;
}

function updatePopulationMap() {
  if (choroLegend) { map.removeControl(choroLegend); choroLegend = null; }
  layerGroup.clearLayers();
  const f = currentFilters();
  const notice = document.getElementById('populationNotice');
  const metric = mapMode === 'density' ? 'density' : 'population';

  document.getElementById('mapTitle').textContent = metric === 'density' ? 'Population density map' : 'Population map';
  document.getElementById('mapDescription').textContent = metric === 'density'
    ? 'Health-zone polygons are colored by estimated population density for the selected month. Outbreak health zones are outlined in red.'
    : 'Health-zone polygons are colored by estimated population for the selected month when boundaries are available. If boundaries are absent, bubbles are used.';
  document.getElementById('rankingTitle').textContent = metric === 'density' ? 'Population density ranking' : 'Population ranking';
  document.getElementById('rankingDescription').textContent = metric === 'density'
    ? 'Top health zones by estimated population density for the selected month.'
    : 'Top health zones by estimated population for the selected month.';

  if (hasBoundaries() && hasPopulationData()) {
    const metricRows = boundaryMetricRows(f.month, metric);
    const values = metricRows.map(r => r.value).filter(v => Number.isFinite(v) && v > 0);
    const breaks = [0.2, 0.4, 0.6, 0.8].map(q => quantile(values, q));
    const byFeature = new Map(metricRows.map(r => [r.feature, r]));

    notice.style.display = 'block';
    notice.innerHTML = metric === 'density'
      ? 'Polygon layer: color shows estimated population density calculated as population divided by health-zone polygon area.'
      : 'Polygon layer: color shows estimated health-zone population. Use Density to normalize by polygon area.';

    L.geoJSON(healthZoneBoundaries, {
      style: feature => {
        const r = byFeature.get(feature) || { value: 0, is_outbreak: false };
        const fill = choroplethColor(r.value, breaks);
        return {
          color: r.is_outbreak ? '#7a271a' : '#ffffff',
          weight: r.is_outbreak ? 3 : 0.6,
          fillColor: fill,
          fillOpacity: r.value > 0 ? 0.72 : 0.18,
          opacity: 1
        };
      },
      onEachFeature: (feature, layer) => {
        const r = byFeature.get(feature) || {};
        const valueLabel = metric === 'density'
          ? `${fmt.format(Math.round(toNumber(r.density)))} people/km²`
          : `${fmt.format(Math.round(toNumber(r.population)))} people`;
        layer.bindPopup(`<strong>${r.zone_name || featureZoneName(feature) || 'Health zone'}</strong><br>${r.province || featureProvince(feature) || ''}<br>${metric === 'density' ? 'Density' : 'Population'}: ${valueLabel}<br>Area: ${r.area_km2 ? fmt.format(Math.round(r.area_km2)) + ' km²' : '—'}<br>${f.month}`);
      }
    }).addTo(layerGroup);
    addBoundaryLegend(metric, breaks);

    // Outbreak labels stay visible above polygons.
    origins.forEach(o => {
      const latlng = [toNumber(o.lat), toNumber(o.lon)];
      if (!Number.isFinite(latlng[0]) || !Number.isFinite(latlng[1])) return;
      L.circleMarker(latlng, { radius: 7, color: '#7a271a', weight: 2, fillColor: '#d92d20', fillOpacity: 0.95 })
        .bindPopup(`<strong>${o.zone_name}</strong><br>${o.province}<br>Current outbreak health zone`).addTo(layerGroup);
      L.marker(latlng, { icon: L.divIcon({ className: 'origin-label', html: `<span>${o.zone_name}</span>`, iconSize: [100, 22], iconAnchor: [-8, 28] }), interactive: false }).addTo(layerGroup);
    });
    return;
  }

  // Fallback: bubble map if polygon boundaries have not yet been added.
  const rows = enrichPopulationRows(selectedPopulationRows()).filter(r => r.population > 0);
  const maxPop = Math.max(...rows.map(r => r.population), 1);
  if (!rows.length) {
    notice.style.display = 'block';
    notice.innerHTML = 'Population data are not available. Add <code>data/population_by_hz.csv</code>. For polygon choropleths, also add <code>data/health_zones.geojson</code>.';
    return;
  }
  notice.style.display = 'block';
  notice.innerHTML = metric === 'density'
    ? 'Density requires health-zone boundary polygons to calculate area. Add <code>data/health_zones.geojson</code>. Showing population bubbles instead.'
    : 'Boundary polygons are not loaded. Showing population bubbles. Add <code>data/health_zones.geojson</code> for a choropleth map.';
  rows.forEach(r => {
    const isOutbreak = origins.some(o => o.zone_id === r.zone_id);
    const radius = 4 + 24 * Math.sqrt(r.population / maxPop);
    let color = '#667085';
    if (r.is_kinshasa === 1 || r.category === 'kinshasa') color = '#1f5d8c';
    if (r.is_uganda_border === 1 || r.category === 'uganda_border') color = '#b54708';
    if (isOutbreak) color = '#d92d20';
    L.circleMarker([r.lat, r.lon], { radius, color: isOutbreak ? '#7a271a' : color, weight: isOutbreak ? 3 : 1.5, fillColor: color, fillOpacity: isOutbreak ? 0.82 : 0.44 })
      .bindPopup(`<strong>${r.zone_name}</strong><br>${r.province}<br>Estimated population: ${fmt.format(Math.round(r.population))}<br>${f.month}`).addTo(layerGroup);
  });
}


function updateRiskMap() {
  if (choroLegend) { map.removeControl(choroLegend); choroLegend = null; }
  layerGroup.clearLayers();
  const f = currentFilters();
  const notice = document.getElementById('populationNotice');
  const selectedOrigins = affectedOriginRows().filter(o => selectedOriginIds().has(String(o.zone_id)));
  const riskRows = riskRowsForMonth(f.month).filter(r => r.incoming > 0 || r.is_outbreak);
  const values = riskRows.map(r => r.risk).filter(v => Number.isFinite(v) && v > 0);
  const breaks = [0.2, 0.4, 0.6, 0.8].map(q => quantile(values, q));

  document.getElementById('mapTitle').textContent = 'Mobility-based Ebola spread risk';
  document.getElementById('mapDescription').textContent = 'Health zones are colored by estimated monthly arrivals from selected outbreak health zone(s). This is a mobility-pressure indicator, not a predicted probability of transmission.';
  document.getElementById('rankingTitle').textContent = 'Spread-risk ranking';
  document.getElementById('rankingDescription').textContent = 'Top destination health zones by mobility-based spread pressure for the selected month.';
  notice.style.display = 'block';

  if (hasBoundaries()) {
    const byId = new Map(riskRows.map(r => [String(r.zone_id), r]));
    const byName = new Map(riskRows.map(r => [normalizedString(r.zone_name), r]));
    notice.innerHTML = 'Risk layer: color shows estimated incoming movement from selected outbreak zone(s). Values are not normalized by population. Red outlines indicate current outbreak health zones.';
    L.geoJSON(healthZoneBoundaries, {
      style: feature => {
        const id = featureZoneId(feature);
        const name = featureZoneName(feature);
        const r = byId.get(String(id)) || byName.get(normalizedString(name)) || { risk: 0, is_outbreak: false };
        return {
          color: r.is_outbreak ? '#7a271a' : '#ffffff',
          weight: r.is_outbreak ? 3 : 0.6,
          fillColor: riskColor(toNumber(r.risk), breaks),
          fillOpacity: toNumber(r.risk) > 0 ? 0.74 : 0.12,
          opacity: 1
        };
      },
      onEachFeature: (feature, layer) => {
        const id = featureZoneId(feature);
        const name = featureZoneName(feature);
        const r = byId.get(String(id)) || byName.get(normalizedString(name)) || {};
        layer.bindPopup(`<strong>${r.zone_name || name || 'Health zone'}</strong><br>${r.province || featureProvince(feature) || ''}<br>Incoming from outbreak zone(s): ${fmt.format(Math.round(toNumber(r.incoming)))}<br>Population: ${r.population ? fmt.format(Math.round(r.population)) : '—'}<br>Spread-risk inflow: ${fmt.format(Math.round(toNumber(r.risk)))} estimated arrivals<br>${f.month}`);
      }
    }).addTo(layerGroup);
    addRiskLegend(breaks);
  } else {
    notice.innerHTML = 'Boundary polygons are not loaded. Showing risk bubbles. Add <code>data/health_zones.geojson</code> for health-zone choropleth risk polygons.';
    const maxRisk = Math.max(...riskRows.map(r => toNumber(r.risk)), 1);
    riskRows.filter(r => Number.isFinite(r.lat) && Number.isFinite(r.lon) && r.incoming > 0).forEach(r => {
      const radius = 5 + 24 * Math.sqrt(toNumber(r.risk) / maxRisk);
      L.circleMarker([r.lat, r.lon], { radius, color: '#7a271a', weight: 1.5, fillColor: '#d92d20', fillOpacity: 0.54 })
        .bindPopup(`<strong>${r.zone_name}</strong><br>${r.province}<br>Incoming from outbreak zone(s): ${fmt.format(Math.round(r.incoming))}<br>Population: ${r.population ? fmt.format(Math.round(r.population)) : '—'}<br>Spread-risk inflow: ${fmt.format(Math.round(r.risk))} estimated arrivals<br>${f.month}`).addTo(layerGroup);
    });
  }

  // In Spread risk mode, show the choropleth only; do not overlay movement arrows.

  selectedOrigins.forEach(o => {
    const latlng = [toNumber(o.lat), toNumber(o.lon)];
    if (!Number.isFinite(latlng[0]) || !Number.isFinite(latlng[1])) return;
    L.circleMarker(latlng, { radius: 18, color: '#7a271a', weight: 2, fillColor: '#f04438', fillOpacity: 0.18 }).addTo(layerGroup);
    L.circleMarker(latlng, { radius: 8, color: '#7a271a', weight: 2, fillColor: '#d92d20', fillOpacity: 0.95 })
      .bindPopup(`<strong>${o.zone_name}</strong><br>${o.province}<br>Current outbreak health zone`).addTo(layerGroup);
    L.marker(latlng, { icon: L.divIcon({ className: 'origin-label', html: `<span>${o.zone_name}</span>`, iconSize: [100, 22], iconAnchor: [-8, 28] }), interactive: false }).addTo(layerGroup);
  });
}

function updateMap(destRows) {
  if (choroLegend) { map.removeControl(choroLegend); choroLegend = null; }
  layerGroup.clearLayers();
  document.getElementById('populationNotice').style.display = 'none';
  document.getElementById('mapTitle').textContent = 'Flow map';
  document.getElementById('mapDescription').textContent = 'Directed lines show monthly movement from outbreak health zones. Outbreak origins are highlighted in red, Kinshasa destinations in blue, and Uganda-border proxy zones in orange.';
  document.getElementById('rankingTitle').textContent = 'Destination ranking';
  document.getElementById('rankingDescription').textContent = 'Top destination health zones by estimated monthly movement.';
  const f = currentFilters();
  const selectedOrigins = affectedOriginRows().filter(o => selectedOriginIds().has(String(o.zone_id)));
  const rowsForMonth = selectedFlows();
  const maxMove = Math.max(...destRows.map(d => toNumber(d.movement)), 1);

  // First draw the local top-N flows lightly. Strategic Kinshasa and Uganda-border corridors
  // are drawn afterward, so they remain visible even when their component HZs are not top-N.
  destRows.slice(0, f.topN).forEach(d => {
    const color = destinationColor(d);
    selectedOrigins.forEach(o => {
      const row = flows.find(r => r.month === f.month && r.origin_id === o.zone_id && r.destination_id === d.zone_id);
      if (!row) return;
      const movement = toNumber(row.movement);
      if (movement <= 0) return;
      const from = [toNumber(o.lat), toNumber(o.lon)];
      const to = [toNumber(d.lat), toNumber(d.lon)];
      const weight = 1 + 6 * Math.sqrt(movement / maxMove);
      const opacity = d.is_kinshasa === 1 || d.is_uganda_border === 1 ? 0.48 : 0.22;
      L.polyline([from, to], {
        color,
        weight,
        opacity,
        smoothFactor: 1,
        dashArray: d.is_kinshasa === 1 || d.is_uganda_border === 1 ? null : '5 7'
      }).bindPopup(`${o.zone_name} → ${d.zone_name}<br>${fmt.format(Math.round(movement))} movements<br>${f.month}`).addTo(layerGroup);
      if (d.is_kinshasa === 1 || d.is_uganda_border === 1) addArrow(from, to, color, movement, { size: 20 });
    });
  });

  drawStrategicCorridors(rowsForMonth);

  // Destination bubbles.
  destRows.slice(0, f.topN).forEach(d => {
    const radius = 5 + 17 * Math.sqrt(toNumber(d.movement) / maxMove);
    const color = destinationColor(d);
    L.circleMarker([toNumber(d.lat), toNumber(d.lon)], {
      radius,
      color,
      weight: 2,
      fillColor: color,
      fillOpacity: d.is_kinshasa === 1 || d.is_uganda_border === 1 ? 0.72 : 0.50
    }).bindPopup(`<strong>${d.zone_name}</strong><br>${d.province}<br>${d.category}<br>Movement: ${fmt.format(Math.round(d.movement))}<br>${f.month}`).addTo(layerGroup);
  });

  // Outbreak origins with a clear halo and label.
  selectedOrigins.forEach(o => {
    const latlng = [toNumber(o.lat), toNumber(o.lon)];
    L.circleMarker(latlng, {
      radius: 18,
      color: '#7a271a',
      weight: 2,
      fillColor: '#f04438',
      fillOpacity: 0.18
    }).addTo(layerGroup);
    L.circleMarker(latlng, {
      radius: 8,
      color: '#7a271a',
      weight: 2,
      fillColor: '#d92d20',
      fillOpacity: 0.95
    }).bindPopup(`<strong>${o.zone_name}</strong><br>${o.province}<br>Current outbreak health zone`).addTo(layerGroup);
    L.marker(latlng, {
      icon: L.divIcon({
        className: 'origin-label',
        html: `<span>${o.zone_name}</span>`,
        iconSize: [100, 22],
        iconAnchor: [-8, 28]
      }),
      interactive: false
    }).addTo(layerGroup);
  });
}
function fitMapToData() {
  const layers = [];
  layerGroup.eachLayer(l => layers.push(l));
  const group = L.featureGroup(layers);
  if (layers.length) map.fitBounds(group.getBounds().pad(0.16));
}

function updateBarChart(destRows) {
  const f = currentFilters();

  if (mapMode === 'cases') {
    const rows = caseRowsLatest().filter(r => toNumber(r.confirmed_cases)>0).sort((a,b)=>toNumber(b.confirmed_cases)-toNumber(a.confirmed_cases)).slice(0, f.topN).reverse();
    Plotly.newPlot('barChart', [{ type:'bar', orientation:'h', x: rows.map(d=>d.confirmed_cases), y: rows.map(d=>`${d.health_zone} (${d.province})`), hovertemplate: '%{y}<br>Confirmed cases: %{x:,.0f}<extra></extra>' }], { margin:{l:155,r:20,t:18,b:40}, xaxis:{title:'Confirmed cases', gridcolor:'#e7eef7'}, yaxis:{automargin:true}, paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)'}, {responsive:true, displayModeBar:false});
    return;
  }

  if (mapMode === 'weighted' || mapMode === 'forecast' || mapMode === 'air' || mapMode === 'contact') {
    const forecast = mapMode === 'forecast';
    const isAir = mapMode === 'air';
    const isContact = mapMode === 'contact';
    const sourceRows = isAir ? airAdjustedRiskRowsForMonth(f.month, false) : (isContact ? contactAdjustedRiskRowsForMonth(f.month) : weightedRiskRowsForMonth(f.month, forecast));
    const metricKey = isAir ? 'air_adjusted' : (isContact ? 'contact_adjusted' : 'weighted');
    const rows = sourceRows.filter(r => toNumber(r[metricKey])>0).sort((a,b)=>toNumber(b[metricKey])-toNumber(a[metricKey])).slice(0, f.topN).reverse();
    const label = isAir ? 'Air-adjusted score' : (isContact ? 'Contact-adjusted score' : 'Case-weighted score');
    const xTitle = isAir ? 'Air-adjusted case-weighted score' : (isContact ? 'Contact-adjusted case-weighted score' : (forecast ? 'Forecast case-weighted movement score' : 'Case-weighted movement score'));
    Plotly.newPlot('barChart', [{ type:'bar', orientation:'h', x: rows.map(d=>d[metricKey]), y: rows.map(d=>`${d.zone_name} (${d.province})`), hovertemplate: `%{y}<br>${label}: %{x:,.0f}<extra></extra>` }], { margin:{l:155,r:20,t:18,b:40}, xaxis:{title: xTitle, gridcolor:'#e7eef7'}, yaxis:{automargin:true}, paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)', annotations: rows.length ? [] : [{text:'No risk score for selected settings', x:0.5, y:0.5, xref:'paper', yref:'paper', showarrow:false, font:{size:14, color:'#667085'}}]}, {responsive:true, displayModeBar:false});
    return;
  }


  if (['response_contact_gap','response_intensity'].includes(mapMode)) {
    const metric = responseLayerMetricForMode();
    const provs = [...new Set((healthZoneBoundaries?.features || []).map(featureProvince).filter(Boolean))];
    const rows = provs.map(province => ({ province, value: responseMetricValue(metric, province) })).filter(r => Number.isFinite(r.value)).sort((a,b)=>b.value-a.value).reverse();
    Plotly.newPlot('barChart', [{ type:'bar', orientation:'h', x: rows.map(d=>['contact_followup_rate','contact_gap','alert_investigation_rate','poe_screening_coverage','response_intensity'].includes(metric) ? d.value*100 : d.value), y: rows.map(d=>d.province), hovertemplate: '%{y}<br>%{x:.1f}%<extra></extra>' }], { margin:{l:110,r:20,t:18,b:46}, xaxis:{title: responseMetricLabel(metric), ticksuffix: ['contact_followup_rate','contact_gap','alert_investigation_rate','poe_screening_coverage','response_intensity'].includes(metric) ? '%' : '', gridcolor:'#e7eef7'}, yaxis:{automargin:true}, paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)'}, {responsive:true, displayModeBar:false});
    return;
  }

  if (mapMode === 'rwi') {
    const rows = healthZoneRwi.slice().filter(r => Number.isFinite(rwiValue(r))).sort((a,b)=>rwiValue(b)-rwiValue(a)).slice(0, f.topN).reverse();
    Plotly.newPlot('barChart', [{ type:'bar', orientation:'h', x: rows.map(d=>rwiValue(d)), y: rows.map(d=>`${d.zone_name} (${d.province})`), customdata: rows.map(d=>rwiMetric(d)), hovertemplate:'%{y}<br>RWI percentile: %{x:.0f}<br>Original median RWI: %{customdata:.3f}<extra></extra>' }], { margin:{l:155,r:20,t:18,b:40}, xaxis:{title:'Relative wealth percentile within DRC', range:[0,100], gridcolor:'#e7eef7'}, yaxis:{automargin:true}, paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)'}, {responsive:true, displayModeBar:false});
    return;
  }

  if (mapMode === 'uganda_border') {
    const rows = ugandaObservedDistrictRows().slice(0, f.topN).reverse();
    Plotly.newPlot('barChart', [{ type:'bar', orientation:'h', x: rows.map(d=>d.observed_movements), y: rows.map(d=>d.uganda_district), hovertemplate:'%{y}<br>Observed movements: %{x:,.0f}<extra></extra>' }], { margin:{l:145,r:20,t:18,b:40}, xaxis:{title:'Observed movements, 15–24 May 2026', gridcolor:'#e7eef7'}, yaxis:{automargin:true}, paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)'}, {responsive:true, displayModeBar:false});
    return;
  }

  if (mapMode === 'uganda_import') {
    const rows = ugandaImportationRowsForMonth(f.month).filter(r=>r.importation_pressure>0).slice(0, f.topN).reverse();
    Plotly.newPlot('barChart', [{ type:'bar', orientation:'h', x: rows.map(d=>d.importation_pressure), y: rows.map(d=>d.uganda_district), hovertemplate:'%{y}<br>Importation-pressure score: %{x:,.0f}<extra></extra>' }], { margin:{l:145,r:20,t:18,b:40}, xaxis:{title:'Uganda importation-pressure score', gridcolor:'#e7eef7'}, yaxis:{automargin:true}, paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)'}, {responsive:true, displayModeBar:false});
    return;
  }

  if (mapMode === 'uganda') {
    const rows = ugandaProjectionRows(f.month).filter(r => r.projected > 0).slice(0, f.topN).reverse();
    Plotly.newPlot('barChart', [{
      type: 'bar', orientation: 'h',
      x: rows.map(d => d.projected), y: rows.map(d => `${d.uganda_name} (${d.district})`),
      hovertemplate: '%{y}<br>Projected movements: %{x:,.0f}<extra></extra>'
    }], {
      margin: { l: 150, r: 20, t: 18, b: 40 },
      xaxis: { title: 'Scenario-projected Uganda-side movement', gridcolor: '#e7eef7' },
      yaxis: { automargin: true },
      paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
      annotations: rows.length ? [] : [{ text: 'No Uganda-side projection for selected month/scenario', x: 0.5, y: 0.5, xref: 'paper', yref: 'paper', showarrow: false, font: { size: 14, color: '#667085' } }]
    }, { responsive: true, displayModeBar: false });
    return;
  }

  if (mapMode === 'risk') {
    const rows = riskRowsForMonth(f.month).filter(r => r.incoming > 0)
      .sort((a, b) => toNumber(b.risk) - toNumber(a.risk)).slice(0, f.topN).reverse();
    Plotly.newPlot('barChart', [{
      type: 'bar', orientation: 'h', x: rows.map(d => d.risk), y: rows.map(d => `${d.zone_name} (${d.province})`),
      hovertemplate: '%{y}<br>Estimated arrivals from outbreak zones: %{x:,.0f}<extra></extra>'
    }], {
      margin: { l: 145, r: 20, t: 18, b: 40 },
      xaxis: { title: 'Estimated monthly arrivals from outbreak zones', gridcolor: '#e7eef7' },
      yaxis: { automargin: true },
      paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
      annotations: rows.length ? [] : [{ text: 'No incoming movement from selected outbreak zone(s)', x: 0.5, y: 0.5, xref: 'paper', yref: 'paper', showarrow: false, font: { size: 14, color: '#667085' } }]
    }, { responsive: true, displayModeBar: false });
    return;
  }

  if (mapMode === 'population' || mapMode === 'density') {
    let rows = [];
    if (mapMode === 'density' && hasBoundaries()) {
      rows = boundaryMetricRows(f.month, 'density').filter(r => r.density > 0)
        .sort((a, b) => b.density - a.density).slice(0, f.topN).reverse();
      Plotly.newPlot('barChart', [{
        type: 'bar', orientation: 'h', x: rows.map(d => d.density), y: rows.map(d => `${d.zone_name} (${d.province})`),
        hovertemplate: '%{y}<br>Density: %{x:,.0f} people/km²<extra></extra>'
      }], {
        margin: { l: 145, r: 20, t: 18, b: 40 },
        xaxis: { title: 'Estimated population density (people/km²)', gridcolor: '#e7eef7' },
        yaxis: { automargin: true },
        paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
        annotations: rows.length ? [] : [{ text: 'Add data/health_zones.geojson to show density ranking', x: 0.5, y: 0.5, xref: 'paper', yref: 'paper', showarrow: false, font: { size: 14, color: '#667085' } }]
      }, { responsive: true, displayModeBar: false });
      return;
    }

    rows = enrichPopulationRows(selectedPopulationRows()).filter(r => r.population > 0)
      .sort((a, b) => b.population - a.population).slice(0, f.topN).reverse();
    Plotly.newPlot('barChart', [{
      type: 'bar', orientation: 'h', x: rows.map(d => d.population), y: rows.map(d => `${d.zone_name} (${d.province})`),
      hovertemplate: '%{y}<br>Population: %{x:,}<extra></extra>'
    }], {
      margin: { l: 145, r: 20, t: 18, b: 40 },
      xaxis: { title: 'Estimated population', gridcolor: '#e7eef7' },
      yaxis: { automargin: true },
      paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
      annotations: rows.length ? [] : [{ text: 'Add data/population_by_hz.csv to show population ranking', x: 0.5, y: 0.5, xref: 'paper', yref: 'paper', showarrow: false, font: { size: 14, color: '#667085' } }]
    }, { responsive: true, displayModeBar: false });
    return;
  }

  const rows = destRows.slice(0, f.topN).reverse();
  const labels = rows.map(d => `${d.zone_name} (${d.province})`);
  const values = rows.map(d => d.movement);
  Plotly.newPlot('barChart', [{
    type: 'bar', orientation: 'h', x: values, y: labels,
    hovertemplate: '%{y}<br>Movement: %{x:,}<extra></extra>'
  }], {
    margin: { l: 145, r: 20, t: 18, b: 40 },
    xaxis: { title: 'Estimated monthly movement', gridcolor: '#e7eef7' },
    yaxis: { automargin: true },
    paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)'
  }, { responsive: true, displayModeBar: false });
}

function updateTrendChart() {
  const el = document.getElementById('trendChart');
  if (!el) return;
  const f = currentFilters();


  if (mapMode === 'rwi') {
    const rows = rwiRowsForChart().filter(r => r.cases > 0).sort((a,b)=>b.cases-a.cases).slice(0, 12).reverse();
    Plotly.newPlot('trendChart', [{ type:'bar', orientation:'h', x: rows.map(d=>d.cases), y: rows.map(d=>`${d.zone_name} (${d.province})`), hovertemplate:'%{y}<br>Cases: %{x:,.0f}<br>RWI percentile: %{customdata:.0f}<extra></extra>', customdata: rows.map(d=>d.rwi_percentile) }], { margin:{l:155,r:20,t:18,b:46}, xaxis:{title:'Confirmed cases at selected reporting date', gridcolor:'#e7eef7'}, yaxis:{automargin:true}, paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)'}, {responsive:true, displayModeBar:false});
    return;
  }

  if (mapMode === 'cases') {
    const rows = caseRowsLatest().filter(r => toNumber(r.confirmed_cases)>0).sort((a,b)=>toNumber(b.confirmed_cases)-toNumber(a.confirmed_cases)).slice(0, 12).reverse();
    Plotly.newPlot('trendChart', [{ type:'bar', orientation:'h', name:'Confirmed cases', x: rows.map(r=>r.confirmed_cases), y: rows.map(r=>`${r.health_zone} (${r.province})`), hovertemplate:'%{y}<br>Cases: %{x:,.0f}<extra></extra>'}], { margin:{l:155,r:20,t:18,b:40}, xaxis:{title:'Confirmed cases', gridcolor:'#e7eef7'}, yaxis:{automargin:true}, paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)'}, {responsive:true, displayModeBar:false});
    return;
  }

  if (mapMode === 'weighted' || mapMode === 'forecast' || mapMode === 'air' || mapMode === 'contact') {
    const forecast = mapMode === 'forecast';
    const isAir = mapMode === 'air';
    const isContact = mapMode === 'contact';
    const months = [...new Set(flows.map(d => d.month))].sort();
    const rows = months.map(month => {
      const sourceRows = isAir ? airAdjustedRiskRowsForMonth(month, false) : (isContact ? contactAdjustedRiskRowsForMonth(month) : weightedRiskRowsForMonth(month, forecast));
      const valueKey = isAir ? 'air_adjusted' : (isContact ? 'contact_adjusted' : 'weighted');
      const rr = sourceRows.filter(r=>toNumber(r[valueKey])>0);
      return { month, total: rr.reduce((a,b)=>a+toNumber(b[valueKey]),0), top: rr.length ? Math.max(...rr.map(r=>toNumber(r[valueKey]))) : 0 };
    });
    Plotly.newPlot('trendChart', [
      { type:'scatter', mode:'lines+markers', name: isAir ? 'Total air-adjusted score' : (isContact ? 'Total contact-adjusted score' : 'Total weighted score'), x: rows.map(r=>r.month), y: rows.map(r=>r.total), hovertemplate:'%{x}<br>Total score: %{y:,.0f}<extra></extra>'},
      { type:'scatter', mode:'lines+markers', name:'Top destination score', x: rows.map(r=>r.month), y: rows.map(r=>r.top), hovertemplate:'%{x}<br>Top score: %{y:,.0f}<extra></extra>'}
    ], { margin:{l:58,r:20,t:18,b:46}, yaxis:{title: isAir ? 'Air-adjusted case-weighted score' : (isContact ? 'Contact-adjusted score' : (forecast ? 'Forecast case-weighted score' : 'Case-weighted score')), gridcolor:'#e7eef7'}, xaxis:{title:'Mobility month'}, legend:{orientation:'h', y:-0.25}, paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)'}, {responsive:true, displayModeBar:false});
    return;
  }

  if (mapMode === 'uganda_border') {
    const rows = ugandaObservedDistrictRows().slice(0, f.topN).reverse();
    Plotly.newPlot('barChart', [{ type:'bar', orientation:'h', x: rows.map(d=>d.observed_movements), y: rows.map(d=>d.uganda_district), hovertemplate:'%{y}<br>Observed movements: %{x:,.0f}<extra></extra>' }], { margin:{l:145,r:20,t:18,b:40}, xaxis:{title:'Observed movements, 15–24 May 2026', gridcolor:'#e7eef7'}, yaxis:{automargin:true}, paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)'}, {responsive:true, displayModeBar:false});
    return;
  }

  if (mapMode === 'uganda_import') {
    const rows = ugandaImportationRowsForMonth(f.month).filter(r=>r.importation_pressure>0).slice(0, f.topN).reverse();
    Plotly.newPlot('barChart', [{ type:'bar', orientation:'h', x: rows.map(d=>d.importation_pressure), y: rows.map(d=>d.uganda_district), hovertemplate:'%{y}<br>Importation-pressure score: %{x:,.0f}<extra></extra>' }], { margin:{l:145,r:20,t:18,b:40}, xaxis:{title:'Uganda importation-pressure score', gridcolor:'#e7eef7'}, yaxis:{automargin:true}, paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)'}, {responsive:true, displayModeBar:false});
    return;
  }

  if (mapMode === 'uganda_border') {
    const rows = ugandaObservedFmpRows().slice().reverse();
    Plotly.newPlot('trendChart', [{ type:'bar', orientation:'h', x: rows.map(r=>r.observed_movements), y: rows.map(r=>r.fmp), hovertemplate:'%{y}<br>Observed movements: %{x:,.0f}<extra></extra>' }], { margin:{l:125,r:20,t:18,b:40}, xaxis:{title:'Observed movements by FMP', gridcolor:'#e7eef7'}, yaxis:{automargin:true}, paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)'}, {responsive:true, displayModeBar:false});
    return;
  }

  if (mapMode === 'uganda_import') {
    const months = [...new Set(flows.map(d => d.month))].sort();
    const rows = months.map(month => { const rr = ugandaImportationRowsForMonth(month); return { month, total: rr.reduce((a,b)=>a+toNumber(b.importation_pressure),0), top: rr[0]?.importation_pressure || 0 }; });
    Plotly.newPlot('trendChart', [
      { type:'scatter', mode:'lines+markers', name:'Total importation-pressure score', x:rows.map(r=>r.month), y:rows.map(r=>r.total), hovertemplate:'%{x}<br>Total score: %{y:,.0f}<extra></extra>' },
      { type:'scatter', mode:'lines+markers', name:'Top Uganda district score', x:rows.map(r=>r.month), y:rows.map(r=>r.top), hovertemplate:'%{x}<br>Top score: %{y:,.0f}<extra></extra>' }
    ], { margin:{l:58,r:20,t:18,b:46}, yaxis:{title:'Importation-pressure score', gridcolor:'#e7eef7'}, xaxis:{title:'Mobility month'}, legend:{orientation:'h', y:-0.25}, paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)'}, {responsive:true, displayModeBar:false});
    return;
  }

  if (mapMode === 'uganda') {
    const months = [...new Set(flows.map(d => d.month))].sort();
    const rows = months.map(month => {
      const projectedRows = ugandaProjectionRows(month);
      return {
        month,
        total: projectedRows.reduce((a,b)=>a+toNumber(b.projected),0),
        top: projectedRows[0]?.projected || 0,
        kampala: projectedRows.find(r => r.uganda_id === 'UGA_KAMPALA')?.projected || 0
      };
    });
    Plotly.newPlot('trendChart', [
      { type: 'scatter', mode: 'lines+markers', name: 'Projected Uganda-side total', x: rows.map(r => r.month), y: rows.map(r => r.total), hovertemplate: '%{x}<br>Total projection: %{y:,.0f}<extra></extra>' },
      { type: 'scatter', mode: 'lines+markers', name: 'Top Uganda destination', x: rows.map(r => r.month), y: rows.map(r => r.top), hovertemplate: '%{x}<br>Top destination: %{y:,.0f}<extra></extra>' },
      { type: 'scatter', mode: 'lines+markers', name: 'Kampala component', x: rows.map(r => r.month), y: rows.map(r => r.kampala), hovertemplate: '%{x}<br>Kampala: %{y:,.0f}<extra></extra>' }
    ], {
      margin: { l: 58, r: 20, t: 18, b: 46 },
      yaxis: { title: 'Scenario-projected movements', gridcolor: '#e7eef7' },
      xaxis: { title: 'Month' },
      legend: { orientation: 'h', y: -0.25 },
      paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)'
    }, { responsive: true, displayModeBar: false });
    return;
  }

  if (mapMode === 'risk') {
    const rows = [...new Set(flows.map(d => d.month))].sort().map(month => {
      const rrows = riskRowsForMonth(month).filter(r => r.incoming > 0);
      const total = rrows.reduce((a,b)=>a+toNumber(b.incoming),0);
      const kin = rrows.filter(r => r.is_kinshasa === 1 || r.category === 'kinshasa').reduce((a,b)=>a+toNumber(b.incoming),0);
      const border = rrows.filter(r => r.is_uganda_border === 1 || r.category === 'uganda_border').reduce((a,b)=>a+toNumber(b.incoming),0);
      return { month, total, kinshasa: kin, border };
    });
    Plotly.newPlot('trendChart', [
      { type: 'scatter', mode: 'lines+markers', name: 'All destinations', x: rows.map(r => r.month), y: rows.map(r => r.total), hovertemplate: '%{x}<br>Total incoming from outbreak zones: %{y:,}<extra></extra>' },
      { type: 'scatter', mode: 'lines+markers', name: 'Kinshasa', x: rows.map(r => r.month), y: rows.map(r => r.kinshasa), hovertemplate: '%{x}<br>Kinshasa: %{y:,}<extra></extra>' },
      { type: 'scatter', mode: 'lines+markers', name: 'Uganda-border proxy zones', x: rows.map(r => r.month), y: rows.map(r => r.border), hovertemplate: '%{x}<br>Uganda-border proxy: %{y:,}<extra></extra>' }
    ], {
      margin: { l: 58, r: 20, t: 18, b: 46 },
      yaxis: { title: 'Monthly incoming movements from outbreak zones', gridcolor: '#e7eef7' },
      xaxis: { title: 'Month' },
      legend: { orientation: 'h', y: -0.25 },
      paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)'
    }, { responsive: true, displayModeBar: false });
    return;
  }

  if (mapMode === 'population' || mapMode === 'density') {
    const months = [...new Set(population.map(d => d.month))].sort();
    const outbreakIds = new Set(origins.map(o => o.zone_id));
    const rows = months.map(month => {
      const popRows = enrichPopulationRows(population.filter(r => r.month === month));
      if (mapMode === 'density' && hasBoundaries()) {
        const drows = boundaryMetricRows(month, 'density');
        const outbreak = drows.filter(r => outbreakIds.has(r.zone_id) || r.is_outbreak);
        const kin = drows.filter(r => r.is_kinshasa || r.category === 'kinshasa');
        const border = drows.filter(r => r.is_uganda_border || r.category === 'uganda_border');
        const weightedDensity = arr => { const area = arr.reduce((a,b)=>a+toNumber(b.area_km2),0); const pop = arr.reduce((a,b)=>a+toNumber(b.population),0); return area > 0 ? pop / area : 0; };
        return { month, outbreak: weightedDensity(outbreak), kinshasa: weightedDensity(kin), border: weightedDensity(border) };
      }
      return {
        month,
        outbreak: popRows.filter(r => outbreakIds.has(r.zone_id)).reduce((a, b) => a + toNumber(b.population), 0),
        kinshasa: popRows.filter(r => r.is_kinshasa === 1 || r.category === 'kinshasa').reduce((a, b) => a + toNumber(b.population), 0),
        border: popRows.filter(r => r.is_uganda_border === 1 || r.category === 'uganda_border').reduce((a, b) => a + toNumber(b.population), 0)
      };
    }).filter(r => r.outbreak || r.kinshasa || r.border);
    Plotly.newPlot('trendChart', [
      { type: 'scatter', mode: 'lines+markers', name: 'Outbreak zones', x: rows.map(r => r.month), y: rows.map(r => r.outbreak), hovertemplate: '%{x}<br>Outbreak zones: %{y:,}<extra></extra>' },
      { type: 'scatter', mode: 'lines+markers', name: 'Kinshasa zones', x: rows.map(r => r.month), y: rows.map(r => r.kinshasa), hovertemplate: '%{x}<br>Kinshasa: %{y:,}<extra></extra>' },
      { type: 'scatter', mode: 'lines+markers', name: 'Uganda-border proxy zones', x: rows.map(r => r.month), y: rows.map(r => r.border), hovertemplate: '%{x}<br>Uganda-border proxy: %{y:,}<extra></extra>' }
    ], {
      margin: { l: 58, r: 20, t: 18, b: 46 },
      yaxis: { title: mapMode === 'density' ? 'Estimated population density (people/km²)' : 'Estimated population', gridcolor: '#e7eef7' },
      xaxis: { title: 'Month' },
      legend: { orientation: 'h', y: -0.25 },
      paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
      annotations: rows.length ? [] : [{ text: mapMode === 'density' ? 'Density time series will appear after adding data/health_zones.geojson' : 'Population time series will appear after adding data/population_by_hz.csv', x: 0.5, y: 0.5, xref: 'paper', yref: 'paper', showarrow: false, font: { size: 14, color: '#667085' } }]
    }, { responsive: true, displayModeBar: false });
    return;
  }

  const rows = groupByMonth(f.origin);
  Plotly.newPlot('trendChart', [
    { type: 'scatter', mode: 'lines+markers', name: 'Kinshasa', x: rows.map(r => r.month), y: rows.map(r => r.kinshasa), hovertemplate: '%{x}<br>Kinshasa: %{y:,}<extra></extra>' },
    { type: 'scatter', mode: 'lines+markers', name: 'Uganda-border proxy zones', x: rows.map(r => r.month), y: rows.map(r => r.border), hovertemplate: '%{x}<br>Uganda-border proxy: %{y:,}<extra></extra>' }
  ], {
    margin: { l: 58, r: 20, t: 18, b: 46 },
    yaxis: { title: 'Estimated monthly movement', gridcolor: '#e7eef7' },
    xaxis: { title: 'Month' },
    legend: { orientation: 'h', y: -0.25 },
    paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)'
  }, { responsive: true, displayModeBar: false });
}


function updateEpiTimelineChart() {
  const el = document.getElementById('epiTimelineChart');
  if (!el) return;
  const rows = reportSummary
    .filter(r => r.reporting_date && toNumber(r.drc_confirmed_cases) > 0)
    .slice()
    .sort((a, b) => String(a.reporting_date).localeCompare(String(b.reporting_date)));
  if (!rows.length) return;
  const selected = selectedCaseDate();
  const selectedRow = rows.find(r => String(r.reporting_date) === String(selected)) || rows[rows.length - 1];
  const x = rows.map(r => displayDateLabel(r.reporting_date));
  const rawDates = rows.map(r => r.reporting_date);
  const y = rows.map(r => toNumber(r.drc_confirmed_cases));
  const deaths = rows.map(r => toNumber(r.drc_confirmed_deaths));
  const reports = rows.map(r => r.report_no || '');
  const selectedLabel = displayDateLabel(selectedRow.reporting_date);
  const selectedCases = toNumber(selectedRow.drc_confirmed_cases);
  const selectedDeaths = toNumber(selectedRow.drc_confirmed_deaths);
  const selectedReport = selectedRow.report_no || '';
  const maxY = Math.max(...y, selectedCases, 1);
  const selectedIdx = rawDates.indexOf(String(selectedRow.reporting_date));

  Plotly.newPlot('epiTimelineChart', [
    {
      type: 'scatter',
      mode: 'lines+markers',
      name: 'Cumulative confirmed cases',
      x,
      y,
      customdata: rows.map((r, i) => [reports[i], r.reporting_date, deaths[i]]),
      hovertemplate: '%{customdata[0]}<br>%{x}<br>Confirmed cases: %{y:,.0f}<br>Confirmed deaths: %{customdata[2]:,.0f}<extra></extra>'
    },
    {
      type: 'scatter',
      mode: 'markers+text',
      name: 'Selected reporting date',
      x: [selectedLabel],
      y: [selectedCases],
      text: [selectedReport],
      textposition: 'top center',
      marker: { size: 13, symbol: 'circle-open', line: { width: 3 } },
      hovertemplate: `${selectedReport}<br>${selectedLabel}<br>Selected cases: ${fmt.format(selectedCases)}<br>Selected deaths: ${fmt.format(selectedDeaths)}<extra></extra>`
    }
  ], {
    margin: { l: 62, r: 24, t: 18, b: 112 },
    xaxis: { title: { text: 'Reporting date', standoff: 16 }, tickangle: -40, gridcolor: '#eef3f8', automargin: true },
    yaxis: { title: 'Confirmed cases', gridcolor: '#e7eef7', rangemode: 'tozero', automargin: true },
    legend: { orientation: 'h', x: 0.5, xanchor: 'center', y: -0.58, yanchor: 'top' },
    shapes: selectedIdx >= 0 ? [{
      type: 'line', xref: 'x', yref: 'y', x0: selectedLabel, x1: selectedLabel, y0: 0, y1: maxY,
      line: { width: 2, dash: 'dot' }
    }] : [],
    annotations: [{
      x: selectedLabel,
      y: selectedCases,
      text: `${selectedReport}: ${fmt.format(selectedCases)}`,
      showarrow: true,
      arrowhead: 2,
      ax: 18,
      ay: -36,
      bgcolor: 'rgba(255,255,255,0.92)',
      bordercolor: '#d0d5dd',
      borderwidth: 1,
      font: { size: 11 }
    }],
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(0,0,0,0)'
  }, { responsive: true, displayModeBar: false });
}

function addDaysIso(dateStr, days) {
  const d = new Date(dateStr + 'T00:00:00Z');
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

function daysBetweenIso(a, b) {
  const da = new Date(a + 'T00:00:00Z');
  const db = new Date(b + 'T00:00:00Z');
  return Math.round((db - da) / 86400000);
}

function seedFromString(s) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

function mulberry32(a) {
  return function() {
    let t = a += 0x6D2B79F5;
    t = Math.imul(t ^ t >>> 15, t | 1);
    t ^= t + Math.imul(t ^ t >>> 7, t | 61);
    return ((t ^ t >>> 14) >>> 0) / 4294967296;
  };
}

function normalRand(rng) {
  const u1 = Math.max(rng(), 1e-12);
  const u2 = Math.max(rng(), 1e-12);
  return Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
}

function logGamma(z) {
  const p = [
    676.5203681218851, -1259.1392167224028, 771.32342877765313,
    -176.61502916214059, 12.507343278686905, -0.13857109526572012,
    9.9843695780195716e-6, 1.5056327351493116e-7
  ];
  if (z < 0.5) return Math.log(Math.PI) - Math.log(Math.sin(Math.PI * z)) - logGamma(1 - z);
  z -= 1;
  let x = 0.99999999999980993;
  for (let i = 0; i < p.length; i++) x += p[i] / (z + i + 1);
  const t = z + p.length - 0.5;
  return 0.5 * Math.log(2 * Math.PI) + (z + 0.5) * Math.log(t) - t + Math.log(x);
}

function gammaPdf(x, shape, scale) {
  if (x <= 0 || shape <= 0 || scale <= 0) return 0;
  return Math.exp((shape - 1) * Math.log(x) - x / scale - logGamma(shape) - shape * Math.log(scale));
}

function discretizedGammaWeights(mean = 12, sd = 5, maxLag = 40) {
  const shape = (mean / sd) ** 2;
  const scale = (sd * sd) / mean;
  const w = [0];
  let total = 0;
  for (let k = 1; k <= maxLag; k++) {
    const v = gammaPdf(k - 0.5, shape, scale);
    w[k] = v;
    total += v;
  }
  if (total <= 0) return w.map(() => 0);
  for (let k = 1; k <= maxLag; k++) w[k] /= total;
  return w;
}

function gammaRand(shape, scale, rng) {
  if (shape <= 0 || scale <= 0) return 0;
  if (shape < 1) {
    const u = Math.max(rng(), 1e-12);
    return gammaRand(shape + 1, scale, rng) * Math.pow(u, 1 / shape);
  }
  const d = shape - 1 / 3;
  const c = 1 / Math.sqrt(9 * d);
  while (true) {
    let x, v;
    do {
      x = normalRand(rng);
      v = 1 + c * x;
    } while (v <= 0);
    v = v * v * v;
    const u = rng();
    if (u < 1 - 0.0331 * x ** 4) return scale * d * v;
    if (Math.log(u) < 0.5 * x * x + d * (1 - v + Math.log(v))) return scale * d * v;
  }
}

function poissonRand(lambda, rng) {
  lambda = Math.max(0, lambda);
  if (lambda <= 0) return 0;
  if (lambda < 30) {
    const L = Math.exp(-lambda);
    let k = 0, p = 1;
    do { k++; p *= rng(); } while (p > L);
    return k - 1;
  }
  return Math.max(0, Math.round(lambda + Math.sqrt(lambda) * normalRand(rng)));
}

function negativeBinomialRand(mean, dispersion, rng) {
  mean = Math.max(0, mean);
  const k = Math.max(0.05, dispersion || 0.4);
  if (mean <= 0) return 0;
  const lambda = gammaRand(k, mean / k, rng);
  return poissonRand(lambda, rng);
}

function quantile(values, q) {
  const arr = values.filter(Number.isFinite).slice().sort((a, b) => a - b);
  if (!arr.length) return NaN;
  const pos = (arr.length - 1) * q;
  const lo = Math.floor(pos), hi = Math.ceil(pos);
  if (lo === hi) return arr[lo];
  return arr[lo] + (arr[hi] - arr[lo]) * (pos - lo);
}

function dailyObservedSeriesUntil(selectedDate = selectedCaseDate()) {
  const rows = reportSummary
    .filter(r => r.reporting_date && toNumber(r.drc_confirmed_cases) > 0 && String(r.reporting_date) <= String(selectedDate))
    .slice()
    .sort((a, b) => String(a.reporting_date).localeCompare(String(b.reporting_date)));
  if (!rows.length) return [];
  const out = [];
  let prevDate = null;
  let prevCum = 0;
  for (const r of rows) {
    const d = String(r.reporting_date);
    const cum = Math.max(0, toNumber(r.drc_confirmed_cases));
    if (!prevDate) {
      out.push({ date: d, incidence: cum, cumulative: cum, report_no: r.report_no || '' });
    } else {
      const gap = Math.max(1, daysBetweenIso(prevDate, d));
      const inc = Math.max(0, cum - prevCum);
      const daily = inc / gap;
      for (let j = 1; j <= gap; j++) {
        const nd = addDaysIso(prevDate, j);
        out.push({ date: nd, incidence: daily, cumulative: prevCum + daily * j, report_no: j === gap ? (r.report_no || '') : '' });
      }
    }
    prevDate = d;
    prevCum = cum;
  }
  return out.filter(r => String(r.date) <= String(selectedDate));
}

function infectiousnessAt(incidence, t, weights) {
  let lam = 0;
  const maxLag = Math.min(weights.length - 1, t);
  for (let s = 1; s <= maxLag; s++) lam += Math.max(0, incidence[t - s] || 0) * weights[s];
  return lam;
}

function estimateRtFromSeries(incidence, weights, window = 10) {
  const n = incidence.length;
  const start = Math.max(1, n - window);
  let num = 0, den = 0;
  for (let t = start; t < n; t++) {
    num += Math.max(0, incidence[t] || 0);
    den += infectiousnessAt(incidence, t, weights);
  }
  const priorShape = 1;
  const priorRate = 1;
  const shape = priorShape + num;
  const rate = priorRate + den;
  const mean = rate > 0 ? shape / rate : 1;
  return { mean, shape, rate, numerator: num, denominator: den, startIndex: start };
}

function makeForecast(selectedDate = selectedCaseDate()) {
  const horizon = Math.max(1, Number(document.getElementById('forecastHorizonSelect')?.value || 7));
  const siMean = Math.max(1, Number(document.getElementById('forecastSiSelect')?.value || 12));
  const siSd = Math.max(1, siMean * (5 / 12));
  const observed = dailyObservedSeriesUntil(selectedDate);
  if (observed.length < 4) return null;
  const weights = discretizedGammaWeights(siMean, siSd, 40);
  const obsInc = observed.map(r => Math.max(0, toNumber(r.incidence)));
  const rt = estimateRtFromSeries(obsInc, weights, 10);
  const seed = seedFromString(`${selectedDate}|${horizon}|${siMean}|${obsInc.map(x => x.toFixed(3)).join(',')}`);
  const rng = mulberry32(seed);
  const nSim = 1000;
  const dispersion = 0.4;
  const trajectories = [];
  const rtSamples = [];
  const sums = [];
  const finalCums = [];
  const observedCum = observed.length ? toNumber(observed[observed.length - 1].cumulative) : 0;
  for (let i = 0; i < nSim; i++) {
    const sampledRt = gammaRand(rt.shape, 1 / Math.max(rt.rate, 1e-9), rng);
    rtSamples.push(sampledRt);
    const inc = obsInc.slice();
    const future = [];
    for (let h = 1; h <= horizon; h++) {
      const t = inc.length;
      const lam = infectiousnessAt(inc, t, weights);
      const mean = Math.max(0, sampledRt * lam);
      const val = negativeBinomialRand(mean, dispersion, rng);
      inc.push(val);
      future.push(val);
    }
    trajectories.push(future);
    const totalNew = future.reduce((a, b) => a + b, 0);
    sums.push(totalNew);
    finalCums.push(observedCum + totalNew);
  }
  const futureDates = Array.from({ length: horizon }, (_, i) => addDaysIso(selectedDate, i + 1));
  const daily = futureDates.map((date, j) => {
    const vals = trajectories.map(tr => tr[j]);
    return {
      date,
      median: quantile(vals, 0.5),
      lo50: quantile(vals, 0.25),
      hi50: quantile(vals, 0.75),
      lo90: quantile(vals, 0.05),
      hi90: quantile(vals, 0.95)
    };
  });
  return {
    selectedDate, horizon, siMean, observed, daily, rt,
    rtMedian: quantile(rtSamples, 0.5),
    rtLo: quantile(rtSamples, 0.025),
    rtHi: quantile(rtSamples, 0.975),
    probRtAbove1: rtSamples.filter(x => x > 1).length / rtSamples.length,
    newMedian: quantile(sums, 0.5),
    newLo: quantile(sums, 0.05),
    newHi: quantile(sums, 0.95),
    finalCumMedian: quantile(finalCums, 0.5),
    finalCumLo: quantile(finalCums, 0.05),
    finalCumHi: quantile(finalCums, 0.95)
  };
}

function updateForecastChart() {
  const el = document.getElementById('forecastChart');
  if (!el) return;
  const fc = makeForecast(selectedCaseDate());
  if (!fc) {
    Plotly.newPlot('forecastChart', [], {
      margin: { l: 62, r: 24, t: 18, b: 58 },
      annotations: [{ text: 'Not enough SitRep observations for projection', x: 0.5, y: 0.5, xref: 'paper', yref: 'paper', showarrow: false, font: { size: 13, color: '#667085' } }],
      paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)'
    }, { responsive: true, displayModeBar: false });
    return;
  }
  const obs = fc.observed.slice(-21);
  const obsDates = obs.map(r => r.date);
  const obsY = obs.map(r => Number(r.incidence.toFixed(2)));
  const fDates = fc.daily.map(r => r.date);
  const selectedDate = fc.selectedDate;
  const maxY = Math.max(...obsY, ...fc.daily.map(r => r.hi90), 1);
  const traces = [
    {
      type: 'bar', name: 'Observed reported cases', x: obsDates, y: obsY,
      marker: { color: '#344054', opacity: 0.55 },
      hovertemplate: '%{x}<br>Estimated reported cases: %{y:.1f}<extra></extra>'
    },
    { type: 'scatter', mode: 'lines', name: '90% PI lower', x: fDates, y: fc.daily.map(r => r.lo90), line: { width: 0 }, hoverinfo: 'skip', showlegend: false },
    {
      type: 'scatter', mode: 'lines', name: '90% prediction interval', x: fDates, y: fc.daily.map(r => r.hi90),
      line: { width: 0 }, fill: 'tonexty', fillcolor: 'rgba(46, 144, 250, 0.14)',
      hovertemplate: '%{x}<br>90% upper: %{y:.1f}<extra></extra>'
    },
    { type: 'scatter', mode: 'lines', name: '50% PI lower', x: fDates, y: fc.daily.map(r => r.lo50), line: { width: 0 }, hoverinfo: 'skip', showlegend: false },
    {
      type: 'scatter', mode: 'lines', name: '50% prediction interval', x: fDates, y: fc.daily.map(r => r.hi50),
      line: { width: 0 }, fill: 'tonexty', fillcolor: 'rgba(46, 144, 250, 0.24)',
      hovertemplate: '%{x}<br>50% upper: %{y:.1f}<extra></extra>'
    },
    {
      type: 'scatter', mode: 'lines+markers', name: 'Forecast median', x: fDates, y: fc.daily.map(r => r.median),
      line: { width: 2.5, color: '#175cd3' }, marker: { size: 6, color: '#175cd3' },
      hovertemplate: '%{x}<br>Median projected cases: %{y:.1f}<extra></extra>'
    }
  ];
  Plotly.newPlot('forecastChart', traces, {
    margin: { l: 62, r: 24, t: 18, b: 76 },
    xaxis: { title: { text: 'Date', standoff: 12 }, tickangle: -35, gridcolor: '#eef3f8', automargin: true },
    yaxis: { title: 'Daily reported confirmed cases', gridcolor: '#e7eef7', rangemode: 'tozero', automargin: true },
    legend: { orientation: 'h', x: 0.5, xanchor: 'center', y: -0.44, yanchor: 'top' },
    shapes: [{
      type: 'line', xref: 'x', yref: 'y', x0: selectedDate, x1: selectedDate, y0: 0, y1: maxY,
      line: { width: 2, dash: 'dot', color: '#667085' }
    }],
    annotations: [{
      x: selectedDate, y: maxY, xref: 'x', yref: 'y', text: 'projection start', showarrow: false, yshift: 8, font: { size: 10, color: '#667085' }
    }],
    paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)'
  }, { responsive: true, displayModeBar: false });
  const stat = document.getElementById('forecastStats');
  if (stat) {
    stat.innerHTML = `Projection from <strong>${displayDateLabel(fc.selectedDate)}</strong> using a renewal/branching-process model; generation-interval mean <strong>${fc.siMean} days</strong>, SD approximately <strong>${(fc.siMean * 5 / 12).toFixed(1)} days</strong>. Estimated Rt: <strong>${fc.rtMedian.toFixed(2)}</strong> (95% CrI ${fc.rtLo.toFixed(2)}–${fc.rtHi.toFixed(2)}); P(Rt &gt; 1): <strong>${pct(fc.probRtAbove1)}</strong>. Projected new cases over ${fc.horizon} days: <strong>${fmt.format(Math.round(fc.newMedian))}</strong> (90% PI ${fmt.format(Math.round(fc.newLo))}–${fmt.format(Math.round(fc.newHi))}); projected cumulative cases: <strong>${fmt.format(Math.round(fc.finalCumMedian))}</strong> (90% PI ${fmt.format(Math.round(fc.finalCumLo))}–${fmt.format(Math.round(fc.finalCumHi))}). Reporting-date data are not adjusted for onset date or reporting delay.`;
  }
}


function daysBetweenIso(a, b) {
  const da = new Date(String(a) + 'T00:00:00Z');
  const db = new Date(String(b) + 'T00:00:00Z');
  const diff = (db - da) / (24 * 60 * 60 * 1000);
  return Number.isFinite(diff) ? diff : 0;
}

function assessmentLevelClass(level) {
  return {
    high: 'assessment-high',
    moderate_high: 'assessment-moderate-high',
    moderate: 'assessment-moderate',
    low: 'assessment-low',
    very_low: 'assessment-very-low',
    uncertain: 'assessment-uncertain'
  }[level] || 'assessment-uncertain';
}

function setAssessmentCard(id, item) {
  const el = document.getElementById(id);
  if (!el) return;
  el.className = `assessment-card ${assessmentLevelClass(item.level)}`;
  const cat = el.querySelector('.assessment-category');
  const text = el.querySelector('.assessment-text');
  const drivers = el.querySelector('.assessment-drivers');
  if (cat) cat.textContent = item.category;
  if (text) text.textContent = item.text;
  if (drivers) drivers.textContent = item.drivers || '';
}

function sumIncidenceBetween(series, fromExclusive, toInclusive) {
  return series.filter(r => String(r.date) > String(fromExclusive) && String(r.date) <= String(toInclusive))
    .reduce((a, r) => a + toNumber(r.incidence), 0);
}

function latestObservedIncidenceWindow(selectedDate, days = 7) {
  const series = dailyObservedSeriesUntil(selectedDate);
  if (!series.length) return { recent: 0, previous: 0, ratio: NaN };
  const recentStart = addDaysIso(selectedDate, -days);
  const previousStart = addDaysIso(selectedDate, -days * 2);
  const recent = sumIncidenceBetween(series, recentStart, selectedDate);
  const previous = sumIncidenceBetween(series, previousStart, recentStart);
  return { recent, previous, ratio: previous > 0 ? recent / previous : (recent > 0 ? Infinity : NaN) };
}

function localTrajectoryAssessment(fc) {
  const selected = selectedCaseDate();
  const w = latestObservedIncidenceWindow(selected, 7);
  const contactGap = responseMetricValue('contact_gap');
  const rt = fc?.rtMedian;
  const prob = fc?.probRtAbove1;
  let level = 'uncertain';
  let category = '評価困難';
  if (fc) {
    if ((rt > 1.2 && prob > 0.65) || prob > 0.80 || w.ratio > 1.25) {
      level = 'high';
      category = '拡大傾向／高い懸念';
    } else if (rt < 0.8 && prob < 0.30 && w.ratio < 0.85) {
      level = 'low';
      category = '減少傾向／低めの懸念';
    } else {
      level = 'moderate';
      category = '横ばいまたは混在／中程度の懸念';
    }
    if (Number.isFinite(contactGap) && contactGap >= 0.30 && level !== 'high') {
      level = 'moderate_high';
      category = '中〜高程度の懸念';
    }
  }
  const trendPhrase = w.recent >= w.previous ? '直近の報告症例数は明確には減少していません' : '直近の報告症例数は前週より低下しています';
  const responsePhrase = Number.isFinite(contactGap)
    ? `接触者追跡ギャップは${pct(contactGap)}です`
    : '対応カバレッジのデータは不完全です';
  return {
    level, category,
    text: fc
      ? `流行地の状況は、直近のSitRep症例数、推定Rt、response指標に基づき評価しています。${trendPhrase}。推定Rtは${rt.toFixed(2)}で、${responsePhrase}。`
      : '短期予測モデルで流行地の状況を評価するには、観察データが不足しています。',
    drivers: fc ? `Rt ${rt.toFixed(2)}、P(Rt>1) ${pct(prob)}、直近7日 ${fmt.format(Math.round(w.recent))}例 vs 前7日 ${fmt.format(Math.round(w.previous))}例、${responsePhrase}。` : '短期予測は利用できません。'
  };
}

function kinshasaCaseCount() {
  return caseRowsLatest().filter(r => normalizedString(r.province).includes('kinshasa') || normalizedString(r.health_zone).includes('kinshasa'))
    .reduce((a, r) => a + toNumber(r.confirmed_cases), 0);
}

function capitalRiskAssessment() {
  const f = currentFilters();
  const rows = airAdjustedRiskRowsForMonth(f.month, false).filter(r => toNumber(r.air_adjusted) > 0);
  const total = rows.reduce((a, r) => a + toNumber(r.air_adjusted), 0);
  const kinRows = rows.filter(r => r.is_kinshasa === 1 || normalizedString(r.category).includes('kinshasa') || normalizedString(r.zone_name).includes('kinshasa'));
  const kin = kinRows.reduce((a, r) => a + toNumber(r.air_adjusted), 0);
  const share = total > 0 ? kin / total : 0;
  const kCases = kinshasaCaseCount();
  let level = 'low', category = '低い';
  if (kCases > 0) { level = 'high'; category = '高い'; }
  else if (share >= 0.15) { level = 'moderate_high'; category = '中〜高程度'; }
  else if (share >= 0.05) { level = 'moderate'; category = '中程度'; }
  else if (share < 0.01) { level = 'very_low'; category = '非常に低い'; }
  return {
    level, category,
    text: kCases > 0
      ? '首都圏で確定例が報告されているため、首都圏への拡大リスクは高いと評価され、直ちに確認が必要です。'
      : `首都圏へのリスクは、航空移動抑制を考慮したcase-weighted mobility指標に基づき評価しています。現在の指標では首都圏への流入圧は「${category}」と評価されますが、リスクはゼロではなく、移動時スクリーニングと報告の完全性に依存します。`,
    drivers: `Kinshasa確定例 ${fmt.format(Math.round(kCases))}例、air-adjusted share ${pct(share)}、Kinshasa関連destination ${kinRows.length}件。`
  };
}

function crossBorderRiskAssessment() {
  const f = currentFilters();
  const weighted = weightedRiskRowsForMonth(f.month, false).filter(r => toNumber(r.weighted) > 0);
  const total = weighted.reduce((a, r) => a + toNumber(r.weighted), 0);
  const border = weighted.filter(r => r.is_uganda_border === 1 || normalizedString(r.category).includes('uganda'))
    .reduce((a, r) => a + toNumber(r.weighted), 0);
  const share = total > 0 ? border / total : 0;
  const ugRows = ugandaImportationRowsForMonth(f.month).filter(r => toNumber(r.importation_pressure) > 0);
  const poe = responseMetricValue('poe_screening_coverage');
  const ituriCases = caseRowsLatest().filter(r => normalizedString(r.province) === 'ituri').reduce((a, r) => a + toNumber(r.confirmed_cases), 0);
  let level = 'moderate', category = '中程度';
  if (share >= 0.25 || ituriCases >= 400) { level = 'moderate_high'; category = '中〜高程度'; }
  if (share >= 0.40 && (!Number.isFinite(poe) || poe < 0.90)) { level = 'high'; category = '高い'; }
  if (share < 0.08 && Number.isFinite(poe) && poe >= 0.95) { level = 'low'; category = '低い'; }
  return {
    level, category,
    text: `ウガンダ・周辺国への拡大リスクは、ウガンダ国境方向のimportation pressure、DRC東部の症例数、PoE/PoCスクリーニング指標に基づき評価しています。現在の指標では「${category}」のリスクが示唆され、スクリーニング下でも国境を越える移動は継続しています。`,
    drivers: `Ituri症例 ${fmt.format(Math.round(ituriCases))}例、border-pressure share ${pct(share)}、Uganda destination ${ugRows.length}件、PoE screening ${Number.isFinite(poe) ? pct(poe) : 'データなし'}。`
  };
}

function updateAssessmentPanel() {
  const fc = makeForecast(selectedCaseDate());
  setAssessmentCard('assessmentLocal', localTrajectoryAssessment(fc));
  setAssessmentCard('assessmentCapital', capitalRiskAssessment());
  setAssessmentCard('assessmentCrossBorder', crossBorderRiskAssessment());
  const u = document.getElementById('assessmentUpdated');
  if (u) {
    const meta = reportSummaryForDate(selectedCaseDate());
    u.textContent = `${displayDateLabel(selectedCaseDate())}時点の評価${meta?.report_no ? '（' + meta.report_no + '）' : ''}`;
  }
}


function updateDashboard() {
  document.getElementById('topNValue').textContent = document.getElementById('topN').value;
  const month = document.getElementById('monthSelect').value;
  const idx = monthsCache.indexOf(month);
  const monthSlider = document.getElementById('monthSlider');
  const monthSliderLabel = document.getElementById('monthSliderLabel');
  if (monthSlider && idx >= 0) monthSlider.value = idx;
  if (monthSliderLabel) monthSliderLabel.textContent = month;
  const reportSliderLabel = document.getElementById('reportDateSliderLabel');
  const caseModeLabel = document.getElementById('caseModeLabel');
  if (reportSliderLabel) {
    const meta = reportSummaryForDate(selectedCaseDate());
    reportSliderLabel.textContent = `${displayDateLabel(selectedCaseDate())}${meta?.report_no ? ' / ' + meta.report_no : ''}`;
  }
  if (caseModeLabel) caseModeLabel.textContent = caseDisplayMode === 'recent' ? `Recent increase since ${displayDateLabel(comparisonCaseDate(selectedCaseDate()))}` : 'Cumulative cases';
  const destRows = groupByDestination(selectedFlows());
  updateKpis(destRows);
  setEpiKpis();
  updateAssessmentPanel();
  updateEpiTimelineChart();
  updateForecastChart();
  updateResponseTimelineChart();
  updateRwiScatterChart();
  if (mapMode === 'cases') updateCasesMap();
  else if (mapMode === 'risk') updateRiskMap();
  else if (mapMode === 'weighted') updateWeightedRiskMap(false);
  else if (mapMode === 'contact') updateContactAdjustedRiskMap();
  else if (mapMode === 'air') updateAirAdjustedRiskMap();
  else if (mapMode === 'uganda') updateUgandaProjectionMap();
  else if (mapMode === 'uganda_border') updateUgandaBorderFlowMap();
  else if (mapMode === 'uganda_import') updateUgandaImportationMap();
  else if (mapMode === 'population' || mapMode === 'density') updatePopulationMap();
  else if (mapMode === 'rwi') updateRwiMap();
  else if (['response_contact_gap','response_intensity'].includes(mapMode)) updateResponseMap();
  else updateMap(destRows);
  updateBarChart(destRows);
  updateTrendChart();
}

async function main() {
  [origins, destinations, flows, scenarios, population, healthZoneBoundaries, ugandaProfile, cases, airAdjustment, contactFollowup, ugandaFmpFlows, ugandaDistrictFlows, reportSummary, healthZoneRwi, responseIndicators] = await Promise.all([
    loadCsv(files.origins), loadCsv(files.destinations), loadCsv(files.flows), loadCsv(files.scenarios), loadCsvOptional(files.population), loadGeoJsonOptional(files.boundaries), loadCsvOptional(files.ugandaProfile), loadCsvOptional(files.cases), loadCsvOptional(files.airAdjustment), loadCsvOptional(files.contactFollowup), loadCsvOptional(files.ugandaFmpFlows), loadCsvOptional(files.ugandaDistrictFlows), loadCsvOptional(files.reportSummary), loadCsvOptional(files.rwi), loadCsvOptional(files.response)
  ]);
  buildIndexes();
  initMap();
  populateControls();
  const summaryRows = (reportSummary || []).slice().sort((a, b) => new Date(a.reporting_date) - new Date(b.reporting_date));
  const latestSummary = summaryRows[summaryRows.length - 1] || null;
  const firstSummary = summaryRows[0] || null;
  const latestReport = latestSummary?.report_no || 'latest SitRep';
  const firstReport = firstSummary?.report_no || 'first SitRep';
  const latestReporting = latestSummary?.reporting_date ? displayDateLabel(latestSummary.reporting_date) : 'latest reporting date';
  const latestPublished = latestSummary?.publication_date ? displayDateLabel(latestSummary.publication_date) : 'latest publication date';
  document.getElementById('dataStatus').textContent = `SitRep ${firstReport.replace(/^N/i, 'N')}–${latestReport}まで更新済み（最新 ${latestReport}）`;
  const popMsg = hasPopulationData() ? `人口データ ${population.length}行` : '人口データ未読込';
  const boundaryMsg = hasBoundaries() ? `health-zoneポリゴン ${healthZoneBoundaries.features.length}件` : 'ポリゴン境界未読込';
  const caseMsg = cases.length ? `症例データ ${availableCaseDates().length}報告日・${cases.length}行（選択中 ${selectedCaseDate()}）` : '症例データ未読込';
  const rwiMsg = healthZoneRwi.length ? `RWI ${healthZoneRwi.length} zones` : 'RWI未読込';
  const responseMsg = responseIndicators.length ? `response指標 ${responseIndicators.length}行` : 'response指標未読込';
  const ugandaMsg = ugandaFmpFlows.length ? `Uganda DTM ${ugandaFmpFlows.length} FMP / ${ugandaDistrictFlows.length} district` : 'Uganda DTM未読込';
  document.getElementById('lastUpdated').textContent = `INSP SitRepページを6時間ごとに自動確認し、最新PDFを取得・抽出して更新する設定です。最新は${latestReport}（報告 ${latestReporting}、公開 ${latestPublished}）。抽出値の検証に失敗した場合は自動公開せず、GitHub Issueで確認を求めます。読込データ：OD ${flows.length}行、${popMsg}、${boundaryMsg}、${caseMsg}、${rwiMsg}、${responseMsg}、${ugandaMsg}。`;
  updateDashboard();
  setTimeout(fitMapToData, 300);
}

main().catch(err => {
  console.error(err);
  document.body.insertAdjacentHTML('afterbegin', `<div style="background:#fee4e2;color:#912018;padding:12px 20px;font-weight:700">Dashboard failed to load: ${err.message}</div>`);
});
