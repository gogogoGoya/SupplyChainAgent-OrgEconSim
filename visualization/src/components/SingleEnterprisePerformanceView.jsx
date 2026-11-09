import LoadingState from './LoadingState';
import {setReviewChart} from '../utils/chartReview';
import { normalizeReviewState, englishDepartmentLabel } from '../utils/reviewMetrics.mjs';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import * as echarts from '../utils/echartsRuntime';
import { buildDataUrl, safeFetchJson } from '../utils/dataSource';
import { getArtifactReview,getArtifactTrend } from '../utils/operationsApi';
import {reviewLocation} from '../utils/reviewDisplay';
import { getActionDisplayName, isActionPassAction } from '../utils/actionLabels';

const DEPARTMENTS = ['finance', 'production', 'sales', 'inventory', 'procurement', 'hr'];

const DEPARTMENT_LABELS = {
  finance: 'Finance',
  production: 'Production',
  sales: 'Sales',
  inventory: 'Inventory',
  procurement: 'Procurement',
  hr: 'Human Resources',
};

const DEPARTMENT_COLORS = {
  finance: '#1890ff',
  production: '#52c41a',
  sales: '#faad14',
  inventory: '#722ed1',
  procurement: '#eb2f96',
  hr: '#13c2c2',
};

const toNumber = (value, fallback = 0) => {
  if (value === null || value === undefined || value === '') {
    return fallback;
  }
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
};

const normalizeActions = (payload) => {
  if (payload && typeof payload === 'object' && !Array.isArray(payload)) {
    const nested = payload.workflow || payload.actions || payload.action_records || payload.items;
    if (Array.isArray(nested)) {
      return normalizeActions(nested);
    }
    return [payload];
  }
  if (!Array.isArray(payload)) {
    return [];
  }
  if (payload.length === 1 && Array.isArray(payload[0])) {
    return payload[0];
  }
  return payload.filter((item) => item && typeof item === 'object');
};

const getActionNameFromEntry = (entry) => (
  entry?.action?.action_name
  || entry?.actionName
  || entry?.action_name
  || entry?.name
  || (entry?.pass_reason ? 'action_pass' : '')
);

const isActionPassEntry = (entry) => isActionPassAction(getActionNameFromEntry(entry));

const countErrorPayload = (payload) => {
  if (!payload) {
    return 0;
  }
  if (Array.isArray(payload)) {
    return payload.length;
  }
  if (typeof payload === 'object') {
    return Object.keys(payload).length > 0 ? 1 : 0;
  }
  return 1;
};

const ERROR_RETRY_INDICES = Array.from({ length: 6 }, (_, index) => index);

const uniqueSortedNumbers = (values) => Array.from(new Set(
  (values || []).map((value) => Number(value)).filter(Number.isFinite)
)).sort((a, b) => a - b);

const dayLabel = (day) => `Round ${day}`;

const formatMillionValue = (value) => `${(Number(value || 0) / 1000000).toFixed(2)}M`;

const formatNumber = (value, suffix = '') => {
  if (value === null || value === undefined || value === '') {
    return '-';
  }
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return String(value);
  }
  return `${numeric.toLocaleString('en-US', { maximumFractionDigits: 2 })}${suffix}`;
};

const asArray = (value) => {
  if (Array.isArray(value)) {
    return value.filter((item) => item && typeof item === 'object');
  }
  if (!value || typeof value !== 'object') {
    return [];
  }
  return Object.entries(value).map(([id, item]) => (
    item && typeof item === 'object'
      ? { id, ...item }
      : { id, value: item }
  ));
};

const getRecordId = (record, candidates) => (
  candidates.map((key) => record?.[key]).find((value) => value !== undefined && value !== null && value !== '')
  || record?.id
  || '-'
);

const renderValueList = (items, emptyText = 'Data not available') => {
  if (!items || items.length === 0) {
    return <p className="single-dept-empty">{emptyText}</p>;
  }
  return (
    <div className="single-dept-kv-list">
      {items.map((item) => (
        <div key={item.label}>
          <span>{item.label}</span>
          <strong>{item.value}</strong>
        </div>
      ))}
    </div>
  );
};

const fetchObservationPayload = async (dataRoot, company, day) => {
  const candidates = [
    `enterprises/${company}/observations/observation_day${day}.txt`,
    `observations/observation_day${day}.txt`,
  ];
  for (const path of candidates) {
    const payload = await safeFetchJson(buildDataUrl(dataRoot, path));
    if (payload && typeof payload === 'object') {
      return payload;
    }
  }
  return null;
};

const mergeDepartmentState = (departmentPayload, observationPayload, dept) => ({
  ...((observationPayload?.[dept] && typeof observationPayload[dept] === 'object') ? observationPayload[dept] : {}),
  ...((departmentPayload?.self_state && typeof departmentPayload.self_state === 'object')
    ? departmentPayload.self_state
    : (departmentPayload && typeof departmentPayload === 'object' ? departmentPayload : {})),
});

const buildForecastLabel = (currentDay, day) => {
  const dayNumber = Number(day);
  const baseDay = Number(currentDay);
  if (Number.isFinite(dayNumber) && Number.isFinite(baseDay) && dayNumber > baseDay) {
    return `T+${dayNumber - baseDay}`;
  }
  return dayLabel(day);
};

const hasSeriesData = (option) => (
  Array.isArray(option?.series)
  && option.series.some((series) => Array.isArray(series.data) && series.data.length > 0)
);

const FORECAST_HORIZON = 7;
const PRODUCT_ITEM_TYPES = new Set([
  'product',
  'finished_product',
  'finished_good',
  'produce_daily',
  'produce_completed',
]);
const TERMINAL_ORDER_STATUSES = new Set([
  'completed',
  'delivered',
  'rejected',
  'cancelled',
  'canceled',
  'breached',
  'failed',
]);
const COMMITTED_ORDER_STATUSES = new Set([
  'accepted',
  'in_progress',
  'backlog',
  'confirmed',
]);
const ORDER_STATUS_LABELS = {
  available: 'Available',
  accepted: 'Accepted, Pending Fulfillment',
  in_progress: 'In Fulfillment',
  completed: 'Completed',
  delivered: 'Delivered',
  rejected: 'Rejected',
  breached: 'Breached',
  cancelled: 'Canceled',
  canceled: 'Canceled',
  failed: 'Failed',
  unknown: 'Unknown status',
};

const normalizeOrderStatus = (status) => {
  const normalized = String(status || 'unknown').trim().toLowerCase();
  return normalized || 'unknown';
};

const getOrderStatusLabel = (status) => ORDER_STATUS_LABELS[normalizeOrderStatus(status)] || String(status || 'Unknown status');

const hasOrderDeadline = (order) => order?.delivery_deadline !== null
  && order?.delivery_deadline !== undefined
  && order?.delivery_deadline !== '';

const shouldTrackOrderDeadline = (order) => {
  const status = normalizeOrderStatus(order?.status);
  return !TERMINAL_ORDER_STATUSES.has(status)
    && hasOrderDeadline(order)
    && toNumber(order?.quantity) > 0;
};

const buildOrdersSummary = (orders, sourceSummary) => {
  const byStatus = {};
  (orders || []).forEach((order) => {
    const status = normalizeOrderStatus(order?.status);
    byStatus[status] = (byStatus[status] || 0) + 1;
  });
  return {
    ...(sourceSummary && typeof sourceSummary === 'object' ? sourceSummary : {}),
    total: orders?.length || toNumber(sourceSummary?.total),
    by_status: Object.keys(byStatus).length > 0 ? byStatus : (sourceSummary?.by_status || {}),
  };
};

const mapKey = (day, id) => `${Number(day)}::${id || ''}`;
const addToQuantityMap = (target, day, id, quantity) => {
  const key = mapKey(day, id);
  target[key] = (target[key] || 0) + toNumber(quantity);
};
const getQuantityMapValue = (source, day, id) => toNumber(source?.[mapKey(day, id)]);

const flattenStatusBucket = (payload) => {
  if (Array.isArray(payload)) {
    return payload.filter((item) => item && typeof item === 'object');
  }
  if (!payload || typeof payload !== 'object') {
    return [];
  }
  return Object.entries(payload).flatMap(([status, bucket]) => (
    Array.isArray(bucket)
      ? bucket
        .filter((item) => item && typeof item === 'object')
        .map((item) => ({ ...item, status: item.status || status }))
      : []
  ));
};

const flattenSalesOrders = (observation) => flattenStatusBucket(observation?.sales?.sales_orders);
const latestInventoryItems = (observation) => (
  Array.isArray(observation?.inventory?.inventory_items)
    ? observation.inventory.inventory_items.filter((item) => item && typeof item === 'object')
    : []
);

const finishedStockByProduct = (observation) => {
  const stock = {};
  latestInventoryItems(observation).forEach((item) => {
    if (!PRODUCT_ITEM_TYPES.has(String(item.item_type || '').toLowerCase())) {
      return;
    }
    const productId = String(item.item_id || 'product');
    stock[productId] = (stock[productId] || 0) + toNumber(item.quantity);
  });
  return stock;
};

const recipeMaterialsByProduct = (observation) => {
  const recipes = observation?.production?.product_recipes || [];
  const recipeMap = {};
  const iterable = Array.isArray(recipes)
    ? recipes
    : Object.entries(recipes || {}).map(([productId, recipe]) => (
      recipe && typeof recipe === 'object'
        ? { product_id: productId, ...recipe }
        : null
    )).filter(Boolean);
  iterable.forEach((recipe) => {
    const productId = recipe?.product_id;
    const rawMaterials = recipe?.raw_materials;
    if (!productId || !rawMaterials || typeof rawMaterials !== 'object') {
      return;
    }
    const materialMap = {};
    Object.entries(rawMaterials).forEach(([materialId, quantity]) => {
      const amount = toNumber(quantity);
      if (amount > 0) {
        materialMap[String(materialId)] = amount;
      }
    });
    if (Object.keys(materialMap).length > 0) {
      recipeMap[String(productId)] = materialMap;
    }
  });
  return recipeMap;
};

const inferProductIds = (observation) => {
  const productIds = new Set();
  (observation?.production?.products_idList || []).forEach((productId) => {
    if (productId) {
      productIds.add(String(productId));
    }
  });
  Object.keys(recipeMaterialsByProduct(observation)).forEach((productId) => productIds.add(productId));
  flattenSalesOrders(observation).forEach((order) => {
    if (order.product_id) {
      productIds.add(String(order.product_id));
    }
  });
  latestInventoryItems(observation).forEach((item) => {
    if (PRODUCT_ITEM_TYPES.has(String(item.item_type || '').toLowerCase()) && item.item_id) {
      productIds.add(String(item.item_id));
    }
  });
  return Array.from(productIds).sort();
};

const flattenProductionPlans = (observation) => (
  flattenStatusBucket(observation?.production?.production_plans)
);

const isActiveProductionPlan = (plan) => {
  const status = String(plan?.status || '').toLowerCase();
  if (['completed', 'finished', 'failed', 'cancelled', 'canceled'].includes(status)) {
    return false;
  }
  const quantity = toNumber(plan?.quantity);
  const produced = toNumber(plan?.progress?.quantity_produced);
  return quantity <= 0 || produced < quantity;
};

const forecastPlanDailyRows = (observation, currentDay, horizon = FORECAST_HORIZON) => {
  const rows = [];
  flattenProductionPlans(observation).forEach((plan) => {
    if (!isActiveProductionPlan(plan)) {
      return;
    }
    const quantity = toNumber(plan.quantity);
    const produced = toNumber(plan.progress?.quantity_produced);
    let remaining = Math.max(0, quantity - produced);
    if (remaining <= 0) {
      return;
    }
    const dailyCapacity = Math.max(toNumber(plan.daily_capacity, remaining), 1);
    const firstDay = Math.max(Number(currentDay) + 1, Math.trunc(toNumber(plan.start_time, Number(currentDay) + 1)));
    const completionDay = Math.trunc(toNumber(plan.completion_time, Number(currentDay) + 1));
    const lastDay = Math.min(Number(currentDay) + horizon, Math.max(firstDay, completionDay));
    for (let futureDay = firstDay; futureDay <= lastDay && remaining > 0; futureDay += 1) {
      const output = Math.min(dailyCapacity, remaining);
      rows.push({ futureDay, plan, output });
      remaining -= output;
    }
  });
  return rows;
};

