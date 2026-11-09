import LoadingState from './LoadingState';
import {setReviewChart} from '../utils/chartReview';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import * as echarts from '../utils/echartsRuntime';
import { buildDataUrl, safeFetchJson } from '../utils/dataSource';

const CHART_COLORS = {
  price: '#255f63',
  supply: '#d99735',
  plan: '#4f9d69',
  deviation: '#c76538',
  tolerance: '#7c8b7b',
  inventory: '#2d6cdf',
  backlog: '#c04949',
  fillRate: '#2b9f8a',
  theory: '#9f7aea',
  agent: '#2f7d5b',
};

const ACTION_REASON_NOISE_TERMS = [
  'recommended_plan_quantity',
  'recommended_daily_capacity',
  'recommended quantity',
  'recovery recommendation',
  'recovery_guard recommendation',
  'recovery_guard recommendation',
  'allowed deviation',
  'deviation no greater than',
  '±2',
  'affordable_recovery_candidates',
  'should_recover_now',
];

const toFiniteNumber = (value, fallback = null) => {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
};

const formatNumber = (value, maximumFractionDigits = 2) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return '-';
  }
  return new Intl.NumberFormat('en-US', { maximumFractionDigits }).format(numeric);
};

const formatPercent = (value) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return '-';
  }
  return `${formatNumber(numeric * 100, 1)}%`;
};

const formatSigned = (value, maximumFractionDigits = 2) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return '-';
  }
  const absText = formatNumber(Math.abs(numeric), maximumFractionDigits);
  return numeric > 0 ? `+${absText}` : numeric < 0 ? `-${absText}` : '0';
};

const truncateText = (value, maxLength = 96) => {
  const text = String(value || '');
  if (text.length <= maxLength) {
    return text || '-';
  }
  return `${text.slice(0, maxLength)}...`;
};

const extractCobwebHistory = (payload) => {
  const history = (
    payload?.data?.history
    || payload?.history
    || payload?.data?.cobweb_history
    || payload?.cobweb_history
    || payload?.simulation_context?.cobweb_history
    || []
  );
  return Array.isArray(history) ? history : [];
};

const getDepartmentSelfState = (payload) => payload?.self_state || payload?.data?.self_state || {};

const getInventoryQuantity = (inventoryPayload, productId = 'beer') => {
  const selfState = getDepartmentSelfState(inventoryPayload);
  const items = Array.isArray(selfState.inventory_items) ? selfState.inventory_items : [];
  const item = items.find((entry) => String(entry?.item_id).toLowerCase() === String(productId).toLowerCase());
  return toFiniteNumber(item?.su_quantity, toFiniteNumber(item?.quantity));
};

const getPlanAction = (actionsPayload) => {
  const actions = Array.isArray(actionsPayload) ? actionsPayload : actionsPayload?.actions || [];
  return actions.find((entry) => entry?.action?.action_name === 'create_production_plan')
    || actions.find((entry) => entry?.action?.action_name && entry?.action?.action_name !== 'pass')
    || null;
};

const buildEquilibriumFromConfig = (cobwebConfig) => {
  const demandIntercept = toFiniteNumber(cobwebConfig?.demand_intercept);
  const demandSlope = toFiniteNumber(cobwebConfig?.demand_slope);
  const supplyIntercept = toFiniteNumber(cobwebConfig?.supply_intercept);
  const supplySlope = toFiniteNumber(cobwebConfig?.supply_slope);

  if (
    demandIntercept === null
    || demandSlope === null
    || supplyIntercept === null
    || supplySlope === null
    || demandSlope + supplySlope === 0
  ) {
    return { price: null, quantity: null };
  }

  const price = (demandIntercept - supplyIntercept) / (demandSlope + supplySlope);
  const quantity = supplyIntercept + supplySlope * price;
  return { price, quantity };
};

const getProducerSpec = (enterpriseSpecs = []) => (
  enterpriseSpecs.find((spec) => (spec?.enabled_functions || []).includes('production'))
  || enterpriseSpecs[0]
  || null
);

const mean = (values) => {
  const finite = values.filter((value) => Number.isFinite(Number(value)));
  if (finite.length === 0) {
    return null;
  }
  return finite.reduce((total, value) => total + Number(value), 0) / finite.length;
};

const getScenarioType = (runMeta) => {
  const scenarioId = String(runMeta?.scenario_id || '').toLowerCase();
  const stabilityLabel = String(
    runMeta?.cobweb_config?.stability_label
    || runMeta?.scenario_config?.simulation?.cobweb_config?.stability_label
    || ''
  ).toLowerCase();
  if (scenarioId.includes('divergent') || stabilityLabel === 'divergent') {
    return 'divergent';
  }
  if (scenarioId.includes('neutral') || stabilityLabel === 'neutral') {
    return 'neutral';
  }
  if (scenarioId.includes('convergent') || stabilityLabel === 'convergent') {
    return 'convergent';
  }
  return 'unknown';
};

