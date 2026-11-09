import LoadingState from './LoadingState';
import {setReviewChart} from '../utils/chartReview';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import * as echarts from '../utils/echartsRuntime';
import {getArtifactView} from '../utils/operationsApi';
import {reviewLocation} from '../utils/reviewDisplay';
import {clearReadCache} from '../utils/readClient.mjs';
import { buildDataUrl, safeFetchJson } from '../utils/dataSource';
import {
  convertQuantityByItem,
  getQuantityAxisLabel,
  sumConvertedFieldByList,
} from '../utils/productEquivalent';
import { buildExternalFlowSeries } from '../utils/externalMarketFlows';

const FLOW_COLORS = ['#2f80ed', '#21a67a', '#f2994a', '#9b51e0', '#eb5757', '#56ccf2'];

const buildFallbackFlowSequence = (enterpriseSpecs = []) => {
  const sorted = [...enterpriseSpecs]
    .filter((spec) => spec?.id || spec?.enterprise_id)
    .sort((left, right) => {
      const tierGap = Number(left?.tier ?? 0) - Number(right?.tier ?? 0);
      if (tierGap !== 0) {
        return tierGap;
      }
      return String(left?.id || left?.enterprise_id || '').localeCompare(String(right?.id || right?.enterprise_id || ''));
    });
  const companies = sorted.map((spec) => ({
    id: spec.enterprise_id || spec.id,
    name: spec.enterprise_name || spec.name || spec.enterprise_id || spec.id,
    tier: spec.tier,
    normalizeByRecipe: Boolean((spec?.enabled_functions || []).includes('production')),
  }));
  if (companies.length === 0) {
    return [];
  }

  const customerFlow = {
    order: 0,
    canonical_key: 'customer_to_bottom_tier',
    dynamic_key: 'customer_to_bottom_tier',
    label: `Customer -> ${companies[companies.length - 1].name}`,
    upstream_enterprise_id: companies[companies.length - 1].id,
    upstream_enterprise_name: companies[companies.length - 1].name,
    upstream_tier: companies[companies.length - 1].tier,
    downstream_enterprise_id: 'external_market',
    downstream_enterprise_name: 'Customer',
    downstream_tier: null,
    normalize_by_recipe: false,
  };

  const edgeFlows = companies.slice(0, -1).map((company, index) => {
    const downstream = companies[index + 1];
    return {
      order: index + 1,
      canonical_key: `${downstream.id.toLowerCase()}_to_${company.id.toLowerCase()}`,
      dynamic_key: `tier_${downstream.tier}_to_tier_${company.tier}`,
      label: `${downstream.name} -> ${company.name}`,
      upstream_enterprise_id: company.id,
      upstream_enterprise_name: company.name,
      upstream_tier: company.tier,
      downstream_enterprise_id: downstream.id,
      downstream_enterprise_name: downstream.name,
      downstream_tier: downstream.tier,
      normalize_by_recipe: Boolean(downstream.normalizeByRecipe),
    };
  });

  return [customerFlow, ...edgeFlows];
};

const buildFlowModels = (flowSequenceInput, enterpriseSpecs = []) => {
  const flowSequence = Array.isArray(flowSequenceInput) && flowSequenceInput.length > 0
    ? flowSequenceInput
    : buildFallbackFlowSequence(enterpriseSpecs);
  const fallbackSequence = buildFallbackFlowSequence(enterpriseSpecs);
  const customerFlow = flowSequence[0] || fallbackSequence[0];
  const edgeFlows = flowSequence.slice(1);

  const requestLayers = flowSequence.map((flow, index) => {
    const color = FLOW_COLORS[index % FLOW_COLORS.length];
    if (index === 0) {
      return {
        key: flow.dynamic_key,
        color,
        name: 'External Market Demand',
      };
    }
    return {
      key: flow.dynamic_key,
      color,
      name: `${flow.downstream_enterprise_name} Requests to ${flow.upstream_enterprise_name}`,
    };
  });

  const edgeLayers = edgeFlows.map((flow, index) => {
    const color = FLOW_COLORS[(index + 1) % FLOW_COLORS.length];
    const exchangeId = (
      Number.isFinite(Number(flow.upstream_tier)) && Number.isFinite(Number(flow.downstream_tier))
    )
      ? `${flow.upstream_tier}-${flow.downstream_tier}_exchange`
      : null;
    return {
      key: flow.dynamic_key,
      canonicalKey: flow.canonical_key || flow.dynamic_key,
      name: `${flow.downstream_enterprise_name} → ${flow.upstream_enterprise_name}`,
      shortName: flow.downstream_enterprise_name,
      color,
      exchangeId,
      downstreamEnterpriseId: flow.downstream_enterprise_id,
      upstreamEnterpriseId: flow.upstream_enterprise_id,
      downstreamTier: flow.downstream_tier,
      upstreamTier: flow.upstream_tier,
      normalizeByRecipe: Boolean(flow.normalize_by_recipe),
    };
  });

  const propagationEdgeLayers = [...edgeLayers].sort((left, right) => {
    const leftDownstreamTier = Number(left?.downstreamTier);
    const rightDownstreamTier = Number(right?.downstreamTier);
    const leftHasTier = Number.isFinite(leftDownstreamTier);
    const rightHasTier = Number.isFinite(rightDownstreamTier);

    if (leftHasTier && rightHasTier && leftDownstreamTier !== rightDownstreamTier) {
      return rightDownstreamTier - leftDownstreamTier;
    }

    const leftUpstreamTier = Number(left?.upstreamTier);
    const rightUpstreamTier = Number(right?.upstreamTier);
    const leftHasUpstreamTier = Number.isFinite(leftUpstreamTier);
    const rightHasUpstreamTier = Number.isFinite(rightUpstreamTier);

    if (leftHasUpstreamTier && rightHasUpstreamTier && leftUpstreamTier !== rightUpstreamTier) {
      return rightUpstreamTier - leftUpstreamTier;
    }

    return String(left?.key || '').localeCompare(String(right?.key || ''));
  });

  const demandAmplificationLayers = propagationEdgeLayers.map((flow) => ({
    key: `${flow.key}__vs_customer`,
    name: `${flow.shortName} / Customer`,
  }));

  const transmissionLayers = propagationEdgeLayers.map((flow, index) => ({
    key: index === 0 ? `${flow.key}__vs_customer` : `${flow.key}__vs_prev`,
    name: index === 0
      ? `${flow.shortName} / Customer`
      : `${flow.shortName} / ${propagationEdgeLayers[index - 1]?.shortName || 'Previous'}`,
    color: flow.color,
    numeratorKey: flow.key,
    denominatorKey: index === 0 ? customerFlow.dynamic_key : propagationEdgeLayers[index - 1]?.key,
  }));

  const statusLayers = edgeLayers
    .filter((flow) => flow.exchangeId)
    .map((flow) => ({
      key: flow.key,
      exchangeId: flow.exchangeId,
      name: flow.name,
    }));

  return {
    flowSequence,
    customerFlow,
    edgeLayers,
    requestLayers,
    demandAmplificationLayers,
    transmissionLayers,
    statusLayers,
  };
};

const getTopInternalFlow = (edgeLayers = []) =>
  [...edgeLayers].sort((left, right) => {
    const leftUpstreamTier = Number(left?.upstreamTier);
    const rightUpstreamTier = Number(right?.upstreamTier);
    const leftHasUpstreamTier = Number.isFinite(leftUpstreamTier);
    const rightHasUpstreamTier = Number.isFinite(rightUpstreamTier);
    if (leftHasUpstreamTier && rightHasUpstreamTier && leftUpstreamTier !== rightUpstreamTier) {
      return leftUpstreamTier - rightUpstreamTier;
    }
    const leftDownstreamTier = Number(left?.downstreamTier);
    const rightDownstreamTier = Number(right?.downstreamTier);
    const leftHasDownstreamTier = Number.isFinite(leftDownstreamTier);
    const rightHasDownstreamTier = Number.isFinite(rightDownstreamTier);
    if (leftHasDownstreamTier && rightHasDownstreamTier && leftDownstreamTier !== rightDownstreamTier) {
      return leftDownstreamTier - rightDownstreamTier;
    }
    return String(left?.key || '').localeCompare(String(right?.key || ''));
  })[0] || null;

const formatNumber = (value) => {
  if (value === null || value === undefined || value === '') {
    return '-';
  }
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return '-';
  }
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(numeric);
};

const formatRatio = (value) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return '-';
  }
  return `${formatNumber(numeric)}x`;
};

const formatPercent = (value) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return '-';
  }
  return `${formatNumber(numeric * 100)}%`;
};

const getRequestChartDisplayValue = (value, mode, peakValue = 0) => {
  const numericValue = Number(value || 0);
  if (!Number.isFinite(numericValue)) {
    return 0;
  }
  if (mode === 'log') {
    return Math.log10(numericValue + 1);
  }
  if (mode === 'relative_peak') {
    if (peakValue <= 0) {
      return 0;
    }
    return (numericValue / peakValue) * 100;
  }
  return numericValue;
};

const toFiniteNumber = (value, fallback = 0) => {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
};

const getInventoryQuantity = (observation, itemId) => {
  const items = observation?.inventory?.inventory_items || [];
  const item = items.find((entry) => entry.item_id === itemId);
  return Number(item?.quantity || 0);
};

const getRawMaterialEquivalentInventory = (observation, quantityView) => {
  const items = observation?.inventory?.inventory_items || [];
  const rawMaterialItems = items.filter((item) => item?.item_type === 'raw_material');
  return sumConvertedFieldByList(rawMaterialItems, 'quantity', 'item_id', quantityView);
};

const normalizeSeries = (series, availableDays) => {
  const byRound = new Map((series || []).map((point) => [Number(point.round), Number(point.quantity || 0)]));
  return availableDays.map((day) => (byRound.has(day) ? byRound.get(day) : 0));
};