const forecastPlannedOutput = (observation, currentDay, horizon = FORECAST_HORIZON) => {
  const planned = {};
  const defaultProduct = inferProductIds(observation)[0] || 'product';
  forecastPlanDailyRows(observation, currentDay, horizon).forEach(({ futureDay, plan, output }) => {
    addToQuantityMap(planned, futureDay, String(plan.product_id || defaultProduct), output);
  });
  return planned;
};

const forecastMaterialConsumption = (observation, currentDay, horizon = FORECAST_HORIZON) => {
  const consumption = {};
  forecastPlanDailyRows(observation, currentDay, horizon).forEach(({ futureDay, plan, output }) => {
    const dailyMaterials = plan.daily_materials && typeof plan.daily_materials === 'object'
      ? plan.daily_materials
      : {};
    const materialsNeeded = plan.materials_needed && typeof plan.materials_needed === 'object'
      ? plan.materials_needed
      : {};
    const quantity = Math.max(1, toNumber(plan.quantity, output));
    const sourceMaterials = Object.keys(dailyMaterials).length > 0
      ? dailyMaterials
      : Object.fromEntries(Object.entries(materialsNeeded).map(([materialId, required]) => [
        materialId,
        toNumber(required) * toNumber(plan.daily_capacity, output) / quantity,
      ]));
    Object.entries(sourceMaterials).forEach(([materialId, dailyQuantity]) => {
      const plannedUse = toNumber(dailyQuantity) * output / Math.max(toNumber(plan.daily_capacity, output), 1);
      if (plannedUse > 0) {
        addToQuantityMap(consumption, futureDay, String(materialId), plannedUse);
      }
    });
  });
  return consumption;
};

const forecastInboundMaterial = (observation, currentDay, horizon = FORECAST_HORIZON) => {
  const inbound = {};
  flattenStatusBucket(observation?.procurement?.orders).forEach((order) => {
    const status = String(order.status || '').toLowerCase();
    if (['received', 'completed', 'cancelled', 'canceled', 'failed'].includes(status) || !order.material_id) {
      return;
    }
    const arrivalDay = Math.trunc(toNumber(order.actual_arrival_time || order.arrival_time || order.delivery_time, -1));
    if (Number(currentDay) < arrivalDay && arrivalDay <= Number(currentDay) + horizon) {
      addToQuantityMap(inbound, arrivalDay, String(order.material_id), order.quantity);
    }
  });
  return inbound;
};

const forecastCommittedDeliveries = (observation, currentDay, horizon = FORECAST_HORIZON) => {
  const deliveries = {};
  flattenSalesOrders(observation).forEach((order) => {
    const status = String(order.status || '').toLowerCase();
    if (!['accepted', 'in_progress'].includes(status)) {
      return;
    }
    const deadline = Math.trunc(toNumber(order.delivery_deadline, currentDay));
    if (Number(currentDay) < deadline && deadline <= Number(currentDay) + horizon) {
      addToQuantityMap(deliveries, deadline, String(order.product_id || 'product'), order.quantity);
    }
  });
  return deliveries;
};

const forecastUnplannedOrderMaterialDemand = (observation, currentDay, horizon = FORECAST_HORIZON) => {
  const recipeMap = recipeMaterialsByProduct(observation);
  const plannedOutput = forecastPlannedOutput(observation, currentDay, horizon);
  const projectedSupply = finishedStockByProduct(observation);
  const demand = {};
  flattenSalesOrders(observation).forEach((order) => {
    const status = String(order.status || '').toLowerCase();
    const productId = String(order.product_id || '');
    if (!productId || !COMMITTED_ORDER_STATUSES.has(status) || !recipeMap[productId]) {
      return;
    }
    const deadline = Math.trunc(toNumber(order.delivery_deadline, currentDay));
    if (Number(currentDay) < deadline && deadline <= Number(currentDay) + horizon) {
      addToQuantityMap(demand, deadline, productId, order.quantity);
    }
  });

  const materialDemand = {};
  for (let futureDay = Number(currentDay) + 1; futureDay <= Number(currentDay) + horizon; futureDay += 1) {
    const productIds = new Set(Object.keys(recipeMap));
    productIds.forEach((productId) => {
      projectedSupply[productId] = (projectedSupply[productId] || 0) + getQuantityMapValue(plannedOutput, futureDay, productId);
      const dueQuantity = getQuantityMapValue(demand, futureDay, productId);
      if (dueQuantity <= 0) {
        return;
      }
      const shortage = Math.max(0, dueQuantity - (projectedSupply[productId] || 0));
      projectedSupply[productId] = Math.max(0, (projectedSupply[productId] || 0) - dueQuantity);
      if (shortage <= 0) {
        return;
      }
      Object.entries(recipeMap[productId] || {}).forEach(([materialId, perUnit]) => {
        addToQuantityMap(materialDemand, futureDay, materialId, shortage * toNumber(perUnit));
      });
    });
  }
  return materialDemand;
};

const buildSnapshotChartsFromObservations = (observationsByDay, currentDay) => {
  const currentObservation = observationsByDay?.[String(currentDay)];
  if (!currentObservation) {
    return null;
  }
  const horizon = FORECAST_HORIZON;
  const orders = flattenSalesOrders(currentObservation);
  const productIds = new Set(inferProductIds(currentObservation));
  const finishedStock = finishedStockByProduct(currentObservation);
  Object.keys(finishedStock).forEach((productId) => productIds.add(productId));
  const plannedOutput = forecastPlannedOutput(currentObservation, currentDay, horizon);

  const demand = {};
  orders.forEach((order) => {
    const status = normalizeOrderStatus(order.status);
    if (!COMMITTED_ORDER_STATUSES.has(status)) {
      return;
    }
    const productId = String(order.product_id || Array.from(productIds)[0] || 'product');
    productIds.add(productId);
    const deadline = Math.trunc(toNumber(order.delivery_deadline, currentDay));
    if (Number(currentDay) < deadline && deadline <= Number(currentDay) + horizon) {
      addToQuantityMap(demand, deadline, productId, order.quantity);
    }
  });
  if (productIds.size === 0) {
    productIds.add('product');
  }

  const fulfillmentRows = [];
  const demandCapacityRows = [];
  const availableCapacityRaw = currentObservation.production?.production_lines?.available_capacity;
  const freeOutput = (
    availableCapacityRaw === null
    || availableCapacityRaw === undefined
    || availableCapacityRaw === ''
  )
    ? toNumber(currentObservation.production?.production_lines?.total_capacity)
    : toNumber(availableCapacityRaw);
  Array.from(productIds).sort().forEach((productId) => {
    let cumulativeDemand = 0;
    let cumulativePlannedOutput = 0;
    let optionalCumulativeGap = 0;
    for (let offset = 1; offset <= horizon; offset += 1) {
      const futureDay = Number(currentDay) + offset;
      const dailyDemand = getQuantityMapValue(demand, futureDay, productId);
      const plannedQuantity = getQuantityMapValue(plannedOutput, futureDay, productId);
      cumulativeDemand += dailyDemand;
      cumulativePlannedOutput += plannedQuantity;
      const cumulativeSupply = (finishedStock[productId] || 0) + cumulativePlannedOutput;
      const capacityGap = dailyDemand - (plannedQuantity + freeOutput);
      optionalCumulativeGap += Math.max(0, capacityGap);
      fulfillmentRows.push({
        future_day: futureDay,
        product_id: productId,
        daily_delivery_demand: dailyDemand,
        cumulative_supply: cumulativeSupply,
        cumulative_demand: cumulativeDemand,
        coverage_gap: cumulativeSupply - cumulativeDemand,
      });
      demandCapacityRows.push({
        future_day: futureDay,
        product_id: productId,
        required_output: dailyDemand,
        planned_output: plannedQuantity,
        free_output_equiv: freeOutput,
        optional_cumulative_gap: optionalCumulativeGap,
        capacity_gap: capacityGap,
      });
    }
  });

  const plannedConsumption = forecastMaterialConsumption(currentObservation, currentDay, horizon);
  const latentOrderDemand = forecastUnplannedOrderMaterialDemand(currentObservation, currentDay, horizon);
  const inboundArrivals = forecastInboundMaterial(currentObservation, currentDay, horizon);
  let materialItems = latestInventoryItems(currentObservation)
    .filter((item) => ['material', 'raw_material'].includes(String(item.item_type || '').toLowerCase()));
  if (materialItems.length === 0) {
    materialItems = latestInventoryItems(currentObservation)
      .filter((item) => !PRODUCT_ITEM_TYPES.has(String(item.item_type || '').toLowerCase()));
  }
  const rawMaterialRows = [];
  materialItems.forEach((item) => {
    const materialId = String(item.item_id || 'material');
    let projectedStock = toNumber(item.quantity);
    for (let offset = 1; offset <= horizon; offset += 1) {
      const futureDay = Number(currentDay) + offset;
      const inbound = getQuantityMapValue(inboundArrivals, futureDay, materialId);
      const plannedUse = getQuantityMapValue(plannedConsumption, futureDay, materialId);
      const latentNeed = getQuantityMapValue(latentOrderDemand, futureDay, materialId);
      const totalRequirement = plannedUse + latentNeed;
      projectedStock += inbound - totalRequirement;
      rawMaterialRows.push({
        future_day: futureDay,
        material_id: materialId,
        projected_stock: projectedStock,
        inbound_arrival: inbound,
        planned_consumption: plannedUse,
        latent_order_material_demand: latentNeed,
        total_material_requirement: totalRequirement,
        coverage_gap: projectedStock,
      });
    }
  });

  const committedDeliveries = forecastCommittedDeliveries(currentObservation, currentDay, horizon);
  const inventory = currentObservation.inventory || {};
  const currentInventoryQuantity = latestInventoryItems(currentObservation)
    .reduce((sum, item) => sum + toNumber(item.quantity), 0);
  const warehouseCapacity = toNumber(inventory.warehouse_capacity) > 0
    ? toNumber(inventory.warehouse_capacity)
    : Math.max(currentInventoryQuantity, 1);
  let projectedUsedCapacity = toNumber(
    inventory.used_capacity,
    currentInventoryQuantity
  );
  const projectedProductStock = { ...finishedStock };
  const warehouseRows = [];
  for (let offset = 1; offset <= horizon; offset += 1) {
    const futureDay = Number(currentDay) + offset;
    const inboundTotal = Object.entries(inboundArrivals).reduce((sum, [key, value]) => (
      key.startsWith(`${futureDay}::`) ? sum + toNumber(value) : sum
    ), 0);
    const plannedOutputTotal = Object.entries(plannedOutput).reduce((sum, [key, value]) => (
      key.startsWith(`${futureDay}::`) ? sum + toNumber(value) : sum
    ), 0);
    const plannedConsumptionTotal = Object.entries(plannedConsumption).reduce((sum, [key, value]) => (
      key.startsWith(`${futureDay}::`) ? sum + toNumber(value) : sum
    ), 0);
    let deliveredTotal = 0;
    Array.from(productIds).forEach((productId) => {
      projectedProductStock[productId] = (projectedProductStock[productId] || 0) + getQuantityMapValue(plannedOutput, futureDay, productId);
      const committed = getQuantityMapValue(committedDeliveries, futureDay, productId);
      const delivered = Math.min(projectedProductStock[productId] || 0, committed);
      projectedProductStock[productId] = Math.max(0, (projectedProductStock[productId] || 0) - delivered);
      deliveredTotal += delivered;
    });
    projectedUsedCapacity = Math.max(
      0,
      projectedUsedCapacity + inboundTotal + plannedOutputTotal - plannedConsumptionTotal - deliveredTotal
    );
    warehouseRows.push({
      future_day: futureDay,
      warehouse_capacity: warehouseCapacity,
      projected_used_capacity: projectedUsedCapacity,
      inbound_arrival: inboundTotal,
      planned_output: plannedOutputTotal,
      planned_material_consumption: plannedConsumptionTotal,
      committed_delivery: deliveredTotal,
      capacity_gap: warehouseCapacity - projectedUsedCapacity,
    });
  }

  const days = Object.keys(observationsByDay).map(Number).filter((day) => day <= Number(currentDay)).sort((a, b) => a - b);
  let previousFinanceRow = null;
  const historicalTrend = days.map((day) => {
    const finance = observationsByDay[String(day)]?.finance || {};
    const row = {
      day,
      cash: toNumber(finance.cash),
      revenue: toNumber(finance.total_revenue),
      total_cost_proxy: toNumber(finance.total_cost),
      net_profit: toNumber(finance.financial_indicators?.net_profit),
    };
    row.cash_change = previousFinanceRow ? row.cash - previousFinanceRow.cash : 0;
    row.revenue_change = previousFinanceRow ? row.revenue - previousFinanceRow.revenue : 0;
    row.total_cost_change = previousFinanceRow ? row.total_cost_proxy - previousFinanceRow.total_cost_proxy : 0;
    row.net_profit_change = previousFinanceRow ? row.net_profit - previousFinanceRow.net_profit : 0;
    previousFinanceRow = row;
    return row;
  });
  const latestFinance = currentObservation.finance || {};
  const totalCost = toNumber(latestFinance.total_cost);

  const funnelMap = {};
  const deadlineMap = {};
  orders.forEach((order) => {
    const status = normalizeOrderStatus(order.status);
    funnelMap[status] = funnelMap[status] || { order_count: 0, total_quantity: 0 };
    funnelMap[status].order_count += 1;
    funnelMap[status].total_quantity += toNumber(order.quantity);
    if (shouldTrackOrderDeadline(order)) {
      const daysToDeadline = Math.trunc(toNumber(order.delivery_deadline, currentDay)) - Number(currentDay);
      const bucket = daysToDeadline < 0
        ? 'overdue'
        : daysToDeadline === 0
          ? 'due_today'
          : daysToDeadline <= 2
            ? 'due_in_1_2_days'
            : daysToDeadline <= 5
              ? 'due_in_3_5_days'
              : 'due_after_5_days';
      const key = `${status}::${bucket}`;
      deadlineMap[key] = deadlineMap[key] || { order_count: 0, total_quantity: 0 };
      deadlineMap[key].order_count += 1;
      deadlineMap[key].total_quantity += toNumber(order.quantity);
    }
  });

  return {
    order_fulfillment_coverage: {
      title: 'Order Fulfillment Coverage',
      latest_day_only: true,
      current_day: currentDay,
      rows: fulfillmentRows,
    },
    raw_material_coverage: {
      title: 'Raw-Material and Arrival Coverage',
      latest_day_only: true,
      current_day: currentDay,
      rows: rawMaterialRows,
    },
    warehouse_capacity_pressure: {
      title: 'Warehouse Capacity Pressure',
      latest_day_only: true,
      current_day: currentDay,
      rows: warehouseRows,
    },
    cash_pressure_structure: {
      title: 'Cash-Pressure Structure',
      latest_day_only: false,
      historical_trend: historicalTrend,
      current_state: historicalTrend[historicalTrend.length - 1] || {},
      cost_structure: totalCost ? [{ category: 'total_cost', amount: totalCost }] : [],
    },
    demand_capacity_gap: {
      title: 'Demand–Capacity Gap',
      latest_day_only: true,
      current_day: currentDay,
      rows: demandCapacityRows,
    },
    order_funnel_and_aging: {
      title: 'Order Funnel and Deadline Structure',
      latest_day_only: true,
      current_day: currentDay,
      funnel: Object.entries(funnelMap).sort(([a], [b]) => a.localeCompare(b)).map(([status, metrics]) => ({
        status,
        status_label: getOrderStatusLabel(status),
        ...metrics,
      })),
      commitment_deadline_distribution: Object.entries(deadlineMap).sort(([a], [b]) => a.localeCompare(b)).map(([key, metrics]) => {
        const [status, deadlineBucket] = key.split('::');
        return {
          status,
          status_label: getOrderStatusLabel(status),
          deadline_bucket: deadlineBucket,
          ...metrics,
        };
      }),
      orders_summary: buildOrdersSummary(orders, currentObservation.sales?.orders_summary),
    },
  };
};

