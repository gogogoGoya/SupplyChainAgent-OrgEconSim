import LoadingState from './LoadingState';
import {setReviewChart} from '../utils/chartReview';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import * as echarts from '../utils/echartsRuntime';
import {getArtifactReview,getArtifactView} from '../utils/operationsApi';
import {reviewLocation,displayMetric} from '../utils/reviewDisplay';
import { buildDataUrl, safeFetchJson } from '../utils/dataSource';

const ENTERPRISE_COLORS = {
  Supplier: '#31708e',
  Manufacturer: '#b04a3a',
  Distributor: '#437a56',
  Retailer: '#a87517',
};

const PHASES = [
  { name: 'Stable Baseline', start: 0, end: 39, strategy: 'Establish a coordinated trading baseline' },
  { name: 'Material Price Increase', start: 40, end: 79, strategy: 'Build buffers and protect margins' },
  { name: 'Conversion-Cost Shock', start: 80, end: 119, strategy: 'Constrain production and order exposure' },
  { name: 'Trade and Logistics Tightening', start: 120, end: 159, strategy: 'Reduce external procurement and protect fulfillment' },
  { name: 'Demand Softening and Cost Relief', start: 160, end: 199, strategy: 'Slow operations and control exposure' },
];

const RESPONSE_ACTIONS = {
  procurement: ['create_purchase_order', 'create_purchase_demand', 'create_replenishment_order'],
  production: ['create_production_plan'],
  market: [
    'adjust_sales_demand',
    'accept_order',
    'reject_order',
    'accept_proposal_order',
    'reject_proposal_order',
  ],
};

const toNumber = (value, fallback = 0) => {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
};

const formatNumber = (value, digits = 0) => new Intl.NumberFormat('en-US', {
  maximumFractionDigits: digits,
}).format(toNumber(value));

const formatCurrencyCompact = (value) => {
  const numeric = toNumber(value);
  if (Math.abs(numeric) >= 1000000) return `SCU ${formatNumber(numeric / 1000000, 2)}M`;
  if (Math.abs(numeric) >= 1000) return `SCU ${formatNumber(numeric / 1000, 1)}K`;
  return `SCU ${formatNumber(numeric)}`;
};

const chunkedLoad = async (turns, loader, chunkSize = 20) => {
  const rows = [];
  for (let index = 0; index < turns.length; index += chunkSize) {
    const chunk = turns.slice(index, index + chunkSize);
    const chunkRows = await Promise.all(chunk.map(loader));
    rows.push(...chunkRows);
  }
  return rows;
};

const readTurn = async (dataRoot, turn) => {
  const [environmentRoot, environmentEnd, metrics, enterpriseDaily, runtimeHealth] = await Promise.all([
    safeFetchJson(buildDataUrl(dataRoot, `public/exchange/day${turn}/external_environment.json`)),
    safeFetchJson(buildDataUrl(dataRoot, `public/exchange/day${turn}/end_of_day/external_environment.json`)),
    safeFetchJson(buildDataUrl(dataRoot, `projections/run_metrics/day${turn}/run_metrics_projection.json`)),
    safeFetchJson(buildDataUrl(
      dataRoot,
      `projections/multi_enterprise/day${turn}/enterprise_daily_metrics_projection.json`,
    )),
    safeFetchJson(buildDataUrl(dataRoot, `runtime_health/day${turn}_agent_timeout.json`)),
  ]);
  return {
    turn,
    environment: (
      environmentRoot?.enabled
        ? environmentRoot
        : environmentEnd?.enabled
          ? environmentEnd
          : metrics?.external_environment || {}
    ),
    metrics: metrics || {},
    enterpriseDaily: enterpriseDaily || {},
    runtimeHealth: runtimeHealth || {},
  };
};

const buildEventMarkLines = (rows) => {
  const seen = new Set();
  const events = [];
  rows.forEach((row) => {
    (row.environment?.triggered_events || []).forEach((event) => {
      const eventId = event.event_id || `${event.turn}:${event.label}`;
      if (seen.has(eventId)) return;
      seen.add(eventId);
      events.push({
        ...event,
        eventId,
        name: ({upstream_material_price_surge:'Material price increase',manufacturing_conversion_cost_surge:'Conversion-cost shock',import_trade_logistics_tightening:'Trade and logistics tightening',cost_normalization_and_demand_softening:'Demand softening and cost relief'})[event.event_id] || event.label || eventId,
        xAxis: Number(event.turn ?? row.turn),
      });
    });
  });
  return events;
};