const buildSeriesFromEntries = (
  entries,
  availableDays,
  quantityView,
  roundField = 'created_round',
  quantityField = 'quantity',
  itemField = 'product_id'
) => {
  const byRound = new Map();
  (entries || []).forEach((entry) => {
    const round = Number(entry?.[roundField]);
    const quantity = convertQuantityByItem(entry?.[quantityField], entry?.[itemField], quantityView);
    if (!Number.isFinite(round)) {
      return;
    }
    byRound.set(round, (byRound.get(round) || 0) + quantity);
  });
  return availableDays.map((day) => byRound.get(day) || 0);
};

const isEffectiveLifecycleStatus = (status) => !['superseded', 'expired'].includes(status);

const buildRequestSeriesFromBuyRequests = (
  requests,
  availableDays,
  quantityView,
  effectiveOnly = false
) => {
  const byRound = new Map();
  (requests || []).forEach((request) => {
    if (effectiveOnly && !isEffectiveLifecycleStatus(request?.lifecycle_status)) {
      return;
    }
    const round = Number(request?.created_round);
    if (!Number.isFinite(round)) {
      return;
    }
    const quantity = Number(request?.quantity || 0);
    byRound.set(round, (byRound.get(round) || 0) + quantity);
  });
  return availableDays.map((day) => byRound.get(day) || 0);
};

const buildRecipeEquivalentRequestSeries = (
  requests,
  availableDays,
  recipeRawMaterials,
  effectiveOnly = false
) => {
  const recipeMap = recipeRawMaterials || {};
  const byRoundMaterial = new Map();
  (requests || []).forEach((request) => {
    if (effectiveOnly && !isEffectiveLifecycleStatus(request?.lifecycle_status)) {
      return;
    }
    const round = Number(request?.created_round);
    const materialId = request?.product_id;
    const quantity = Number(request?.quantity || 0);
    if (!Number.isFinite(round)) {
      return;
    }
    if (!byRoundMaterial.has(round)) {
      byRoundMaterial.set(round, new Map());
    }
    const materialMap = byRoundMaterial.get(round);
    materialMap.set(materialId, (materialMap.get(materialId) || 0) + quantity);
  });

  const byRound = new Map();
  byRoundMaterial.forEach((materialMap, round) => {
    const equivalents = [];
    materialMap.forEach((quantity, materialId) => {
      const recipeAmount = Number(recipeMap?.[materialId] || 0);
      if (recipeAmount > 0) {
        equivalents.push(quantity / recipeAmount);
      } else {
        equivalents.push(quantity);
      }
    });
    byRound.set(round, equivalents.length > 0 ? Math.min(...equivalents) : 0);
  });

  return availableDays.map((day) => byRound.get(day) || 0);
};

const buildEnterpriseOrderSeries = (exchangePayload, availableDays, quantityView, edgeLayers) => {
  const exchanges = exchangePayload?.data?.exchanges || {};
  const result = {};

  (edgeLayers || []).forEach((layer) => {
    result[layer.key] = availableDays.map(() => 0);
    if (!layer.exchangeId) {
      return;
    }
    const orders = exchanges?.[layer.exchangeId]?.orders?.list || [];
    result[layer.key] = buildSeriesFromEntries(orders, availableDays, quantityView, 'created_round', 'quantity', 'product_id');
  });

  return result;
};

const getLatest = (rows, company) => {
  const companyRows = rows.filter((row) => row.company === company);
  return companyRows[companyRows.length - 1]?.observation || {};
};

const getObservationAtDay = (rows, company, day) =>
  rows.find((entry) => entry.company === company && entry.day === day)?.observation || {};

const buildRatioSeries = (numeratorSeries, denominatorSeries) =>
  numeratorSeries.map((value, index) => {
    const denominator = Number(denominatorSeries[index] || 0);
    if (denominator <= 0) {
      return null;
    }
    return Number(value || 0) / denominator;
  });

const sumSeries = (values) => (values || []).reduce((sum, value) => sum + Number(value || 0), 0);

const getMean = (values) => {
  const numericValues = (values || []).map((value) => Number(value)).filter((value) => Number.isFinite(value));
  if (numericValues.length === 0) {
    return 0;
  }
  return numericValues.reduce((sum, value) => sum + value, 0) / numericValues.length;
};

const getVariance = (values) => {
  const numericValues = (values || []).map((value) => Number(value)).filter((value) => Number.isFinite(value));
  if (numericValues.length === 0) {
    return 0;
  }
  const mean = getMean(numericValues);
  return numericValues.reduce((sum, value) => sum + ((value - mean) ** 2), 0) / numericValues.length;
};

const getStandardDeviation = (values) => Math.sqrt(getVariance(values));

const getCoefficientOfVariation = (values) => {
  const mean = getMean(values);
  if (mean <= 0) {
    return 0;
  }
  return getStandardDeviation(values) / mean;
};

const buildSeriesFromOrdersByRound = (
  orders,
  availableDays,
  quantityView,
  roundField,
  quantityField,
  itemField,
  statusFilter = null
) => {
  const byRound = new Map();
  (orders || []).forEach((order) => {
    if (statusFilter && !statusFilter(order)) {
      return;
    }
    const round = Number(order?.[roundField]);
    if (!Number.isFinite(round)) {
      return;
    }
    const quantity = convertQuantityByItem(order?.[quantityField], order?.[itemField], quantityView);
    byRound.set(round, (byRound.get(round) || 0) + quantity);
  });
  return availableDays.map((day) => byRound.get(day) || 0);
};

const buildSeriesFromTopTierPlanByDay = (statesByDay, availableDays, quantityView) => {
  const byDay = new Map();
  (availableDays || []).forEach((day) => {
    const state = statesByDay?.[day] || {};
    const recommendedActions = state?.top_tier_supply_plan?.recommended_actions || [];
    const total = (recommendedActions || []).reduce(
      (sum, action) => sum + convertQuantityByItem(action?.recommended_quantity, action?.material_id, quantityView),
      0
    );
    byDay.set(day, total);
  });
  return availableDays.map((day) => byDay.get(day) || 0);
};

const extractOrdersFromBuckets = (orderBuckets) =>
  Object.values(orderBuckets || {}).flatMap((bucket) => (Array.isArray(bucket) ? bucket : [bucket]));

const buildProposalAcceptedSeries = (proposals, availableDays, quantityView) =>
  buildSeriesFromEntries(
    (proposals || []).filter((proposal) => proposal?.buyer_response === 'accept' && proposal?.seller_response === 'accept'),
    availableDays,
    quantityView,
    'created_round',
    'quantity',
    'product_id'
  );

const buildHeatmapData = (availableDays, layerOrder, seriesMap) => {
  const cells = [];
  availableDays.forEach((day, dayIndex) => {
    layerOrder.forEach((layer, layerIndex) => {
      const value = seriesMap[layer.key]?.[dayIndex];
      if (value === null || value === undefined || !Number.isFinite(Number(value))) {
        return;
      }
      cells.push([dayIndex, layerIndex, Number(value)]);
    });
  });
  return cells;
};

const getPeakValuePoint = (seriesMap, layerOrder, availableDays) => {
  let best = { value: -Infinity, day: null, layerName: '-' };
  layerOrder.forEach((layer) => {
    (seriesMap[layer.key] || []).forEach((value, index) => {
      const numericValue = Number(value);
      if (Number.isFinite(numericValue) && numericValue > best.value) {
        best = {
          value: numericValue,
          day: availableDays[index],
          layerName: layer.name
        };
      }
    });
  });
  return best;
};

const findWeakestConversion = (conversionRows) => {
  let weakest = null;
  conversionRows.forEach((row) => {
    const requestTotal = Number(row.requestTotal || 0);
    const orderTotal = Number(row.orderTotal || 0);
    if (requestTotal <= 0) {
      return;
    }
    const ratio = orderTotal / requestTotal;
    if (!weakest || ratio < weakest.ratio) {
      weakest = { layerName: row.name, ratio };
    }
  });
  return weakest;
};

const findPeakBacklog = (responseRows, availableDays) => {
  let peak = { company: '-', day: null, value: 0 };
  responseRows.forEach((row) => {
    (row.backlogSeries || []).forEach((value, index) => {
      const numeric = Number(value || 0);
      if (numeric > peak.value) {
        peak = { company: row.company, day: availableDays[index], value: numeric };
      }
    });
  });
  return peak;
};

const findFirstAmplifiedDay = (series, availableDays, threshold = 2) => {
  const index = (series || []).findIndex((value) => Number(value || 0) >= threshold);
  if (index < 0) {
    return null;
  }
  return availableDays[index];
};

const findPeakRound = (series, availableDays) => {
  let bestValue = -Infinity;
  let bestDay = null;
  (series || []).forEach((value, index) => {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) {
      return;
    }
    if (numeric > bestValue) {
      bestValue = numeric;
      bestDay = availableDays[index];
    }
  });
  return { value: bestValue, day: bestDay };
};