const normalizeAxisStyle = (axis) => ({
  ...axis,
  axisLabel: {
    fontSize: 13,
    color: '#40536b',
    ...(axis?.axisLabel || {}),
  },
  nameTextStyle: {
    fontSize: 13,
    fontWeight: 800,
    color: '#52657f',
    ...(axis?.nameTextStyle || {}),
  },
});

const normalizeGrid = (grid) => {
  if (!grid) {
    return { left: '5%', right: '4%', top: 48, bottom: '7%', containLabel: true };
  }
  if (Array.isArray(grid)) {
    return grid.map((item, index) => ({
      ...item,
      top: index === 0 ? 58 : item.top,
      height: index === 0 && item.height ? item.height : item.height,
      containLabel: true,
    }));
  }
  return {
    ...grid,
    top: typeof grid.top === 'number' ? Math.min(grid.top, 50) : grid.top,
    left: grid.left || '5%',
    right: grid.right || '4%',
    bottom: grid.bottom || '7%',
    containLabel: true,
  };
};

const normalizeSeriesStyle = (series) => {
  if (!series || typeof series !== 'object') {
    return series;
  }
  if (series.type === 'line') {
    return {
      ...series,
      symbolSize: series.symbolSize || 7,
      lineStyle: {
        width: 2.8,
        ...(series.lineStyle || {}),
      },
      emphasis: {
        focus: 'series',
        ...(series.emphasis || {}),
      },
    };
  }
  if (series.type === 'bar') {
    return {
      ...series,
      barMaxWidth: Math.max(Number(series.barMaxWidth || 0), 28),
      emphasis: {
        focus: 'series',
        ...(series.emphasis || {}),
      },
    };
  }
  if (series.type === 'pie') {
    return {
      ...series,
      radius: ['30%', '54%'],
      label: {
        fontSize: 12,
        fontWeight: 700,
        ...(series.label || {}),
      },
    };
  }
  return series;
};

const enhanceChartOption = (option) => ({
  ...option,
  title: option.title
    ? { ...option.title, show: false }
    : option.title,
  textStyle: {
    fontSize: 13,
    color: '#334155',
    ...(option.textStyle || {}),
  },
  tooltip: {
    ...(option.tooltip || {}),
    textStyle: {
      fontSize: 13,
      ...(option.tooltip?.textStyle || {}),
    },
  },
  legend: option.legend
    ? {
      ...option.legend,
      top: 4,
      itemWidth: 18,
      itemHeight: 10,
      textStyle: {
        fontSize: 13,
        fontWeight: 700,
        color: '#40536b',
        ...(option.legend.textStyle || {}),
      },
    }
    : option.legend,
  grid: normalizeGrid(option.grid),
  xAxis: Array.isArray(option.xAxis)
    ? option.xAxis.map(normalizeAxisStyle)
    : normalizeAxisStyle(option.xAxis || {}),
  yAxis: Array.isArray(option.yAxis)
    ? option.yAxis.map(normalizeAxisStyle)
    : normalizeAxisStyle(option.yAxis || {}),
  series: Array.isArray(option.series)
    ? option.series.map(normalizeSeriesStyle)
    : option.series,
});

const buildFulfillmentOption = (charts, currentDay) => {
  const fulfillmentData = charts?.order_fulfillment_coverage || {};
  const rows = fulfillmentData.rows || [];
  const days = uniqueSortedNumbers(rows.map((row) => row.future_day));
  const byProduct = {};
  rows.forEach((row) => {
    const productId = row.product_id || 'UNKNOWN';
    byProduct[productId] = byProduct[productId] || {};
    byProduct[productId][row.future_day] = row;
  });

  return {
    title: { text: fulfillmentData.title || 'Order Fulfillment Coverage over the Next 3/5/7 Turns', textStyle: { fontSize: 13 } },
    tooltip: { trigger: 'axis' },
    legend: { type: 'scroll', top: 28, textStyle: { fontSize: 11 } },
    grid: { left: '4%', right: '4%', bottom: '5%', top: 72, containLabel: true },
    xAxis: { type: 'category', data: days.map((day) => buildForecastLabel(currentDay, day)) },
    yAxis: [
      { type: 'value', name: 'Cumulative Supply / Demand' },
      { type: 'value', name: 'Gap' },
    ],
    series: Object.keys(byProduct).flatMap((productId) => {
      const rowMap = byProduct[productId];
      return [
        {
          name: `${productId} Supply`,
          type: 'line',
          smooth: true,
          data: days.map((day) => rowMap[day]?.cumulative_supply || 0),
        },
        {
          name: `${productId} Demand`,
          type: 'line',
          smooth: true,
          lineStyle: { type: 'dashed' },
          data: days.map((day) => rowMap[day]?.cumulative_demand || 0),
        },
        {
          name: `${productId} Gap`,
          type: 'bar',
          yAxisIndex: 1,
          barMaxWidth: 18,
          data: days.map((day) => rowMap[day]?.coverage_gap || 0),
          itemStyle: { color: 'rgba(245, 34, 45, 0.35)' },
        },
      ];
    }),
  };
};

const buildRawMaterialOption = (charts, currentDay) => {
  const rawMaterialData = charts?.raw_material_coverage || {};
  const rows = rawMaterialData.rows || [];
  const days = uniqueSortedNumbers(rows.map((row) => row.future_day));
  const byMaterial = {};
  rows.forEach((row) => {
    const materialId = row.material_id || 'UNKNOWN';
    byMaterial[materialId] = byMaterial[materialId] || {};
    byMaterial[materialId][row.future_day] = row;
  });

  return {
    title: { text: rawMaterialData.title || 'Raw-Material and Arrival Coverage', textStyle: { fontSize: 13 } },
    tooltip: { trigger: 'axis' },
    legend: { type: 'scroll', top: 28, textStyle: { fontSize: 11 } },
    grid: { left: '4%', right: '4%', bottom: '5%', top: 72, containLabel: true },
    xAxis: { type: 'category', data: days.map((day) => buildForecastLabel(currentDay, day)) },
    yAxis: { type: 'value', name: 'Inventory / Flow' },
    series: Object.keys(byMaterial).flatMap((materialId) => {
      const rowMap = byMaterial[materialId];
      return [
        {
          name: `${materialId} Inventory`,
          type: 'line',
          smooth: true,
          data: days.map((day) => rowMap[day]?.projected_stock || 0),
        },
        {
          name: `${materialId} Delivery`,
          type: 'bar',
          stack: `${materialId}_flow`,
          barMaxWidth: 16,
          data: days.map((day) => rowMap[day]?.inbound_arrival || 0),
        },
        {
          name: `${materialId} Consumption`,
          type: 'bar',
          stack: `${materialId}_flow`,
          barMaxWidth: 16,
          data: days.map((day) => -(rowMap[day]?.planned_consumption || 0)),
        },
        {
          name: `${materialId} Latent Order Demand`,
          type: 'bar',
          stack: `${materialId}_flow`,
          barMaxWidth: 16,
          data: days.map((day) => -(rowMap[day]?.latent_order_material_demand || 0)),
          itemStyle: { color: 'rgba(250, 173, 20, 0.65)' },
        },
      ];
    }),
  };
};

const buildWarehouseOption = (charts, currentDay) => {
  const warehouseData = charts?.warehouse_capacity_pressure || {};
  const rows = warehouseData.rows || [];
  return {
    title: { text: warehouseData.title || 'Warehouse Capacity Pressure', textStyle: { fontSize: 13 } },
    tooltip: { trigger: 'axis' },
    legend: { top: 28, textStyle: { fontSize: 11 }, data: ['Warehouse Capacity', 'Projected Utilization', 'Capacity Gap'] },
    grid: { left: '4%', right: '4%', bottom: '5%', top: 72, containLabel: true },
    xAxis: { type: 'category', data: rows.map((row) => buildForecastLabel(currentDay, row.future_day)) },
    yAxis: [
      { type: 'value', name: 'Capacity' },
      { type: 'value', name: 'Gap' },
    ],
    series: [
      {
        name: 'Warehouse Capacity',
        type: 'line',
        smooth: true,
        lineStyle: { type: 'dashed' },
        data: rows.map((row) => row.warehouse_capacity || 0),
      },
      {
        name: 'Projected Utilization',
        type: 'line',
        smooth: true,
        data: rows.map((row) => row.projected_used_capacity || 0),
      },
      {
        name: 'Capacity Gap',
        type: 'bar',
        yAxisIndex: 1,
        barMaxWidth: 20,
        data: rows.map((row) => row.capacity_gap || 0),
        itemStyle: {
          color: (params) => (params.value > 0 ? '#52c41a' : '#ff4d4f'),
        },
      },
    ],
  };
};