const getRoundActionCounts = (row) => (
  row.metrics?.external_response_observability?.round_actions?.total_action_counts || {}
);

const sumActionGroup = (row, actionNames) => actionNames.reduce(
  (sum, actionName) => sum + toNumber(getRoundActionCounts(row)[actionName]),
  0,
);

const sumAllActions = (rows) => rows.reduce((sum, row) => (
  sum + Object.values(getRoundActionCounts(row)).reduce(
    (roundSum, count) => roundSum + toNumber(count),
    0,
  )
), 0);

const rollingActionCounts = (rows, actionNames, windowSize = 5) => rows.map((_, index) => {
  const start = Math.max(0, index - windowSize + 1);
  return rows.slice(start, index + 1).reduce(
    (sum, row) => sum + sumActionGroup(row, actionNames),
    0,
  );
});

const getTradeSnapshot = (row) => {
  const enterpriseMetrics = row.enterpriseDaily?.enterprise_metrics || {};
  return Object.values(enterpriseMetrics).reduce((totals, enterprise) => {
    const participation = enterprise?.order_participation || {};
    return {
      orderCount: totals.orderCount + toNumber(participation.sold_order_count),
      orderQuantity: totals.orderQuantity + toNumber(participation.sold_quantity),
      orderValue: totals.orderValue + toNumber(participation.sold_value),
    };
  }, { orderCount: 0, orderQuantity: 0, orderValue: 0 });
};

const getAuditStats = (row) => {
  const totals = row.metrics?.round_integrity?.totals || {};
  const valid = toNumber(totals.skill_audit_valid);
  const fallback = toNumber(totals.skill_audit_fallback);
  return { valid, fallback, direct: Math.max(0, valid - fallback) };
};

const formatFactorSummary = (event) => {
  const factorLabels = {
    external_material_price: 'Materials',
    production_conversion_cost: 'Conversion',
    external_logistics_cost: 'Logistics',
    external_demand_quantity: 'Demand',
    external_customer_price: 'Customer Price',
  };
  return Object.entries(event.factor_updates || {})
    .map(([key, value]) => `${factorLabels[key] || key} ${formatNumber(value, 2)}x`)
    .join(' · ');
};