const getScenarioExpectation = (scenarioType) => {
  if (scenarioType === 'convergent') {
    return {
      title: 'Convergent: Deviations Should Contract',
      detail: 'With a supply slope below the demand slope, price and supply should converge toward equilibrium.',
    };
  }
  if (scenarioType === 'neutral') {
    return {
      title: 'Neutral: Theory Predicts a Persistent Cycle',
      detail: 'Similar supply and demand slopes produce constant-amplitude oscillation under mechanical supply; agent stabilization indicates endogenous damping.',
    };
  }
  if (scenarioType === 'divergent') {
    return {
      title: 'Divergent: Deviations Should Expand or Oscillate Strongly',
      detail: 'A supply slope above the demand slope amplifies deviations, although enterprise constraints may bound the resulting oscillation.',
    };
  }
  return {
    title: 'Cobweb Scenario',
    detail: 'Evaluate whether lagged price–supply feedback produces cobweb dynamics.',
  };
};

const getCobwebControllerMeta = (runMeta) => {
  const cobwebConfig = runMeta?.cobweb_config || runMeta?.scenario_config?.simulation?.cobweb_config || {};
  const scripted = cobwebConfig.production_response_mode === 'scripted_formula'
    || String(runMeta?.scenario_id || '').endsWith('_scripted');
  return scripted
    ? {
      mode: 'scripted',
      label: 'Scripted',
      expectedSupplySource: 'scripted_production_plan_created',
      inputFilterRequired: false,
    }
    : {
      mode: 'agent',
      label: 'Agent',
      expectedSupplySource: 'agent_production_plan_created',
      inputFilterRequired: true,
    };
};