const buildCashPressureOption = (charts) => {
  const cashPressureData = charts?.cash_pressure_structure || {};
  const trend = cashPressureData.historical_trend || [];
  return {
    title: {
      text: cashPressureData.title || 'Cash-Pressure Structure',
      subtext: `Cash ${formatMillionValue(cashPressureData.current_state?.cash)} | Net profit ${formatMillionValue(cashPressureData.current_state?.net_profit)}`,
      textStyle: { fontSize: 13 },
      subtextStyle: { fontSize: 11 },
    },
    tooltip: { trigger: 'axis' },
    legend: { top: 46, textStyle: { fontSize: 11 }, data: ['Cash', 'Revenue', 'Cost', 'Net Profit'] },
    grid: { left: '6%', right: '44%', top: 96, bottom: '10%' },
    xAxis: { type: 'category', data: trend.map((item) => dayLabel(item.day)) },
    yAxis: { type: 'value', name: 'Millions' },
    series: [
      {
        name: 'Cash',
        type: 'line',
        smooth: true,
        data: trend.map((item) => Number(item.cash || 0) / 1000000),
      },
      {
        name: 'Revenue',
        type: 'line',
        smooth: true,
        data: trend.map((item) => Number(item.revenue || 0) / 1000000),
      },
      {
        name: 'Cost',
        type: 'line',
        smooth: true,
        data: trend.map((item) => Number(item.total_cost_proxy || 0) / 1000000),
      },
      {
        name: 'Net Profit',
        type: 'line',
        smooth: true,
        data: trend.map((item) => Number(item.net_profit || 0) / 1000000),
      },
      {
        name: 'Cost Structure',
        type: 'pie',
        radius: ['22%', '40%'],
        center: ['80%', '58%'],
        tooltip: {
          trigger: 'item',
          formatter: (params) => `${params.name}<br/>${formatMillionValue(params.value * 1000000)}`,
        },
        label: { formatter: '{b}\n{d}%' },
        data: (cashPressureData.cost_structure || []).map((item) => ({
          name: item.category,
          value: Number(item.amount || 0) / 1000000,
        })),
      },
    ],
  };
};

const buildDemandCapacityOption = (charts, currentDay) => {
  const demandData = charts?.demand_capacity_gap || {};
  const rows = demandData.rows || [];
  const days = uniqueSortedNumbers(rows.map((row) => row.future_day));
  const byProduct = {};
  rows.forEach((row) => {
    const productId = row.product_id || 'UNKNOWN';
    byProduct[productId] = byProduct[productId] || {};
    byProduct[productId][row.future_day] = row;
  });

  return {
    title: { text: demandData.title || 'Demand–Capacity Gap', textStyle: { fontSize: 13 } },
    tooltip: { trigger: 'axis' },
    legend: { type: 'scroll', top: 28, textStyle: { fontSize: 11 } },
    grid: { left: '4%', right: '4%', bottom: '5%', top: 72, containLabel: true },
    xAxis: { type: 'category', data: days.map((day) => buildForecastLabel(currentDay, day)) },
    yAxis: [
      { type: 'value', name: 'Day output' },
      { type: 'value', name: 'Cumulative shortfall' },
    ],
    series: Object.keys(byProduct).flatMap((productId) => {
      const rowMap = byProduct[productId];
      return [
        {
          name: `${productId} Demand`,
          type: 'bar',
          stack: `${productId}_req`,
          data: days.map((day) => rowMap[day]?.required_output || 0),
        },
        {
          name: `${productId} Planned`,
          type: 'bar',
          stack: `${productId}_supply`,
          data: days.map((day) => rowMap[day]?.planned_output || 0),
        },
        {
          name: `${productId} Available Capacity`,
          type: 'line',
          smooth: true,
          data: days.map((day) => rowMap[day]?.free_output_equiv || 0),
        },
        {
          name: `${productId} Cumulative shortfall`,
          type: 'line',
          smooth: true,
          yAxisIndex: 1,
          data: days.map((day) => rowMap[day]?.optional_cumulative_gap || 0),
        },
      ];
    }),
  };
};

const buildOrderFunnelOption = (charts) => {
  const funnelData = charts?.order_funnel_and_aging || {};
  const funnelRows = funnelData.funnel || [];
  const deadlineRows = funnelData.commitment_deadline_distribution || [];
  const deadlineBuckets = ['overdue', 'due_today', 'due_in_1_2_days', 'due_in_3_5_days', 'due_after_5_days'];
  const bucketLabels = {
    overdue: 'Overdue',
    due_today: 'Due on day',
    due_in_1_2_days: 'Term expires on 1-2 rounds',
    due_in_3_5_days: 'Term expires on 3-5 rounds',
    due_after_5_days: 'Term expires after 5 rounds',
  };
  const rowStatusLabel = (row) => row?.status_label || getOrderStatusLabel(row?.status);
  const statuses = Array.from(new Set(deadlineRows.map(rowStatusLabel)));
  const bucketMatrix = {};
  deadlineRows.forEach((row) => {
    const statusLabel = rowStatusLabel(row);
    bucketMatrix[statusLabel] = bucketMatrix[statusLabel] || {};
    bucketMatrix[statusLabel][row.deadline_bucket] = row.order_count;
  });

  return {
    title: {
      text: funnelData.title || 'Order Funnel and Backlog Aging',
      subtext: `Total orders ${funnelData.orders_summary?.total || 0}`,
      textStyle: { fontSize: 13 },
      subtextStyle: { fontSize: 11 },
    },
    tooltip: { trigger: 'axis' },
    legend: { type: 'scroll', top: 46, textStyle: { fontSize: 11 }, data: ['Order Count', ...statuses] },
    grid: [
      { left: '8%', right: '4%', top: 92, height: '26%' },
      { left: '8%', right: '4%', top: '58%', height: '24%' },
    ],
    xAxis: [
      { type: 'category', gridIndex: 0, data: funnelRows.map((row) => rowStatusLabel(row)) },
      { type: 'category', gridIndex: 1, data: deadlineBuckets.map((bucket) => bucketLabels[bucket]) },
    ],
    yAxis: [
      { type: 'value', gridIndex: 0, name: 'Order Count' },
      { type: 'value', gridIndex: 1, name: 'Open Orders' },
    ],
    series: [
      {
        name: 'Order Count',
        type: 'bar',
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: funnelRows.map((row) => row.order_count || 0),
        itemStyle: { color: '#1890ff' },
      },
      ...statuses.map((status) => ({
        name: status,
        type: 'bar',
        stack: 'deadline',
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: deadlineBuckets.map((bucket) => bucketMatrix[status]?.[bucket] || 0),
      })),
    ],
  };
};

const buildFinancialTrendOption = (charts) => {
  const data = charts?.financial_trend || {};
  return {
    title: { text: data.title || 'Financial Indicator Trends' },
    tooltip: { trigger: 'axis' },
    legend: { top: 30, data: ['Cash', 'Revenue', 'Profit'] },
    grid: { left: '4%', right: '4%', bottom: '5%', top: 74, containLabel: true },
    xAxis: { type: 'category', data: data.days || [] },
    yAxis: { type: 'value', name: data.unit || 'Millions' },
    series: [
      { name: 'Cash', data: data.series?.cash || [], type: 'line', smooth: true, itemStyle: { color: '#1890ff' } },
      { name: 'Revenue', data: data.series?.revenue || [], type: 'line', smooth: true, itemStyle: { color: '#52c41a' } },
      { name: 'Profit', data: data.series?.profit || [], type: 'line', smooth: true, itemStyle: { color: '#faad14' } },
    ],
  };
};

const buildProductionSalesTrendOption = (charts) => {
  const data = charts?.production_sales_trend || {};
  return {
    title: { text: data.title || 'Production and Sales Trends' },
    tooltip: { trigger: 'axis' },
    legend: { top: 30, data: ['Production', 'Sales Revenue'] },
    grid: { left: '4%', right: '5%', bottom: '5%', top: 74, containLabel: true },
    xAxis: { type: 'category', data: data.days || [] },
    yAxis: [
      { type: 'value', name: 'Production' },
      { type: 'value', name: 'Sales Revenue (Millions)' },
    ],
    series: [
      { name: 'Production', data: data.series?.production || [], type: 'bar', yAxisIndex: 0, itemStyle: { color: '#722ed1' } },
      { name: 'Sales Revenue', data: data.series?.sales_revenue_million || [], type: 'line', smooth: true, yAxisIndex: 1, itemStyle: { color: '#eb2f96' } },
    ],
  };
};

const buildCapacityOption = (charts) => {
  const data = charts?.capacity_utilization_analysis || {};
  return {
    title: { text: data.title || 'Capacity and Utilization Analysis' },
    tooltip: { trigger: 'axis' },
    legend: { top: 30, data: ['Total Capacity', 'Occupied Capacity', 'Available Capacity', 'Capacity Utilization'] },
    grid: { left: '4%', right: '5%', bottom: '5%', top: 74, containLabel: true },
    xAxis: { type: 'category', data: data.days || [] },
    yAxis: [
      { type: 'value', name: 'Capacity' },
      { type: 'value', name: 'Utilization rate (%)', max: 100 },
    ],
    series: [
      { name: 'Total Capacity', data: data.series?.total_capacity || [], type: 'bar', yAxisIndex: 0, itemStyle: { color: '#52c41a' } },
      { name: 'Occupied Capacity', data: data.series?.occupied_capacity || [], type: 'bar', yAxisIndex: 0, itemStyle: { color: '#1890ff' } },
      { name: 'Available Capacity', data: data.series?.available_capacity || [], type: 'bar', yAxisIndex: 0, itemStyle: { color: '#13c2c2' } },
      {
        name: 'Capacity Utilization',
        data: data.series?.capacity_utilization_percent || [],
        type: 'line',
        smooth: true,
        yAxisIndex: 1,
        itemStyle: { color: '#faad14' },
        areaStyle: { opacity: 0.1 },
      },
    ],
  };
};

const buildEfficiencyOption = (charts) => {
  const data = charts?.production_efficiency_analysis || {};
  return {
    title: { text: data.title || 'Production Efficiency and Output Analysis' },
    tooltip: { trigger: 'axis' },
    legend: { top: 30, data: ['Production Efficiency', 'Total Output', 'Planned Output'] },
    grid: { left: '4%', right: '5%', bottom: '5%', top: 74, containLabel: true },
    xAxis: { type: 'category', data: data.days || [] },
    yAxis: [
      { type: 'value', name: 'Efficiency (%)', max: 100 },
      { type: 'value', name: 'Production' },
    ],
    series: [
      { name: 'Production Efficiency', data: data.series?.production_efficiency_percent || [], type: 'line', smooth: true, yAxisIndex: 0, itemStyle: { color: '#fa8c16' } },
      { name: 'Total Output', data: data.series?.total_production || [], type: 'bar', yAxisIndex: 1, itemStyle: { color: '#722ed1' } },
      { name: 'Planned Output', data: data.series?.total_planned || [], type: 'bar', yAxisIndex: 1, itemStyle: { color: '#eb2f96' } },
    ],
  };
};

const buildInventoryQuantityOption = (charts) => {
  const data = charts?.inventory_quantity_change || {};
  const items = data.items || {};
  const inventoryColors = {
    raw_material: '#52c41a',
    material: '#52c41a',
    product: '#1890ff',
  };
  const legend = [];
  const series = Object.keys(items).map((itemId) => {
    const item = items[itemId] || {};
    const type = String(item.item_type || '').toLowerCase();
    const label = type.includes('material') ? `Raw Material ${itemId}` : `Product ${itemId}`;
    legend.push(label);
    return {
      name: label,
      type: 'line',
      smooth: true,
      data: item.data || [],
      itemStyle: { color: inventoryColors[type] || '#13c2c2' },
    };
  });

  return {
    title: { text: data.title || 'Inventory Quantity Changes' },
    tooltip: { trigger: 'axis' },
    legend: { type: 'scroll', top: 30, data: legend },
    grid: { left: '4%', right: '4%', bottom: '5%', top: 76, containLabel: true },
    xAxis: { type: 'category', data: data.days || [] },
    yAxis: { type: 'value', name: 'Quantity' },
    series,
  };
};

