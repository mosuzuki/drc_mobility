(() => {
  const LANG_KEY = 'drcMobilityDashboardLang';

  const dict = {
    pageTitle: {
      ja: 'DRC・ウガンダ Bundibugyo型エボラ流行ダッシュボード',
      en: 'DRC–Uganda Bundibugyo Ebola Outbreak Dashboard'
    },
    eyebrow: { ja: '疫学・移動・対応状況の統合ダッシュボード', en: 'Integrated epidemiology, mobility and response dashboard' },
    subtitle: {
      ja: 'DRC東部のhealth zone別症例、人口移動、ウガンダ国境フロー、対応指標を統合し、流行状況と拡大リスクを把握するためのダッシュボードです。',
      en: 'This dashboard integrates health-zone case counts, population mobility, Uganda border flows and response indicators to monitor the outbreak situation and spread risk in eastern DRC.'
    },
    language: { ja: '表示言語', en: 'Language' },
    statusLabel: { ja: 'データ更新状況', en: 'Data update status' },
    assessmentEyebrow: { ja: 'AI支援による状況評価', en: 'AI-assisted situational intelligence' },
    assessmentTitle: { ja: 'AI支援による状況評価', en: 'AI-assisted situational assessment' },
    assessmentLead: {
      ja: '更新時に差分要約を生成し、疫学・人口移動・対応指標に基づく公衆衛生アセスメントをあわせて表示します。公式のリスク評価ではなく、専門家による確認が必要です。',
      en: 'At each update, the dashboard generates a change summary and displays a public-health assessment based on epidemiology, mobility and response indicators. This is not an official risk assessment and requires expert review.'
    },
    assessmentUpdatedDefault: { ja: '最新の選択SitRepに基づき更新', en: 'Updated from the selected SitRep' },
    latestSituation: { ja: '最新の状況', en: 'Latest situation' },
    latestSituationMeta: { ja: 'SitRep更新ごとの差分要約', en: 'Change summary by SitRep update' },
    drcLabel: { ja: 'DRC：', en: 'DRC:' },
    ugandaLabel: { ja: 'ウガンダ：', en: 'Uganda:' },
    loadingDrc: { ja: 'SitRep更新ごとの差分要約を読み込み中です。', en: 'Loading the SitRep change summary.' },
    loadingUganda: { ja: 'ウガンダ側の更新情報を読み込み中です。', en: 'Loading Uganda-side update information.' },
    publicHealth: { ja: '公衆衛生アセスメント', en: 'Public-health assessment' },
    assessmentBasis: { ja: '現在の指標に基づく評価', en: 'Assessment based on current indicators' },
    localTransmission: { ja: '流行地における流行状況', en: 'Local outbreak trajectory' },
    capitalRisk: { ja: '首都圏への拡大リスク', en: 'Risk of spread to the capital area' },
    crossBorderRisk: { ja: 'ウガンダ・周辺国への拡大リスク', en: 'Risk of spread to Uganda and neighbouring countries' },
    underReview: { ja: '評価中', en: 'Under review' },
    loadingData: { ja: 'データを読み込み中です。', en: 'Loading data.' },
    kpiDrcCases: { ja: 'DRC 確定症例数', en: 'DRC confirmed cases' },
    kpiDrcDeaths: { ja: 'DRC 確定死亡数', en: 'DRC confirmed deaths' },
    kpiUgCases: { ja: 'ウガンダ 確定症例数', en: 'Uganda confirmed cases' },
    kpiUgDeaths: { ja: 'ウガンダ 確定死亡数', en: 'Uganda confirmed deaths' },
    mapTitleCases: { ja: 'Health zone別エボラ確定症例', en: 'Confirmed Ebola cases by health zone' },
    mapDescCases: {
      ja: '確定症例はhealth zone重心上の比例円で表示しています。報告日スライダーで累積時点または直近1週間の増加を切り替えられます。',
      en: 'Confirmed cases are shown as proportional bubbles at health-zone centroids. Use the reporting-date slider to change the cumulative time point or show the recent 1-week increase.'
    },
    fitMap: { ja: '地図を合わせる', en: 'Fit map' },
    mapLayer: { ja: '地図レイヤー', en: 'Map layer' },
    cases: { ja: '症例', en: 'Cases' },
    spreadRisk: { ja: '拡大リスク', en: 'Spread risk' },
    weightedRisk: { ja: '症例重み付きリスク', en: 'Weighted risk' },
    contactAdjusted: { ja: '接触者追跡補正リスク', en: 'Contact-adjusted risk' },
    airAdjusted: { ja: '航空移動補正リスク', en: 'Air-adjusted risk' },
    ugandaProjection: { ja: 'ウガンダ予測', en: 'Uganda projection' },
    ugandaBorderFlow: { ja: 'ウガンダ国境フロー', en: 'Uganda border flow' },
    ugandaImportPressure: { ja: 'ウガンダ流入圧', en: 'Uganda importation pressure' },
    population: { ja: '人口', en: 'Population' },
    density: { ja: '人口密度', en: 'Density' },
    rwi: { ja: '相対的富裕度パーセンタイル', en: 'Relative wealth percentile' },
    contactGap: { ja: '接触者追跡ギャップ', en: 'Contact gap' },
    responseIntensity: { ja: '対応強度', en: 'Response intensity' },
    movement: { ja: '人口移動', en: 'Movement' },
    originSet: { ja: '起点セット', en: 'Origin set' },
    customOrigins: { ja: '任意の起点', en: 'Custom origins' },
    customHelp: { ja: 'Ctrl/Cmdキーを押しながら複数のhealth zoneを選択できます。', en: 'Hold Ctrl/Cmd to select multiple health zones.' },
    month: { ja: '月', en: 'Month' },
    ugandaScenario: { ja: 'ウガンダ国境シナリオ', en: 'Uganda cross-border scenario' },
    sitrepTimePoint: { ja: 'SitRep時点', en: 'SitRep time point' },
    sitrepHelp: { ja: 'このスライダーで、報告日別に症例バブルと症例重み付きリスクレイヤーを更新します。', en: 'Use this slider to update case bubbles and case-weighted risk layers by reporting date.' },
    reportingDateShown: { ja: '地図に表示する報告日', en: 'Reporting date shown on map' },
    latest: { ja: '最新', en: 'Latest' },
    cumulativeCases: { ja: '累積症例', en: 'Cumulative cases' },
    cumulative: { ja: '累積', en: 'Cumulative' },
    recentIncrease: { ja: '直近1週間の増加', en: 'Recent 1-week increase' },
    currentOutbreakHz: { ja: '現在の流行health zone', en: 'Current outbreak health zone' },
    confirmedCases: { ja: '確定症例', en: 'Confirmed cases' },
    spreadWeightedRisk: { ja: '拡大／症例重み付きリスク', en: 'Spread / weighted risk' },
    kinshasaDest: { ja: 'キンシャサ方面destination', en: 'Kinshasa destination' },
    ugandaBorderProxy: { ja: 'ウガンダ国境proxy zone', en: 'Uganda-border proxy zone' },
    projectedUgandaDest: { ja: '予測されるウガンダdestination', en: 'Projected Uganda destination' },
    populationDensity: { ja: '人口／人口密度', en: 'Population / density' },
    responseIndicators: { ja: '対応指標', en: 'Response indicators' },
    movementDirection: { ja: '移動方向', en: 'Movement direction' },
    reportedCumulativeCases: { ja: '報告日別累積症例数', en: 'Reported cumulative cases' },
    epiTimelineDesc: { ja: 'SitRep報告日別のDRC確定症例数。スライダーで選択中の日付を強調表示します。', en: 'DRC confirmed cases by SitRep reporting date. The selected slider date is highlighted.' },
    shortProjection: { ja: '短期予測', en: 'Short-term projection' },
    projectionDesc: { ja: '最近の報告確定症例に基づくrenewal／branching-process予測。選択したSitRep日から予測を開始します。', en: 'Renewal / branching-process projection based on recent reported confirmed cases. The projection starts from the selected SitRep date.' },
    responseTimeline: { ja: '対応指標の時系列', en: 'Response timeline' },
    responseTimelineDesc: { ja: 'SitRepから抽出した対応指標。報告内容に応じて、全国、州、または運用拠点レベルの値を含みます。', en: 'Selected response indicators extracted from SitReps. Values may be national, province-level, or operational-site summaries depending on what was reported.' },
    rwiVsCases: { ja: 'RWIとエボラ症例', en: 'RWI vs Ebola cases' },
    rwiDesc: { ja: 'Health zoneレベルの生態学的比較。RWIはDRC内パーセンタイルで表示し、選択中の報告日と症例表示モードを使用します。', en: 'Health-zone ecological comparison. RWI is shown as a within-DRC percentile; selected reporting date and case mode are used.' },
    rankingTitle: { ja: 'Destinationランキング', en: 'Destination ranking' },
    rankingDesc: { ja: '推定月間移動量が多いdestination health zone。', en: 'Top destination health zones by estimated monthly movement.' },
    topRankedAreas: { ja: '表示する上位エリア数', en: 'Top ranked areas shown' },
    scenarioInterpretation: { ja: 'シナリオ解釈', en: 'Scenario interpretation' },
    dataSources: { ja: '接続すべきデータソース', en: 'Data sources to connect' },
    important: { ja: '重要：', en: 'Important:' },
    limitation: {
      ja: 'ウガンダ推定値には、2026年5月15–24日のIOM DTM EVD国境フロー観測サマリーと、DRC側の症例重み付き国境方向移動に2026年ウガンダdestinationプロファイルを組み合わせたシナリオベースの流入圧スコアが含まれます。これらはエボラ感染確率ではありません。',
      en: 'Uganda estimates include both observed IOM DTM EVD border-flow summaries from 15–24 May 2026 and scenario-based importation-pressure scores that combine DRC-side case-weighted movement toward border proxy zones with the 2026 Uganda destination profile. They are not Ebola transmission probabilities.'
    },
    footer: {
      ja: 'Flowminder / HDX由来のDRC health-zone人口移動推定を用いたプロトタイプダッシュボードです。ウガンダ関連値は、国境横断データで校正されるまではproxy推定です。',
      en: 'Prototype dashboard using Flowminder / HDX-derived DRC health-zone mobility estimates. Uganda values remain proxy estimates unless calibrated with cross-border data.'
    },
    sevenDays: { ja: '7日間', en: '7 days' },
    fourteenDays: { ja: '14日間', en: '14 days' },
    shortGi: { ja: '短いGI 平均9日', en: 'Short GI mean 9d' },
    baselineGi: { ja: '標準GI 平均12日', en: 'Baseline GI mean 12d' },
    longGi: { ja: '長いGI 平均15日', en: 'Long GI mean 15d' },
    contactFollowupRate: { ja: '接触者フォローアップ率', en: 'Contact follow-up rate' },
    alertInvestigation: { ja: 'アラート調査カバレッジ', en: 'Alert investigation coverage' },
    poeScreening: { ja: 'PoE/PoCスクリーニングカバレッジ', en: 'PoE/PoC screening coverage' },
    samplesAnalysed: { ja: '解析検体数', en: 'Samples analysed' },
    travellersScreened: { ja: 'スクリーニング済み旅行者数', en: 'Travellers screened' },
    casesPer100k: { ja: '人口10万対症例数', en: 'Cases per 100,000' },
    affectedOnly: { ja: '流行地のみ', en: 'Affected only' },
    allHealthZones: { ja: '全health zone', en: 'All health zones' },
    top25Affected: { ja: '症例上位25 health zone', en: 'Top 25 affected' },
    linearScale: { ja: '線形スケール', en: 'Linear scale' },
    logScale: { ja: 'log1pスケール', en: 'log1p scale' },
    majorOnly: { ja: '主要流行zoneのみ', en: 'Major outbreak zones only' },
    allAffected: { ja: '全流行health zone', en: 'All affected health zones' },
    ituriOnly: { ja: 'Ituriのみ', en: 'Ituri only' },
    northKivuOnly: { ja: 'North Kivuのみ', en: 'North Kivu only' },
    southKivuOnly: { ja: 'South Kivuのみ', en: 'South Kivu only' },
    customSelection: { ja: '任意選択', en: 'Custom selection' }
  };

  const selectors = {
    '.topbar > div:first-child .eyebrow': 'eyebrow',
    '.topbar h1': 'pageTitle',
    '.topbar .subtitle': 'subtitle',
    '.language-switch-label': 'language',
    '.status-card .label': 'statusLabel',
    '.assessment-eyebrow': 'assessmentEyebrow',
    '.assessment-main-title': 'assessmentTitle',
    '.ai-assessment-panel .assessment-header p': 'assessmentLead',
    '#assessmentUpdated': 'assessmentUpdatedDefault',
    '.latest-situation-block h3': 'latestSituation',
    '#latestSituationMeta': 'latestSituationMeta',
    '.latest-situation-item:nth-child(1) strong': 'drcLabel',
    '.latest-situation-item:nth-child(2) strong': 'ugandaLabel',
    '#latestSituationDrc': 'loadingDrc',
    '#latestSituationUganda': 'loadingUganda',
    '.public-health-subhead h3': 'publicHealth',
    '.public-health-subhead span': 'assessmentBasis',
    '#assessmentLocal .assessment-label': 'localTransmission',
    '#assessmentCapital .assessment-label': 'capitalRisk',
    '#assessmentCrossBorder .assessment-label': 'crossBorderRisk',
    '#assessmentLocal .assessment-category': 'underReview',
    '#assessmentCapital .assessment-category': 'underReview',
    '#assessmentCrossBorder .assessment-category': 'underReview',
    '#assessmentLocal .assessment-text': 'loadingData',
    '#assessmentCapital .assessment-text': 'loadingData',
    '#assessmentCrossBorder .assessment-text': 'loadingData',
    '.epi-kpi-grid .kpi-card:nth-child(1) span': 'kpiDrcCases',
    '.epi-kpi-grid .kpi-card:nth-child(2) span': 'kpiDrcDeaths',
    '.epi-kpi-grid .kpi-card:nth-child(3) span': 'kpiUgCases',
    '.epi-kpi-grid .kpi-card:nth-child(4) span': 'kpiUgDeaths',
    '#fitMap': 'fitMap',
    '.toolbar-label': 'mapLayer',
    '#modeCases': 'cases',
    '#modeRisk': 'spreadRisk',
    '#modeWeighted': 'weightedRisk',
    '#modeContact': 'contactAdjusted',
    '#modeAir': 'airAdjusted',
    '#modeUganda': 'ugandaProjection',
    '#modeUgandaBorder': 'ugandaBorderFlow',
    '#modeUgandaImport': 'ugandaImportPressure',
    '#modePopulation': 'population',
    '#modeDensity': 'density',
    '#modeRwi': 'rwi',
    '#modeContactGap': 'contactGap',
    '#modeResponseIntensity': 'responseIntensity',
    '#modeMovement': 'movement',
    'label[for="originSelect"]': 'originSet',
    'label[for="customOriginSelect"]': 'customOrigins',
    '.custom-origin-control small': 'customHelp',
    'label[for="monthSelect"]': 'month',
    'label[for="scenarioSelect"]': 'ugandaScenario',
    '.timeline-controls-title strong': 'sitrepTimePoint',
    '.timeline-controls-title span': 'sitrepHelp',
    '.report-slider-head strong': 'reportingDateShown',
    '#modeCumulativeCases': 'cumulative',
    '#modeRecentIncrease': 'recentIncrease',
    '.map-legend span:nth-child(1)': 'currentOutbreakHz',
    '.map-legend span:nth-child(2)': 'confirmedCases',
    '.map-legend span:nth-child(3)': 'spreadWeightedRisk',
    '.map-legend span:nth-child(4)': 'kinshasaDest',
    '.map-legend span:nth-child(5)': 'ugandaBorderProxy',
    '.map-legend span:nth-child(6)': 'airAdjusted',
    '.map-legend span:nth-child(7)': 'projectedUgandaDest',
    '.map-legend span:nth-child(8)': 'populationDensity',
    '.map-legend span:nth-child(9)': 'rwi',
    '.map-legend span:nth-child(10)': 'responseIndicators',
    '.map-legend span:nth-child(11)': 'movementDirection',
    '.timeline-chart-block h2': 'reportedCumulativeCases',
    '.timeline-chart-block p': 'epiTimelineDesc',
    '.forecast-chart-block h2': 'shortProjection',
    '.forecast-chart-block p': 'projectionDesc',
    '.response-chart-block h2': 'responseTimeline',
    '.response-chart-block p': 'responseTimelineDesc',
    '.rwi-chart-block h2': 'rwiVsCases',
    '.rwi-chart-block p': 'rwiDesc',
    'label[for="topN"]': 'topRankedAreas',
    '.scenario-panel h2': 'scenarioInterpretation',
    '.sources h2': 'dataSources',
    '.limitation strong': 'important',
    '.limitation': 'limitation',
    'footer': 'footer'
  };

  const phraseKeys = Object.keys(dict);
  const textToKey = new Map();
  for (const key of phraseKeys) {
    const item = dict[key];
    if (item.ja) textToKey.set(normalizeText(item.ja), key);
    if (item.en) textToKey.set(normalizeText(item.en), key);
  }

  function normalizeText(s) {
    return String(s ?? '').replace(/\s+/g, ' ').trim();
  }

  function getLang() {
    const saved = localStorage.getItem(LANG_KEY);
    return saved === 'en' ? 'en' : 'ja';
  }

  function t(key, lang = getLang()) {
    return dict[key]?.[lang] || dict[key]?.en || key;
  }

  function setText(selector, key, lang) {
    document.querySelectorAll(selector).forEach(el => {
      if (!el) return;
      if (selector === '.limitation') {
        el.innerHTML = `<strong>${t('important', lang)}</strong> ${t('limitation', lang)}`;
        return;
      }
      // For legend spans, preserve the icon element and replace only the trailing text node.
      const icon = el.querySelector('i');
      if (icon && el.childNodes.length) {
        Array.from(el.childNodes).forEach(node => {
          if (node.nodeType === Node.TEXT_NODE) node.nodeValue = '';
        });
        el.appendChild(document.createTextNode(t(key, lang)));
      } else {
        el.textContent = t(key, lang);
      }
    });
  }

  function translateExactTextNodes(root, lang) {
    const skip = new Set(['SCRIPT', 'STYLE', 'TEXTAREA', 'CODE']);
    const walker = document.createTreeWalker(root || document.body, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        if (!node.parentElement || skip.has(node.parentElement.tagName)) return NodeFilter.FILTER_REJECT;
        const text = normalizeText(node.nodeValue);
        if (!text || /^[-—•0-9,.:/%\s]+$/.test(text)) return NodeFilter.FILTER_REJECT;
        return textToKey.has(text) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      }
    });
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const node of nodes) {
      const key = textToKey.get(normalizeText(node.nodeValue));
      if (key) node.nodeValue = t(key, lang);
    }
  }

  function translateOptions(lang) {
    const optionMappings = [
      ['#forecastHorizonSelect option[value="7"]', 'sevenDays'],
      ['#forecastHorizonSelect option[value="14"]', 'fourteenDays'],
      ['#forecastSiSelect option[value="9"]', 'shortGi'],
      ['#forecastSiSelect option[value="12"]', 'baselineGi'],
      ['#forecastSiSelect option[value="15"]', 'longGi'],
      ['#responseMetricSelect option[value="contact_followup_rate"]', 'contactFollowupRate'],
      ['#responseMetricSelect option[value="contact_gap"]', 'contactGap'],
      ['#responseMetricSelect option[value="alert_investigation_rate"]', 'alertInvestigation'],
      ['#responseMetricSelect option[value="poe_screening_coverage"]', 'poeScreening'],
      ['#responseMetricSelect option[value="samples_analysed"]', 'samplesAnalysed'],
      ['#responseMetricSelect option[value="travellers_screened"]', 'travellersScreened'],
      ['#rwiOutcomeSelect option[value="per100k"]', 'casesPer100k'],
      ['#rwiOutcomeSelect option[value="cases"]', 'confirmedCases'],
      ['#rwiDisplaySelect option[value="affected"]', 'affectedOnly'],
      ['#rwiDisplaySelect option[value="all"]', 'allHealthZones'],
      ['#rwiDisplaySelect option[value="top25"]', 'top25Affected'],
      ['#rwiScaleSelect option[value="linear"]', 'linearScale'],
      ['#rwiScaleSelect option[value="log1p"]', 'logScale'],
      ['#originSelect option[value="major"]', 'majorOnly'],
      ['#originSelect option[value="all_affected"]', 'allAffected'],
      ['#originSelect option[value="ituri"]', 'ituriOnly'],
      ['#originSelect option[value="north_kivu"]', 'northKivuOnly'],
      ['#originSelect option[value="south_kivu"]', 'southKivuOnly'],
      ['#originSelect option[value="custom"]', 'customSelection']
    ];
    for (const [selector, key] of optionMappings) setText(selector, key, lang);
  }

  function translateKnownDynamic(lang) {
    const mapTitle = document.getElementById('mapTitle');
    const mapDesc = document.getElementById('mapDescription');
    if (mapTitle && textToKey.has(normalizeText(mapTitle.textContent))) mapTitle.textContent = t(textToKey.get(normalizeText(mapTitle.textContent)), lang);
    if (mapDesc && textToKey.has(normalizeText(mapDesc.textContent))) mapDesc.textContent = t(textToKey.get(normalizeText(mapDesc.textContent)), lang);

    const active = document.getElementById('activeLayerLabel');
    if (active) {
      const key = textToKey.get(normalizeText(active.textContent));
      if (key) active.textContent = t(key, lang);
    }
    const caseMode = document.getElementById('caseModeLabel');
    if (caseMode) {
      const text = normalizeText(caseMode.textContent);
      if (text === dict.cumulativeCases.en || text === dict.cumulativeCases.ja) caseMode.textContent = t('cumulativeCases', lang);
      else if (text.startsWith('Recent increase since')) caseMode.textContent = lang === 'ja' ? text.replace('Recent increase since', '直近増加：基準日') : text;
      else if (text.startsWith('直近増加：基準日')) caseMode.textContent = lang === 'en' ? text.replace('直近増加：基準日', 'Recent increase since') : text;
    }

    // Common dynamic map/ranking titles set by app.js.
    const dynamicMap = {
      'Relative wealth percentile by health zone': { ja: 'Health zone別の相対的富裕度パーセンタイル', en: 'Relative wealth percentile by health zone' },
      'Relative wealth percentile ranking': { ja: '相対的富裕度パーセンタイルランキング', en: 'Relative wealth percentile ranking' },
      'Forecast-risk ranking': { ja: '予測リスクランキング', en: 'Forecast-risk ranking' },
      'Weighted-risk ranking': { ja: '症例重み付きリスクランキング', en: 'Weighted-risk ranking' },
      'Uganda border flow — observed DTM FMP data': { ja: 'ウガンダ国境フロー — DTM FMP観測データ', en: 'Uganda border flow — observed DTM FMP data' },
      'Uganda destination ranking': { ja: 'ウガンダdestinationランキング', en: 'Uganda destination ranking' },
      'Uganda importation pressure': { ja: 'ウガンダ流入圧', en: 'Uganda importation pressure' },
      'Uganda importation-pressure ranking': { ja: 'ウガンダ流入圧ランキング', en: 'Uganda importation-pressure ranking' },
      'Destination ranking': { ja: 'Destinationランキング', en: 'Destination ranking' },
      'Top destination health zones by estimated monthly movement.': { ja: '推定月間移動量が多いdestination health zone。', en: 'Top destination health zones by estimated monthly movement.' },
      'No response data available for this metric.': { ja: 'この指標で利用可能なresponseデータはありません。', en: 'No response data available for this metric.' }
    };
    document.querySelectorAll('#mapTitle,#rankingTitle,#rankingDescription,#responseStats').forEach(el => {
      const val = normalizeText(el.textContent);
      if (dynamicMap[val]) el.textContent = dynamicMap[val][lang];
    });
  }

  let applying = false;
  function applyLanguage(lang = getLang(), opts = {}) {
    if (applying || !document.body) return;
    applying = true;
    try {
      document.documentElement.lang = lang;
      document.title = t('pageTitle', lang);
      for (const [selector, key] of Object.entries(selectors)) setText(selector, key, lang);
      translateOptions(lang);
      translateKnownDynamic(lang);
      // Full-body text walking is relatively expensive on a dashboard with Leaflet/Plotly.
      // Run it only on initial load or an explicit language switch, not after every chart/map redraw.
      if (opts.deep) translateExactTextNodes(document.body, lang);
      document.querySelectorAll('.language-button').forEach(btn => btn.classList.toggle('active', btn.dataset.lang === lang));
    } finally {
      applying = false;
    }
  }

  function setLanguage(lang) {
    const target = lang === 'en' ? 'en' : 'ja';
    localStorage.setItem(LANG_KEY, target);
    // Redraw once so app.js-generated labels, charts, legends and case units use the selected language.
    if (typeof window.updateDashboard === 'function') {
      try { window.updateDashboard(); } catch (e) { /* app not ready yet */ }
    }
    applyLanguage(target, { deep: true });
  }

  window.dashboardI18n = { t, setLanguage, applyLanguage, getLang };
  window.t = t;
  window.applyDashboardLanguage = applyLanguage;
  window.getDashboardLang = getLang;

  document.addEventListener('click', (event) => {
    const btn = event.target.closest('.language-button');
    if (!btn) return;
    setLanguage(btn.dataset.lang);
  });

  document.addEventListener('DOMContentLoaded', () => {
    applyLanguage(getLang(), { deep: true });
  });
})();