const buildSummary = (rows, runMeta, producerId) => {
  const scenarioType = getScenarioType(runMeta);
  const scenarioExpectation = getScenarioExpectation(scenarioType);
  const controller = getCobwebControllerMeta(runMeta);
  const planRows = rows.filter((row) => row.planQuantity !== null);
  const latestRow = rows[rows.length - 1] || {};
  const equilibriumPrice = rows.find((row) => row.equilibriumPrice !== null)?.equilibriumPrice ?? null;
  const equilibriumQuantity = rows.find((row) => row.equilibriumQuantity !== null)?.equilibriumQuantity ?? null;
  const finalPriceGap = latestRow.price !== null && equilibriumPrice !== null
    ? latestRow.price - equilibriumPrice
    : null;
  const finalQuantityGap = latestRow.marketSupply !== null && equilibriumQuantity !== null
    ? latestRow.marketSupply - equilibriumQuantity
    : null;
  const priceDeviationRows = rows
    .map((row) => row.priceDeviationAbs)
    .filter((value) => Number.isFinite(Number(value)));
  const postDay0PriceDeviation = rows
    .filter((row) => row.day !== 0)
    .map((row) => row.priceDeviationAbs)
    .filter((value) => Number.isFinite(Number(value)));
  const splitIndex = Math.max(1, Math.floor(postDay0PriceDeviation.length / 2));
  const firstHalfDeviation = postDay0PriceDeviation.slice(0, splitIndex);
  const secondHalfDeviation = postDay0PriceDeviation.slice(splitIndex);
  const firstHalfMeanAbsPrice = mean(firstHalfDeviation);
  const secondHalfMeanAbsPrice = mean(secondHalfDeviation);
  const lastSixMeanAbsPrice = mean(priceDeviationRows.slice(-6));
  const normalizationAppliedCount = rows.filter((row) => row.targetNormalizationApplied).length;
  const controllerSupplyCount = rows.filter((row) => (
    row.marketSupplySource === controller.expectedSupplySource
  )).length;
  const fallbackSupplyCount = rows.filter((row) => (
    row.marketSupplySource && row.marketSupplySource !== controller.expectedSupplySource
  )).length;
  const agentInputFilterCount = rows.filter((row) => row.agentInputFilterApplied).length;
  const residualNoiseCount = rows.filter((row) => row.actionReasonNoiseTerms.length > 0).length;
  const directionRows = planRows.filter((row) => row.directionOk !== null);
  const directionOkCount = directionRows.filter((row) => row.directionOk).length;
  const pricesPostDay0 = rows.filter((row) => row.day !== 0).map((row) => row.price).filter((value) => value !== null);
  const suppliesPostDay0 = rows.filter((row) => row.day !== 0).map((row) => row.marketSupply).filter((value) => value !== null);
  const priceRangePostDay0 = pricesPostDay0.length > 0 ? Math.max(...pricesPostDay0) - Math.min(...pricesPostDay0) : null;
  const supplyRangePostDay0 = suppliesPostDay0.length > 0 ? Math.max(...suppliesPostDay0) - Math.min(...suppliesPostDay0) : null;
  const engineeringReady = rows.length > 0
    && controllerSupplyCount >= Math.max(0, rows.length - 1)
    && planRows.length === rows.length
    && (!controller.inputFilterRequired || agentInputFilterCount === rows.length);
  const agentAligned = directionRows.length > 0 && directionOkCount === directionRows.length && residualNoiseCount === 0;

  let effectExplanation = `Cobweb data are available; interpretation combines price deviations with ${controller.label} behavior.`;
  if (scenarioType === 'convergent') {
    const converged = (
      secondHalfMeanAbsPrice !== null
      && firstHalfMeanAbsPrice !== null
      && secondHalfMeanAbsPrice < firstHalfMeanAbsPrice
      && lastSixMeanAbsPrice !== null
      && lastSixMeanAbsPrice <= Math.max(4, firstHalfMeanAbsPrice * 0.45)
    );
    effectExplanation = converged
      ? 'Price deviations are substantially lower in the second half, with small late-stage fluctuations around equilibrium.'
      : 'Price has not stabilized near equilibrium; inspect a longer horizon and the production rationale.';
  } else if (scenarioType === 'neutral') {
    const stabilized = lastSixMeanAbsPrice !== null && lastSixMeanAbsPrice <= 1;
    effectExplanation = stabilized
      ? (controller.mode === 'scripted'
        ? 'The formula-driven trajectory unexpectedly stabilizes near equilibrium; inspect the supply source and recurrence parameters.'
        : 'The agent rapidly damps the theoretical constant-amplitude shock toward equilibrium.')
      : 'A constant-amplitude or multi-state cycle remains; inspect whether its amplitude stabilizes.';
  } else if (scenarioType === 'divergent') {
    const highVolatility = (
      priceRangePostDay0 !== null
      && lastSixMeanAbsPrice !== null
      && priceRangePostDay0 >= 100
      && lastSixMeanAbsPrice >= 20
    );
    effectExplanation = highVolatility
      ? (controller.mode === 'scripted'
        ? 'Divergent parameters and price/quantity bounds jointly produce strong but bounded oscillation.'
        : 'Divergent parameters create substantial price fluctuations, while agent operating constraints bound the trajectory.')
      : (controller.mode === 'scripted'
        ? 'The formula baseline fluctuates weakly; inspect the supply source, recurrence parameters, and configured bounds.'
        : 'Price fluctuations are moderated by agent smoothing or configured bounds.');
  }

  return {
    runId: runMeta?.run_id || '-',
    scenarioName: runMeta?.scenario_meta?.name || runMeta?.scenario_id || '-',
    scenarioType,
    scenarioExpectation,
    producerId,
    planCount: planRows.length,
    normalizationAppliedCount,
    controllerMode: controller.mode,
    controllerLabel: controller.label,
    expectedSupplySource: controller.expectedSupplySource,
    controllerSupplyCount,
    fallbackSupplyCount,
    agentInputFilterCount,
    directionOkCount,
    directionTotal: directionRows.length,
    residualNoiseCount,
    finalPriceGap,
    finalQuantityGap,
    firstHalfMeanAbsPrice,
    secondHalfMeanAbsPrice,
    lastSixMeanAbsPrice,
    priceRangePostDay0,
    supplyRangePostDay0,
    latestRow,
    equilibriumPrice,
    equilibriumQuantity,
    engineeringReady,
    agentAligned,
    effectExplanation,
  };
};