const buildOrderTrackingOption = (charts, availableDays) => {
  const data = charts?.order_tracking || {};
  const orders = data.orders || [];
  const orderColors = {
    created: '#1890ff',
    accepted: '#52c41a',
    available: '#13c2c2',
    completed: '#faad14',
    rejected: '#ff4d4f',
    in_progress: '#722ed1',
  };
  const orderSeries = [];
  orders.forEach((order, index) => {
    const tasks = [];
    if (order.accepted_time !== null && order.accepted_time !== undefined) {
      tasks.push({ name: 'Created to Accepted', value: [order.created_time, order.accepted_time] });
    } else {
      tasks.push({ name: 'Pending', value: [order.created_time, Number(order.created_time || 0) + 1] });
    }

    if (order.accepted_time !== null && order.accepted_time !== undefined && order.delivered_time !== null && order.delivered_time !== undefined) {
      tasks.push({ name: 'Accepted to Delivered', value: [order.accepted_time, order.delivered_time] });
    } else if (order.accepted_time !== null && order.accepted_time !== undefined) {
      tasks.push({ name: 'Pending Delivery', value: [order.accepted_time, Number(order.accepted_time || 0) + 1] });
    }

    orderSeries.push({
      name: order.order_id,
      type: 'custom',
      renderItem: (params, api) => {
        const categoryIndex = api.value(0);
        const start = api.coord([api.value(1), categoryIndex]);
        const end = api.coord([api.value(2), categoryIndex]);
        const height = api.size([0, 1])[1] * 0.72;
        const rectShape = echarts.graphic.clipRectByRect({
          x: start[0],
          y: start[1] - height / 2,
          width: Math.max(end[0] - start[0], 2),
          height,
        }, {
          x: params.coordSys.x,
          y: params.coordSys.y,
          width: params.coordSys.width,
          height: params.coordSys.height,
        });
        return rectShape && {
          type: 'rect',
          shape: rectShape,
          style: api.style(),
        };
      },
      itemStyle: { color: orderColors[order.status] || '#8c8c8c' },
      encode: { x: [1, 2], y: 0 },
      data: tasks.map((task) => [index, task.value[0], task.value[1], task.name]),
    });
  });
  const orderMaxDay = Math.max(
    Number(data.latest_day || 0),
    ...orders.flatMap((order) => [order.created_time, order.accepted_time, order.delivered_time]
      .filter((value) => value !== null && value !== undefined)
      .map(Number))
  );

  return {
    title: { text: data.title || 'Order Tracking' },
    tooltip: {
      formatter: (params) => {
        const row = orders[params.data?.[0]];
        if (!row) {
          return params.seriesName;
        }
        return [
          `Order: ${row.order_id}`,
          `Status: ${row.status}`,
          `Product: ${row.product_id || '-'}`,
          `Quantity: ${row.quantity || 0}`,
          `Amount: ${Number(row.total_amount || 0).toFixed(2)}`,
          `Phase: ${params.data[3]}`,
          `Time: Round ${params.data[1]} - Round ${params.data[2]}`,
        ].join('<br/>');
      },
    },
    grid: { left: '8%', right: '4%', top: 76, bottom: '8%', containLabel: true },
    xAxis: {
      type: 'value',
      name: 'Simulation Turn',
      min: availableDays.length > 0 ? Math.min(...availableDays) : 0,
      max: Number.isFinite(orderMaxDay) ? orderMaxDay : 0,
    },
    yAxis: { type: 'category', data: orders.map((order) => order.order_id) },
    series: orderSeries,
  };
};

const buildActionStatisticsOption = (charts, fallbackRows) => {
  const actionData = charts?.department_action_statistics || {};
  const fallbackDays = fallbackRows.map((row) => row.dayLabel);
  const fallbackData = {};
  const fallbackDetails = {};
  fallbackRows.forEach((row) => {
    const dayKey = String(row.day);
    fallbackData[dayKey] = row.actionCounts || {};
    fallbackDetails[dayKey] = row.actionDetails || {};
  });
  const departments = actionData.departments || DEPARTMENTS;
  const sourceData = { ...(actionData.data || {}) };
  Object.entries(fallbackData).forEach(([dayKey, counts]) => {
    sourceData[dayKey] = { ...(sourceData[dayKey] || {}), ...counts };
  });
  const rawData = {};
  Object.keys(sourceData).forEach((dayKey) => {
    rawData[dayKey] = {};
    departments.forEach((dept) => {
      const details = actionData.details?.[dayKey]?.[dept];
      const normalizedDetails = Array.isArray(details)
        ? details.filter((detail) => {
          const rawActionType = detail.actionType || detail.actionName;
          const isLegacyPrewarmSeed = (
            rawActionType === 'Unknown Action'
            && String(detail.message || '').includes('prewarm seed action')
          );
          return !isActionPassAction(isLegacyPrewarmSeed ? 'action_pass' : rawActionType);
        })
        : [];
      const rawFileDetails = fallbackDetails?.[dayKey]?.[dept] || [];
      const chosenDetails = rawFileDetails.length > normalizedDetails.length
        ? rawFileDetails
        : normalizedDetails;
      rawData[dayKey][dept] = Math.max(
        chosenDetails.length,
        toNumber(sourceData?.[dayKey]?.[dept])
      );
    });
  });
  const dayKeys = Object.keys(rawData).sort((a, b) => Number(a) - Number(b));

  return {
    title: { text: actionData.title || 'Department Action Statistics' },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params) => {
        const dayKey = dayKeys[params[0]?.dataIndex];
        let html = `<div style="font-weight: bold; margin-bottom: 5px;">Round ${dayKey}</div>`;
        params.forEach((param) => {
          const dept = departments.find((item) => (DEPARTMENT_LABELS[item] || item) === param.seriesName);
          const chartDetails = (actionData.details?.[dayKey]?.[dept] || []).filter((detail) => {
            const rawActionType = detail.actionType || detail.actionName;
            const isLegacyPrewarmSeed = (
              rawActionType === 'Unknown Action'
              && String(detail.message || '').includes('prewarm seed action')
            );
            return !isActionPassAction(isLegacyPrewarmSeed ? 'action_pass' : rawActionType);
          });
          const rawFileDetails = fallbackDetails?.[dayKey]?.[dept] || [];
          const details = rawFileDetails.length > chartDetails.length
            ? rawFileDetails
            : chartDetails;
          html += `<div style="margin: 5px 0; border-bottom: 1px solid #eee; padding-bottom: 5px;">`;
          html += `<span style="display: inline-block; width: 10px; height: 10px; background: ${param.color}; border-radius: 50%; margin-right: 5px;"></span>`;
          html += `<strong>${param.seriesName}</strong>: ${param.value} actions<br/>`;
          details.forEach((detail) => {
            const statusColor = detail.success ? '#52c41a' : '#ff4d4f';
            const statusIcon = detail.success ? '✓' : '✗';
            const rawActionType = detail.actionType || detail.actionName;
            const isLegacyPrewarmSeed = (
              rawActionType === 'Unknown Action'
              && String(detail.message || '').includes('prewarm seed action')
            );
            const actionLabel = getActionDisplayName(isLegacyPrewarmSeed ? 'action_pass' : rawActionType);
            const message = detail.message === 'single-case prewarm seed action'
              ? ''
              : detail.message;
            html += `<div style="margin-left: 15px; margin-top: 3px; font-size: 12px;">`;
            html += `<span style="color: ${statusColor}; font-weight: bold;">${statusIcon}</span> ${actionLabel}`;
            if (message) {
              html += `<span style="color: #999; margin-left: 5px;">(${message})</span>`;
            }
            html += '</div>';
          });
          html += '</div>';
        });
        return html;
      },
    },
    legend: { type: 'scroll', top: 30, data: departments.map((dept) => DEPARTMENT_LABELS[dept] || dept) },
    grid: { left: '4%', right: '4%', bottom: '5%', top: 82, containLabel: true },
    xAxis: {
      type: 'category',
      data: dayKeys.map((dayKey) => `Round ${dayKey}`),
      name: 'Simulation Turn',
      axisLabel: { rotate: 35 },
    },
    yAxis: { type: 'value', name: 'Number of Actions', minInterval: 1 },
    series: departments.map((dept) => ({
      name: DEPARTMENT_LABELS[dept] || dept,
      type: 'bar',
      data: dayKeys.map((dayKey) => rawData?.[dayKey]?.[dept] || 0),
      itemStyle: { color: DEPARTMENT_COLORS[dept] || '#666' },
    })),
  };
};

const renderActionDetails = (details = []) => {
  const visibleDetails = details.filter((detail) => !isActionPassAction(detail.actionType || detail.actionName));
  if (!visibleDetails.length) {
    return <p className="single-dept-empty">No executable action was recorded for this department in the current turn.</p>;
  }
  return (
    <div className="single-dept-action-list">
      {visibleDetails.map((detail, index) => (
        <article key={`${detail.actionType || 'action'}-${index}`}>
          <b>{getActionDisplayName(detail.actionType || detail.actionName || 'Unknown Action')}</b>
          {detail.message && <span>{detail.message}</span>}
        </article>
      ))}
    </div>
  );
};

const renderProductionDetails = (state) => {
  state = normalizeReviewState(state, 'production');
  const productionLines = state.production_lines || {};
  const lineRows = asArray(productionLines.details);
  const planRows = flattenStatusBucket(state.production_plans);
  const byStatus = productionLines.by_status || {};
  const recipeRows = Object.entries(recipeMaterialsByProduct({ production: state }));
  const recoverySummary = state.recovery_guard?.summary || {};
  const marginSummary = state.margin_guard?.summary || {};
  return (
    <>
      {renderValueList([
        { label: 'Production Lines', value: formatNumber(productionLines.total ?? lineRows.length) },
        { label: 'Total Capacity', value: formatNumber(productionLines.total_capacity ?? state.total_capacity) },
        { label: 'Available Capacity', value: formatNumber(productionLines.available_capacity ?? state.available_capacity) },
        { label: 'Occupied Capacity', value: formatNumber(productionLines.occupied_capacity ?? state.occupied_capacity) },
        { label: 'Capacity Utilization', value: formatNumber(toNumber(state.production_metrics?.capacity_utilization ?? state.capacity_utilization) * 100, '%') },
        { label: 'Cumulative Output', value: formatNumber(state.production_metrics?.total_production) },
        { label: 'Product Types', value: formatNumber((state.products_idList || []).length) },
        { label: 'Recovery Candidates', value: formatNumber(recoverySummary.candidate_count ?? state.recovery_guard?.candidates?.length) },
        { label: 'Capacity-Expansion Candidates', value: formatNumber(marginSummary.capacity_expansion_candidate_count ?? marginSummary.capacity_candidate_count) },
      ])}
      {Object.keys(byStatus).length > 0 && (
        <div className="single-dept-section">
          <h4>Production-Line Summary</h4>
          <div className="single-dept-chip-list">
            {Object.entries(byStatus).map(([status, count]) => (
              <span key={status}>{status}: {formatNumber(count)}</span>
            ))}
          </div>
        </div>
      )}
      <div className="single-dept-section">
        <h4>Production Line Status</h4>
        {lineRows.length ? (
          <div className="single-dept-table compact">
            <span>Production Line</span><span>Status</span><span>Type</span><span>Capacity</span>
            {lineRows.map((line, index) => (
              <React.Fragment key={line.line_id || line.id || index}>
                <strong>{line.line_id || line.id || `Line ${index + 1}`}</strong>
                <em>{line.status || '-'}</em>
                <em>{line.line_type || line.type || '-'}</em>
                <em>{formatNumber(line.capacity || line.daily_capacity)}</em>
              </React.Fragment>
            ))}
          </div>
        ) : <p className="single-dept-empty">No production-line details available.</p>}
      </div>
      <div className="single-dept-section">
        <h4>Production Plans</h4>
        {planRows.length ? (
          <div className="single-dept-table plan">
            <span>Plan</span><span>Product</span><span>Status</span><span>Quantity / Daily Capacity</span><span>Progress</span>
            {planRows.map((plan, index) => {
              const quantity = toNumber(plan.quantity);
              const produced = toNumber(plan.progress?.quantity_produced ?? plan.produced_quantity);
              return (
                <React.Fragment key={plan.plan_id || plan.id || index}>
                  <strong>{getRecordId(plan, ['plan_id', 'production_plan_id'])}</strong>
                  <em>{plan.product_id || '-'}</em>
                  <em>{plan.status || '-'}</em>
                  <em>{formatNumber(quantity)} / {formatNumber(plan.daily_capacity)}</em>
                  <em>{quantity > 0 ? `${formatNumber(produced)} (${formatNumber((produced / quantity) * 100, '%')})` : '-'}</em>
                </React.Fragment>
              );
            })}
          </div>
        ) : <p className="single-dept-empty">No production plans available.</p>}
      </div>
      <div className="single-dept-section">
        <h4>Product Recipes</h4>
        {recipeRows.length ? (
          <div className="single-dept-table compact">
            <span>Product</span><span>Raw Material</span><span>Usage</span><span>Unit</span>
            {recipeRows.flatMap(([productId, materials]) => (
              Object.entries(materials || {}).map(([materialId, amount]) => (
                <React.Fragment key={`${productId}-${materialId}`}>
                  <strong>{productId}</strong>
                  <em>{materialId}</em>
                  <em>{formatNumber(amount)}</em>
                  <em>per product unit</em>
                </React.Fragment>
              ))
            ))}
          </div>
        ) : <p className="single-dept-empty">No recipe details available.</p>}
      </div>
    </>
  );
};