const EvolutionExperimentView = ({ availableDays = [], dataRoot, enterpriseSpecs = [] }) => {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);const [error,setError]=useState('');const [reload,setReload]=useState(0);
  const factorRef = useRef(null);
  const revenueRef = useRef(null);
  const tradeRef = useRef(null);
  const serviceRef = useRef(null);
  const qualityRef = useRef(null);
  const responseRef = useRef(null);
  const chartsRef = useRef([]);

  const enterpriseIds = useMemo(() => (
    enterpriseSpecs.map((item) => item.enterprise_id || item.id).filter(Boolean)
  ), [enterpriseSpecs]);
  const [healthReview,setHealthReview]=useState(null);
  useEffect(()=>{let active=true;const {source,name}=reviewLocation(dataRoot);
    getArtifactReview(source,name).then(r=>{if(active)setHealthReview(r.data?.execution?.metrics?.all_agent_timeout_rounds||null);});
    return()=>{active=false;};
  },[dataRoot]);
  const visibleEnterpriseIds = enterpriseIds.length
    ? enterpriseIds
    : ['Supplier', 'Manufacturer', 'Distributor', 'Retailer'];

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);setError('');
      try{
      const {source,name}=reviewLocation(dataRoot);const result=await getArtifactView(source,name,'evolution',{forceRefresh:reload>0});
      if(result.error&&result.status!==404)throw new Error(result.error);
      if(result.data&&!['available','not_recorded','not_published'].includes(result.data.availability))throw new Error('The long-run projection is invalid or changed. Republish this archive.');
      const fast=result.data?.availability==='available'?result.data.data:null;
      const days=new Set(availableDays);
      const loaded = fast?fast.rows.filter(row=>days.has(row.turn)):await chunkedLoad(availableDays, (turn) => readTurn(dataRoot, turn));
      if (!cancelled) {
        setRows(loaded);
      }
      }catch(error){if(!cancelled)setError(error.message);}
      finally{if(!cancelled)setLoading(false);}
    };
    load();
    return () => { cancelled = true; };
  }, [availableDays.join(','), dataRoot,reload]);

  useEffect(() => {
    chartsRef.current.forEach((chart) => chart?.dispose());
    chartsRef.current = [];
    if (!rows.length||!factorRef.current) return undefined;

    const turns = rows.map((row) => row.turn);
    const eventLines = buildEventMarkLines(rows);
    const commonMarkLine = {
      silent: true,
      symbol: 'none',
      label: { formatter: '{b}', color: '#6b3f34', fontSize: 10 },
      lineStyle: { color: '#c77865', type: 'dashed', width: 1 },
      data: eventLines,
    };
    const baseOption = {
      animation: false,
      tooltip: { trigger: 'axis' },
      grid: { left: 64, right: 54, top: 76, bottom: 60 },
      xAxis: {
        type: 'category',
        name: 'Round',
        nameLocation: 'middle',
        nameGap: 32,
        data: turns,
        axisLabel: { hideOverlap: true },
      },
      textStyle: { fontFamily: 'Inter, system-ui, sans-serif' },
    };

    const factorChart = echarts.init(factorRef.current);
    setReviewChart(factorChart, {
      ...baseOption,
      legend: { top: 4 },
      yAxis: { type: 'value', name: 'Relative to Baseline', axisLabel: { formatter: '{value}x' } },
      series: [
        ['Material Price', 'external_material_price'],
        ['Conversion Cost', 'production_conversion_cost'],
        ['Trade Logistics', 'external_logistics_cost'],
        ['Customer Demand', 'external_demand_quantity'],
        ['Customer Price', 'external_customer_price'],
      ].map(([name, key], index) => ({
        name,
        type: 'line',
        showSymbol: false,
        data: rows.map((row) => toNumber(row.environment?.active_factors?.[key], 1)),
        markLine: index === 0 ? commonMarkLine : undefined,
      })),
    });

    const revenueChart = echarts.init(revenueRef.current);
    setReviewChart(revenueChart, {
      ...baseOption,
      legend: { top: 4 },
      yAxis: { type: 'value', name: 'Cumulative Revenue', axisLabel: { formatter: (value) => `${value / 1000}K` } },
      series: visibleEnterpriseIds.map((enterpriseId, index) => ({
        name: enterpriseId,
        type: 'line',
        showSymbol: false,
        color: ENTERPRISE_COLORS[enterpriseId],
        data: rows.map((row) => (
          row.metrics?.enterprise_metrics?.[enterpriseId]?.finance?.total_revenue ?? null
        )),
        markLine: index === 0 ? commonMarkLine : undefined,
      })),
    });

    const tradeChart = echarts.init(tradeRef.current);
    setReviewChart(tradeChart, {
      ...baseOption,
      legend: { top: 4 },
      yAxis: [
        { type: 'value', name: 'Cumulative Orders' },
        { type: 'value', name: 'Cumulative Value', axisLabel: { formatter: (value) => `${value / 1000}K` } },
      ],
      series: [
        {
          name: 'B2B Orders',
          type: 'line',
          showSymbol: false,
          data: rows.map((row) => getTradeSnapshot(row).orderCount),
          markLine: commonMarkLine,
        },
        {
          name: 'B2B Transaction Value',
          type: 'line',
          yAxisIndex: 1,
          areaStyle: { opacity: 0.08 },
          showSymbol: false,
          data: rows.map((row) => getTradeSnapshot(row).orderValue),
        },
      ],
    });

    const serviceChart = echarts.init(serviceRef.current);
    setReviewChart(serviceChart, {
      ...baseOption,
      legend: { top: 4 },
      yAxis: {
        type: 'value',
        name: 'Cumulative Fulfillment Rate',
        min: 0,
        max: 1,
        axisLabel: { formatter: (value) => `${Math.round(value * 100)}%` },
      },
      series: ['Supplier', 'Manufacturer', 'Distributor'].map((enterpriseId, index) => ({
        name: enterpriseId,
        type: 'line',
        showSymbol: false,
        color: ENTERPRISE_COLORS[enterpriseId],
        data: rows.map((row) => (
          row.metrics?.enterprise_metrics?.[enterpriseId]?.sales?.fill_rate ?? null
        )),
        markLine: index === 0 ? commonMarkLine : undefined,
      })),
    });

    let cumulativeValid = 0;
    let cumulativeDirect = 0;
    const currentDirectRates = [];
    const cumulativeDirectRates = [];
    rows.forEach((row) => {
      const audit = getAuditStats(row);
      cumulativeValid += audit.valid;
      cumulativeDirect += audit.direct;
      currentDirectRates.push(audit.valid ? (audit.direct / audit.valid) * 100 : null);
      cumulativeDirectRates.push(cumulativeValid ? (cumulativeDirect / cumulativeValid) * 100 : null);
    });
    const qualityChart = echarts.init(qualityRef.current);
    setReviewChart(qualityChart, {
      ...baseOption,
      legend: { top: 4 },
      yAxis: {
        type: 'value',
        name: 'Agent Direct-Completion Rate',
        min: 75,
        max: 100,
        axisLabel: { formatter: '{value}%' },
      },
      series: [
        {
          name: 'Per-Round Direct Completion',
          type: 'line',
          step: 'end',
          showSymbol: false,
          data: currentDirectRates,
          markLine: commonMarkLine,
        },
        {
          name: 'Cumulative Direct Completion',
          type: 'line',
          showSymbol: false,
          lineStyle: { width: 3 },
          data: cumulativeDirectRates,
        },
      ],
    });

    const responseChart = echarts.init(responseRef.current);
    setReviewChart(responseChart, {
      ...baseOption,
      legend: { top: 4 },
      yAxis: { type: 'value', name: 'Actions in Prior 5 Turns', minInterval: 1 },
      series: [
        ['Procurement and Replenishment', RESPONSE_ACTIONS.procurement, '#31708e'],
        ['Production Adjustments', RESPONSE_ACTIONS.production, '#b04a3a'],
        ['Order and Market Decisions', RESPONSE_ACTIONS.market, '#a87517'],
      ].map(([name, actionNames, color], index) => ({
        name,
        type: 'line',
        stack: 'response',
        areaStyle: { opacity: 0.16 },
        showSymbol: false,
        color,
        data: rollingActionCounts(rows, actionNames),
        markLine: index === 0 ? commonMarkLine : undefined,
      })),
    });

    chartsRef.current = [
      factorChart,
      revenueChart,
      tradeChart,
      serviceChart,
      qualityChart,
      responseChart,
    ];
    const handleResize = () => chartsRef.current.forEach((chart) => chart?.resize());
    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      chartsRef.current.forEach((chart) => chart?.dispose());
      chartsRef.current = [];
    };
  }, [rows, visibleEnterpriseIds.join('|')]);

  const latest = rows[rows.length - 1] || {};
  const latestMetrics = latest.metrics || {};
  const eventLines = buildEventMarkLines(rows);
  const totalActions = sumAllActions(rows);
  const latestTrade = getTradeSnapshot(latest);
  const auditTotals = rows.reduce((totals, row) => {
    const audit = getAuditStats(row);
    return { valid: totals.valid + audit.valid, direct: totals.direct + audit.direct };
  }, { valid: 0, direct: 0 });
  const directRate = auditTotals.valid ? (auditTotals.direct / auditTotals.valid) * 100 : null;
  const knownHealth=rows.filter(row=>typeof row.runtimeHealth?.all_agent_calls_timed_out==='boolean');
  const allTimeoutTurns=knownHealth.length===rows.length?knownHealth.filter(row=>row.runtimeHealth.all_agent_calls_timed_out).length:null;
  const eventResponses = eventLines.map((event) => ({
    ...event,
    responseActions: sumAllActions(rows.filter(
      (row) => row.turn >= event.xAxis && row.turn <= event.xAxis + 9,
    )),
  }));
  const phaseSummaries = PHASES.map((phase, index) => {
    const phaseRows = rows.filter((row) => row.turn >= phase.start && row.turn <= phase.end);
    const endRow = phaseRows[phaseRows.length - 1];
    const previousRows = rows.filter((row) => row.turn < phase.start);
    const previousTrade = getTradeSnapshot(previousRows[previousRows.length - 1] || {});
    const endTrade = getTradeSnapshot(endRow || {});
    const serviceRates = ['Supplier', 'Manufacturer', 'Distributor']
      .map((enterpriseId) => toNumber(
        endRow?.metrics?.enterprise_metrics?.[enterpriseId]?.sales?.fill_rate,
        NaN,
      ))
      .filter(Number.isFinite);
    return {
      ...phase,
      index,
      loadedRounds: phaseRows.length,
      expectedRounds: phase.end - phase.start + 1,
      actions: sumAllActions(phaseRows),
      newOrders: endTrade.orderCount - previousTrade.orderCount,
      tradeValue: endTrade.orderValue - previousTrade.orderValue,
      serviceRate: serviceRates.length
        ? serviceRates.reduce((sum, value) => sum + value, 0) / serviceRates.length
        : null,
    };
  }).filter((phase) => phase.actions || phase.newOrders || phase.index === 0);

  if(error)return <section className="view-error" role="alert"><h3>Long-run review unavailable</h3><p>{error}</p><button onClick={()=>setReload(v=>v+1)}>Retry long-run review</button></section>;
  if (loading) {
    return <LoadingState label="Loading long-horizon evolution data…" variant="chart"/>;
  }

  return (
    <div className="evolution-view">
      {rows.length !== Number(latestMetrics.completed_steps ?? availableDays.length) && <p className="review-coverage-note">Loaded {rows.length} recorded snapshots of {latestMetrics.completed_steps ?? availableDays.length} declared rounds. Action, timeout and response counts use the loaded snapshots; transaction totals use cumulative endpoint records.</p>}
      <section className="evolution-summary-band">
        <div><span>Completed Run</span><strong>{latestMetrics.completed_steps ?? rows.length} / {latestMetrics.planned_total_steps ?? 200}</strong></div>
        <div><span>Department Agent Direct Completion</span><strong>{directRate==null?'Not recorded':`${formatNumber(directRate,1)}%`}</strong></div>
        <div><span>All-Agent Timeout Turns</span><strong>{healthReview?.value!=null?displayMetric(healthReview):allTimeoutTurns==null?'Not recorded':allTimeoutTurns}</strong></div>
        <div><span>Operating Actions</span><strong>{formatNumber(totalActions)}</strong></div>
        <div><span>B2B Transactions</span><strong>{formatNumber(latestTrade.orderCount)}</strong></div>
        <div><span>B2B Transaction Value</span><strong>{formatCurrencyCompact(latestTrade.orderValue)}</strong></div>
      </section>

      <section className="evolution-event-strip">
        {eventResponses.length ? eventResponses.map((event) => (
          <div key={`${event.xAxis}:${event.name}`}>
            <span>Round {event.xAxis}</span>
            <strong>{event.name}</strong>
            <small>{formatFactorSummary(event)}</small>
            <small>{formatNumber(event.responseActions)} operating responses in the next 10 turns</small>
          </div>
        )) : <p>Triggered external changes appear here from Round 40 onward.</p>}
      </section>

      <section className="evolution-phase-table-wrap">
        <h3>Phase-Level Adaptation Profile</h3>
        <div className="evolution-phase-table-scroll">
          <table className="evolution-phase-table">
            <thead>
              <tr>
                <th>Phase</th>
                <th>Strategic Orientation</th>
                <th>Snapshot Coverage</th>
                <th>Recorded Operating Actions</th>
                <th>New B2B Orders</th>
                <th>New Transaction Value</th>
                <th>Mean B2B Fulfillment Rate</th>
              </tr>
            </thead>
            <tbody>
              {phaseSummaries.map((phase) => (
                <tr key={phase.name}>
                  <td><strong>{phase.name}</strong><small>Round {phase.start}-{phase.end}</small></td>
                  <td>{phase.strategy}</td>
                  <td>{phase.loadedRounds} / {phase.expectedRounds}</td>
                  <td>{formatNumber(phase.actions)}</td>
                  <td>{formatNumber(phase.newOrders)}</td>
                  <td>{formatCurrencyCompact(phase.tradeValue)}</td>
                  <td>{phase.serviceRate === null ? '-' : `${formatNumber(phase.serviceRate * 100, 1)}%`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="evolution-chart-grid">
        <article><h3>External Environment Evolution</h3><div ref={factorRef} className="evolution-chart" /></article>
        <article><h3>Cumulative Enterprise Revenue</h3><div ref={revenueRef} className="evolution-chart" /></article>
        <article><h3>Supply-Chain Transaction Scale</h3><div ref={tradeRef} className="evolution-chart" /></article>
        <article><h3>B2B Fulfillment Resilience</h3><div ref={serviceRef} className="evolution-chart" /></article>
        <article><h3>Long-Horizon Agent Stability</h3><div ref={qualityRef} className="evolution-chart" /></article>
        <article><h3>Post-Shock Operating Responses</h3><div ref={responseRef} className="evolution-chart" /></article>
      </section>
    </div>
  );
};

export default EvolutionExperimentView;