function CobwebModelValidationView({
  availableDays = [],
  dataRoot,
  enterpriseSpecs = [],
  currentRunId,
  currentRunSourceLabel,
}) {
  const convergenceRef = useRef(null);
  const productionRef = useRef(null);
  const deviationRef = useRef(null);
  const phaseRef = useRef(null);
  const operationsRef = useRef(null);
  const producerSpec = useMemo(() => getProducerSpec(enterpriseSpecs), [enterpriseSpecs]);
  const producerId = producerSpec?.enterprise_id || producerSpec?.id || 'Manufacturer';
  const [showCobwebDetails, setShowCobwebDetails] = useState(false);
  const [viewState, setViewState] = useState({
    loading: true,
    error: null,
    rows: [],
    runMeta: null,
  });

  useEffect(() => {
    let cancelled = false;

    const loadData = async () => {
      try {
        setViewState((prev) => ({ ...prev, loading: true, error: null }));
        const days = [...availableDays]
          .map((day) => Number(day))
          .filter((day) => Number.isFinite(day))
          .sort((left, right) => left - right);

        if (days.length === 0) {
          setViewState({ loading: false, error: null, rows: [], runMeta: null });
          return;
        }

        const runMeta = await safeFetchJson(buildDataUrl(dataRoot, 'run_meta.json'));
        const cobwebConfig = runMeta?.scenario_config?.simulation?.cobweb_config || {};
        const fallbackEquilibrium = buildEquilibriumFromConfig(cobwebConfig);
        const latestDay = days[days.length - 1];
        const demandPayload = await safeFetchJson(buildDataUrl(dataRoot, `public/exchange/day${latestDay}/external_demand.json`));
        const demandHistory = extractCobwebHistory(demandPayload);
        const demandByRound = new Map(demandHistory.map((entry) => [Number(entry?.round), entry]));

        const rows = await Promise.all(days.map(async (day) => {
          const [
            productionPayload,
            actionsPayload,
            inventoryPayload,
            salesPayload,
          ] = await Promise.all([
            safeFetchJson(buildDataUrl(dataRoot, `enterprises/${producerId}/department/production/day${day}/production.json`)),
            safeFetchJson(buildDataUrl(dataRoot, `enterprises/${producerId}/department/production/day${day}/production_action.json`)),
            safeFetchJson(buildDataUrl(dataRoot, `enterprises/${producerId}/department/inventory/day${day}/inventory.json`)),
            safeFetchJson(buildDataUrl(dataRoot, `enterprises/${producerId}/department/sales/day${day}/sales.json`)),
          ]);

          const productionSelfState = getDepartmentSelfState(productionPayload);
          const signal = productionSelfState?.cobweb_decision_signal || productionPayload?.cobweb_decision_signal || {};
          const productionHistory = extractCobwebHistory(productionPayload);
          const marketRecord = demandByRound.get(day)
            || productionHistory.find((entry) => Number(entry?.round) === day)
            || productionHistory[productionHistory.length - 1]
            || null;
          const planAction = getPlanAction(actionsPayload);
          const planQuantity = toFiniteNumber(planAction?.action?.action_param?.quantity);
          const theoreticalSupply = toFiniteNumber(
            marketRecord?.theoretical_planned_supply_quantity,
            toFiniteNumber(signal?.theoretical_supply_quantity)
          );
          const actualSupply = toFiniteNumber(
            marketRecord?.actual_supply_quantity,
            toFiniteNumber(signal?.actual_supply_quantity)
          );
          const marketSupply = toFiniteNumber(
            marketRecord?.market_supply_quantity,
            toFiniteNumber(marketRecord?.planned_supply_quantity, toFiniteNumber(signal?.market_supply_quantity))
          );
          const salesSelfState = getDepartmentSelfState(salesPayload);
          const serviceSummary = salesSelfState?.service_level_summary || {};
          const equilibriumPrice = toFiniteNumber(signal?.equilibrium_price, fallbackEquilibrium.price);
          const equilibriumQuantity = toFiniteNumber(signal?.equilibrium_quantity, fallbackEquilibrium.quantity);
          const price = toFiniteNumber(marketRecord?.unit_price, toFiniteNumber(signal?.current_market_price));
          const direction = signal?.suggested_supply_direction || '';
          const actionReason = planAction?.action_reason || '';
          const actionReasonNoiseTerms = ACTION_REASON_NOISE_TERMS.filter((term) => actionReason.includes(term));

          return {
            day,
            price,
            marketSupply,
            plannedSupply: marketSupply,
            marketDemandQuantity: toFiniteNumber(marketRecord?.quantity),
            actualSupply,
            theoreticalSupply,
            marketSupplySource: marketRecord?.market_supply_source || signal?.market_supply_source || '',
            planQuantity,
            planToMarketGap: planQuantity !== null && marketSupply !== null ? planQuantity - marketSupply : null,
            planToTheoryGap: planQuantity !== null && theoreticalSupply !== null ? planQuantity - theoreticalSupply : null,
            priceDeviation: price !== null && equilibriumPrice !== null ? price - equilibriumPrice : null,
            priceDeviationAbs: price !== null && equilibriumPrice !== null ? Math.abs(price - equilibriumPrice) : null,
            supplyDeviation: marketSupply !== null && equilibriumQuantity !== null ? marketSupply - equilibriumQuantity : null,
            supplyDeviationAbs: marketSupply !== null && equilibriumQuantity !== null ? Math.abs(marketSupply - equilibriumQuantity) : null,
            actionName: planAction?.action?.action_name || 'No production-plan action',
            actionReason,
            actionReasonNoiseTerms,
            direction,
            directionOk: null,
            equilibriumPrice,
            equilibriumQuantity,
            target: productionPayload?.target || '',
            rawTarget: productionPayload?.raw_target || '',
            targetNormalizationApplied: Boolean(productionPayload?.target_normalization?.applied),
            targetNormalizationSource: productionPayload?.target_normalization?.source || '',
            targetPriority: productionPayload?.target_normalization?.non_cobweb_target_priority || '',
            agentInputFilterApplied: Boolean(productionSelfState?.agent_input_filter?.applied),
            inventoryQuantity: getInventoryQuantity(inventoryPayload, signal?.product_id || cobwebConfig?.product_id || 'beer'),
            fillRate: toFiniteNumber(serviceSummary?.fill_rate),
            backlogQuantity: toFiniteNumber(serviceSummary?.confirmed_order_backlog_quantity, toFiniteNumber(serviceSummary?.backlog_quantity)),
            totalProduction: toFiniteNumber(productionSelfState?.production_metrics?.total_production),
          };
        }));
        let previousPlanQuantity = null;
        const rowsWithDirection = rows.map((row) => {
          let directionOk = null;
          if (row.planQuantity !== null && previousPlanQuantity !== null) {
            if (row.priceDeviation > 0) {
              directionOk = row.planQuantity >= previousPlanQuantity;
            } else if (row.priceDeviation < 0) {
              directionOk = row.planQuantity <= previousPlanQuantity;
            } else {
              directionOk = true;
            }
          } else if (row.planQuantity !== null) {
            directionOk = true;
          }
          if (row.planQuantity !== null) {
            previousPlanQuantity = row.planQuantity;
          }
          return { ...row, directionOk };
        });

        if (!cancelled) {
          setViewState({
            loading: false,
            error: null,
            rows: rowsWithDirection.filter((row) => (
              row.price !== null
              || row.marketSupply !== null
              || row.planQuantity !== null
              || row.theoreticalSupply !== null
            )),
            runMeta,
          });
        }
      } catch (error) {
        if (!cancelled) {
          setViewState({
            loading: false,
            error: `Failed to load cobweb-model validation data: ${error.message}`,
            rows: [],
            runMeta: null,
          });
        }
      }
    };

    loadData();
    return () => {
      cancelled = true;
    };
  }, [availableDays, dataRoot, producerId]);

  const summary = useMemo(
    () => buildSummary(viewState.rows, viewState.runMeta, producerId),
    [viewState.rows, viewState.runMeta, producerId]
  );

  useEffect(() => {
    const rows = viewState.rows;
    if (rows.length === 0) {
      return undefined;
    }

    const days = rows.map((row) => `D${row.day}`);
    const finitePriceDeviations = rows
      .map((row) => row.priceDeviationAbs)
      .filter((value) => Number.isFinite(Number(value)));
    const instances = [
      {
        ref: convergenceRef,
        option: {
          color: [CHART_COLORS.price, CHART_COLORS.supply, CHART_COLORS.agent, '#8aa09a', '#cbb07a'],
          tooltip: { trigger: 'axis' },
          legend: { top: 0 },
          grid: { left: 52, right: 56, top: 48, bottom: 36 },
          xAxis: { type: 'category', data: days, boundaryGap: false },
          yAxis: [
            { type: 'value', name: 'Price', scale: true },
            { type: 'value', name: 'Supply Quantity', scale: true },
          ],
          series: [
            {
              name: 'Market Price',
              type: 'line',
              smooth: true,
              symbolSize: 7,
              data: rows.map((row) => row.price),
            },
            {
              name: 'Planned Supply',
              type: 'line',
              yAxisIndex: 1,
              smooth: true,
              symbolSize: 7,
              data: rows.map((row) => row.marketSupply),
            },
            {
              name: `${summary.controllerLabel} Production Plan`,
              type: 'bar',
              yAxisIndex: 1,
              barMaxWidth: 18,
              data: rows.map((row) => row.planQuantity),
            },
            {
              name: 'Equilibrium Price',
              type: 'line',
              symbol: 'none',
              lineStyle: { type: 'dashed', width: 2 },
              data: rows.map((row) => row.equilibriumPrice),
            },
            {
              name: 'Equilibrium Quantity',
              type: 'line',
              yAxisIndex: 1,
              symbol: 'none',
              lineStyle: { type: 'dashed', width: 2 },
              data: rows.map((row) => row.equilibriumQuantity),
            },
          ],
        },
      },
      {
        ref: productionRef,
        option: {
          color: [CHART_COLORS.plan, CHART_COLORS.supply, CHART_COLORS.theory],
          tooltip: { trigger: 'axis' },
          legend: { top: 0 },
          grid: { left: 48, right: 18, top: 48, bottom: 36 },
          xAxis: { type: 'category', data: days },
          yAxis: { type: 'value', name: 'Quantity', scale: true },
          series: [
            {
              name: `${summary.controllerLabel} Production Plan`,
              type: 'line',
              smooth: true,
              symbolSize: 7,
              data: rows.map((row) => row.planQuantity),
            },
            {
              name: 'Market-Used Supply',
              type: 'line',
              smooth: true,
              symbolSize: 7,
              data: rows.map((row) => row.marketSupply),
            },
            {
              name: 'Theoretical Reference Supply',
              type: 'line',
              smooth: true,
              symbolSize: 5,
              lineStyle: { type: 'dotted' },
              data: rows.map((row) => row.theoreticalSupply),
            },
          ],
        },
      },
      {
        ref: deviationRef,
        option: {
          color: [CHART_COLORS.deviation, CHART_COLORS.supply],
          tooltip: { trigger: 'axis' },
          legend: { top: 0 },
          grid: { left: 56, right: 52, top: 48, bottom: 36 },
          xAxis: { type: 'category', data: days },
          yAxis: [
            { type: 'value', name: 'Price Deviation', scale: true },
            { type: 'value', name: 'Supply Deviation', scale: true },
          ],
          series: [
            {
              name: '|Price - Equilibrium Price|',
              type: 'bar',
              barMaxWidth: 22,
              data: rows.map((row) => row.priceDeviationAbs),
              itemStyle: { color: CHART_COLORS.deviation },
            },
            {
              name: '|Supply - Equilibrium Quantity|',
              type: 'line',
              yAxisIndex: 1,
              smooth: true,
              symbolSize: 7,
              data: rows.map((row) => row.supplyDeviationAbs),
              itemStyle: { color: CHART_COLORS.supply },
              markLine: {
                symbol: 'none',
                lineStyle: { type: 'dashed', color: '#91a39a' },
                data: finitePriceDeviations.length > 0 ? [{ yAxis: 0, name: 'Equilibrium' }] : [],
              },
            },
          ],
        },
      },
      {
        ref: phaseRef,
        option: {
          color: [CHART_COLORS.price, CHART_COLORS.supply],
          tooltip: {
            trigger: 'item',
            formatter: (params) => {
              const row = rows[params.dataIndex];
              return `D${row?.day}<br/>Supply: ${formatNumber(row?.marketSupply)}<br/>Price: ${formatNumber(row?.price)}<br/>Source: ${row?.marketSupplySource || '-'}`;
            },
          },
          grid: { left: 56, right: 24, top: 24, bottom: 44 },
          xAxis: { type: 'value', name: 'Market Supply', scale: true },
          yAxis: { type: 'value', name: 'Price', scale: true },
          series: [
            {
              name: 'Cobweb Trajectory',
              type: 'line',
              smooth: false,
              symbolSize: 9,
              data: rows.map((row) => [row.marketSupply, row.price]),
              markPoint: {
                symbolSize: 58,
                data: summary.equilibriumQuantity !== null && summary.equilibriumPrice !== null
                  ? [{ name: 'Equilibrium', coord: [summary.equilibriumQuantity, summary.equilibriumPrice] }]
                  : [],
              },
            },
          ],
        },
      },
      {
        ref: operationsRef,
        option: {
          color: [CHART_COLORS.inventory, CHART_COLORS.backlog, CHART_COLORS.fillRate],
          tooltip: { trigger: 'axis' },
          legend: { top: 0 },
          grid: { left: 54, right: 52, top: 48, bottom: 36 },
          xAxis: { type: 'category', data: days },
          yAxis: [
            { type: 'value', name: 'Quantity', scale: true },
            { type: 'value', name: 'Fill Rate', min: 0, max: 1, axisLabel: { formatter: '{value}' } },
          ],
          series: [
            {
              name: 'Finished-Goods Inventory',
              type: 'line',
              smooth: true,
              data: rows.map((row) => row.inventoryQuantity),
            },
            {
              name: 'Confirmed Backlog',
              type: 'line',
              smooth: true,
              data: rows.map((row) => row.backlogQuantity),
            },
            {
              name: 'Fill Rate',
              type: 'line',
              yAxisIndex: 1,
              smooth: true,
              data: rows.map((row) => row.fillRate),
            },
          ],
        },
      },
    ].map(({ ref, option }) => {
      if (!ref.current) {
        return null;
      }
      const chart = echarts.init(ref.current);
      setReviewChart(chart, option);
      return chart;
    }).filter(Boolean);

    const resizeCharts = () => instances.forEach((chart) => chart.resize());
    window.addEventListener('resize', resizeCharts);
    return () => {
      window.removeEventListener('resize', resizeCharts);
      instances.forEach((chart) => chart.dispose());
    };
  }, [viewState.rows, summary]);

  if (viewState.loading) {
    return (
      <div className="cobweb-page">
        <LoadingState label="Loading cobweb-model validation data…" variant="chart"/>
      </div>
    );
  }

  if (viewState.error) {
    return (
      <div className="cobweb-page">
        <div className="cobweb-empty-state warning">{viewState.error}</div>
      </div>
    );
  }

  if (viewState.rows.length === 0) {
    return (
      <div className="cobweb-page">
        <div className="cobweb-empty-state">
          No cobweb-model data were detected. Confirm that the scenario uses market_demand_mode=cobweb.
        </div>
      </div>
    );
  }

  const latestRow = summary.latestRow || {};
  const cobwebConfig = viewState.runMeta?.cobweb_config || viewState.runMeta?.scenario_config?.simulation?.cobweb_config || {};
  const slopeRelation = toFiniteNumber(cobwebConfig?.supply_slope) !== null && toFiniteNumber(cobwebConfig?.demand_slope) !== null
    ? `${formatNumber(cobwebConfig.supply_slope, 2)} / ${formatNumber(cobwebConfig.demand_slope, 2)}`
    : '-';
  const cobwebTypeLabel = {
    convergent: 'Convergent',
    neutral: 'Neutral',
    divergent: 'Divergent',
    unknown: 'Unclassified',
  }[summary.scenarioType] || 'Unclassified';
  const cobwebTypeNote = summary.scenarioType === 'unknown'
    ? 'The run lacks a recognizable cobweb scenario identifier'
    : `Classified by d / b = ${slopeRelation}: below 1 convergent, equal to 1 neutral, above 1 divergent`;
  const scenarioEffectTitle = String(summary.scenarioExpectation.title || '').replace(/^.*?:\s*/, '');
  const scenarioEffectText = [scenarioEffectTitle, summary.effectExplanation]
    .filter(Boolean)
    .join(', ');
  const cobwebParameterCards = [
    {
      label: 'a Demand Intercept',
      value: formatNumber(cobwebConfig?.demand_intercept, 2),
      note: 'Theoretical market-demand ceiling at zero price',
    },
    {
      label: 'b Demand Slope',
      value: formatNumber(cobwebConfig?.demand_slope, 2),
      note: 'Demand reduction per unit increase in price',
    },
    {
      label: 'c Supply Intercept',
      value: formatNumber(cobwebConfig?.supply_intercept, 2),
      note: 'Theoretical base supply at zero lagged price',
    },
    {
      label: 'd Supply Slope',
      value: formatNumber(cobwebConfig?.supply_slope, 2),
      note: 'Theoretical supply increase per unit increase in lagged price',
    },
    {
      label: 'd / b Stability Relation',
      value: slopeRelation,
      note: 'Below 1 convergent, equal to 1 constant-amplitude, above 1 divergent',
    },
    {
      label: 'P0 Initial Price',
      value: formatNumber(cobwebConfig?.initial_price, 2),
      note: 'Lagged price signal used for Day 0 initialization',
    },
    {
      label: 'Supply Lag',
      value: `${formatNumber(cobwebConfig?.endogenous_supply_lag_rounds, 0)} turns`,
      note: `The market uses the prior-turn ${summary.controllerLabel} production plan as supply`,
    },
    {
      label: 'Supply Source',
      value: cobwebConfig?.endogenous_supply_source || '-',
      note: `Uses production-plan quantities created by ${summary.controllerLabel}`,
    },
  ];

  return (
    <div className="cobweb-page">
      <section className="cobweb-hero">
        <div className="cobweb-hero-main">
          <span className="cobweb-eyebrow">Cobweb Validation</span>
          <div className="cobweb-hero-title-row">
            <h2>Cobweb-Model Experiment Analysis</h2>
          </div>
          <p>
            Demand follows <strong>Qd_t = a - b * P_t</strong>, while <strong>Q_ref,t = c + d * P_(t-1)</strong> provides the theoretical lagged-supply reference. Under <strong>{cobwebConfig?.production_response_mode || '-'}</strong>, the market derives the current price from the <strong>{summary.controllerLabel} production plan</strong>.
          </p>
        </div>
        <aside className={`cobweb-hero-summary cobweb-type-card ${summary.scenarioType}`}>
          <span>Cobweb Type</span>
          <div className="cobweb-summary-heading">
            <strong>{cobwebTypeLabel}</strong>
            <p>{scenarioEffectText}</p>
          </div>
          <small>{cobwebTypeNote}</small>
          <button
            className="cobweb-detail-toggle"
            type="button"
            aria-expanded={showCobwebDetails}
            onClick={() => setShowCobwebDetails((current) => !current)}
          >
            {showCobwebDetails ? 'Hide Parameter Details' : 'Show Parameter Details'}
          </button>
        </aside>
        {showCobwebDetails && (
          <div className="cobweb-param-grid">
            {cobwebParameterCards.map((item) => (
              <div className="cobweb-param-card" key={item.label}>
                <span>{item.label}</span>
                <strong>{item.value}</strong>
                <small>{item.note}</small>
              </div>
            ))}
          </div>
        )}
      </section>

      <div className="cobweb-source-strip">
        <span>Run: {currentRunId || summary.runId}</span>
        <span>Scenario: {summary.scenarioName}</span>
        <span>Producer: {summary.producerId}</span>
        <span>Data Source: {currentRunSourceLabel || dataRoot}</span>
      </div>

      <section className="cobweb-kpi-grid">
        <div className="cobweb-kpi-card">
          <span>Final Price Deviation</span>
          <strong>{formatSigned(summary.finalPriceGap, 4)}</strong>
          <small>P* = {formatNumber(summary.equilibriumPrice, 2)}</small>
        </div>
        <div className="cobweb-kpi-card">
          <span>Final Supply Deviation</span>
          <strong>{formatSigned(summary.finalQuantityGap, 4)}</strong>
          <small>Q* = {formatNumber(summary.equilibriumQuantity, 2)}</small>
        </div>
        <div className="cobweb-kpi-card">
          <span>{summary.controllerLabel} Supply Coverage</span>
          <strong>{summary.controllerSupplyCount}/{viewState.rows.length}</strong>
          <small>fallback in {summary.fallbackSupplyCount} turns</small>
        </div>
        <div className="cobweb-kpi-card">
          <span>Late-Stage Mean Price Deviation</span>
          <strong>{formatNumber(summary.lastSixMeanAbsPrice, 2)}</strong>
          <small>First half {formatNumber(summary.firstHalfMeanAbsPrice, 2)} / second half {formatNumber(summary.secondHalfMeanAbsPrice, 2)}</small>
        </div>
        <div className="cobweb-kpi-card">
          <span>Production-Direction Alignment</span>
          <strong>{summary.directionOkCount}/{summary.directionTotal}</strong>
          <small>Expand at high prices and contract at low prices</small>
        </div>
        <div className="cobweb-kpi-card">
          <span>Final Inventory / Fill Rate</span>
          <strong>{formatNumber(latestRow.inventoryQuantity, 0)} / {formatPercent(latestRow.fillRate)}</strong>
          <small>Backlog {formatNumber(latestRow.backlogQuantity, 0)}</small>
        </div>
      </section>

      <section className="cobweb-layout">
        <article className="cobweb-chart-card wide">
          <div className="cobweb-card-title">Price and {summary.controllerLabel} Supply Trajectory</div>
          <div className="cobweb-card-note">
            Equilibrium references expose the core cobweb trajectory. Bars show {summary.controllerLabel} production plans, and the supply line shows quantities used in market pricing.
          </div>
          <div className="cobweb-chart large" ref={convergenceRef} />
        </article>

        <article className="cobweb-chart-card">
          <div className="cobweb-card-title">{summary.controllerLabel} Plan vs. Market Supply vs. Theoretical Reference</div>
          <div className="cobweb-card-note">
            Verifies that realized plans enter the market and align with the controller's price-response rule.
          </div>
          <div className="cobweb-chart" ref={productionRef} />
        </article>

        <article className="cobweb-chart-card">
          <div className="cobweb-card-title">Equilibrium Deviations</div>
          <div className="cobweb-card-note">
            Deviations should decline under convergence, remain approximately stable under neutral dynamics, and expand or remain high under divergence.
          </div>
          <div className="cobweb-chart" ref={deviationRef} />
        </article>

        <article className="cobweb-chart-card">
          <div className="cobweb-card-title">Price–Supply Phase Plot</div>
          <div className="cobweb-chart" ref={phaseRef} />
        </article>

        <article className="cobweb-chart-card">
          <div className="cobweb-card-title">Inventory and Fulfillment Context</div>
          <div className="cobweb-card-note">
            These contextual metrics verify that service and inventory do not dominate the intended price–supply response.
          </div>
          <div className="cobweb-chart" ref={operationsRef} />
        </article>

        <article className="cobweb-chart-card wide">
          <div className="cobweb-card-title">Per-Round Evidence Audit</div>
          <div className="cobweb-card-note">
            Each turn audits the supply source, controller plan, theoretical reference, direction alignment, and input filtering to verify the configured decision pathway.
          </div>
          <div className="cobweb-table-wrapper">
            <table className="cobweb-table">
              <thead>
                <tr>
                  <th>Round</th>
                  <th>Price</th>
                  <th>Market Supply</th>
                  <th>{summary.controllerLabel} Plan</th>
                  <th>Theoretical Reference</th>
                  <th>Supply Source</th>
                  <th>Direction</th>
                  <th>Input Filter</th>
                  <th>Production-Rationale Summary</th>
                </tr>
              </thead>
              <tbody>
                {viewState.rows.map((row) => (
                  <tr key={row.day}>
                    <td>D{row.day}</td>
                    <td>{formatNumber(row.price, 2)}</td>
                    <td>{formatNumber(row.marketSupply, 0)}</td>
                    <td>{formatNumber(row.planQuantity, 0)}</td>
                    <td>{formatNumber(row.theoreticalSupply, 1)}</td>
                    <td>
                      <span className={`cobweb-pill ${row.marketSupplySource === summary.expectedSupplySource ? 'success' : 'muted'}`}>
                        {row.marketSupplySource || '-'}
                      </span>
                    </td>
                    <td className={row.directionOk ? 'cobweb-ok-cell' : 'cobweb-warning-cell'}>
                      {row.direction || '-'}
                    </td>
                    <td>
                      <span className={`cobweb-pill ${row.agentInputFilterApplied ? 'success' : 'muted'}`}>
                        {summary.controllerMode === 'scripted'
                          ? 'Not applicable'
                          : (row.agentInputFilterApplied ? 'Applied' : 'Not applied')}
                      </span>
                    </td>
                    <td title={row.actionReason}>
                      {truncateText(row.actionReason, 120)}
                      {row.actionReasonNoiseTerms.length > 0 && (
                        <span className="cobweb-noise-mark">Contains noise term</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </article>
      </section>
    </div>
  );
}

export default CobwebModelValidationView;