const renderSalesDetails = (state) => {
  state = normalizeReviewState(state, 'sales');
  const orders = flattenStatusBucket(state.sales_orders);
  const markets = asArray(state.markets);
  const proposals = [
    ...asArray(state.proposals_list),
    ...asArray(state.proposal_history),
  ];
  const ordersSummary = state.orders_summary || {};
  const marketsSummary = state.markets_summary || {};
  const backlogBreakdown = state.backlog_breakdown || state.demand_backlog || {};
  return (
    <>
      {renderValueList([
        { label: 'Total Orders', value: formatNumber(state.sales_metrics?.total_orders) },
        { label: 'Accepted Orders', value: formatNumber(state.sales_metrics?.accepted_orders) },
        { label: 'Completed Orders', value: formatNumber(state.sales_metrics?.completed_orders) },
        { label: 'Breached Orders', value: formatNumber(state.sales_metrics?.breached_orders) },
        { label: 'Sales Revenue', value: formatNumber(state.sales_metrics?.total_revenue) },
        { label: 'Order Completion Rate', value: formatNumber(toNumber(state.sales_metrics?.order_fulfillment_rate) * 100, '%') },
        { label: 'Quantity Fill Rate', value: state.review_fill_rate == null ? '-' : formatNumber(state.review_fill_rate * 100, '%') },
        { label: 'Confirmed Backlog', value: formatNumber(state.service_level_summary?.confirmed_order_backlog_quantity ?? state.sales_metrics?.backlog_quantity) },
        { label: 'Overdue Quantity', value: formatNumber(state.service_level_summary?.overdue_confirmed_order_quantity) },
        { label: 'Orders in Summary', value: formatNumber(ordersSummary.total) },
        { label: 'Active Markets', value: formatNumber(marketsSummary.active ?? marketsSummary.active_count) },
        { label: 'Market Coverage', value: formatNumber(toNumber(state.sales_metrics?.market_coverage_rate) * 100, '%') },
        { label: 'Pending Proposals', value: formatNumber(state.pending_proposals_count) },
      ])}
      {state.review_backlog.length > 0 && <div className="single-dept-section">
        <h4>Demand and Backlog by Product</h4>
        {state.review_backlog.map(row=><p key={row.product}>{row.product}: received {formatNumber(row.received_quantity)} · fulfilled {formatNumber(row.fulfilled_quantity)} · backlog {formatNumber(row.backlog_quantity)} · lost {formatNumber(row.lost_sales_quantity)}</p>)}
      </div>}
      <div className="single-dept-section">
        <h4>Sales Orders · {orders.length} records</h4>
        {orders.length ? (
          <div className="single-dept-table orders">
            <span>Order</span><span>Status</span><span>Product</span><span>Quantity</span><span>Amount / Deadline</span>
            {orders.map((order, index) => (
              <React.Fragment key={order.order_id || order.id || index}>
                <strong>{getRecordId(order, ['order_id', 'sales_order_id'])}</strong>
                <em>{getOrderStatusLabel(order.status)}</em>
                <em>{order.product_id || '-'}</em>
                <em>{formatNumber(order.quantity)}</em>
                <em>{formatNumber(order.total_amount)} / {order.delivery_deadline ?? '-'}</em>
              </React.Fragment>
            ))}
          </div>
        ) : <p className="single-dept-empty">No sales orders available.</p>}
      </div>
      <div className="single-dept-section">
        <h4>Market status</h4>
        {markets.length ? (
          <div className="single-dept-table compact">
            <span>Market</span><span>Type</span><span>Status</span><span>Personnel</span>
            {markets.map((market, index) => (
              <React.Fragment key={market.market_id || market.market_name || market.id || index}>
                <strong>{market.market_name || market.market_id || market.name || market.id || `Market ${index + 1}`}</strong>
                <em>{market.market_type || market.type || '-'}</em>
                <em>{market.status || '-'}</em>
                <em>{formatNumber(market.assigned_workers || market.workers || market.staff_count)}</em>
              </React.Fragment>
            ))}
          </div>
        ) : <p className="single-dept-empty">No market details are available.</p>}
      </div>
      <div className="single-dept-section">
        <h4>Sales Proposals</h4>
        {proposals.length ? (
          <div className="single-dept-table orders">
            <span>Proposals</span><span>Status</span><span>Products</span><span>Number</span><span>Amount/Object</span>
            {proposals.map((proposal, index) => (
              <React.Fragment key={proposal.proposal_id || proposal.id || index}>
                <strong>{getRecordId(proposal, ['proposal_id'])}</strong>
                <em>{proposal.status || '-'}</em>
                <em>{proposal.product_id || proposal.material_id || '-'}</em>
                <em>{formatNumber(proposal.quantity)}</em>
                <em>{formatNumber(proposal.total_amount)} / {proposal.buyer_company_id || proposal.sender_id || '-'}</em>
              </React.Fragment>
            ))}
          </div>
        ) : <p className="single-dept-empty">No sales proposals available.</p>}
      </div>
    </>
  );
};

const renderProcurementDetails = (state) => {
  state = normalizeReviewState(state, 'procurement');
  const orders = flattenStatusBucket(state.orders);
  const suppliers = [
    ...asArray(state.suppliers),
    ...asArray(state.suppliers_detail),
  ];
  const operationalSummary = state.operational_summary || {};
  const pendingByMaterial = operationalSummary.pending_by_material || state.replenishment?.pending_by_material || {};
  const inventoryPositions = operationalSummary.inventory_position_by_material || {};
  const recoverySignals = state.recipe_recovery_signal_by_material || {};
  return (
    <>
      {renderValueList([
        { label: 'Procurement Orders', value: formatNumber(state.procurement_metrics?.total_orders) },
        { label: 'Completed Orders', value: formatNumber(state.procurement_metrics?.completed_orders) },
        { label: 'Procurement Cost', value: formatNumber(state.procurement_metrics?.total_cost) },
        { label: 'Purchased Quantity', value: formatNumber(state.procurement_metrics?.total_quantity) },
        { label: 'On-Time Rate', value: formatNumber(toNumber(state.procurement_metrics?.on_time_rate) * 100, '%') },
        { label: 'Purchasable Materials', value: formatNumber((state.purchasable_materials_idList || []).length) },
        { label: 'Materials Pending Receipt', value: formatNumber(Object.keys(pendingByMaterial).length) },
        { label: 'Materials with Recovery Signals', value: formatNumber(Object.keys(recoverySignals).length) },
        { label: 'Pending Proposals', value: formatNumber(state.pending_proposals_count) },
      ])}
      {(Object.keys(pendingByMaterial).length > 0 || Object.keys(inventoryPositions).length > 0) && (
        <div className="single-dept-section">
          <h4>Material Inventory Position</h4>
          <div className="single-dept-table orders">
            <span>Material</span><span>On Hand</span><span>In Transit</span><span>Backlog</span><span>Inventory Position / Pending</span>
            {Array.from(new Set([
              ...Object.keys(pendingByMaterial),
              ...Object.keys(inventoryPositions),
            ])).map((materialId) => {
              const row = inventoryPositions[materialId] || {};
              return (
                <React.Fragment key={materialId}>
                  <strong>{materialId}</strong>
                  <em>{formatNumber(row.on_hand)}</em>
                  <em>{formatNumber(row.incoming)}</em>
                  <em>{formatNumber(row.backlog)}</em>
                  <em>{formatNumber(row.inventory_position)} / {formatNumber(pendingByMaterial[materialId])}</em>
                </React.Fragment>
              );
            })}
          </div>
        </div>
      )}
      <div className="single-dept-section">
        <h4>Procurement Orders</h4>
        {orders.length ? (
          <div className="single-dept-table orders">
            <span>Order</span><span>Status</span><span>Material</span><span>Quantity</span><span>Supplier / Arrival</span>
            {orders.map((order, index) => (
              <React.Fragment key={order.order_id || order.id || index}>
                <strong>{getRecordId(order, ['order_id', 'purchase_order_id', 'procurement_order_id'])}</strong>
                <em>{order.status || '-'}</em>
                <em>{order.material_id || '-'}</em>
                <em>{formatNumber(order.quantity)}</em>
                <em>{order.supplier_name || order.supplier_id || '-'} / {order.arrival_time ?? order.delivery_time ?? '-'}</em>
              </React.Fragment>
            ))}
          </div>
        ) : <p className="single-dept-empty">No procurement orders available.</p>}
      </div>
      <div className="single-dept-section">
        <h4>Suppliers</h4>
        {suppliers.length ? (
          <div className="single-dept-chip-list">
            {suppliers.map((supplier, index) => (
              <span key={supplier.supplier_name || supplier.id || index}>
                {supplier.supplier_name || supplier.name || supplier.id || `Supplier ${index + 1}`}
                {supplier.unit_price ? ` / ${formatNumber(supplier.unit_price)}` : ''}
              </span>
            ))}
          </div>
        ) : <p className="single-dept-empty">No supplier details are available.</p>}
      </div>
    </>
  );
};

const renderInventoryDetails = (state) => {
  state = normalizeReviewState(state, 'inventory');
  const items = (state.inventory_items || []);
  const warehouseCapacity = toNumber(state.warehouse_capacity);
  const usedCapacity = toNumber(state.used_capacity);
  const metrics = state.inventory_metrics || {};
  return (
    <>
      {renderValueList([
        { label: 'Warehouse Capacity', value: formatNumber(warehouseCapacity) },
        { label: 'Used Capacity', value: formatNumber(usedCapacity) },
        { label: 'Warehouse Utilization', value: formatNumber(toNumber(state.warehouse_utilization, warehouseCapacity > 0 ? usedCapacity / warehouseCapacity : 0) * 100, '%') },
        { label: 'Inventory Item Types', value: formatNumber(items.length) },
        { label: 'Inventory Item Units (Unconverted)', value: formatNumber(state.total_inventory_su ?? state.total_inventory) },
        { label: 'Total Inventory Value', value: formatNumber(state.total_value ?? metrics.total_value) },
        { label: 'Operating Costs', value: formatNumber(state.total_operating_cost) },
        { label: 'Maintenance Costs', value: formatNumber(state.total_maintenance_cost) },
        { label: 'Inventory Events', value: formatNumber(state.event_count) },
      ])}
      <div className="single-dept-section">
        <h4>Inventory Details</h4>
        {items.length ? (
          <div className="single-dept-table inventory">
            <span>Item</span><span>Type</span><span>Quantity</span><span>Safety Stock</span><span>Status</span>
            {items.map((item, index) => (
              <React.Fragment key={item.item_id || index}>
                <strong>{item.item_id || '-'}</strong>
                <em>{item.item_type || '-'}</em>
                <em>{formatNumber(item.quantity)}</em>
                <em>{formatNumber(item.safety_stock)}</em>
                <em>{item.is_low_stock || item.is_below_reorder_point ? 'Attention' : 'Normal'}</em>
              </React.Fragment>
            ))}
          </div>
        ) : <p className="single-dept-empty">No inventory details available.</p>}
      </div>
    </>
  );
};