const BullwhipEffectView = ({
  availableDays,
  dataRoot,
  quantityView,
  enterpriseSpecs = [],
  includeUpstreamExternalSupplierMode = false,
  includeDownstreamExternalMarketMode = false,
  currentRunId = '',
  currentRunSourceLabel = '',
}) => {
  const [requestMode, setRequestMode] = useState('effective');
  const [requestChartViewMode, setRequestChartViewMode] = useState('absolute');
  const [bullwhip, setBullwhip] = useState(null);
  const [externalDemand, setExternalDemand] = useState(null);
  const [exchangeSnapshot, setExchangeSnapshot] = useState(null);
  const [observerRows, setObserverRows] = useState([]);
  const [externalTradeFlows, setExternalTradeFlows] = useState(null);
  const [latestDepartmentStates, setLatestDepartmentStates] = useState(null);
  const [topUpstreamProcurementStatesByDay, setTopUpstreamProcurementStatesByDay] = useState({});
  const [loading, setLoading] = useState(true);
  const [loadError,setLoadError]=useState('');const [reload,setReload]=useState(0);
  const requestRef = useRef(null);
  const loadGeneration = useRef(0);
  const requestHeatmapRef = useRef(null);
  const orderHeatmapRef = useRef(null);
  const transmissionRef = useRef(null);
  const conversionRef = useRef(null);
  const responseRef = useRef(null);
  const gapRef = useRef(null);
  const inventoryRef = useRef(null);
  const chartRefs = useRef([]);

  const latestDay = availableDays.length > 0 ? Math.max(...availableDays) : 0;
  const flowModels = useMemo(() => buildFlowModels(bullwhip?.flow_sequence, enterpriseSpecs), [bullwhip, enterpriseSpecs]);

  useEffect(() => {
    const generation = ++loadGeneration.current;
    loadBullwhipData(generation).catch(error=>{if(generation===loadGeneration.current){setLoadError(error.message);setLoading(false);}});
    return () => {loadGeneration.current += 1;};
  }, [availableDays.join(','), dataRoot, quantityView, enterpriseSpecs,reload]);

  useEffect(() => {
    renderCharts();
    const handleResize = () => {
      chartRefs.current.forEach((chart) => chart?.resize());
    };
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
    };
  }, [
    loading,
    bullwhip,
    externalDemand,
    exchangeSnapshot,
    observerRows,
    requestMode,
    requestChartViewMode,
    quantityView,
    externalTradeFlows,
    includeUpstreamExternalSupplierMode,
    includeDownstreamExternalMarketMode
  ]);

  useEffect(() => () => {
    chartRefs.current.forEach((chart) => chart?.dispose());
    chartRefs.current = [];
  }, []);

  const loadBullwhipData = async (generation) => {
    setLoading(true);setLoadError('');
    const {source,name}=reviewLocation(dataRoot);const projected=await getArtifactView(source,name,'bullwhip',{forceRefresh:reload>0});
    if(projected.error&&projected.status!==404)throw new Error(projected.error);
    if(projected.data&&!['available','not_recorded','not_published'].includes(projected.data.availability))throw new Error('Bullwhip evidence is invalid or changed. Republish this archive.');
    const files=projected.data?.availability==='available'?projected.data.data?.files:null;
    const read=url=>files?Promise.resolve(files[url.slice(dataRoot.replace(/\/$/,'').length+1)]??null):safeFetchJson(url);


    const [
      bullwhipData,
      externalData,
      exchangeData
    ] = await Promise.all([
      read(buildDataUrl(dataRoot, `public/exchange/day${latestDay}/bullwhip_metrics.json`)),
      read(buildDataUrl(dataRoot, `public/exchange/day${latestDay}/external_demand.json`)),
      read(buildDataUrl(dataRoot, `public/exchange/day${latestDay}/exchange.json`))
    ]);
    const bullwhipPayload = bullwhipData?.data || null;
    const runtimeFlowModels = buildFlowModels(bullwhipPayload?.flow_sequence);
    const enterpriseIds = Array.from(
      new Set(
        runtimeFlowModels.flowSequence
          .flatMap((flow) => [flow.upstream_enterprise_id, flow.downstream_enterprise_id])
          .filter((enterpriseId) => enterpriseId && enterpriseId !== 'external_market')
      )
    );
    const procurementEntries = await Promise.all(
      enterpriseIds.map(async (enterpriseId) => ([
        enterpriseId,
        await read(buildDataUrl(dataRoot, `enterprises/${enterpriseId}/department/procurement/day${latestDay}/procurement.json`))
      ]))
    );
    const salesEntries = await Promise.all(
      enterpriseIds.map(async (enterpriseId) => ([
        enterpriseId,
        await read(buildDataUrl(dataRoot, `enterprises/${enterpriseId}/department/sales/day${latestDay}/sales.json`))
      ]))
    );
    const procurementByEnterprise = Object.fromEntries(
      procurementEntries.map(([enterpriseId, payload]) => [enterpriseId, payload?.self_state || {}])
    );
    const salesByEnterprise = Object.fromEntries(
      salesEntries.map(([enterpriseId, payload]) => [enterpriseId, payload?.self_state || {}])
    );

    const rows = [];
    const topUpstreamProcurementStates = {};
    const roundRows = await Promise.all(availableDays.map(async (day) => {
      const fallbackEnterpriseIds = enterpriseSpecs.map((spec) => spec.enterprise_id || spec.id).filter(Boolean);
      const dayRows = await Promise.all((enterpriseIds.length > 0 ? enterpriseIds : fallbackEnterpriseIds).map(async (company) => {
        const data = await read(buildDataUrl(dataRoot, `public/observer_state/day${day}/end_of_day/${company}.json`));
        return {
          day,
          company,
          observation: data?.observation || {}
        };
      }));
      return dayRows;
    }));
    rows.push(...roundRows.flat());
    const bottomEnterpriseId = runtimeFlowModels.customerFlow?.upstream_enterprise_id;
    const topUpstreamEnterpriseId = getTopInternalFlow(runtimeFlowModels.edgeLayers)?.upstreamEnterpriseId;
    if (topUpstreamEnterpriseId) {
      const dailyProcurementEntries = await Promise.all(
        availableDays.map(async (day) => ([
          day,
          await read(buildDataUrl(dataRoot, `enterprises/${topUpstreamEnterpriseId}/department/procurement/day${day}/procurement.json`))
        ]))
      );
      dailyProcurementEntries.forEach(([day, payload]) => {
        topUpstreamProcurementStates[day] = payload?.self_state || {};
      });
    }

    if (generation !== loadGeneration.current) return;
    setBullwhip(bullwhipPayload);
    setExternalDemand(externalData?.data || null);
    setExchangeSnapshot(exchangeData || null);
    setObserverRows(rows);
    setExternalTradeFlows(
      buildExternalFlowSeries({
        availableDays,
        quantityView,
        procurementState: procurementByEnterprise[topUpstreamEnterpriseId] || {},
        salesState: salesByEnterprise[bottomEnterpriseId] || {},
      })
    );
    setLatestDepartmentStates({
      procurementByEnterprise,
      salesByEnterprise,
    });
    setTopUpstreamProcurementStatesByDay(topUpstreamProcurementStates);
    setLoading(false);
  };

  const resetCharts = () => {
    chartRefs.current.forEach((chart) => chart?.dispose());
    chartRefs.current = [];
  };

  const rawRequestSeriesMap = useMemo(
    () => {
      const exchanges = exchangeSnapshot?.data?.exchanges || {};
      const result = {
        [flowModels.customerFlow.key || flowModels.customerFlow.dynamic_key || flowModels.customerFlow.canonical_key || 'customer_to_retailer']: normalizeSeries(
          bullwhip?.dynamic_raw_series?.[flowModels.customerFlow.dynamic_key]
            || bullwhip?.raw_series?.customer_to_retailer
            || externalDemand?.history
            || [],
          availableDays
        )
      };
      flowModels.edgeLayers.forEach((layer) => {
        const exchange = exchanges?.[layer.exchangeId] || {};
        if (bullwhip?.dynamic_raw_series?.[layer.key]) {
          result[layer.key] = normalizeSeries(bullwhip.dynamic_raw_series[layer.key], availableDays);
          return;
        }
        const requests = exchange?.buy_requests?.list || [];
        result[layer.key] = layer.normalizeByRecipe
          ? buildRecipeEquivalentRequestSeries(requests, availableDays, bullwhip?.flow_sequence?.find((flow) => flow.dynamic_key === layer.key)?.recipe_raw_materials, false)
          : buildRequestSeriesFromBuyRequests(requests, availableDays, quantityView, false);
      });
      return result;
    },
    [bullwhip, externalDemand, availableDays, exchangeSnapshot, quantityView, flowModels]
  );

  const effectiveRequestSeriesMap = useMemo(
    () => {
      const exchanges = exchangeSnapshot?.data?.exchanges || {};
      const result = {
        [flowModels.customerFlow.key || flowModels.customerFlow.dynamic_key || flowModels.customerFlow.canonical_key || 'customer_to_retailer']: normalizeSeries(
          bullwhip?.dynamic_effective_series?.[flowModels.customerFlow.dynamic_key]
            || bullwhip?.effective_series?.customer_to_retailer
            || bullwhip?.series?.customer_to_retailer
            || externalDemand?.history
            || [],
          availableDays
        )
      };
      flowModels.edgeLayers.forEach((layer) => {
        const exchange = exchanges?.[layer.exchangeId] || {};
        if (bullwhip?.dynamic_effective_series?.[layer.key]) {
          result[layer.key] = normalizeSeries(bullwhip.dynamic_effective_series[layer.key], availableDays);
          return;
        }
        if (bullwhip?.effective_series?.[layer.canonicalKey] || bullwhip?.series?.[layer.canonicalKey]) {
          result[layer.key] = normalizeSeries(
            bullwhip?.effective_series?.[layer.canonicalKey] || bullwhip?.series?.[layer.canonicalKey] || [],
            availableDays
          );
          return;
        }
        const requests = exchange?.buy_requests?.list || [];
        result[layer.key] = layer.normalizeByRecipe
          ? buildRecipeEquivalentRequestSeries(requests, availableDays, bullwhip?.flow_sequence?.find((flow) => flow.dynamic_key === layer.key)?.recipe_raw_materials, true)
          : buildRequestSeriesFromBuyRequests(requests, availableDays, quantityView, true);
      });
      return result;
    },
    [bullwhip, externalDemand, availableDays, exchangeSnapshot, quantityView, flowModels]
  );

  const requestSeriesMap = requestMode === 'raw' ? rawRequestSeriesMap : effectiveRequestSeriesMap;
  const customerBaseSeries = requestSeriesMap[flowModels.customerFlow.dynamic_key] || requestSeriesMap.customer_to_retailer || [];
  const topInternalFlow = useMemo(() => getTopInternalFlow(flowModels.edgeLayers), [flowModels]);
  const topInternalFlowName = topInternalFlow?.upstreamEnterpriseId
    ? (topInternalFlow?.name || topInternalFlow?.upstreamEnterpriseId)
    : '';
  const externalUpstreamDemandSeries = useMemo(
    () => buildSeriesFromTopTierPlanByDay(topUpstreamProcurementStatesByDay, availableDays, quantityView),
    [topUpstreamProcurementStatesByDay, availableDays, quantityView]
  );
  const externalUpstreamQuantitySeries = externalTradeFlows?.upstreamQuantitySeries || availableDays.map(() => 0);
  const hasExternalUpstreamDemandData = useMemo(
    () => externalUpstreamDemandSeries.some((value) => Number(value || 0) > 0),
    [externalUpstreamDemandSeries]
  );
  const hasExternalUpstreamQuantityData = useMemo(
    () => externalUpstreamQuantitySeries.some((value) => Number(value || 0) > 0),
    [externalUpstreamQuantitySeries]
  );
  const externalUpstreamDemandLayer = useMemo(() => {
    if (!includeUpstreamExternalSupplierMode || !topInternalFlow || !hasExternalUpstreamDemandData) {
      return null;
    }
    return {
      key: 'external_upstream_demand',
      name: `External Replenishment Demand → ${topInternalFlow.upstreamEnterpriseId || topInternalFlowName}`,
      shortName: 'External Replenishment Demand',
      color: '#6c5ce7',
    };
  }, [includeUpstreamExternalSupplierMode, topInternalFlow, topInternalFlowName, hasExternalUpstreamDemandData]);
  const externalUpstreamOrderLayer = useMemo(() => {
    if (!includeUpstreamExternalSupplierMode || !topInternalFlow || !hasExternalUpstreamQuantityData) {
      return null;
    }
    return {
      key: 'external_upstream_procurement',
      name: `External Supplier → ${topInternalFlow.upstreamEnterpriseId || topInternalFlowName}`,
      shortName: 'External Supplier',
      color: '#34495e',
    };
  }, [includeUpstreamExternalSupplierMode, topInternalFlow, topInternalFlowName, hasExternalUpstreamQuantityData]);

  const enterpriseOrderSeries = useMemo(
    () => buildEnterpriseOrderSeries(exchangeSnapshot, availableDays, quantityView, flowModels.edgeLayers),
    [exchangeSnapshot, availableDays, quantityView, flowModels]
  );

  const proposalSeriesMap = useMemo(() => {
    const exchanges = exchangeSnapshot?.data?.exchanges || {};
    const result = {};
    flowModels.edgeLayers.forEach((layer) => {
      const proposals = exchanges?.[layer.exchangeId]?.proposals?.list || [];
      result[layer.key] = buildSeriesFromEntries(proposals, availableDays, quantityView, 'created_round', 'quantity', 'product_id');
    });
    return result;
  }, [exchangeSnapshot, availableDays, quantityView, flowModels]);

  const confirmedProposalSeriesMap = useMemo(() => {
    const exchanges = exchangeSnapshot?.data?.exchanges || {};
    const result = {};
    flowModels.edgeLayers.forEach((layer) => {
      const proposals = (exchanges?.[layer.exchangeId]?.proposals?.list || []).filter((item) => item?.status === 'confirmed');
      result[layer.key] = buildSeriesFromEntries(proposals, availableDays, quantityView, 'created_round', 'quantity', 'product_id');
    });
    return result;
  }, [exchangeSnapshot, availableDays, quantityView, flowModels]);

  const acceptedProposalSeriesMap = useMemo(() => {
    const exchanges = exchangeSnapshot?.data?.exchanges || {};
    const result = {};
    flowModels.edgeLayers.forEach((layer) => {
      const proposals = exchanges?.[layer.exchangeId]?.proposals?.list || [];
      result[layer.key] = buildProposalAcceptedSeries(proposals, availableDays, quantityView);
    });
    return result;
  }, [exchangeSnapshot, availableDays, quantityView, flowModels]);

  const arrivedOrderSeriesMap = useMemo(() => {
    const procurementStateMap = latestDepartmentStates?.procurementByEnterprise || {};
    const result = {};

    flowModels.edgeLayers.forEach((layer) => {
      const state = procurementStateMap[layer.downstreamEnterpriseId];
      const orders = extractOrdersFromBuckets(state?.orders);
      result[layer.key] = buildSeriesFromOrdersByRound(
        orders,
        availableDays,
        quantityView,
        'actual_arrival_time',
        'quantity',
        'material_id',
        (order) => order?.status === 'received'
      );
    });
    return result;
  }, [latestDepartmentStates, availableDays, quantityView, flowModels]);

  const gapSeriesMap = useMemo(
    () => Object.fromEntries(
      flowModels.edgeLayers.map((layer) => ([
        layer.key,
        (requestSeriesMap[layer.key] || availableDays.map(() => 0)).map(
          (value, index) => value - ((enterpriseOrderSeries[layer.key] || [])[index] || 0)
        )
      ]))
    ),
    [requestSeriesMap, enterpriseOrderSeries, flowModels, availableDays]
  );

  const demandAmplificationSeriesMap = useMemo(() => Object.fromEntries(
    [
      ...flowModels.edgeLayers.map((layer) => ([
        `${layer.key}__vs_customer`,
        buildRatioSeries(
          requestSeriesMap[layer.key] || [],
          customerBaseSeries
        )
      ])),
      ...(externalUpstreamDemandLayer ? [[
        `${externalUpstreamDemandLayer.key}__vs_customer`,
        buildRatioSeries(
          externalUpstreamDemandSeries,
          customerBaseSeries
        )
      ]] : [])
    ]
  ), [requestSeriesMap, flowModels, customerBaseSeries, externalUpstreamDemandLayer, externalUpstreamDemandSeries]);

  const orderAmplificationSeriesMap = useMemo(() => {
    const result = Object.fromEntries(
      flowModels.edgeLayers.map((layer) => ([
        `${layer.key}__vs_customer`,
        buildRatioSeries(
          enterpriseOrderSeries[layer.key] || [],
          customerBaseSeries
        )
      ]))
    );
    if (externalUpstreamOrderLayer) {
      result[`${externalUpstreamOrderLayer.key}__vs_customer`] = buildRatioSeries(
        externalUpstreamQuantitySeries,
        customerBaseSeries
      );
    }
    return result;
  }, [enterpriseOrderSeries, customerBaseSeries, flowModels, externalUpstreamOrderLayer, externalUpstreamQuantitySeries]);

  const layerTransmissionSeriesMap = useMemo(() => Object.fromEntries(
    flowModels.transmissionLayers.map((layer) => ([
      layer.key,
      buildRatioSeries(
        requestSeriesMap[layer.numeratorKey] || [],
        requestSeriesMap[layer.denominatorKey] || []
      )
    ]))
  ), [requestSeriesMap, flowModels]);

  const requestChartSeriesMeta = useMemo(
    () => {
      const baseSeries = flowModels.requestLayers.map((entry) => {
        const rawSeries = requestSeriesMap[entry.key] || [];
        const peakValue = Math.max(...rawSeries.map((value) => Number(value || 0)), 0);
        return {
          ...entry,
          peakValue,
          rawSeries,
          seriesType: 'request',
          displaySeries: rawSeries.map((value) => ({
            value: getRequestChartDisplayValue(value, requestChartViewMode, peakValue),
            rawValue: Number(value || 0),
          })),
        };
      });
      if (externalUpstreamDemandLayer) {
        const peakValue = Math.max(...externalUpstreamDemandSeries.map((value) => Number(value || 0)), 0);
        baseSeries.push({
          ...externalUpstreamDemandLayer,
          peakValue,
          rawSeries: externalUpstreamDemandSeries,
          seriesType: 'external_demand',
          displaySeries: externalUpstreamDemandSeries.map((value) => ({
            value: getRequestChartDisplayValue(value, requestChartViewMode, peakValue),
            rawValue: Number(value || 0),
          })),
        });
      }
      if (externalUpstreamOrderLayer) {
        const peakValue = Math.max(...externalUpstreamQuantitySeries.map((value) => Number(value || 0)), 0);
        baseSeries.push({
          ...externalUpstreamOrderLayer,
          peakValue,
          rawSeries: externalUpstreamQuantitySeries,
          seriesType: 'external_order',
          displaySeries: externalUpstreamQuantitySeries.map((value) => ({
            value: getRequestChartDisplayValue(value, requestChartViewMode, peakValue),
            rawValue: Number(value || 0),
          })),
        });
      }
      return baseSeries;
    },
    [
      flowModels,
      requestSeriesMap,
      requestChartViewMode,
      externalUpstreamDemandLayer,
      externalUpstreamDemandSeries,
      externalUpstreamOrderLayer,
      externalUpstreamQuantitySeries
    ]
  );

  const responseRows = useMemo(() => {
    const dynamicCompanies = flowModels.edgeLayers.map((layer) => ({
      company: layer.downstreamEnterpriseId,
      backlogName: `${layer.shortName} backlog`,
      fillName: `${layer.shortName} fill rate`,
      color: layer.color,
    }));
    const companies = dynamicCompanies;
    return companies.map((entry) => ({
      ...entry,
      backlogSeries: availableDays.map((day) => {
        const observation = getObservationAtDay(observerRows, entry.company, day);
        return toFiniteNumber(observation?.sales?.sales_metrics?.backlog_quantity, 0);
      }),
      fillRateSeries: availableDays.map((day) => {
        const observation = getObservationAtDay(observerRows, entry.company, day);
        return toFiniteNumber(observation?.sales?.sales_metrics?.order_fulfillment_rate, 0);
      })
    }));
  }, [observerRows, availableDays, flowModels]);

  const conversionRows = useMemo(
    () => flowModels.edgeLayers.map((layer) => ({
      ...layer,
      requestTotal: sumSeries(requestSeriesMap[layer.key]),
      proposalTotal: sumSeries(proposalSeriesMap[layer.key]),
      acceptedProposalTotal: sumSeries(acceptedProposalSeriesMap[layer.key]),
      confirmedProposalTotal: sumSeries(confirmedProposalSeriesMap[layer.key]),
      orderTotal: sumSeries(enterpriseOrderSeries[layer.key]),
      arrivedTotal: sumSeries(arrivedOrderSeriesMap[layer.key]),
    })),
    [requestSeriesMap, proposalSeriesMap, acceptedProposalSeriesMap, confirmedProposalSeriesMap, enterpriseOrderSeries, arrivedOrderSeriesMap, flowModels]
  );

  const ratioComparisonRows = useMemo(() => {
    const rawRatioEntries = bullwhip?.dynamic_raw_bullwhip_ratio || [];
    const effectiveRatioEntries = bullwhip?.dynamic_effective_bullwhip_ratio || [];
    if (flowModels.transmissionLayers.length === 0) {
      return [];
    }
    return flowModels.transmissionLayers.map((layer, index) => ({
      name: layer.name,
      rawVariance: bullwhip?.dynamic_raw_variance?.[layer.numeratorKey],
      effectiveVariance: bullwhip?.dynamic_effective_variance?.[layer.numeratorKey],
      rawRatio: rawRatioEntries[index]?.ratio,
      effectiveRatio: effectiveRatioEntries[index]?.ratio,
    }));
  }, [bullwhip, flowModels]);

  const lifecycleRows = useMemo(() => {
    const exchanges = exchangeSnapshot?.data?.exchanges || {};
    return flowModels.statusLayers.map((layer) => {
      const exchange = exchanges[layer.exchangeId] || {};
      const requestStatusCounts = {};
      const proposalStatusCounts = {};
      (exchange?.buy_requests?.list || []).forEach((item) => {
        const status = item?.lifecycle_status || 'unknown';
        requestStatusCounts[status] = (requestStatusCounts[status] || 0) + 1;
      });
      (exchange?.proposals?.list || []).forEach((item) => {
        const status = item?.status || 'unknown';
        proposalStatusCounts[status] = (proposalStatusCounts[status] || 0) + 1;
      });
      return {
        name: layer.name,
        requestStatusCounts,
        proposalStatusCounts,
        rawRequestTotal: sumConvertedFieldByList(exchange?.buy_requests?.list || [], 'quantity', 'product_id', quantityView),
        effectiveRequestTotal: sumSeries(effectiveRequestSeriesMap[layer.key] || []),
      };
    });
  }, [exchangeSnapshot, effectiveRequestSeriesMap, quantityView, flowModels]);

  const requestAmplificationLayers = useMemo(() => {
    const layers = [...flowModels.demandAmplificationLayers];
    if (externalUpstreamDemandLayer) {
      layers.push({
        key: `${externalUpstreamDemandLayer.key}__vs_customer`,
        name: `${externalUpstreamDemandLayer.shortName} / Customer`,
        color: externalUpstreamDemandLayer.color,
      });
    }
    return layers;
  }, [flowModels, externalUpstreamDemandLayer]);
  const requestHeatmapData = useMemo(
    () => buildHeatmapData(availableDays, requestAmplificationLayers, demandAmplificationSeriesMap),
    [availableDays, requestAmplificationLayers, demandAmplificationSeriesMap]
  );
  const orderAmplificationLayers = useMemo(() => {
    const layers = [...flowModels.demandAmplificationLayers];
    if (externalUpstreamOrderLayer) {
      layers.push({
        key: `${externalUpstreamOrderLayer.key}__vs_customer`,
        name: `${externalUpstreamOrderLayer.shortName} / Customer`,
        color: externalUpstreamOrderLayer.color,
      });
    }
    return layers;
  }, [flowModels, externalUpstreamOrderLayer]);
  const orderHeatmapData = useMemo(
    () => buildHeatmapData(availableDays, orderAmplificationLayers, orderAmplificationSeriesMap),
    [availableDays, orderAmplificationLayers, orderAmplificationSeriesMap]
  );

  const renderCharts = () => {
    if (loading||!requestRef.current||!bullwhip || observerRows.length === 0) {
      return;
    }

    resetCharts();
    renderRequestChart();
    renderTransmissionChart();
    renderRequestHeatmap();
    renderOrderHeatmap();
    renderConversionChart();
    renderResponseChart();
    renderGapChart();
    renderInventoryChart();
  };

  const renderRequestChart = () => {
    const chart = echarts.init(requestRef.current);
    chartRefs.current.push(chart);

    const yAxisName = requestChartViewMode === 'log'
      ? `log10(1 + ${getQuantityAxisLabel('Demand / Request Quantity', quantityView)})`
      : requestChartViewMode === 'relative_peak'
        ? 'Relative to Own Peak (%)'
        : getQuantityAxisLabel('Demand / Request Quantity', quantityView);

    setReviewChart(chart, {
      tooltip: {
        trigger: 'axis',
        formatter: (params) => {
          if (!Array.isArray(params) || params.length === 0) {
            return '';
          }
          const dayLabel = params[0]?.axisValueLabel || params[0]?.name || '';
          const lines = [dayLabel];
          params.forEach((param) => {
            const rawValue = Number(param?.data?.rawValue ?? param?.value ?? 0);
            const displayValue = Number(param?.data?.value ?? param?.value ?? 0);
            const extra = requestChartViewMode === 'log'
              ? ` (log display ${formatNumber(displayValue)})`
              : requestChartViewMode === 'relative_peak'
                ? ` (relative peak ${formatNumber(displayValue)}%)`
                : '';
            lines.push(`${param.marker}${param.seriesName}: ${formatNumber(rawValue)}${extra}`);
          });
          return lines.join('<br/>');
        }
      },
      legend: { type: 'scroll', bottom: 0 },
      grid: { left: 44, right: 20, top: 24, bottom: 54 },
      xAxis: { type: 'category', data: availableDays.map((day) => `Round ${day}`) },
      yAxis: {
        type: 'value',
        name: yAxisName,
        axisLabel: requestChartViewMode === 'log'
          ? {
            formatter: (value) => formatNumber((10 ** Number(value || 0)) - 1)
          }
          : requestChartViewMode === 'relative_peak'
            ? {
              formatter: (value) => `${formatNumber(value)}%`
            }
            : undefined
      },
      series: requestChartSeriesMeta.map((entry, index) => ({
        name: entry.name,
        type: 'line',
        smooth: true,
        symbolSize: 6,
        lineStyle: {
          width: (entry.seriesType === 'external_order' || entry.seriesType === 'external_demand') ? 2.5 : (index === 0 ? 2.5 : 3),
          type: entry.seriesType === 'external_order' ? 'dashed' : entry.seriesType === 'external_demand' ? 'dotted' : 'solid',
        },
        areaStyle: index === 0 ? { opacity: 0.04 } : undefined,
        itemStyle: { color: entry.color },
        data: entry.displaySeries
      }))
    });
  };

  const renderHeatmap = (targetRef, titleSeries, cells, maxValue, name) => {
    const chart = echarts.init(targetRef.current);
    chartRefs.current.push(chart);

    setReviewChart(chart, {
      tooltip: {
        position: 'top',
        formatter: (params) => {
          const [dayIndex, layerIndex, value] = params.data;
          return `Round ${availableDays[dayIndex]}<br/>${titleSeries[layerIndex].name}<br/>${name}: ${formatRatio(value)}`;
        }
      },
      grid: { left: 92, right: 56, top: 18, bottom: 52 },
      xAxis: {
        type: 'category',
        data: availableDays.map((day) => `Round ${day}`),
        splitArea: { show: true }
      },
      yAxis: {
        type: 'category',
        data: titleSeries.map((entry) => entry.name),
        splitArea: { show: true }
      },
      visualMap: {
        min: 0,
        max: Math.max(1, maxValue),
        calculable: true,
        orient: 'vertical',
        right: 0,
        top: 'middle',
        formatter: (value) => `${formatNumber(value)}x`
      },
      series: [{
        name,
        type: 'heatmap',
        data: cells,
        label: {
          show: true,
          formatter: ({ value }) => formatNumber(value?.[2]),
          color: '#1f2d3d',
          fontSize: 10
        },
        emphasis: {
          itemStyle: {
            shadowBlur: 8,
            shadowColor: 'rgba(0, 0, 0, 0.18)'
          }
        }
      }]
    });
  };

  const renderRequestHeatmap = () => {
    const maxValue = Math.max(...requestHeatmapData.map((item) => Number(item[2] || 0)), 1);
    renderHeatmap(requestHeatmapRef, requestAmplificationLayers, requestHeatmapData, maxValue, 'Request Amplification Ratio');
  };

  const renderOrderHeatmap = () => {
    const maxValue = Math.max(...orderHeatmapData.map((item) => Number(item[2] || 0)), 1);
    renderHeatmap(orderHeatmapRef, orderAmplificationLayers, orderHeatmapData, maxValue, 'Order Amplification Ratio');
  };

  const renderTransmissionChart = () => {
    const chart = echarts.init(transmissionRef.current);
    chartRefs.current.push(chart);

    const transmissionTotals = [
      ...flowModels.transmissionLayers.map((layer) => ({
        ...layer,
        value: sumSeries(requestSeriesMap[layer.denominatorKey] || []) > 0
          ? sumSeries(requestSeriesMap[layer.numeratorKey] || []) / sumSeries(requestSeriesMap[layer.denominatorKey] || [])
          : null
      })),
      ...(externalUpstreamDemandLayer && topInternalFlow
        ? [{
          key: `${externalUpstreamDemandLayer.key}__vs_prev`,
          name: `${externalUpstreamDemandLayer.shortName} / ${topInternalFlow.shortName || topInternalFlow.upstreamEnterpriseId}`,
          color: externalUpstreamDemandLayer.color,
          value: sumSeries(requestSeriesMap[topInternalFlow.key] || []) > 0
            ? sumSeries(externalUpstreamDemandSeries) / sumSeries(requestSeriesMap[topInternalFlow.key] || [])
            : null,
          isExternalDemandTransmission: true,
        }]
        : []),
      ...(externalUpstreamOrderLayer && topInternalFlow
        ? [{
          key: `${externalUpstreamOrderLayer.key}__vs_prev`,
          name: `${externalUpstreamOrderLayer.shortName} / ${topInternalFlow.shortName || topInternalFlow.upstreamEnterpriseId}`,
          color: externalUpstreamOrderLayer.color,
          value: sumSeries(requestSeriesMap[topInternalFlow.key] || []) > 0
            ? sumSeries(externalUpstreamQuantitySeries) / sumSeries(requestSeriesMap[topInternalFlow.key] || [])
            : null,
          isExternalOrderTransmission: true,
        }]
        : [])
    ];
    const maxRatio = Math.max(...transmissionTotals.map((entry) => Number(entry.value || 0)), 1);

    setReviewChart(chart, {
      tooltip: {
        trigger: 'item',
        formatter: (params) => {
          const value = Number(params.value || 0);
          const delta = value - 1;
          const demandSourceNote = params?.data?.isExternalDemandTransmission
            ? '<br/>Definition: external replenishment demand / top-tier cumulative requests'
            : '';
          const sourceNote = params?.data?.isExternalOrderTransmission
            ? '<br/>Definition: external procurement volume / top-tier cumulative requests'
            : '';
          return `${params.name}<br/>Aggregate transmission ratio: ${formatRatio(value)}<br/>Deviation from 1x: ${delta >= 0 ? '+' : ''}${formatNumber(delta)}x${demandSourceNote}${sourceNote}`;
        }
      },
      grid: { left: 152, right: 42, top: 20, bottom: 28 },
      xAxis: {
        type: 'value',
        min: 0,
        max: Math.max(1.6, Math.ceil((maxRatio + 0.25) * 10) / 10),
        axisLabel: {
          formatter: (value) => `${formatNumber(value)}x`
        },
        splitLine: {
          lineStyle: {
            color: '#e7edf7'
          }
        }
      },
      yAxis: {
        type: 'category',
        data: transmissionTotals.map((entry) => entry.name)
      },
      series: [{
        name: 'Aggregate Transmission Ratio',
        type: 'bar',
        barWidth: 24,
        data: transmissionTotals.map((entry) => ({
          value: entry.value,
          itemStyle: { color: entry.color }
        })),
        label: {
          show: true,
          position: 'right',
          color: '#2d3748',
          fontWeight: 600,
          formatter: ({ value }) => {
            const delta = Number(value || 0) - 1;
            return `${formatRatio(value)}  (${delta >= 0 ? '+' : ''}${formatNumber(delta)}x)`;
          }
        },
        markLine: {
          symbol: 'none',
          label: {
            formatter: '1x baseline',
            color: '#7f8db5'
          },
          lineStyle: {
            type: 'dashed',
            color: '#9fb0dd'
          },
          data: [{ xAxis: 1 }]
        }
      }]
    });
  };

  const renderConversionChart = () => {
    const chart = echarts.init(conversionRef.current);
    chartRefs.current.push(chart);

    setReviewChart(chart, {
      tooltip: { trigger: 'axis' },
      legend: { type: 'scroll', bottom: 0 },
      grid: { left: 52, right: 20, top: 24, bottom: 54 },
      xAxis: {
        type: 'category',
        data: conversionRows.map((row) => row.name)
      },
      yAxis: {
        type: 'value',
        name: getQuantityAxisLabel('Cumulative Quantity', quantityView)
      },
      series: [
        {
          name: 'Cumulative Requests',
          type: 'bar',
          itemStyle: { color: '#8ec5ff' },
          data: conversionRows.map((row) => row.requestTotal)
        },
        {
          name: 'Cumulative Proposals',
          type: 'bar',
          itemStyle: { color: '#8fd3a8' },
          data: conversionRows.map((row) => row.proposalTotal)
        },
        {
          name: 'Cumulative Accepted Proposals',
          type: 'bar',
          itemStyle: { color: '#7cc8fa' },
          data: conversionRows.map((row) => row.acceptedProposalTotal)
        },
        {
          name: 'Cumulative Confirmed Proposals',
          type: 'bar',
          itemStyle: { color: '#ffd085' },
          data: conversionRows.map((row) => row.confirmedProposalTotal)
        },
        {
          name: 'Cumulative Orders',
          type: 'bar',
          itemStyle: { color: '#f2994a' },
          data: conversionRows.map((row) => row.orderTotal)
        },
        {
          name: 'Cumulative Arrivals / Deliveries',
          type: 'bar',
          itemStyle: { color: '#e76f51' },
          data: conversionRows.map((row) => row.arrivedTotal)
        }
      ]
    });
  };

  const renderResponseChart = () => {
    const chart = echarts.init(responseRef.current);
    chartRefs.current.push(chart);

    setReviewChart(chart, {
      tooltip: { trigger: 'axis' },
      legend: { type: 'scroll', bottom: 0 },
      grid: { left: 52, right: 62, top: 24, bottom: 56 },
      xAxis: {
        type: 'category',
        data: availableDays.map((day) => `Round ${day}`)
      },
      yAxis: [
        {
          type: 'value',
          name: 'Backlog'
        },
        {
          type: 'value',
          name: 'Fill Rate',
          min: 0,
          max: 1,
          axisLabel: {
            formatter: (value) => `${Math.round(value * 100)}%`
          }
        }
      ],
      series: [
        ...responseRows.map((row) => ({
          name: row.backlogName,
          type: 'bar',
          stack: 'backlog',
          itemStyle: { color: row.color, opacity: 0.32 },
          emphasis: { focus: 'series' },
          data: row.backlogSeries
        })),
        ...responseRows.map((row) => ({
          name: row.fillName,
          type: 'line',
          yAxisIndex: 1,
          smooth: true,
          symbolSize: 5,
          itemStyle: { color: row.color },
          lineStyle: { width: 2.5, color: row.color },
          data: row.fillRateSeries
        }))
      ]
    });
  };

  const renderGapChart = () => {
    const chart = echarts.init(gapRef.current);
    chartRefs.current.push(chart);

    setReviewChart(chart, {
      tooltip: { trigger: 'axis' },
      legend: { type: 'scroll', bottom: 0 },
      grid: { left: 44, right: 20, top: 24, bottom: 54 },
      xAxis: { type: 'category', data: availableDays.map((day) => `Round ${day}`) },
      yAxis: { type: 'value', name: getQuantityAxisLabel('Demand-Order Gap', quantityView) },
      series: flowModels.edgeLayers.map((entry) => ({
        name: `${entry.name} Gap`,
        type: 'bar',
        itemStyle: { color: entry.color },
        data: gapSeriesMap[entry.key] || []
      }))
    });
  };

  const renderInventoryChart = () => {
    const chart = echarts.init(inventoryRef.current);
    chartRefs.current.push(chart);

    const getCompanyInventory = (company, itemId) => availableDays.map((day) => {
      const row = observerRows.find((entry) => entry.company === company && entry.day === day);
      return convertQuantityByItem(getInventoryQuantity(row?.observation, itemId), itemId, quantityView);
    });
    const manufacturingNodeFlow = flowModels.edgeLayers.find((layer) => layer.normalizeByRecipe);
    const manufacturingNodeId = manufacturingNodeFlow?.downstreamEnterpriseId || '';
    const manufacturingNodeName = manufacturingNodeFlow?.shortName || manufacturingNodeId;
    const inventoryCompanies = Array.from(
      new Map(
        flowModels.flowSequence
          .flatMap((flow) => [
            [flow.upstream_enterprise_id, flow.upstream_enterprise_name],
            [flow.downstream_enterprise_id, flow.downstream_enterprise_name],
          ])
          .filter(([enterpriseId]) => enterpriseId && enterpriseId !== 'external_market')
      ).entries()
    );
    const finishedGoodsSeries = inventoryCompanies.map(([enterpriseId, enterpriseName], index) => ({
      name: `${enterpriseName} beer`,
      type: 'line',
      smooth: true,
      itemStyle: { color: FLOW_COLORS[index % FLOW_COLORS.length] },
      lineStyle: { color: FLOW_COLORS[index % FLOW_COLORS.length] },
      data: getCompanyInventory(enterpriseId, 'beer')
    }));
    const manufacturingRawEquivalentSeries = availableDays.map((day) => {
      const row = observerRows.find((entry) => entry.company === manufacturingNodeId && entry.day === day);
      return getRawMaterialEquivalentInventory(row?.observation, quantityView);
    });

    setReviewChart(chart, {
      tooltip: { trigger: 'axis' },
      legend: { type: 'scroll', bottom: 0 },
      grid: { left: 44, right: 20, top: 24, bottom: 54 },
      xAxis: { type: 'category', data: availableDays.map((day) => `Round ${day}`) },
      yAxis: { type: 'value', name: getQuantityAxisLabel('Inventory', quantityView) },
      series: [
        ...finishedGoodsSeries,
        {
          name: quantityView?.enabled
            ? `${manufacturingNodeName} Total Raw-Material Inventory (${quantityView.productId} Equivalent)`
            : `${manufacturingNodeName} Total Raw-Material Inventory`,
          type: 'bar',
          itemStyle: { color: '#9b51e0', opacity: 0.55 },
          data: manufacturingRawEquivalentSeries
        }
      ]
    });
  };

  const bottomEnterpriseId = flowModels.customerFlow.upstream_enterprise_id || '';
  const bottomEnterpriseName = flowModels.customerFlow.upstream_enterprise_name || bottomEnterpriseId;
  const manufacturingFlow = flowModels.edgeLayers.find((layer) => layer.normalizeByRecipe);
  const manufacturingEnterpriseId = manufacturingFlow?.downstreamEnterpriseId || '';
  const manufacturingEnterpriseName = manufacturingFlow?.shortName || manufacturingEnterpriseId;
  const bottomObservation = useMemo(() => getLatest(observerRows, bottomEnterpriseId), [observerRows, bottomEnterpriseId]);
  const manufacturingObservation = useMemo(() => getLatest(observerRows, manufacturingEnterpriseId), [observerRows, manufacturingEnterpriseId]);
  const customerSeriesKey = flowModels.customerFlow.dynamic_key || 'customer_to_retailer';
  const firstEdgeKey = flowModels.edgeLayers[0]?.key;
  const totalExternalDemand = externalDemand?.total_demand_quantity || 0;
  const totalRetailerRequests = firstEdgeKey ? sumSeries(requestSeriesMap[firstEdgeKey]) : 0;
  const totalEnterpriseOrders = Object.values(enterpriseOrderSeries).reduce(
    (sum, values) => sum + sumSeries(values),
    0
  );
  const totalUpstreamExternalOrders = sumSeries(externalTradeFlows?.upstreamQuantitySeries || []);
  const totalDownstreamExternalOrders = sumSeries(externalTradeFlows?.downstreamQuantitySeries || []);
  const totalDisplayedOrders = totalEnterpriseOrders
    + (includeUpstreamExternalSupplierMode ? totalUpstreamExternalOrders : 0)
    + (includeDownstreamExternalMarketMode ? totalDownstreamExternalOrders : 0);
  const totalGap = Object.values(gapSeriesMap).reduce(
    (sum, values) => sum + values.reduce((innerSum, value) => innerSum + Number(value || 0), 0),
    0
  );
  const totalBacklog = responseRows.reduce(
    (sum, row) => sum + toFiniteNumber(row.backlogSeries[row.backlogSeries.length - 1], 0),
    0
  );
  const lowestFillRate = responseRows.reduce((lowest, row) => {
    const current = toFiniteNumber(row.fillRateSeries[row.fillRateSeries.length - 1], 0);
    if (!lowest || current < lowest.value) {
      return { company: row.company, value: current };
    }
    return lowest;
  }, null);
  const peakDemandAmplification = getPeakValuePoint(
    demandAmplificationSeriesMap,
    flowModels.demandAmplificationLayers,
    availableDays
  );
  const peakOrderAmplification = getPeakValuePoint(
    orderAmplificationSeriesMap,
    orderAmplificationLayers,
    availableDays
  );
  const weakestConversion = findWeakestConversion(conversionRows);
  const peakBacklog = findPeakBacklog(responseRows, availableDays);
  const firstRetailerAmplifiedDay = firstEdgeKey
    ? findFirstAmplifiedDay(demandAmplificationSeriesMap[`${firstEdgeKey}__vs_customer`], availableDays, 2)
    : null;
  const customerPeak = findPeakRound(requestSeriesMap[customerSeriesKey], availableDays);
  const peakLagSummary = flowModels.edgeLayers.map((layer) => {
    const peak = findPeakRound(requestSeriesMap[layer.key], availableDays);
    return {
      name: layer.name,
      color: layer.color,
      lag: peak.day === null || customerPeak.day === null ? null : peak.day - customerPeak.day,
      peakDay: peak.day,
      peakValue: peak.value,
      cvBwe: customerPeak.day === null ? null : (
        getCoefficientOfVariation(requestSeriesMap[customerSeriesKey]) > 0
          ? getCoefficientOfVariation(requestSeriesMap[layer.key]) / getCoefficientOfVariation(requestSeriesMap[customerSeriesKey])
          : null
      ),
      staticBwe: customerPeak.day === null ? null : (
        getVariance(requestSeriesMap[customerSeriesKey]) > 0
          ? getVariance(requestSeriesMap[layer.key]) / getVariance(requestSeriesMap[customerSeriesKey])
          : null
      )
    };
  });
  const bottomFinishedGoodsInventory = convertQuantityByItem(getInventoryQuantity(bottomObservation, 'beer'), 'beer', quantityView);
  const manufacturingFinishedGoodsInventory = convertQuantityByItem(getInventoryQuantity(manufacturingObservation, 'beer'), 'beer', quantityView);
  const manufacturingRawEquivalent = getRawMaterialEquivalentInventory(manufacturingObservation, quantityView);
  const maxStaticBwe = Math.max(...peakLagSummary.map((item) => Number(item.staticBwe || 0)), 0);
  const maxStaticCvBwe = Math.max(...peakLagSummary.map((item) => Number(item.cvBwe || 0)), 0);
  const longestPeakLag = peakLagSummary.reduce((best, current) => {
    if (current.lag === null || current.lag === undefined) {
      return best;
    }
    if (!best || current.lag > best.lag) {
      return current;
    }
    return best;
  }, null);
  const totalAcceptedProposalQuantity = conversionRows.reduce((sum, row) => sum + row.acceptedProposalTotal, 0);
  const totalConfirmedProposalQuantity = conversionRows.reduce((sum, row) => sum + row.confirmedProposalTotal, 0);
  const totalRequestQuantity = conversionRows.reduce((sum, row) => sum + row.requestTotal, 0);
  const totalOrderQuantity = conversionRows.reduce((sum, row) => sum + row.orderTotal, 0);
  const totalArrivedQuantity = conversionRows.reduce((sum, row) => sum + row.arrivedTotal, 0);
  const includeAnyExternalMode = includeUpstreamExternalSupplierMode || includeDownstreamExternalMarketMode;
  const hasNativeRawSeries = Boolean(bullwhip?.raw_series);
  const hasNativeEffectiveSeries = Boolean(bullwhip?.effective_series);
  const requestModeLabel = requestMode === 'raw' ? 'Raw Requests' : 'Effective Propagation';
  const requestChartViewLabel = requestChartViewMode === 'log'
    ? 'Log Scale'
    : requestChartViewMode === 'relative_peak'
      ? 'Relative Peak'
      : 'Absolute Quantity';
  const transmissionSummaryText = flowModels.transmissionLayers.length >= 3
    ? `${flowModels.transmissionLayers[0].name} is above 1x, ${flowModels.transmissionLayers[1].name} is below 1x, and ${flowModels.transmissionLayers[2].name} rises above 1x again`
    : 'the first tier is above 1x, the middle tier is below 1x, and the upstream link rises above 1x again';
  const requestModeExplanation = requestMode === 'raw'
    ? `Includes historical buy requests that were later superseded or expired, revealing ordering intent. Source: ${hasNativeRawSeries ? 'bullwhip_metrics.json.raw_series' : 'exchange.json buy_requests fallback'}.`
    : `Excludes superseded and expired requests and fills the complete turn axis with zeros, supporting analysis of effective demand propagation. Source: ${hasNativeEffectiveSeries ? 'bullwhip_metrics.json.effective_series' : 'bullwhip_metrics.json.series / exchange.json fallback'}.`;
  const requestChartViewExplanation = requestChartViewMode === 'log'
    ? 'The log scale preserves peaks while making smaller fluctuations visible.'
    : requestChartViewMode === 'relative_peak'
      ? 'Each series is expressed as a percentage of its own peak, enabling comparison of fluctuation shape and timing.'
      : 'Absolute quantities preserve actual request scale; large tier differences may visually compress smaller series.';
  const requestChartExternalNote = includeUpstreamExternalSupplierMode && (externalUpstreamDemandLayer || externalUpstreamOrderLayer)
    ? `${externalUpstreamDemandLayer ? `The dotted "${externalUpstreamDemandLayer.name}" series shows top-tier replenishment demand from top_tier_supply_plan.` : ''}${externalUpstreamDemandLayer && externalUpstreamOrderLayer ? ' ' : ''}${externalUpstreamOrderLayer ? `The dashed "${externalUpstreamOrderLayer.name}" series shows actual external procurement and is unaffected by the raw/effective filter.` : ''}`
    : includeUpstreamExternalSupplierMode
      ? 'No top-tier external procurement orders were recorded, so no external upstream order series is shown.'
      : '';

  if(loadError)return <section className="view-error" role="alert"><h3>Bullwhip review unavailable</h3><p>{loadError}</p><button onClick={()=>{clearReadCache();setReload(v=>v+1);}}>Retry bullwhip review</button></section>;
  if (loading) {
    return <LoadingState label="Loading bullwhip-effect data…" variant="chart"/>;
  }

  if (!bullwhip) {
    return <div className="error">bullwhip_metrics.json was not found; the bullwhip overview cannot be generated.</div>;
  }

  return (
    <div className="bullwhip-page">
      <div className="bullwhip-kpi-grid">
        <div className="bullwhip-kpi-card">
          <span>Current Measurement</span>
          <strong>{requestModeLabel}</strong>
        </div>
        <div className="bullwhip-kpi-card">
          <span>Cumulative Terminal Demand</span>
          <strong>{formatNumber(totalExternalDemand)}</strong>
        </div>
        <div className="bullwhip-kpi-card warning">
          <span>Peak Request-Amplification Tier</span>
          <strong>{peakDemandAmplification.layerName === '-' ? '-' : `${peakDemandAmplification.layerName} · Round ${peakDemandAmplification.day}`}</strong>
        </div>
        <div className="bullwhip-kpi-card warning">
          <span>Maximum Static BWE</span>
          <strong>{formatRatio(maxStaticBwe)}</strong>
        </div>
        <div className="bullwhip-kpi-card">
          <span>Maximum Static CV-BWE</span>
          <strong>{formatRatio(maxStaticCvBwe)}</strong>
        </div>
        <div className="bullwhip-kpi-card warning">
          <span>Request-to-Arrival / Delivery Conversion</span>
          <strong>{totalRequestQuantity > 0 ? formatPercent(totalArrivedQuantity / totalRequestQuantity) : '-'}</strong>
        </div>
        <div className="bullwhip-kpi-card">
          <span>Maximum Peak Lag</span>
          <strong>{longestPeakLag ? `${longestPeakLag.name} · ${longestPeakLag.lag} turns` : '-'}</strong>
        </div>
      </div>

      <div className="bullwhip-mode-panel">
        <div className="bullwhip-source-strip">
          <span>Current run: {currentRunId || '-'}</span>
          <span>Data source: {currentRunSourceLabel || dataRoot}</span>
          <span>Series source: {requestMode === 'raw'
            ? (hasNativeRawSeries ? 'raw_series' : 'exchange fallback')
            : (hasNativeEffectiveSeries ? 'effective_series' : 'series / exchange fallback')}</span>
        </div>
        <div className="bullwhip-mode-switch">
          <button
            type="button"
            className={`bullwhip-mode-button ${requestMode === 'effective' ? 'active' : ''}`}
            onClick={() => setRequestMode('effective')}
          >
            Effective Propagation
          </button>
          <button
            type="button"
            className={`bullwhip-mode-button ${requestMode === 'raw' ? 'active' : ''}`}
            onClick={() => setRequestMode('raw')}
          >
            Raw Requests
          </button>
        </div>
        <div className="bullwhip-mode-note">{requestModeExplanation}</div>
      </div>

      <div className="bullwhip-layout">
        <div className="bullwhip-chart-card wide">
          <div className="bullwhip-card-header">
            <div className="bullwhip-card-title">
              {quantityView?.enabled ? `${requestModeLabel} Overview (Top Tier as ${quantityView.productId} Equivalents)` : `${requestModeLabel} Overview`}
            </div>
            <div className="bullwhip-inline-switch" aria-label="Request-chart display mode">
              <button
                type="button"
                className={`bullwhip-mode-button compact ${requestChartViewMode === 'absolute' ? 'active' : ''}`}
                onClick={() => setRequestChartViewMode('absolute')}
              >
                Absolute
              </button>
              <button
                type="button"
                className={`bullwhip-mode-button compact ${requestChartViewMode === 'log' ? 'active' : ''}`}
                onClick={() => setRequestChartViewMode('log')}
              >
                Log Scale
              </button>
              <button
                type="button"
                className={`bullwhip-mode-button compact ${requestChartViewMode === 'relative_peak' ? 'active' : ''}`}
                onClick={() => setRequestChartViewMode('relative_peak')}
              >
                Relative Peak
              </button>
            </div>
          </div>
          <div className="bullwhip-inline-note">
            Display mode: {requestChartViewLabel}. {requestChartViewExplanation}
            {requestChartExternalNote ? ` ${requestChartExternalNote}` : ''}
          </div>
          <div className="bullwhip-chart large" ref={requestRef} />
        </div>

        <div className="bullwhip-chart-card">
          <div className="bullwhip-card-title">Inter-Tier Transmission Ratios (Cumulative Requests)</div>
          <div className="bullwhip-chart" ref={transmissionRef} />
        </div>

        <div className="bullwhip-chart-card">
          <div className="bullwhip-card-title">Per-Round Request-Amplification Heatmap</div>
          <div className="bullwhip-chart" ref={requestHeatmapRef} />
        </div>

        <div className="bullwhip-chart-card">
          <div className="bullwhip-card-title">Per-Round Order-Amplification Heatmap</div>
          <div className="bullwhip-chart" ref={orderHeatmapRef} />
        </div>

        <div className="bullwhip-chart-card">
          <div className="bullwhip-card-title">Fulfillment Outcomes: Backlog and Fill Rate</div>
          <div className="bullwhip-chart" ref={responseRef} />
        </div>

        <div className="bullwhip-chart-card wide">
          <div className="bullwhip-card-title">
            {quantityView?.enabled ? `Cumulative Request–Proposal–Acceptance–Order–Arrival Funnel (${quantityView.productId} Equivalent)` : 'Cumulative Request–Proposal–Acceptance–Order–Arrival Funnel'}
          </div>
          <div className="bullwhip-chart" ref={conversionRef} />
        </div>

        <div className="bullwhip-chart-card">
          <div className="bullwhip-card-title">Raw vs. Effective Variance and BWE</div>
          <div className="bullwhip-table-wrapper">
            <table className="bullwhip-table">
              <thead>
                <tr>
                  <th>Tier</th>
                  <th>Raw Variance</th>
                  <th>Effective Variance</th>
                  <th>Raw BWE</th>
                  <th>Effective BWE</th>
                </tr>
              </thead>
              <tbody>
                {ratioComparisonRows.map((row) => (
                  <tr key={row.name}>
                    <td>{row.name}</td>
                    <td>{formatNumber(row.rawVariance)}</td>
                    <td>{formatNumber(row.effectiveVariance)}</td>
                    <td>{formatRatio(row.rawRatio)}</td>
                    <td>{formatRatio(row.effectiveRatio)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="bullwhip-chart-card">
          <div className="bullwhip-card-title">Lifecycle Status Panel</div>
          <div className="bullwhip-table-wrapper">
            <table className="bullwhip-table">
              <thead>
                <tr>
                  <th>Tier</th>
                  <th>Total Raw Requests</th>
                  <th>Total Effective Requests</th>
                  <th>Request Status</th>
                  <th>Proposal Status</th>
                </tr>
              </thead>
              <tbody>
                {lifecycleRows.map((row) => (
                  <tr key={row.name}>
                    <td>{row.name}</td>
                    <td>{formatNumber(row.rawRequestTotal)}</td>
                    <td>{formatNumber(row.effectiveRequestTotal)}</td>
                    <td>{Object.entries(row.requestStatusCounts).map(([key, value]) => `${key}:${value}`).join(' / ') || '-'}</td>
                    <td>{Object.entries(row.proposalStatusCounts).map(([key, value]) => `${key}:${value}`).join(' / ') || '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="bullwhip-chart-card">
          <div className="bullwhip-card-title">
            {quantityView?.enabled ? `Per-Round Demand–Order Gap (${quantityView.productId} Equivalent)` : 'Per-Round Demand–Order Gap'}
          </div>
          <div className="bullwhip-chart" ref={gapRef} />
        </div>

        <div className="bullwhip-chart-card">
          <div className="bullwhip-card-title">
            {quantityView?.enabled ? `Inventory Response and Upstream Bottlenecks (Raw Materials as ${quantityView.productId} Equivalents)` : 'Inventory Response and Upstream Bottlenecks'}
          </div>
          <div className="bullwhip-chart" ref={inventoryRef} />
        </div>

        <div className="bullwhip-chart-card insight-card wide">
          <div className="bullwhip-card-title">Bullwhip-Effect Findings</div>
          <ul>
            <li>Request amplification peaks at {peakDemandAmplification.layerName === '-' ? '-' : `${peakDemandAmplification.layerName}, Round ${peakDemandAmplification.day}`} with {formatRatio(peakDemandAmplification.value)}; order amplification peaks at {peakOrderAmplification.layerName === '-' ? '-' : `${peakOrderAmplification.layerName}, Round ${peakOrderAmplification.day}`} with {formatRatio(peakOrderAmplification.value)}.</li>
            <li>The inter-tier transmission chart compares cumulative requests and labels deviations from `1x`. When {transmissionSummaryText}, the chain exhibits amplification, attenuation, and renewed upstream amplification across successive tiers.</li>
            <li>Maximum static BWE is {formatRatio(maxStaticBwe)}, and maximum static CV-BWE is {formatRatio(maxStaticCvBwe)}. Together they summarize request variability relative to terminal demand.</li>
            <li>Cumulative quantities are: requests {formatNumber(totalRequestQuantity)}, proposals {formatNumber(conversionRows.reduce((sum, row) => sum + row.proposalTotal, 0))}, accepted proposals {formatNumber(totalAcceptedProposalQuantity)}, confirmed proposals {formatNumber(totalConfirmedProposalQuantity)}, orders {formatNumber(totalOrderQuantity)}, and arrivals/deliveries {formatNumber(totalArrivedQuantity)}.</li>
            <li>The weakest request-to-order conversion occurs at {weakestConversion ? `${weakestConversion.layerName} (${formatPercent(weakestConversion.ratio)})` : '-'}. Strong request amplification paired with a narrowing funnel indicates that trading or fulfillment attenuates behavioral demand amplification.</li>
            <li>The first turn with first-tier amplification of at least 2x is {firstRetailerAmplifiedDay === null ? 'not observed' : `Round ${firstRetailerAmplifiedDay}`}. Ending chain-wide backlog is {formatNumber(totalBacklog)}; peak backlog occurs at {peakBacklog.company === '-' ? '-' : `${peakBacklog.company}, Round ${peakBacklog.day}`} with {formatNumber(peakBacklog.value)}. The lowest ending fill rate is {lowestFillRate ? `${lowestFillRate.company} · ${formatPercent(lowestFillRate.value)}` : '-'}.</li>
            <li>Ending beer inventory is {formatNumber(bottomFinishedGoodsInventory)} at {bottomEnterpriseName} and {formatNumber(manufacturingFinishedGoodsInventory)} at {manufacturingEnterpriseName}. Raw-material inventory at {manufacturingEnterpriseName} is {formatNumber(manufacturingRawEquivalent)}{quantityView?.enabled ? ` (${quantityView.productId} equivalent)` : ''}. Simultaneous downstream and raw-material depletion signals a supply bottleneck.</li>
            <li>The view combines dual-series metrics from `bullwhip_metrics.json` with exchange requests, proposals, orders, procurement arrivals, and sales fulfillment to distinguish amplification, attenuation, and operating consequences.</li>
          </ul>
        </div>
      </div>
    </div>
  );
};

export default BullwhipEffectView;