const renderHrDetails = (state) => {
  state = normalizeReviewState(state, 'hr');
  const staffing = Object.entries(state.department_staffing || {});
  const recruitmentStatus = state.recruitment_status || {};
  const history = (recruitmentStatus.recruitment_history || []).slice().reverse();
  return (
    <>
      {renderValueList([
        { label: 'Total Payroll', value: formatNumber(state.total_payroll) },
        { label: 'Total Human-Resource Cost', value: formatNumber(state.total_human_cost) },
        { label: 'Salary Cost', value: formatNumber(state.salary_cost_total) },
        { label: 'Recruitment Cost', value: formatNumber(state.recruit_cost_total) },
        { label: 'Completed Recruitment Batches', value: formatNumber(recruitmentStatus.completed_recruitment_batches) },
        { label: 'Onboarded People', value: formatNumber(recruitmentStatus.recruited_people) },
        { label: 'Pending Onboarding Records', value: formatNumber((recruitmentStatus.pending || []).length) },
        { label: 'Staffing Relaxation', value: state.staffing_relaxation ? 'Enabled' : 'Disabled' },
      ])}
      <div className="single-dept-section">
        <h4>Department Staffing</h4>
        {staffing.length ? (
          <div className="single-dept-table staffing">
            <span>Department</span><span>Employees</span><span>Available</span><span>Allocated</span><span>Pending Recruits</span>
            {staffing.map(([code, row]) => (
              <React.Fragment key={code}>
                <strong>{englishDepartmentLabel(row.label || code)}</strong>
                <em>{formatNumber(row.count)}</em>
                <em>{formatNumber(row.available)}</em>
                <em>{formatNumber(row.allocated)}</em>
                <em>{formatNumber(row.pending_recruits)}</em>
              </React.Fragment>
            ))}
          </div>
        ) : <p className="single-dept-empty">No staffing records available.</p>}
      </div>
      <div className="single-dept-section">
        <h4>Recruitment Records</h4>
        {history.length ? (
          <div className="single-dept-table compact">
            <span>Record</span><span>Department</span><span>People</span><span>Completion Round</span>
            {history.map((record, index) => (
              <React.Fragment key={record.record_id || index}>
                <strong>{record.record_id || `REC ${index + 1}`}</strong>
                <em>{englishDepartmentLabel(record.department || '-')}</em>
                <em>{formatNumber(record.num_people)}</em>
                <em>{record.complete_time ?? '-'}</em>
              </React.Fragment>
            ))}
          </div>
        ) : <p className="single-dept-empty">No recruitment records are available.</p>}
      </div>
    </>
  );
};

const renderFinanceDetails = (state) => (
  <>
    {renderValueList([
      { label: 'Current Cash', value: formatNumber(state.cash_summary?.current_cash ?? state.cash) },
      { label: 'Cash after Warning Buffer', value: formatNumber(state.cash_summary?.available_after_warning_buffer) },
      { label: 'Warning Threshold', value: formatNumber(state.cash_summary?.warning_threshold) },
      { label: 'Critical Threshold', value: formatNumber(state.cash_summary?.critical_threshold) },
      { label: 'Cash Level', value: state.cash_summary?.cash_level || '-' },
      { label: 'Accounts Payable', value: formatNumber(state.cash_summary?.accounts_payable_balance) },
      { label: 'Total Revenue', value: formatNumber(state.total_revenue) },
      { label: 'Total Cost', value: formatNumber(state.total_cost) },
      { label: 'Net Profit', value: formatNumber(state.financial_indicators?.net_profit) },
      { label: 'Gross Profit', value: formatNumber(state.financial_indicators?.gross_profit) },
      { label: 'Net Profit Margin', value: formatNumber(toNumber(state.financial_indicators?.net_profit_rate), '%') },
      { label: 'Gross Profit Margin', value: formatNumber(toNumber(state.financial_indicators?.gross_profit_rate), '%') },
    ])}
  </>
);

const renderDepartmentBusinessDetails = (dept, state) => {
  if (!state || Object.keys(state).length === 0) {
    return <p className="single-dept-empty">No department state was recorded for this turn.</p>;
  }
  if (dept === 'production') {
    return renderProductionDetails(state);
  }
  if (dept === 'sales') {
    return renderSalesDetails(state);
  }
  if (dept === 'procurement') {
    return renderProcurementDetails(state);
  }
  if (dept === 'inventory') {
    return renderInventoryDetails(state);
  }
  if (dept === 'hr') {
    return renderHrDetails(state);
  }
  if (dept === 'finance') {
    return renderFinanceDetails(state);
  }
  return <p className="single-dept-empty">No department details available.</p>;
};

const SingleEnterprisePerformanceView = ({
  availableDays = [],
  currentDay = 0,
  company,
  dataRoot,
  viewMode = 'trend',
  onViewModeChange,
}) => {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError,setLoadError]=useState('');const [reload,setReload]=useState(0);
  const [trendCharts, setTrendCharts] = useState(null);
  const [snapshotCharts, setSnapshotCharts] = useState(null);
  const [selectedDepartment, setSelectedDepartment] = useState('production');

  const financeRef = useRef(null);
  const productionSalesRef = useRef(null);
  const capacityRef = useRef(null);
  const efficiencyRef = useRef(null);
  const inventoryQuantityRef = useRef(null);
  const orderTrackingRef = useRef(null);
  const actionStatisticsRef = useRef(null);
  const fulfillmentRef = useRef(null);
  const rawMaterialRef = useRef(null);
  const warehouseRef = useRef(null);
  const cashPressureRef = useRef(null);
  const demandCapacityRef = useRef(null);
  const orderFunnelRef = useRef(null);
  const chartRefs = useRef([]);

  const daysToLoad = useMemo(
    () => availableDays.filter((day) => day <= currentDay),
    [availableDays, currentDay]
  );

  useEffect(() => {
    let cancelled = false;
    const loadRows = async () => {
      if (!company || daysToLoad.length === 0) {
        setRows([]);
        setLoading(false);
        return;
      }
      setLoading(true);setLoadError('');
      const {source,name}=reviewLocation(dataRoot);
      const trendResult=await getArtifactTrend(source,name,company,{start:daysToLoad[0],end:currentDay,forceRefresh:reload>0});
      if(trendResult.error&&trendResult.status!==404)throw new Error(trendResult.error);
      if(trendResult.data&&!['available','missing','not_recorded','not_published'].includes(trendResult.data.availability))throw new Error('Enterprise trends are invalid or changed. Republish this archive.');
      if(trendResult.data?.availability==='available'){
        const snapshot=await getArtifactReview(source,name,company,currentDay);
        const projectedRows=trendResult.data.rows.map(row=>({...row.series,
          departmentStates:row.round===currentDay?(snapshot.data?.states||{}):{}}));
        if(!cancelled){setRows(projectedRows);setLoading(false);}
        return;
      }
      const nextRows = await Promise.all(daysToLoad.map(async (day) => {
        const [finance, production, sales, inventory, procurement, hr, observation] = await Promise.all([
          safeFetchJson(buildDataUrl(dataRoot, `enterprises/${company}/department/finance/day${day}/finance.json`)),
          safeFetchJson(buildDataUrl(dataRoot, `enterprises/${company}/department/production/day${day}/production.json`)),
          safeFetchJson(buildDataUrl(dataRoot, `enterprises/${company}/department/sales/day${day}/sales.json`)),
          safeFetchJson(buildDataUrl(dataRoot, `enterprises/${company}/department/inventory/day${day}/inventory.json`)),
          safeFetchJson(buildDataUrl(dataRoot, `enterprises/${company}/department/procurement/day${day}/procurement.json`)),
          safeFetchJson(buildDataUrl(dataRoot, `enterprises/${company}/department/hr/day${day}/hr.json`)),
          fetchObservationPayload(dataRoot, company, day),
        ]);

        const actionCounts = {};
        const actionDetails = {};
        const errorCounts = {};
        await Promise.all(DEPARTMENTS.map(async (dept) => {
          const [preAction, action, marketSeedAction, error, marketSeedError, ...retryErrors] = await Promise.all([
            safeFetchJson(buildDataUrl(dataRoot, `enterprises/${company}/department/${dept}/day${day}/pre_${dept}_action.json`)),
            safeFetchJson(buildDataUrl(dataRoot, `enterprises/${company}/department/${dept}/day${day}/${dept}_action.json`)),
            dept === 'sales'
              ? safeFetchJson(buildDataUrl(dataRoot, `enterprises/${company}/department/${dept}/day${day}/sales_market_seed_action.json`))
              : Promise.resolve(null),
            safeFetchJson(buildDataUrl(dataRoot, `enterprises/${company}/department/${dept}/day${day}/${dept}_error.json`)),
            dept === 'sales'
              ? safeFetchJson(buildDataUrl(dataRoot, `enterprises/${company}/department/${dept}/day${day}/sales_market_seed_error.json`))
              : Promise.resolve(null),
            ...ERROR_RETRY_INDICES.map((index) => (
              safeFetchJson(buildDataUrl(dataRoot, `enterprises/${company}/department/${dept}/day${day}/${dept}_error_${index}.json`))
            )),
          ]);
          const rawActions = [
            ...normalizeActions(preAction),
            ...normalizeActions(action),
            ...normalizeActions(marketSeedAction),
          ].filter((item) => !isActionPassEntry(item));
          actionCounts[dept] = rawActions.length;
          actionDetails[dept] = rawActions.map((item) => ({
            actionType: getActionNameFromEntry(item) || 'Unknown Action',
            success: null,
            message: item.action_reason || item.decision_source || 'Read from Action File',
          }));
          errorCounts[dept] = (
            countErrorPayload(error)
            + countErrorPayload(marketSeedError)
            + retryErrors.reduce((sum, retryError) => sum + countErrorPayload(retryError), 0)
          );
        }));

        const financeState = mergeDepartmentState(finance, observation, 'finance');
        const productionState = mergeDepartmentState(production, observation, 'production');
        const salesState = mergeDepartmentState(sales, observation, 'sales');
        const inventoryState = mergeDepartmentState(inventory, observation, 'inventory');
        const procurementState = mergeDepartmentState(procurement, observation, 'procurement');
        const hrState = mergeDepartmentState(hr, observation, 'hr');
        const inventoryItems = inventoryState.inventory_items || [];
        const warehouseCapacity = toNumber(inventoryState.warehouse_capacity);
        const usedCapacity = toNumber(inventoryState.used_capacity);

        return {
          day,
          dayLabel: `Round ${day}`,
          cash: toNumber(financeState.cash, null),
          revenue: toNumber(financeState.total_revenue, null),
          cost: toNumber(financeState.total_cost, null),
          netProfit: toNumber(financeState.financial_indicators?.net_profit, null),
          production: toNumber(productionState.production_metrics?.total_production, null),
          capacityUtilization: toNumber(productionState.production_metrics?.capacity_utilization, null),
          salesRevenue: toNumber(salesState.sales_metrics?.total_revenue, null),
          salesOrders: toNumber(salesState.sales_metrics?.total_orders, null),
          procurementOrders: toNumber(procurementState.procurement_metrics?.total_orders, null),
          inventoryQuantity: inventoryItems.reduce((sum, item) => sum + toNumber(item?.quantity), 0),
          warehouseUtilization: warehouseCapacity > 0 ? usedCapacity / warehouseCapacity : null,
          departmentStates: {
            finance: financeState,
            production: productionState,
            sales: salesState,
            inventory: inventoryState,
            procurement: procurementState,
            hr: hrState,
          },
          actionCounts,
          actionDetails,
          errorCounts,
        };
      }));
      if (!cancelled) {
        setRows(nextRows);
        setLoading(false);
      }
    };
    loadRows().catch(error=>{if(!cancelled){setLoadError(error.message);setLoading(false);}});
    return () => {
      cancelled = true;
    };
  }, [company, dataRoot, currentDay, daysToLoad.join(','),reload]);

  useEffect(() => {
    let cancelled = false;
    const fetchChartPayload = async (candidates) => {
      for (const path of candidates) {
        const payload = await safeFetchJson(buildDataUrl(dataRoot, path));
        if (payload?.charts) {
          return payload.charts;
        }
      }
      return null;
    };
    const loadCharts = async () => {
      if (!company) {
        setTrendCharts(null);
        setSnapshotCharts(null);
        return;
      }
      const latestDay = availableDays.length > 0 ? Math.max(...availableDays) : currentDay;
      const trendCandidates = [
        `enterprises/${company}/records/day${currentDay}/charts_data_export.json`,
        `records/day${currentDay}/charts_data_export.json`,
        `enterprises/${company}/records/day${latestDay}/charts_data_export.json`,
        `records/day${latestDay}/charts_data_export.json`,
        `enterprises/${company}/charts_data_export.json`,
        'charts_data_export.json',
      ];
      const snapshotCandidates = [
        `enterprises/${company}/records/day${currentDay}/charts_data_export.json`,
        `records/day${currentDay}/charts_data_export.json`,
        `enterprises/${company}/charts_data_export.json`,
        'charts_data_export.json',
      ];
      const [nextTrendCharts, nextSnapshotCharts] = await Promise.all([
        fetchChartPayload(trendCandidates),
        fetchChartPayload(snapshotCandidates),
      ]);
      const observationEntries = await Promise.all(daysToLoad.map(async (day) => [
        String(day),
        await fetchObservationPayload(dataRoot, company, day),
      ]));
      const observationsByDay = {};
      observationEntries.forEach(([day, observation]) => {
        if (observation) {
          observationsByDay[day] = observation;
        }
      });
      const rebuiltSnapshotCharts = buildSnapshotChartsFromObservations(
        observationsByDay,
        currentDay
      );
      if (!cancelled) {
        setTrendCharts(nextTrendCharts);
        setSnapshotCharts(rebuiltSnapshotCharts
          ? { ...(nextSnapshotCharts || {}), ...rebuiltSnapshotCharts }
          : nextSnapshotCharts);
      }
    };

    loadCharts();
    return () => {
      cancelled = true;
    };
  }, [company, currentDay, dataRoot, availableDays.join(','), daysToLoad.join(',')]);

  useEffect(() => {
    chartRefs.current.forEach((chart) => chart?.dispose());
    chartRefs.current = [];
    if (loading || rows.length === 0) {
      return undefined;
    }

    const initChart = (ref, option) => {
      if (!ref.current) {
        return null;
      }
      const chart = echarts.init(ref.current);
      setReviewChart(chart, enhanceChartOption(option));
      chartRefs.current.push(chart);
      return chart;
    };

    if (viewMode === 'snapshot') {
      if (snapshotCharts) {
        initChart(fulfillmentRef, buildFulfillmentOption(snapshotCharts, currentDay));
        initChart(rawMaterialRef, buildRawMaterialOption(snapshotCharts, currentDay));
        initChart(warehouseRef, buildWarehouseOption(snapshotCharts, currentDay));
        initChart(cashPressureRef, buildCashPressureOption(snapshotCharts));
        initChart(demandCapacityRef, buildDemandCapacityOption(snapshotCharts, currentDay));
        initChart(orderFunnelRef, buildOrderFunnelOption(snapshotCharts));
      }
    } else {
      if (trendCharts) {
        initChart(financeRef, buildFinancialTrendOption(trendCharts));
        initChart(productionSalesRef, buildProductionSalesTrendOption(trendCharts));
        initChart(capacityRef, buildCapacityOption(trendCharts));
        initChart(efficiencyRef, buildEfficiencyOption(trendCharts));
        initChart(inventoryQuantityRef, buildInventoryQuantityOption(trendCharts));
        initChart(orderTrackingRef, buildOrderTrackingOption(trendCharts, availableDays));
        initChart(actionStatisticsRef, buildActionStatisticsOption(trendCharts, rows));
      } else {
        const days = rows.map((row) => row.dayLabel);
        initChart(financeRef, {
          title: { text: 'Trends in financial indicators' },
          tooltip: { trigger: 'axis' },
          legend: { top: 30 },
          grid: { left: 42, right: 20, top: 74, bottom: 30 },
          xAxis: { type: 'category', data: days },
          yAxis: { type: 'value' },
          series: [
            { name: 'cash', type: 'line', smooth: true, data: rows.map((row) => row.cash) },
            { name: 'Income', type: 'line', smooth: true, data: rows.map((row) => row.revenue) },
            { name: 'Cost', type: 'line', smooth: true, data: rows.map((row) => row.cost) },
            { name: 'Net profit', type: 'line', smooth: true, data: rows.map((row) => row.netProfit) },
          ],
        });
        initChart(actionStatisticsRef, buildActionStatisticsOption(null, rows));
      }
    }

    const handleResize = () => chartRefs.current.forEach((chart) => chart?.resize());
    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      chartRefs.current.forEach((chart) => chart?.dispose());
      chartRefs.current = [];
    };
  }, [rows, loading, trendCharts, snapshotCharts, viewMode, currentDay, availableDays.join(',')]);

  if(loadError)return <section className="view-error" role="alert"><h3>Enterprise review unavailable</h3><p>{loadError}</p><button onClick={()=>setReload(v=>v+1)}>Retry enterprise review</button></section>;
  if (loading) {
    return <LoadingState label="Loading enterprise performance charts…" variant="chart"/>;
  }

  if (!rows.length) {
    return <div className="empty-state">No operating snapshots are available for this selection.</div>;
  }

  const latest = rows[rows.length - 1] || {};
  const activeViewMode = viewMode === 'snapshot' ? 'snapshot' : 'trend';
  const activeDepartment = DEPARTMENTS.includes(selectedDepartment) ? selectedDepartment : 'production';
  const activeDepartmentState = latest.departmentStates?.[activeDepartment] || {};
  const formatMetric = (value, suffix = '') => (
    value === null || value === undefined
      ? '-'
      : `${Number(value).toLocaleString('en-US', { maximumFractionDigits: 2 })}${suffix}`
  );
  const snapshotCards = [
    { label: 'Current Turn', value: `Round ${latest.day ?? currentDay}` },
    { label: 'Cash Balance', value: formatMetric(latest.cash) },
    { label: 'Net Profit', value: formatMetric(latest.netProfit) },
    { label: 'Production Output', value: formatMetric(latest.production) },
    { label: 'Sales Orders', value: formatMetric(latest.salesOrders) },
    { label: 'Procurement Orders', value: formatMetric(latest.procurementOrders) },
    { label: 'Inventory Item Units (Unconverted)', value: formatMetric(latest.inventoryQuantity) },
    {
      label: 'Warehouse Utilization',
      value: latest.warehouseUtilization === null || latest.warehouseUtilization === undefined
        ? '-'
        : formatMetric(latest.warehouseUtilization * 100, '%'),
    },
  ];
  const snapshotOptions = snapshotCharts ? [
    buildFulfillmentOption(snapshotCharts, currentDay),
    buildRawMaterialOption(snapshotCharts, currentDay),
    buildWarehouseOption(snapshotCharts, currentDay),
    buildCashPressureOption(snapshotCharts),
    buildDemandCapacityOption(snapshotCharts, currentDay),
    buildOrderFunnelOption(snapshotCharts),
  ] : [];
  const hasSnapshotCharts = snapshotOptions.some(hasSeriesData);

  return (
    <section className="single-performance-panel">
      <div className="single-performance-header">
        <div>
          <span>Single Enterprise Performance · state snapshots and decision evidence</span>
          <strong>{activeViewMode === 'trend' ? 'End-to-End Trend Analysis' : 'Round-Level Operating State'}</strong>
        </div>
        <div className="single-performance-header-actions">
          <div className="single-performance-toggle">
            <button
              type="button"
              className={activeViewMode === 'trend' ? 'active' : ''}
              onClick={() => onViewModeChange?.('trend')}
            >
              Process Trends
            </button>
            <button
              type="button"
              className={activeViewMode === 'snapshot' ? 'active' : ''}
              onClick={() => onViewModeChange?.('snapshot')}
            >
              Round-Level State
            </button>
          </div>
        </div>
      </div>

      {activeViewMode === 'snapshot' ? (
        <div className="single-performance-snapshot">
          <div className="single-performance-snapshot-grid">
            {snapshotCards.map((card) => (
              <article key={card.label}>
                <span>{card.label}</span>
                <strong>{card.value}</strong>
              </article>
            ))}
          </div>

          <div className="single-performance-dept-workspace">
            <div className="single-performance-dept-tabs">
              {DEPARTMENTS.map((dept) => {
                const actionCount = toNumber(latest.actionCounts?.[dept]);
                const errorCount = toNumber(latest.errorCounts?.[dept]);
                return (
                  <button
                    type="button"
                    className={`single-performance-dept-row ${activeDepartment === dept ? 'active' : ''}`}
                    key={dept}
                    onClick={() => setSelectedDepartment(dept)}
                  >
                    <strong>{DEPARTMENT_LABELS[dept] || dept}</strong>
                    <span>{actionCount} Actions</span>
                    <span className={errorCount > 0 ? 'danger' : ''}>{errorCount} Errors</span>
                    <em className={errorCount > 0 ? 'failed' : 'success'}>
                      {errorCount > 0 ? 'Execution issues' : actionCount > 0 ? 'Actions recorded' : 'No actions recorded'}
                    </em>
                  </button>
                );
              })}
            </div>

            <article className="single-dept-detail-panel">
              <header>
                <div>
                  <span>Details</span>
                  <strong>{DEPARTMENT_LABELS[activeDepartment] || activeDepartment}</strong>
                </div>
                <small>{`Round ${latest.day ?? currentDay}`}</small>
              </header>
              <div className="single-dept-section">
                <h4>Current Round Actions</h4>
                {renderActionDetails(latest.actionDetails?.[activeDepartment] || [])}
              </div>
              <div className="single-dept-section">
                <h4>Operating State</h4>
                {renderDepartmentBusinessDetails(activeDepartment, activeDepartmentState)}
              </div>
            </article>
          </div>

          {hasSnapshotCharts ? (
            <div className="single-performance-grid snapshot-charts">
              <article>
                <h3>Order Fulfillment Coverage over the Next 3/5/7 Turns</h3>
                <div className="single-performance-chart" ref={fulfillmentRef} />
              </article>
              <article>
                <h3>Raw-Material and Arrival Coverage</h3>
                <div className="single-performance-chart" ref={rawMaterialRef} />
              </article>
              <article>
                <h3>Warehouse Capacity Pressure</h3>
                <div className="single-performance-chart" ref={warehouseRef} />
              </article>
              <article className="tall">
                <h3>Cash-Pressure Structure</h3>
                <div className="single-performance-chart tall" ref={cashPressureRef} />
              </article>
              <article>
                <h3>Demand–Capacity Gap</h3>
                <div className="single-performance-chart" ref={demandCapacityRef} />
              </article>
              <article className="tall">
                <h3>Order Funnel and Backlog Aging</h3>
                <div className="single-performance-chart tall" ref={orderFunnelRef} />
              </article>
            </div>
          ) : (
            <article className="single-performance-empty">
              <h3>Round-Level Projection Charts</h3>
              <p>No projection-chart data were recorded for the current turn.</p>
            </article>
          )}
        </div>
      ) : (
        <div className="single-performance-grid trend-charts">
          <article>
            <h3>Financial Indicator Trends</h3>
            <div className="single-performance-chart" ref={financeRef} />
          </article>
          <article>
            <h3>Production and Sales Trends</h3>
            <div className="single-performance-chart" ref={productionSalesRef} />
          </article>
          <article>
            <h3>Capacity and Utilization Analysis</h3>
            <div className="single-performance-chart" ref={capacityRef} />
          </article>
          <article>
            <h3>Production Efficiency and Output Analysis</h3>
            <div className="single-performance-chart" ref={efficiencyRef} />
          </article>
          <article>
            <h3>Inventory Quantity Changes</h3>
            <div className="single-performance-chart" ref={inventoryQuantityRef} />
          </article>
          <article className="tall">
            <h3>Order Tracking</h3>
            <div className="single-performance-chart tall" ref={orderTrackingRef} />
          </article>
          <article>
            <h3>Department Action Statistics</h3>
            <div className="single-performance-chart" ref={actionStatisticsRef} />
          </article>
          {!trendCharts && (
            <article className="single-performance-empty">
              <h3>Trend Chart Data</h3>
              <p>No exported chart bundle was found; the view uses department-state records to construct core financial and operating trends.</p>
            </article>
          )}
        </div>
      )}
    </section>
  );
};

export default SingleEnterprisePerformanceView;
