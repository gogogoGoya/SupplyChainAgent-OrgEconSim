import {setReviewChart} from './utils/chartReview';
import React, { useState, useEffect, useMemo, useRef } from 'react';
import PlatformAccessBadge from './components/PlatformAccessBadge';
import RunEvidencePanel, {RunComparison} from './components/RunEvidencePanel';
import CompanySelector from './components/CompanySelector';
import DaySelector from './components/DaySelector';
import CompanyInfo from './components/CompanyInfo';
import ExchangeInfo from './components/ExchangeInfo';
import CEOAnalysis from './components/CEOAnalysis';
import ExchangeEnterprisePanel from './components/ExchangeEnterprisePanel';
const RunSummary = React.lazy(() => import('./components/RunSummary'));
const FlowProductionView = React.lazy(() => import('./components/FlowProductionView'));
const GlobalSimulationView = React.lazy(() => import('./components/GlobalSimulationView'));
const BullwhipEffectView = React.lazy(() => import('./components/BullwhipEffectView'));
const CobwebModelValidationView = React.lazy(() => import('./components/CobwebModelValidationView'));
const CommonsTragedyView = React.lazy(() => import('./components/CommonsTragedyView'));
const HerdingEffectView = React.lazy(() => import('./components/HerdingEffectView'));
const EvolutionExperimentView = React.lazy(() => import('./components/EvolutionExperimentView'));
const OperationalReviewView = React.lazy(() => import('./components/OperationalReviewView'));
const ExperimentOperationsView = React.lazy(() => import('./components/ExperimentOperationsView'));
const DemandPropagationView = React.lazy(() => import('./components/DemandPropagationView'));
const SingleEnterprisePerformanceView = React.lazy(() => import('./components/SingleEnterprisePerformanceView'));
import SingleEnterpriseActionList from './components/SingleEnterpriseActionList';
import LoadingState from './components/LoadingState';
import ViewerActivity from './components/ViewerActivity';
import DisplayOptions from './components/DisplayOptions';
import ViewBoundary,{ViewLoading} from './components/ViewBoundary';
import LazyDetails from './components/LazyDetails';
import {cancelViewReads,clearDataSourceCache,getDataRequestState} from './utils/dataSource';
import { LATEST_RUN_ID, buildDataUrl, resolveDataRoot, resolveWorkspaceJobRoot, safeFetchJson } from './utils/dataSource';
import { listAllArtifactRuns, listOperationsJobs } from './utils/operationsApi';
import { buildQuantityView } from './utils/productEquivalent';
import { getRunExperimentTag } from './utils/runNaming.mjs';

const formatNumber = (value) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return '-';
  }
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(numeric);
};

const formatCurrency = (value) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return '-';
  }
  return `SCU ${new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 }).format(numeric)}`;
};

const getRunTimeSortValue = (meta) => (
  meta?.finished_at
  || meta?.archived_at
  || meta?.started_at
  || ''
);

const formatDisplayTime = (value) => {
  if (!value) {
    return '-';
  }
  const parsed = new Date(value);
  if (!Number.isNaN(parsed.getTime())) {
    return parsed.toLocaleString('en-US', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    });
  }
  return String(value).replace('T', ' ');
};

const indexActionsByExecutor = (actions = []) => {
  return actions.reduce((acc, action) => {
    const executorId = action?.executor_id;
    if (!executorId) {
      return acc;
    }
    if (!acc[executorId]) {
      acc[executorId] = [];
    }
    acc[executorId].push(action);
    return acc;
  }, {});
};

const getEnterpriseSpecsFromMeta = (runMeta) => (
  runMeta?.scenario_config?.enterprise_specs
  || runMeta?.scenario_config?.enterprise_configs
  || []
);

const SCENARIO_ANALYSIS_PAGES = [
  { id: 'evolution', label: 'Continuous Evolution', description: 'Long-horizon operations under external disturbances' },
  { id: 'bullwhip', label: 'Bullwhip Effect', description: 'Order variability propagating upstream through the supply chain' },
  { id: 'cobweb', label: 'Cobweb Model', description: 'Price dynamics driven by lagged supply responses' },
  { id: 'commons', label: 'Tragedy of the Commons', description: 'Competitive use and depletion of shared resources' },
  { id: 'herding', label: 'Herding Effect', description: 'Peer signals and synchronized capacity expansion' },
];

const RUN_CATEGORY_CONFIGS = [
  ...SCENARIO_ANALYSIS_PAGES,
  { id: 'architecture', label: 'Topology Validation', description: 'Chain, branching, and network supply-chain structures' },
  { id: 'other', label: 'Other Simulations', description: 'Runs without a dedicated scenario analysis view' },
];

const ANALYSIS_SCOPE_CONFIGS = [
  { id: 'multi', label: 'Multi-Enterprise Simulation', description: 'Supply-chain structure, coordination, and economic dynamics' },
  { id: 'single', label: 'Enterprise Capability Simulation', description: 'Standard cases, departmental actions, and capability validation' },
];

const MULTI_ANALYSIS_PAGES = [
  { id: 'daily', label: 'Round View' },
  { id: 'propagation', label: 'Propagation Review' },
  { id: 'global', label: 'Global Review' },
  { id: 'scenario', label: 'Scenario Analysis' },
  { id: 'operations', label: 'Operations Review' },
];

const SINGLE_ANALYSIS_PAGES = [
  { id: 'daily', label: 'Round View' },
];

const SINGLE_CASE_FILTER_CONFIGS = [
  { id: 'all', label: 'All Cases' },
  { id: 'single_case_01_order_selection', label: 'Case 01 Order Selection' },
  { id: 'single_case_02_material_shortage', label: 'Case 02 Materials and Suppliers' },
  { id: 'single_case_03_capacity_bottleneck', label: 'Case 03 Capacity Bottleneck' },
  { id: 'single_case_04_staff_shortage', label: 'Case 04 Staffing Shortage' },
  { id: 'single_case_05_cash_pressure', label: 'Case 05 Cash Pressure' },
];

const MULTI_SCENARIO_FILTER_CONFIGS = [
  { id: 'all', label: 'All Scenarios' },
  ...SCENARIO_ANALYSIS_PAGES,
];

const RUN_CONTROL_MODE_FILTER_CONFIGS = [
  { id: 'all', label: 'All Modes' },
  { id: 'agent', label: 'Agent Skill' },
  { id: 'scripted', label: 'Scripted' },
];

const getScenarioIdFromMeta = (runMeta) => (
  runMeta?.scenario_id
  || runMeta?.scenario_meta?.scenario_id
  || runMeta?.scenario_config?.meta?.scenario_id
  || ''
);

const getMarketModeFromMeta = (runMeta) => (
  runMeta?.market_demand_mode
  || runMeta?.scenario_config?.simulation?.market_demand_mode
  || ''
);

const getSimulationProfileFromMeta = (runMeta) => {
  const scenarioId = String(getScenarioIdFromMeta(runMeta)).toLowerCase();
  return (
    runMeta?.integration_profiles?.simulation?.profile
    || runMeta?.scenario_config?.integration_profiles?.simulation?.profile
    || ((scenarioId.startsWith('single_case_') || scenarioId.startsWith('firm_ablation_fd'))
      ? 'single_enterprise' : 'multi_enterprise')
  );
};

const getAnalysisScopeFromMeta = (runMeta) => (
  getSimulationProfileFromMeta(runMeta) === 'single_enterprise' ? 'single' : 'multi'
);

const isMultiEnterpriseRun = (run) => (
  getAnalysisScopeFromMeta(run?.meta) === 'multi'
);

const pickPreferredRun = (
  runs = [],
  {
    candidateRunId = '',
    keepCandidate = true,
    preferredScope = 'multi',
  } = {}
) => {
  if (keepCandidate && candidateRunId) {
    const candidate = runs.find((run) => run.id === candidateRunId);
    const candidateScopeMatches = (
      !preferredScope
      || preferredScope === 'any'
      || (
        preferredScope === 'multi'
          ? isMultiEnterpriseRun(candidate)
          : getAnalysisScopeFromMeta(candidate?.meta) === preferredScope
      )
    );
    if (candidate && candidateScopeMatches) {
      return candidate;
    }
  }
  const scopedRun = runs.find((run) => (
    preferredScope === 'multi'
      ? isMultiEnterpriseRun(run)
      : getAnalysisScopeFromMeta(run?.meta) === preferredScope
  ));
  return scopedRun || runs[0] || null;
};

const getSingleCaseIdFromMeta = (runMeta) => {
  const scenarioId = String(
    runMeta?.scenario_config?.runtime_injection?.single_enterprise_case_policy?.case_id
    || runMeta?.experiment_design?.source_scenario_id
    || runMeta?.scenario_config?.experiment_design?.source_scenario_id
    || (String(getScenarioIdFromMeta(runMeta)).startsWith('firm_ablation_fd4_')
      ? 'single_case_04_staff_shortage' : getScenarioIdFromMeta(runMeta))
  ).toLowerCase();
  if (!scenarioId.startsWith('single_case_')) {
    return '';
  }
  const baseId = scenarioId.replace(/_scripted$/, '');
  if (baseId === 'single_case_01_market_insufficient') {
    return 'single_case_01_order_selection';
  }
  return baseId;
};

const getRunControlModeFromMeta = (runMeta) => {
  const scenarioId = String(getScenarioIdFromMeta(runMeta)).toLowerCase();
  const scriptedPolicy = (
    runMeta?.scenario_config?.runtime_injection?.scripted_rule_policy
    || runMeta?.runtime_injection?.scripted_rule_policy
    || {}
  );
  const operationsJob = runMeta?.operations_job || runMeta?.job || {};
  const executionMode = String(
    runMeta?.execution_mode
    || operationsJob?.execution_mode
    || operationsJob?.process_execution_mode
    || ''
  ).toLowerCase();
  const tags = [
    ...(Array.isArray(runMeta?.tags) ? runMeta.tags : []),
    ...(Array.isArray(operationsJob?.tags) ? operationsJob.tags : []),
  ].map((tag) => String(tag).toLowerCase());
  if (
    scenarioId.endsWith('_scripted')
    || scriptedPolicy.enabled === true
    || executionMode.includes('scripted')
    || tags.includes('preset:scripted')
  ) {
    return 'scripted';
  }
  return 'agent';
};

const getRunControlModeLabel = (runMeta) => (
  getRunControlModeFromMeta(runMeta) === 'scripted' ? 'Scripted' : 'Agent Skill'
);

const detectScenarioPage = (runMeta) => {
  if (getAnalysisScopeFromMeta(runMeta) !== 'multi') {
    return null;
  }
  const scenarioId = String(getScenarioIdFromMeta(runMeta)).toLowerCase();
  const marketMode = String(getMarketModeFromMeta(runMeta)).toLowerCase();
  const experimentFamily = String(
    runMeta?.scenario_meta?.experiment_family
    || runMeta?.scenario_config?.meta?.experiment_family
    || ''
  ).toLowerCase();
  if (
    experimentFamily === 'long_horizon_evolution'
    || scenarioId === 'long_horizon_evolution'
  ) {
    return 'evolution';
  }
  if (
    experimentFamily === 'architecture_complexity_validation'
    || scenarioId.startsWith('architecture_')
    || runMeta?.scenario_config?.simulation?.architecture_validation_config?.enabled === true
  ) {
    return null;
  }
  if (marketMode === 'cobweb' || scenarioId.includes('cobweb')) {
    return 'cobweb';
  }
  if (marketMode === 'shared_resource_market' || scenarioId.includes('commons')) {
    return 'commons';
  }
  if (marketMode === 'herding_market' || scenarioId.includes('herding')) {
    return 'herding';
  }
  return 'bullwhip';
};

const getScenarioPageConfig = (pageId) => (
  SCENARIO_ANALYSIS_PAGES.find((page) => page.id === pageId) || SCENARIO_ANALYSIS_PAGES[0]
);

const detectRunCategory = (runMeta) => {
  const scenarioPage = detectScenarioPage(runMeta);
  if (scenarioPage) {
    return scenarioPage;
  }
  const scenarioId = String(getScenarioIdFromMeta(runMeta)).toLowerCase();
  const experimentFamily = String(
    runMeta?.scenario_meta?.experiment_family
    || runMeta?.scenario_config?.meta?.experiment_family
    || ''
  ).toLowerCase();
  if (
    experimentFamily === 'architecture_complexity_validation'
    || scenarioId.startsWith('architecture_')
    || runMeta?.scenario_config?.simulation?.architecture_validation_config?.enabled === true
  ) {
    return 'architecture';
  }
  return 'other';
};

const getRunCategoryConfig = (categoryId) => (
  RUN_CATEGORY_CONFIGS.find((category) => category.id === categoryId) || RUN_CATEGORY_CONFIGS[RUN_CATEGORY_CONFIGS.length - 1]
);

const getScenarioCategoryLabel = (runMeta) => getRunCategoryConfig(detectRunCategory(runMeta)).label;

const hasScenarioAnalysisPage = (runMeta) => Boolean(detectScenarioPage(runMeta));

const formatExperimentTag = (tag) => String(tag || '').replace(/_/g, ' ');

const formatRunTimeShort = (run) => {
  const metaTime = run?.meta?.finished_at || run?.meta?.archived_at || run?.meta?.started_at || '';
  if (metaTime) {
    return formatDisplayTime(metaTime);
  }
  const match = String(run?.id || '').match(/^run_(\d{4}-\d{2}-\d{2})_(\d{2})(\d{2})(\d{2})/);
  if (!match) {
    return run?.id || '-';
  }
  return `${match[1]} ${match[2]}:${match[3]}:${match[4]}`;
};

const formatScenarioRunOptionLabel = (run) => {
  const scenarioId = getScenarioIdFromMeta(run?.meta) || 'unknown_scenario';
  const tag = run?.experimentTag ? formatExperimentTag(run.experimentTag) : '';
  const marker = run?.experimentTag ? '★ ' : '';
  return `${marker}${formatRunTimeShort(run)} · ${getRunControlModeLabel(run?.meta)} · ${tag || scenarioId}`;
};

const getRunRecordTimeValue = (run) => (
  run?.meta?.finished_at
  || run?.meta?.archived_at
  || run?.meta?.started_at
  || ''
);

const getRunRecordDateValue = (run) => {
  const value = getRunRecordTimeValue(run);
  if (!value) {
    return '';
  }
  const parsed = new Date(value);
  if (!Number.isNaN(parsed.getTime())) {
    return parsed.toISOString().slice(0, 10);
  }
  return String(value).slice(0, 10);
};

const getRunRecordLastRound = (run) => {
  const meta = run?.meta || {};
  const completed = Number(meta.completed_steps || meta.total_steps || meta.planned_total_steps || 0);
  return Number.isFinite(completed) && completed > 0 ? completed - 1 : 0;
};

const getRunRecordStorageLabel = (run) => (
  run?.dataRoot || run?.meta?.artifact_root || run?.sourceLabel || '-'
);

const buildRunRecordFileLinks = (run) => {
  const root = run?.dataRoot || '';
  const lastRound = getRunRecordLastRound(run);
  if (!root) {
    return [];
  }
  const fileSpecs = [
    ['run_meta', 'run_meta.json'],
    ['Final-turn exchange', `public/exchange/day${lastRound}/exchange.json`],
    ['Round integrity', `round_integrity/day${lastRound}.json`],
    ['Run metrics', `projections/run_metrics/day${lastRound}/run_metrics_projection.json`],
    ['Order lifecycle', `projections/multi_enterprise/day${lastRound}/order_lifecycle_projection.json`],
  ];
  return fileSpecs.map(([label, relativePath]) => ({
    label,
    relativePath,
    url: buildDataUrl(root, relativePath),
  }));
};

const buildRunArchiveUrl = (run) => {
  const dataRoot = String(run?.dataRoot || '');
  if (dataRoot === '/workspace_multi') {
    return '/operations/artifacts/archive?source=workspace_multi';
  }
  const workspacePrefix = '/workspace_jobs/';
  if (dataRoot.startsWith(workspacePrefix)) {
    return `/operations/artifacts/archive?source=workspace_jobs&name=${encodeURIComponent(decodeURIComponent(dataRoot.slice(workspacePrefix.length)))}`;
  }
  const simulationRunsPrefix = '/simulation_runs/';
  if (dataRoot.startsWith(simulationRunsPrefix)) {
    return `/operations/artifacts/archive?source=simulation_runs&name=${encodeURIComponent(decodeURIComponent(dataRoot.slice(simulationRunsPrefix.length)))}`;
  }
  const jobId = run?.meta?.job_id || run?.meta?.operations_job?.job_id || '';
  if (jobId) {
    return `/operations/jobs/${encodeURIComponent(jobId)}/archive`;
  }
  return '';
};

const sanitizeDownloadPart = (value) => (
  String(value || 'run-records').replace(/[^A-Za-z0-9_.=-]+/g, '_').replace(/^_+|_+$/g, '') || 'run-records'
);

const downloadJson = (filename, payload) => {
  const blob = new Blob([JSON.stringify(payload, null, 2)], {
    type: 'application/json;charset=utf-8',
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
};

const downloadUrl = (url) => {
  if (!url) {
    return;
  }
  const link = document.createElement('a');
  link.href = url;
  link.download = '';
  document.body.appendChild(link);
  link.click();
  link.remove();
};

const getWorkspaceNameFromArtifactRoot = (artifactRoot) => {
  const normalized = String(artifactRoot || '')
    .replace(/^file:\/\//, '')
    .replace(/\\/g, '/');
  if (!normalized) {
    return '';
  }
  const marker = '/workspace_jobs/';
  const markerIndex = normalized.lastIndexOf(marker);
  if (markerIndex >= 0) {
    return normalized.slice(markerIndex + marker.length).split('/').filter(Boolean)[0] || '';
  }
  return normalized.split('/').filter(Boolean).pop() || '';
};

const buildRunMetaFromOperationsJob = (job) => ({
  run_id: job?.run_id || job?.job_id || '',
  job_id: job?.job_id || '',
  scenario_id: job?.scenario_id || '',
  scenario_meta: job?.scenario_meta || job?.scenario_config?.meta || {},
  scenario_config: job?.scenario_config || {},
  integration_profiles: job?.integration_profiles || job?.scenario_config?.integration_profiles || {},
  status: job?.status || '',
  planned_total_steps: job?.planned_total_steps,
  completed_steps: job?.completed_steps,
  started_at: job?.started_at,
  finished_at: job?.finished_at,
  archived_at: job?.archived_at,
  artifact_root: job?.artifact_root,
  operations_job: job || {},
  tags: job?.tags || [],
});

const formatRunLabel = (runId, meta, isLatest = false, experimentTag = '') => {
  const scenarioId = meta?.scenario_id || 'unknown_scenario';
  const scenarioCategory = getScenarioCategoryLabel(meta);
  const controlMode = getRunControlModeLabel(meta);
  const finishedAt = meta?.finished_at || meta?.archived_at || meta?.started_at || '';
  const stepLabel = meta?.completed_steps ?? meta?.total_steps;
  const prefix = experimentTag ? `★ ${isLatest ? 'latest' : runId}` : (isLatest ? 'latest' : runId);
  const tagSuffix = experimentTag ? ` · Tag: ${formatExperimentTag(experimentTag)}` : '';
  const timeSuffix = finishedAt ? ` · ${formatDisplayTime(finishedAt)}` : '';
  const stepsSuffix = stepLabel ? ` · ${stepLabel} steps` : '';
  return `${prefix} · [${scenarioCategory}] [${controlMode}] ${scenarioId}${tagSuffix}${stepsSuffix}${timeSuffix}`;
};

const finalizeRunCatalog = (runs = []) => {
  const seenRunKeys = new Set();
  return runs.filter((run) => {
    const baseRunKey = run?.meta?.run_id
      || run?.meta?.job_id
      || run?.resolvedRunId
      || run?.sourceLabel
      || run?.id
      || run?.dataRoot;
    const logicalRunKey = run?.experimentTag
      ? `${baseRunKey || ''}::${run?.sourceLabel || run?.id || run?.dataRoot || run?.experimentTag}`
      : baseRunKey;
    if (!logicalRunKey || seenRunKeys.has(logicalRunKey)) {
      return false;
    }
    seenRunKeys.add(logicalRunKey);
    return true;
  }).sort((left, right) => {
    const leftTime = getRunTimeSortValue(left.meta);
    const rightTime = getRunTimeSortValue(right.meta);
    return String(rightTime).localeCompare(String(leftTime)) || right.id.localeCompare(left.id);
  });
};

const buildRunEntriesFromArtifactIndex = (entries = []) => (
  entries.map((entry) => {
    const meta = {
      ...(entry?.meta || {}),
      _visualization_max_day: entry?.max_day,
      available_rounds: entry?.available_rounds,
      integrity: entry?.integrity,
    };
    const runId = meta.run_id || entry?.resolved_run_id || entry?.name || entry?.id || '';
    const sourceLabel = entry?.source_label || entry?.name || runId;
    const dataRoot = entry?.data_root || '';
    const experimentTag = getRunExperimentTag(sourceLabel, runId, dataRoot);
    const id = entry?.id || (
      entry?.source === 'workspace_jobs'
        ? `workspace_job:${sourceLabel}`
        : entry?.source === 'simulation_runs'
          ? sourceLabel
          : entry?.source === 'workspace_multi'
            ? 'workspace_current'
            : `${entry?.source || 'run'}:${sourceLabel}`
    );
    return {
      id,
      label: formatRunLabel(runId || id, meta, id === 'workspace_current', experimentTag),
      meta,
      dataRoot,
      sourceLabel,
      resolvedRunId: runId,
      experimentTag,
      maxDay: Number.isFinite(Number(entry?.max_day)) ? Number(entry.max_day) : undefined,
    };
  }).filter((run) => run.meta && run.dataRoot)
);

const getEnterpriseId = (spec) => spec?.enterprise_id || spec?.id || '';

const getTopologyEdgesFromSpecs = (enterpriseSpecs = []) => {
  const ids = new Set(enterpriseSpecs.map(getEnterpriseId).filter(Boolean));
  const edges = [];
  enterpriseSpecs.forEach((spec) => {
    const target = getEnterpriseId(spec);
    (spec?.supplier_name_list || []).forEach((source) => {
      if (ids.has(source) && target) {
        edges.push({ source, target });
      }
    });
  });
  if (edges.length > 0) {
    return edges;
  }
  return enterpriseSpecs.slice(0, -1).map((spec, index) => ({
    source: getEnterpriseId(spec),
    target: getEnterpriseId(enterpriseSpecs[index + 1]),
  })).filter((edge) => edge.source && edge.target);
};

const getExchangeEdgeMetrics = (exchangeData) => {
  const metrics = {};
  Object.values(exchangeData?.data?.exchanges || {}).forEach((exchange) => {
    (exchange?.orders?.list || []).forEach((order) => {
      const source = order?.seller_company_id;
      const target = order?.buyer_company_id;
      if (!source || !target) {
        return;
      }
      const key = `${source}->${target}`;
      const quantity = Number(order?.quantity || 0);
      const value = quantity * Number(order?.agreed_price || 0);
      if (!metrics[key]) {
        metrics[key] = { orders: 0, quantity: 0, value: 0, products: new Set() };
      }
      metrics[key].orders += 1;
      metrics[key].quantity += quantity;
      metrics[key].value += value;
      if (order?.product_id) {
        metrics[key].products.add(order.product_id);
      }
    });
  });
  return metrics;
};

const buildTopologyGraph = (enterpriseSpecs = [], currentCompany, companyNameMap, exchangeData) => {
  const tierGroups = new Map();
  enterpriseSpecs.forEach((spec, index) => {
    const enterpriseId = getEnterpriseId(spec);
    if (!enterpriseId) {
      return;
    }
    const tier = Number.isFinite(Number(spec?.tier)) ? Number(spec.tier) : index;
    if (!tierGroups.has(tier)) {
      tierGroups.set(tier, []);
    }
    tierGroups.get(tier).push(spec);
  });

  const tiers = Array.from(tierGroups.keys()).sort((a, b) => a - b);
  const maxRows = Math.max(1, ...Array.from(tierGroups.values()).map((group) => group.length));
  const xGap = tiers.length <= 1 ? 0 : 760 / Math.max(1, tiers.length - 1);
  const yGap = maxRows <= 1 ? 0 : 260 / Math.max(1, maxRows - 1);
  const edgeMetrics = getExchangeEdgeMetrics(exchangeData);

  const nodes = tiers.flatMap((tier, tierIndex) => {
    const group = tierGroups.get(tier) || [];
    const groupHeight = (group.length - 1) * yGap;
    return group.map((spec, rowIndex) => {
      const enterpriseId = getEnterpriseId(spec);
      const isCurrent = currentCompany === enterpriseId;
      return {
        name: enterpriseId,
        x: 90 + tierIndex * xGap,
        y: 80 + (maxRows <= 1 ? 120 : rowIndex * yGap + (260 - groupHeight) / 2),
        symbolSize: isCurrent ? 78 : 62,
        itemStyle: {
          color: isCurrent ? '#1f6feb' : '#8aa0b6',
          borderColor: isCurrent ? '#0b3f88' : '#d7e0ea',
          borderWidth: isCurrent ? 3 : 1,
        },
        label: {
          formatter: companyNameMap[enterpriseId] || enterpriseId,
        },
        tooltip: {
          formatter: [
            `<strong>${companyNameMap[enterpriseId] || enterpriseId}</strong>`,
            `Tier ${tier}`,
            `Purchases: ${(spec?.purchasable_materials_idList || []).join(', ') || '-'}`,
            `Sales: ${(spec?.salable_products_idList || []).join(', ') || '-'}`,
          ].join('<br/>'),
        },
      };
    });
  });

  const links = getTopologyEdgesFromSpecs(enterpriseSpecs).map((edge) => {
    const metric = edgeMetrics[`${edge.source}->${edge.target}`];
    const hasTrade = Boolean(metric?.orders);
    return {
      source: edge.source,
      target: edge.target,
      value: metric?.quantity || 0,
      label: {
        show: true,
        formatter: hasTrade ? `${formatNumber(metric.quantity)} / ${metric.orders} orders` : 'Configured link',
      },
      lineStyle: {
        width: hasTrade ? Math.min(6, 2 + Math.log10(metric.quantity + 1)) : 1.5,
        opacity: hasTrade ? 0.9 : 0.35,
        color: hasTrade ? '#2f80ed' : '#aab6c4',
      },
      tooltip: {
        formatter: hasTrade
          ? `${edge.source} -> ${edge.target}<br/>Orders: ${metric.orders}<br/>Quantity: ${formatNumber(metric.quantity)}<br/>Value: ${formatCurrency(metric.value)}<br/>Products: ${Array.from(metric.products).join(', ')}`
          : `${edge.source} -> ${edge.target}<br/>Configured link; no transaction in this turn`,
      },
    };
  });

  return { nodes, links };
};

const normalizeActionItems = (payload) => {
  if (!Array.isArray(payload)) {
    return [];
  }
  if (payload.length === 1 && Array.isArray(payload[0])) {
    return payload[0];
  }
  return payload.filter((item) => item && typeof item === 'object');
};

const summarizeActionNames = (payload) => (
  normalizeActionItems(payload)
    .map((item) => item?.action?.action_name || (item?.pass_reason ? 'action_pass' : ''))
    .filter(Boolean)
);

const RoundEnterpriseBoard = ({
  enterpriseSpecs,
  currentCompany,
  currentDay,
  dataRoot,
  onCompanyChange,
  companyNameMap,
  includeSingleCaseSeedActions = false,
}) => {
  const [rows, setRows] = useState([]);const [boardLoading,setBoardLoading]=useState(true);const [boardError,setBoardError]=useState('');

  useEffect(() => {
    let cancelled = false;
    const departments = ['finance', 'sales', 'procurement', 'inventory', 'production'];
    const loadRows = async () => {
      setBoardLoading(true);setBoardError('');
      const nextRows = await Promise.all((enterpriseSpecs || []).map(async (spec) => {
        const enterpriseId = getEnterpriseId(spec);
        const departmentData = {};
        await Promise.all(departments.map(async (dept) => {
          departmentData[dept] = await safeFetchJson(
            buildDataUrl(dataRoot, `enterprises/${enterpriseId}/department/${dept}/day${currentDay}/${dept}.json`)
          );
        }));

        const actionNames = [];
        await Promise.all(departments.map(async (dept) => {
          const [preAction, action, marketSeedAction] = await Promise.all([
            safeFetchJson(buildDataUrl(dataRoot, `enterprises/${enterpriseId}/department/${dept}/day${currentDay}/pre_${dept}_action.json`)),
            safeFetchJson(buildDataUrl(dataRoot, `enterprises/${enterpriseId}/department/${dept}/day${currentDay}/${dept}_action.json`)),
            includeSingleCaseSeedActions && dept === 'sales'
              ? safeFetchJson(buildDataUrl(dataRoot, `enterprises/${enterpriseId}/department/${dept}/day${currentDay}/sales_market_seed_action.json`))
              : Promise.resolve(null),
          ]);
          summarizeActionNames(preAction).forEach((name) => actionNames.push(`${dept}:${name}`));
          summarizeActionNames(action).forEach((name) => actionNames.push(`${dept}:${name}`));
          summarizeActionNames(marketSeedAction).forEach((name) => actionNames.push(`${dept}:prewarm_seed:${name}`));
        }));

        const finance = departmentData.finance?.self_state || {};
        const inventory = departmentData.inventory?.self_state || {};
        const production = departmentData.production?.self_state || {};
        const procurement = departmentData.procurement?.self_state || {};
        const sales = departmentData.sales?.self_state || {};
        const inventoryItems = inventory.inventory_items || [];
        const lowStockCount = inventoryItems.filter((item) => item?.is_low_stock || item?.is_below_reorder_point).length;

        return {
          enterpriseId,
          tier: spec?.tier,
          cash: finance.cash,
          netProfit: finance.financial_indicators?.net_profit,
          lowStockCount,
          inventoryKinds: inventoryItems.length,
          productionQuantity: production.production_metrics?.total_production,
          salesOrders: sales.sales_metrics?.total_orders,
          procurementOrders: procurement.procurement_metrics?.total_orders,
          actionNames,
        };
      }));
      if (!cancelled) {
        setRows(nextRows);setBoardLoading(false);
      }
    };
    loadRows().catch(error=>{if(!cancelled){setBoardError(error.message);setBoardLoading(false);}});
    return () => {
      cancelled = true;
    };
  }, [enterpriseSpecs, currentDay, dataRoot]);

  if(boardLoading)return <LoadingState label="Loading round snapshot and enterprise actions…" variant="metrics"/>;
  if(boardError)return <div className="error" role="alert">Round snapshot unavailable: {boardError}</div>;
  if (!rows.length) return <div className="empty-state">No enterprise snapshot is available for this selection.</div>;

  return (
    <section className="round-overview-panel">
      <div className="round-overview-header">
        <div>
          <span>Round Snapshot</span>
          <strong>Enterprise States and Actions for Round {currentDay}</strong>
        </div>
        <small>Select an enterprise card to update the details below. Inventory alerts, output, and action names highlight changes in this turn.</small>
      </div>
      <div className="round-enterprise-grid">
        {rows.map((row) => (
          <button
            type="button"
            key={row.enterpriseId}
            className={`round-enterprise-card ${currentCompany === row.enterpriseId ? 'active' : ''} ${row.lowStockCount > 0 ? 'warning' : ''}`}
            onClick={() => onCompanyChange(row.enterpriseId)}
          >
            <div className="round-enterprise-head">
              <span>{companyNameMap[row.enterpriseId] || row.enterpriseId}</span>
              <small>Tier {row.tier ?? '-'}</small>
            </div>
            <div className="round-enterprise-metrics">
              <span>Cash <strong>{formatCurrency(row.cash)}</strong></span>
              <span>Profit <strong className={Number(row.netProfit || 0) < 0 ? 'negative' : 'positive'}>{formatCurrency(row.netProfit)}</strong></span>
              <span>Inventory Alerts <strong>{row.lowStockCount}/{row.inventoryKinds}</strong></span>
              <span>Output <strong>{formatNumber(row.productionQuantity)}</strong></span>
              <span>Purchase Orders <strong>{formatNumber(row.procurementOrders)}</strong></span>
              <span>Sales Orders <strong>{formatNumber(row.salesOrders)}</strong></span>
            </div>
            <div className="round-action-strip">
              {row.actionNames.length > 0 ? row.actionNames.slice(0, 4).map((name, index) => (
                <em key={`${row.enterpriseId}-${name}-${index}`}>{name}</em>
              )) : (
                <em>No actions</em>
              )}
              {row.actionNames.length > 4 && <em>+{row.actionNames.length - 4}</em>}
            </div>
          </button>
        ))}
      </div>
    </section>
  );
};

function App() {
  const [currentCompany, setCurrentCompany] = useState('');
  const [currentDay, setCurrentDay] = useState(0);
  const [currentPage, setCurrentPage] = useState('daily');
  const [selectedExchangeId, setSelectedExchangeId] = useState(null);
  const [availableDays, setAvailableDays] = useState([]);
  const [availableRuns, setAvailableRuns] = useState([]);
  const [selectedRunId, setSelectedRunId] = useState('');
  const [selectedRunMeta, setSelectedRunMeta] = useState(null);
  const [activeRunId, setActiveRunId] = useState(null);
  const [activeRunMeta, setActiveRunMeta] = useState(null);
  const [scenarioFilter, setScenarioFilter] = useState('all');
  const [analysisScope, setAnalysisScope] = useState('multi');
  const [singleCaseFilter, setSingleCaseFilter] = useState('all');
  const [multiScenarioFilter, setMultiScenarioFilter] = useState('all');
  const [analysisControlModeFilter, setAnalysisControlModeFilter] = useState('all');
  const [runRecordFilters, setRunRecordFilters] = useState({
    scope: 'all',
    category: 'all',
    scenarioId: 'all',
    mode: 'all',
    dateFrom: '',
    dateTo: '',
  });
  const [selectedRunRecordIds, setSelectedRunRecordIds] = useState(new Set());
  const [scenarioPage, setScenarioPage] = useState('bullwhip');
  const [singlePerformanceMode, setSinglePerformanceMode] = useState('trend');
  const [dailyExchangeData, setDailyExchangeData] = useState(null);
  const [productEquivalentMode, setProductEquivalentMode] = useState(false);
  const [includeUpstreamExternalSupplierMode, setIncludeUpstreamExternalSupplierMode] = useState(false);
  const [includeDownstreamExternalMarketMode, setIncludeDownstreamExternalMarketMode] = useState(false);
  const [viewMode, setViewMode] = useState('welcome');
  const [runFiltersOpen,setRunFiltersOpen]=useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const catalogRequest=useRef(0),archiveRequest=useRef(0);
  const chartRef = useRef(null);
  const [topologyLoading,setTopologyLoading]=useState(true);
  const chartInstance = useRef(null);
  const selectedRunEntry = useMemo(
    () => availableRuns.find((run) => run.id === selectedRunId) || null,
    [availableRuns, selectedRunId]
  );
  const activeRunEntry = useMemo(
    () => availableRuns.find((run) => run.id === activeRunId) || null,
    [availableRuns, activeRunId]
  );
  const filteredAvailableRuns = useMemo(
    () => (
      scenarioFilter === 'all'
        ? availableRuns
        : availableRuns.filter((run) => detectRunCategory(run.meta) === scenarioFilter)
    ),
    [availableRuns, scenarioFilter]
  );
  const analysisScopeRuns = useMemo(
    () => availableRuns.filter((run) => {
      if (getAnalysisScopeFromMeta(run.meta) !== analysisScope) {
        return false;
      }
      if (
        analysisControlModeFilter !== 'all'
        && getRunControlModeFromMeta(run.meta) !== analysisControlModeFilter
      ) {
        return false;
      }
      if (analysisScope !== 'single' || singleCaseFilter === 'all') {
        if (analysisScope === 'multi' && multiScenarioFilter !== 'all') {
          return detectScenarioPage(run.meta) === multiScenarioFilter;
        }
        return true;
      }
      return getSingleCaseIdFromMeta(run.meta) === singleCaseFilter;
    }),
    [availableRuns, analysisScope, singleCaseFilter, multiScenarioFilter, analysisControlModeFilter]
  );
  const availableSingleCaseFilterIds = useMemo(
    () => new Set(
      availableRuns
        .filter((run) => getAnalysisScopeFromMeta(run.meta) === 'single')
        .filter((run) => (
          analysisControlModeFilter === 'all'
          || getRunControlModeFromMeta(run.meta) === analysisControlModeFilter
        ))
        .map((run) => getSingleCaseIdFromMeta(run.meta))
        .filter(Boolean)
    ),
    [availableRuns, analysisControlModeFilter]
  );
  const availableAnalysisControlModeIds = useMemo(
    () => new Set(
      availableRuns
        .filter((run) => getAnalysisScopeFromMeta(run.meta) === analysisScope)
        .filter((run) => (
          analysisScope !== 'single'
          || singleCaseFilter === 'all'
          || getSingleCaseIdFromMeta(run.meta) === singleCaseFilter
        ))
        .filter((run) => (
          analysisScope !== 'multi'
          || multiScenarioFilter === 'all'
          || detectScenarioPage(run.meta) === multiScenarioFilter
        ))
        .map((run) => getRunControlModeFromMeta(run.meta))
        .filter(Boolean)
    ),
    [availableRuns, analysisScope, singleCaseFilter, multiScenarioFilter]
  );
  const availableMultiScenarioFilterIds = useMemo(
    () => new Set(
      availableRuns
        .filter((run) => getAnalysisScopeFromMeta(run.meta) === 'multi')
        .filter((run) => (
          analysisControlModeFilter === 'all'
          || getRunControlModeFromMeta(run.meta) === analysisControlModeFilter
        ))
        .map((run) => detectScenarioPage(run.meta))
        .filter(Boolean)
    ),
    [availableRuns, analysisControlModeFilter]
  );
  const runRecordScenarioIds = useMemo(
    () => Array.from(new Set(
      availableRuns
        .map((run) => getScenarioIdFromMeta(run.meta))
        .filter(Boolean)
    )).sort(),
    [availableRuns]
  );
  const filteredRunRecords = useMemo(
    () => availableRuns.filter((run) => {
      const scope = getAnalysisScopeFromMeta(run.meta);
      const category = detectRunCategory(run.meta);
      const scenarioId = getScenarioIdFromMeta(run.meta);
      const mode = getRunControlModeFromMeta(run.meta);
      const dateValue = getRunRecordDateValue(run);
      if (runRecordFilters.scope !== 'all' && scope !== runRecordFilters.scope) {
        return false;
      }
      if (runRecordFilters.category !== 'all' && category !== runRecordFilters.category) {
        return false;
      }
      if (runRecordFilters.scenarioId !== 'all' && scenarioId !== runRecordFilters.scenarioId) {
        return false;
      }
      if (runRecordFilters.mode !== 'all' && mode !== runRecordFilters.mode) {
        return false;
      }
      if (runRecordFilters.dateFrom && (!dateValue || dateValue < runRecordFilters.dateFrom)) {
        return false;
      }
      if (runRecordFilters.dateTo && (!dateValue || dateValue > runRecordFilters.dateTo)) {
        return false;
      }
      return true;
    }),
    [availableRuns, runRecordFilters]
  );
  const selectedRunRecords = useMemo(
    () => availableRuns.filter((run) => selectedRunRecordIds.has(run.id)),
    [availableRuns, selectedRunRecordIds]
  );
  const visibleAnalysisPages = useMemo(
    () => (analysisScope === 'single' ? SINGLE_ANALYSIS_PAGES : MULTI_ANALYSIS_PAGES),
    [analysisScope]
  );
  const activeRunHasScenarioAnalysis = useMemo(
    () => analysisScope === 'multi' && hasScenarioAnalysisPage(activeRunMeta || selectedRunMeta),
    [analysisScope, activeRunMeta, selectedRunMeta]
  );
  const dataRoot = activeRunEntry?.dataRoot || selectedRunEntry?.dataRoot || '/workspace_multi';
  const quantityView = useMemo(
    () => buildQuantityView(productEquivalentMode, activeRunMeta || selectedRunMeta),
    [productEquivalentMode, activeRunMeta, selectedRunMeta]
  );
  const selectedEnterpriseSpecs = useMemo(
    () => getEnterpriseSpecsFromMeta(selectedRunMeta),
    [selectedRunMeta]
  );
  const activeEnterpriseSpecs = useMemo(
    () => getEnterpriseSpecsFromMeta(activeRunMeta || selectedRunMeta),
    [activeRunMeta, selectedRunMeta]
  );
  const companies = useMemo(
    () => activeEnterpriseSpecs.map((spec) => spec.enterprise_id || spec.id).filter(Boolean),
    [activeEnterpriseSpecs]
  );
  const companyNameMap = useMemo(
    () => Object.fromEntries(
      activeEnterpriseSpecs.map((spec) => [
        spec.enterprise_id || spec.id,
        spec.enterprise_name || spec.name || spec.enterprise_id || spec.id,
      ])
    ),
    [activeEnterpriseSpecs]
  );
  const topEnterpriseName = activeEnterpriseSpecs[0]?.enterprise_name
    || activeEnterpriseSpecs[0]?.name
    || activeEnterpriseSpecs[0]?.enterprise_id
    || activeEnterpriseSpecs[0]?.id
    || 'Top';
  const secondEnterpriseName = activeEnterpriseSpecs[1]?.enterprise_name
    || activeEnterpriseSpecs[1]?.name
    || activeEnterpriseSpecs[1]?.enterprise_id
    || activeEnterpriseSpecs[1]?.id
    || 'Next';
  const bottomEnterpriseName = activeEnterpriseSpecs[activeEnterpriseSpecs.length - 1]?.enterprise_name
    || activeEnterpriseSpecs[activeEnterpriseSpecs.length - 1]?.name
    || activeEnterpriseSpecs[activeEnterpriseSpecs.length - 1]?.enterprise_id
    || activeEnterpriseSpecs[activeEnterpriseSpecs.length - 1]?.id
    || 'Retail';

  useEffect(() => {
    loadRunCatalog();
    const changed = () => loadRunCatalog();
    window.addEventListener('platform-access-changed', changed);
    return () => window.removeEventListener('platform-access-changed',changed);
  }, []);

  useEffect(() => {
    let active=true;
    setSelectedRunMeta(selectedRunEntry?.meta || null);
    if(selectedRunEntry?.dataRoot) safeFetchJson(buildDataUrl(selectedRunEntry.dataRoot,'run_meta.json')).then(meta=>{if(active&&meta)setSelectedRunMeta({...selectedRunEntry.meta,...meta});});
    return()=>{active=false;};
  }, [selectedRunEntry]);

  useEffect(() => {
    if (filteredAvailableRuns.length === 0) {
      return;
    }
    if (!filteredAvailableRuns.some((run) => run.id === selectedRunId)) {
      const preferredRun = pickPreferredRun(filteredAvailableRuns, {
        keepCandidate: false,
        preferredScope: 'multi',
      });
      setSelectedRunId(preferredRun?.id || '');
      setSelectedRunMeta(preferredRun?.meta || null);
    }
  }, [filteredAvailableRuns, selectedRunId]);

  useEffect(() => {
    if (!activeRunId || viewMode !== 'analysis') {
      return;
    }
    loadAnalysisData(activeRunId);
    return()=>{archiveRequest.current+=1;};
  }, [activeRunId, viewMode]);

  useEffect(() => {
    if (viewMode !== 'analysis') {
      return;
    }
    if (!visibleAnalysisPages.some((page) => page.id === currentPage)) {
      setCurrentPage(visibleAnalysisPages[0]?.id || 'daily');
    }
  }, [viewMode, visibleAnalysisPages, currentPage]);

  useEffect(() => {
    if (!loading && currentPage === 'daily' && analysisScope === 'multi') {
      return initCompanyNetwork();
    }
  }, [currentCompany, loading, currentPage, viewMode, activeEnterpriseSpecs, dailyExchangeData, analysisScope]);

  useEffect(() => {
    if (viewMode !== 'analysis' || currentPage !== 'daily') {
      return;
    }
    let cancelled = false;
    const loadDailyExchangeSnapshot = async () => {
      const payload = await safeFetchJson(buildDataUrl(dataRoot, `public/exchange/day${currentDay}/exchange.json`));
      if (!cancelled) {
        setDailyExchangeData(payload || null);
      }
    };
    loadDailyExchangeSnapshot();
    return () => {
      cancelled = true;
    };
  }, [viewMode, currentPage, dataRoot, currentDay]);

  useEffect(() => {
    if (companies.length === 0) {
      return;
    }
    if (!currentCompany || !companies.includes(currentCompany)) {
      setCurrentCompany(companies[0]);
    }
  }, [companies, currentCompany]);

  const loadRunCatalog = async ({
    preferredRunId,
    preferLatest = false,
    preferredScope = 'multi',
  } = {}) => {
    const request=++catalogRequest.current;
    try {
      setLoading(true);
      setError(null);

      const runs = await detectAvailableRuns();
      if(request!==catalogRequest.current)return {runs:[],resolvedRunId:'',resolvedRunMeta:null};
      setAvailableRuns(runs);
      const candidateRunId = preferredRunId || selectedRunId;
      const shouldKeepSelection = !preferLatest && candidateRunId && candidateRunId !== 'workspace_current';
      const preferredRun = pickPreferredRun(runs, {
        candidateRunId,
        keepCandidate: shouldKeepSelection,
        preferredScope,
      });
      const resolvedRunId = preferredRun?.id || '';
      const resolvedRunMeta = preferredRun?.meta || null;
      setSelectedRunId(resolvedRunId);
      setSelectedRunMeta(resolvedRunMeta);

      setLoading(false);
      return { runs, resolvedRunId, resolvedRunMeta };
    } catch (error) {
      if(request!==catalogRequest.current)return {runs:[],resolvedRunId:'',resolvedRunMeta:null};
      setError('Failed to load the run catalog: ' + error.message);
      setLoading(false);
      return { runs: [], resolvedRunId: '', resolvedRunMeta: null };
    }
  };

  const loadAnalysisData = async (runId) => {
    const request=++archiveRequest.current;
    try {
      setLoading(true);
      setError(null);

      const runEntry = availableRuns.find((run) => run.id === runId) || null;
      const rootPath = runEntry?.dataRoot || resolveDataRoot(runId);
      const metaUrl=buildDataUrl(rootPath,'run_meta.json');
      const fullMeta = await safeFetchJson(metaUrl);
      if(request!==archiveRequest.current)return;
      if(!fullMeta)throw new Error(getDataRequestState(metaUrl).reason||'The selected archive metadata is unavailable.');
      const reviewMeta = {...(fullMeta || runEntry?.meta || selectedRunMeta || {}),...Object.fromEntries(Object.entries(runEntry?.meta || {}).filter(([key])=>key==='available_rounds'||key==='integrity'||key==='_visualization_max_day'))};
      const maxDay = await detectMaxDay(rootPath, reviewMeta);
      if(request!==archiveRequest.current)return;
      setAvailableDays(runEntry?.meta?.available_rounds ?? Array.from({ length: maxDay + 1 }, (_, i) => i));
      setCurrentDay(maxDay < 0 ? 0 : maxDay);
      setSelectedExchangeId(null);
      setActiveRunMeta(reviewMeta);
      setAnalysisScope(getAnalysisScopeFromMeta(runEntry?.meta || selectedRunMeta));
      setScenarioPage(detectScenarioPage(runEntry?.meta || selectedRunMeta) || 'bullwhip');
      setLoading(false);
    } catch (loadError) {
      if(request!==archiveRequest.current)return;
      setError('Failed to load simulation data: ' + loadError.message);
      setLoading(false);
    }
  };

  const detectAvailableRuns = async () => {
    const artifactRunsResult = await listAllArtifactRuns();
    if (artifactRunsResult.available && !artifactRunsResult.error) {
      return finalizeRunCatalog(buildRunEntriesFromArtifactIndex(artifactRunsResult.entries));
    }

    if(artifactRunsResult.status!==404)throw new Error(artifactRunsResult.error||'The run catalog could not be read. Retry the run list.');
    const workspaceMeta = await safeFetchJson(buildDataUrl(resolveDataRoot(LATEST_RUN_ID), 'run_meta.json'), { forceRefresh: true });
    const simulationRunsListing = await safeFetchJson('/simulation_runs/', { forceRefresh: true });
    const operationsJobResult = await listOperationsJobs({ limit: 500 });
    const runDirs = Object.entries(simulationRunsListing || {})
      .filter(([, type]) => type === 'directory')
      .map(([name]) => name);
    const workspaceJobsListing = await safeFetchJson('/workspace_jobs/', { forceRefresh: true });
    const jobWorkspaceDirs = Object.entries(workspaceJobsListing || {})
      .filter(([, type]) => type === 'directory')
      .map(([name]) => name);

    const historicalRuns = await Promise.all(
      runDirs.map(async (runId) => {
        const meta = await safeFetchJson(
          buildDataUrl(resolveDataRoot(runId), 'run_meta.json'),
          { forceRefresh: true }
        );
        const experimentTag = getRunExperimentTag(runId);
        return {
          id: runId,
          label: formatRunLabel(runId, meta, false, experimentTag),
          meta,
          dataRoot: resolveDataRoot(runId),
          sourceLabel: runId,
          experimentTag,
        };
      })
    );
    const workspaceJobRuns = (await Promise.all(
      jobWorkspaceDirs.map(async (workspaceName) => {
        const dataRoot = resolveWorkspaceJobRoot(workspaceName);
        const meta = await safeFetchJson(
          buildDataUrl(dataRoot, 'run_meta.json'),
          { forceRefresh: true }
        );
        if (!meta) {
          return null;
        }
        const runId = meta.run_id || workspaceName;
        const entryId = `workspace_job:${workspaceName}`;
        const experimentTag = getRunExperimentTag(workspaceName, runId);
        return {
          id: entryId,
          label: formatRunLabel(runId, meta, false, experimentTag),
          meta,
          dataRoot,
          sourceLabel: workspaceName,
          resolvedRunId: runId,
          experimentTag,
        };
      })
    )).filter(Boolean);
    const operationsJobRuns = (await Promise.all(
      (operationsJobResult.jobs || []).map(async (job) => {
        const workspaceName = getWorkspaceNameFromArtifactRoot(job?.artifact_root);
        if (!workspaceName) {
          return null;
        }
        const dataRoot = resolveWorkspaceJobRoot(workspaceName);
        const meta = (
          await safeFetchJson(buildDataUrl(dataRoot, 'run_meta.json'), { forceRefresh: true })
        ) || buildRunMetaFromOperationsJob(job);
        const runId = meta.run_id || job.run_id || job.job_id || workspaceName;
        const experimentTag = getRunExperimentTag(workspaceName, runId, job?.artifact_root);
        return {
          id: `operations_job:${job.job_id || workspaceName}`,
          label: formatRunLabel(runId, meta, false, experimentTag),
          meta,
          dataRoot,
          sourceLabel: workspaceName,
          resolvedRunId: runId,
          experimentTag,
        };
      })
    )).filter(Boolean);

    const workspaceEntry = workspaceMeta ? {
      id: 'workspace_current',
      label: formatRunLabel('workspace_current', workspaceMeta),
      meta: workspaceMeta,
      dataRoot: '/workspace_multi',
      sourceLabel: 'workspace_multi',
      experimentTag: '',
    } : null;

    const allHistoricalRuns = [
      ...workspaceJobRuns,
      ...operationsJobRuns,
      ...historicalRuns,
      workspaceEntry,
    ].filter(Boolean);
    return finalizeRunCatalog(allHistoricalRuns);
  };

  const renderRunMetaValue = (value) => {
    if (value === null || value === undefined || value === '') {
      return '-';
    }
    return String(value);
  };

  const renderRunTimeValue = (value) => {
    if (value === null || value === undefined || value === '') {
      return '-';
    }
    return formatDisplayTime(value);
  };

  const renderScenarioConfigSummary = (runMeta) => {
    const scenarioConfig = runMeta?.scenario_config || {};
    const simulation = scenarioConfig.simulation || {};
    const runtimeInjection = scenarioConfig.runtime_injection || {};
    const autoPolicy = scenarioConfig.auto_policy || {};
    const enterpriseConfigs = scenarioConfig.enterprise_configs || [];
    const initialActionBatches = scenarioConfig.initial_action_batches || {};
    const demandSeries = simulation.beer_game_demand_series || [];
    const initStaffingByEnterprise = indexActionsByExecutor(initialActionBatches.init_action_1 || []);
    const initInventoryByEnterprise = indexActionsByExecutor(initialActionBatches.init_action_2 || []);
    const initPoliciesByEnterprise = indexActionsByExecutor(initialActionBatches.init_action_3 || []);

    const enterpriseSnapshots = enterpriseConfigs.map((enterprise) => {
      const enterpriseId = enterprise.id;
      const staffingAction = (initStaffingByEnterprise[enterpriseId] || []).find(
        (item) => item?.action?.action_name === 'initialize_staffing'
      );
      const capacityAction = (initStaffingByEnterprise[enterpriseId] || []).find(
        (item) => item?.action?.action_name === 'set_capacity'
      );
      const inventoryItems = (initInventoryByEnterprise[enterpriseId] || [])
        .filter((item) => item?.action?.action_name === 'add_inventory')
        .map((item) => item.action.action_param || {});
      const policyItems = (initPoliciesByEnterprise[enterpriseId] || [])
        .filter((item) => item?.action?.action_name === 'set_inventory_policy')
        .map((item) => item.action.action_param || {});

      return {
        enterprise,
        staffing: staffingAction?.action?.action_param?.initial_staffing || {},
        capacity: capacityAction?.action?.action_param?.capacity,
        inventoryItems,
        policyItems,
      };
    });

    const productionSnapshots = enterpriseSnapshots
      .map((snapshot) => {
        const enterpriseId = snapshot.enterprise.id;
        const enterpriseInitActions = initPoliciesByEnterprise[enterpriseId] || [];
        const recipeAction = enterpriseInitActions.find((item) => item?.action?.action_name === 'set_product_recipe');
        const lineActions = enterpriseInitActions.filter((item) => item?.action?.action_name === 'build_production_line');
        return {
          enterpriseId,
          enterpriseName: snapshot.enterprise.name || enterpriseId,
          recipeAction,
          lineActions,
        };
      })
      .filter((snapshot) => snapshot.recipeAction || snapshot.lineActions.length > 0);

    return (
      <div className="welcome-config-sections">
        <section className="welcome-config-section">
          <div className="welcome-config-section-header">
            <h3>Scenario Overview</h3>
            <span>Core horizon, market cadence, and operating-mode settings for this run.</span>
          </div>
          <div className="welcome-meaning-grid">
            <div className="meaning-card">
              <span>Total Simulation Turns</span>
              <strong>{renderRunMetaValue(simulation.service_total_steps)}</strong>
              <p>Total number of turns advanced by the simulation environment.</p>
            </div>
            <div className="meaning-card">
              <span>Agent Decision Turns</span>
              <strong>{renderRunMetaValue(simulation.agent_run_steps)}</strong>
              <p>Number of turns in which enterprise agents participate in decisions.</p>
            </div>
            <div className="meaning-card">
              <span>Retail Unit Price</span>
              <strong>{renderRunMetaValue(simulation.beer_game_unit_price)}</strong>
              <p>Terminal-market price used by {(selectedEnterpriseSpecs[selectedEnterpriseSpecs.length - 1]?.enterprise_name || selectedEnterpriseSpecs[selectedEnterpriseSpecs.length - 1]?.name || 'the downstream enterprise')} for baseline external demand.</p>
            </div>
            <div className="meaning-card">
              <span>Customer Delivery Lead Time</span>
              <strong>{renderRunMetaValue(simulation.beer_game_customer_delivery_lead_time)}</strong>
              <p>Baseline number of turns between an external customer order and delivery.</p>
            </div>
          </div>
        </section>

        <section className="welcome-config-section">
          <div className="welcome-config-section-header">
            <h3>Demand Path</h3>
            <span>Baseline external demand injected into each turn and shared across multiple experiment families.</span>
          </div>
          <div className="demand-series-panel">
            {(demandSeries || []).map((value, index) => (
              <div key={`${index}-${value}`} className="demand-chip">
                <span>Round {index}</span>
                <strong>{formatNumber(value)}</strong>
              </div>
            ))}
          </div>
        </section>

        <section className="welcome-config-section">
          <div className="welcome-config-section-header">
            <h3>Initial Enterprise State</h3>
            <span>Starting cash, storage capacity, staffing, inventory, and inventory-policy thresholds for each enterprise.</span>
          </div>
          <div className="enterprise-config-cards">
            {enterpriseSnapshots.map(({ enterprise, staffing, capacity, inventoryItems, policyItems }) => (
              <article key={enterprise.id} className="enterprise-detail-card">
                <div className="enterprise-detail-header">
                  <h4>{enterprise.name || enterprise.id}</h4>
                  <span>Tier {renderRunMetaValue(enterprise.tier)}</span>
                </div>

                <div className="enterprise-detail-grid">
                  <div className="enterprise-detail-item">
                    <span>Initial Cash</span>
                    <strong>{formatCurrency(enterprise.initial_capital)}</strong>
                    <p>Cash available for operations and transactions at the start of Round 0.</p>
                  </div>
                  <div className="enterprise-detail-item">
                    <span>Initial Storage Capacity</span>
                    <strong>{renderRunMetaValue(capacity)}</strong>
                    <p>Total warehouse capacity measured in standard storage units.</p>
                  </div>
                  <div className="enterprise-detail-item">
                    <span>Purchasable Items</span>
                    <strong>{(enterprise.purchasable_materials_idList || []).join(', ') || '-'}</strong>
                    <p>Materials or products that the enterprise may purchase upstream.</p>
                  </div>
                  <div className="enterprise-detail-item">
                    <span>Saleable Items</span>
                    <strong>{(enterprise.salable_products_idList || []).join(', ') || '-'}</strong>
                    <p>Products that the enterprise may sell downstream or to a market.</p>
                  </div>
                </div>

                <div className="enterprise-detail-subsection">
                  <h5>Initial Staffing</h5>
                  <div className="inline-chip-list">
                    {Object.entries(staffing).length > 0 ? (
                      Object.entries(staffing).map(([department, count]) => (
                        <span key={`${enterprise.id}-${department}`}>{department}: {count}</span>
                      ))
                    ) : (
                      <span>-</span>
                    )}
                  </div>
                </div>

                <div className="enterprise-detail-subsection">
                  <h5>Initial Inventory</h5>
                  <div className="mini-table">
                    <div className="mini-table-row mini-table-head">
                      <span>Item</span>
                      <span>Quantity</span>
                      <span>Type</span>
                    </div>
                    {inventoryItems.length > 0 ? (
                      inventoryItems.map((item, index) => (
                        <div key={`${enterprise.id}-${item.item_id}-${index}`} className="mini-table-row">
                          <span>{item.item_id}</span>
                          <span>{formatNumber(item.quantity)}</span>
                          <span>{item.item_type}</span>
                        </div>
                      ))
                    ) : (
                      <div className="mini-table-row">
                        <span>-</span>
                        <span>-</span>
                        <span>-</span>
                      </div>
                    )}
                  </div>
                </div>

                <div className="enterprise-detail-subsection">
                  <h5>Inventory Policy Thresholds</h5>
                  <div className="mini-table">
                    <div className="mini-table-row mini-table-head">
                      <span>Item</span>
                      <span>Reorder Point</span>
                      <span>Safety Stock</span>
                    </div>
                    {policyItems.length > 0 ? (
                      policyItems.map((item, index) => (
                        <div key={`${enterprise.id}-policy-${item.item_id}-${index}`} className="mini-table-row">
                          <span>{item.item_id}</span>
                          <span>{formatNumber(item.reorder_point)}</span>
                          <span>{formatNumber(item.safety_stock)}</span>
                        </div>
                      ))
                    ) : (
                      <div className="mini-table-row">
                        <span>-</span>
                        <span>-</span>
                        <span>-</span>
                      </div>
                    )}
                  </div>
                </div>
              </article>
            ))}
          </div>
        </section>

        {productionSnapshots.length > 0 && (
          <section className="welcome-config-section">
            <div className="welcome-config-section-header">
              <h3>Production Node Settings</h3>
              <span>Initial manufacturing capabilities for scenarios that include a production module.</span>
            </div>
            {productionSnapshots.map((snapshot) => (
              <div key={snapshot.enterpriseId} className="recipe-panel">
                <h5>{snapshot.enterpriseName}</h5>
                <div className="welcome-meaning-grid">
                  <div className="meaning-card">
                    <span>Production Lines</span>
                    <strong>{renderRunMetaValue(snapshot.lineActions.length)}</strong>
                    <p>Number of production lines available when the run starts.</p>
                  </div>
                  <div className="meaning-card">
                    <span>Line Types</span>
                    <strong>{snapshot.lineActions.map((item) => item?.action?.action_param?.line_type).filter(Boolean).join(', ') || '-'}</strong>
                    <p>Production-line specifications provisioned during initialization.</p>
                  </div>
                  <div className="meaning-card">
                    <span>Target Product</span>
                    <strong>{renderRunMetaValue(snapshot.recipeAction?.action?.action_param?.product_id)}</strong>
                    <p>Primary finished product manufactured by this production node.</p>
                  </div>
                  <div className="meaning-card">
                    <span>Unit Conversion Cost</span>
                    <strong>
                      {formatNumber(
                        Number(snapshot.recipeAction?.action?.action_param?.labor_cost_per_unit || 0)
                        + Number(snapshot.recipeAction?.action?.action_param?.equipment_cost_per_unit || 0)
                      )}
                    </strong>
                    <p>Combined labor and equipment cost for producing one unit.</p>
                  </div>
                </div>
                <div className="inline-chip-list">
                  {Object.entries(snapshot.recipeAction?.action?.action_param?.raw_materials || {}).map(([material, amount]) => (
                    <span key={`${snapshot.enterpriseId}-recipe-${material}`}>{material}: {formatNumber(amount)}</span>
                  ))}
                </div>
              </div>
            ))}
          </section>
        )}

        <section className="welcome-config-section">
          <div className="welcome-config-section-header">
            <h3>Automatic Operating Policies</h3>
            <span>Thresholds controlling automatic recruitment and warehouse expansion during a run.</span>
          </div>
          <div className="welcome-meaning-grid">
            <div className="meaning-card">
              <span>Recruitment Trigger</span>
              <strong>{renderRunMetaValue(autoPolicy.hr?.recruit_trigger_threshold)}</strong>
              <p>Department pressure threshold that triggers automatic recruitment.</p>
            </div>
            <div className="meaning-card">
              <span>Recruitment Ratio</span>
              <strong>{renderRunMetaValue(autoPolicy.hr?.recruit_ratio)}</strong>
              <p>Staffing increment applied when automatic recruitment is triggered.</p>
            </div>
            <div className="meaning-card">
              <span>Expansion Trigger</span>
              <strong>{renderRunMetaValue(autoPolicy.inventory?.expand_trigger_threshold)}</strong>
              <p>Warehouse utilization threshold that triggers capacity expansion.</p>
            </div>
            <div className="meaning-card">
              <span>Expansion Ratio</span>
              <strong>{renderRunMetaValue(autoPolicy.inventory?.expand_ratio)}</strong>
              <p>Capacity increment applied during automatic warehouse expansion.</p>
            </div>
          </div>
        </section>

        <section className="welcome-config-section">
          <div className="welcome-config-section-header">
            <h3>Scheduled Actions and Settlement</h3>
            <span>Actions injected by the environment during each daily phase, including payroll and inventory-cost settlement.</span>
          </div>
          <div className="welcome-meaning-grid">
            <div className="meaning-card">
              <span>Payroll Interval</span>
              <strong>{renderRunMetaValue(runtimeInjection.salary_payment_interval_days)}</strong>
              <p>Number of turns between automatic payroll settlements.</p>
            </div>
            <div className="meaning-card">
              <span>Payroll Enterprises</span>
              <strong>{(runtimeInjection.salary_payment_enterprise_ids || []).join(', ') || '-'}</strong>
              <p>Enterprises included in automatic payroll settlement.</p>
            </div>
            <div className="meaning-card">
              <span>Per-Round Inventory Settlement</span>
              <strong>{runtimeInjection.inventory_cost_daily_settlement_enabled ? 'Enabled' : 'Disabled'}</strong>
              <p>Whether inventory holding and operating costs are settled automatically in each daily cycle.</p>
            </div>
            <div className="meaning-card">
              <span>Inventory-Cost Enterprises</span>
              <strong>{(runtimeInjection.inventory_cost_enterprise_ids || []).join(', ') || '-'}</strong>
              <p>Enterprises subject to automatic inventory-cost settlement.</p>
            </div>
          </div>
        </section>

        <LazyDetails className="welcome-config-raw" summary="View Raw Scenario JSON">{()=> <pre>{JSON.stringify(scenarioConfig,null,2)}</pre>}</LazyDetails>
      </div>
    );
  };

  const handleEnterAnalysis = () => {
    const entry=availableRuns.find(run=>run.id===selectedRunId);
    if(!entry)return;
    cancelViewReads();
    const nextMeta=selectedRunMeta||entry.meta;
    const scope=getAnalysisScopeFromMeta(nextMeta);
    const scene=detectScenarioPage(nextMeta);
    setLoading(true);setError(null);setActiveRunId(entry.id);setActiveRunMeta(nextMeta);
    setAnalysisScope(scope);setScenarioPage(scene||'bullwhip');
    setCurrentPage(scene&&scope==='multi'?'scenario':'daily');
    const specs=getEnterpriseSpecsFromMeta(nextMeta);
    setCurrentCompany(specs[0]?.enterprise_id||specs[0]?.id||'');setViewMode('analysis');
  };

  const handleEnterExperimentOps = () => {
    cancelViewReads();setViewMode('experimentOps');
  };

  const handleBackToWelcome = () => {
    cancelViewReads();setViewMode('welcome');
  };

  const handleScenarioFilterChange = (filterValue) => {
    setScenarioFilter(filterValue);
    const nextRuns = filterValue === 'all'
      ? availableRuns
      : availableRuns.filter((run) => detectRunCategory(run.meta) === filterValue);
    if (nextRuns.length > 0 && !nextRuns.some((run) => run.id === selectedRunId)) {
      const preferredRun = pickPreferredRun(nextRuns, {
        keepCandidate: false,
        preferredScope: 'multi',
      });
      setSelectedRunId(preferredRun?.id || '');
      setSelectedRunMeta(preferredRun?.meta || null);
    }
  };

  const updateRunRecordFilter = (field, value) => {
    setRunRecordFilters((previous) => ({
      ...previous,
      [field]: value,
    }));
  };

  const resetRunRecordFilters = () => {
    setRunRecordFilters({
      scope: 'all',
      category: 'all',
      scenarioId: 'all',
      mode: 'all',
      dateFrom: '',
      dateTo: '',
    });
  };

  const toggleRunRecordSelection = (runId) => {
    setSelectedRunRecordIds((previous) => {
      const next = new Set(previous);
      if (next.has(runId)) {
        next.delete(runId);
      } else {
        next.add(runId);
      }
      return next;
    });
  };

  const selectFilteredRunRecords = () => {
    setSelectedRunRecordIds((previous) => {
      const next = new Set(previous);
      filteredRunRecords.forEach((run) => next.add(run.id));
      return next;
    });
  };

  const clearRunRecordSelection = () => {
    setSelectedRunRecordIds(new Set());
  };

  const buildRunRecordExportPayload = (runs) => ({
    schema_version: 'visualization_run_record_export.v1',
    exported_at: new Date().toISOString(),
    total_records: runs.length,
    records: runs.map((run) => ({
      id: run.id,
      source_label: run.sourceLabel,
      resolved_run_id: run.resolvedRunId || run.meta?.run_id || run.id,
      scenario_id: getScenarioIdFromMeta(run.meta),
      scenario_category: detectRunCategory(run.meta),
      analysis_scope: getAnalysisScopeFromMeta(run.meta),
      control_mode: getRunControlModeFromMeta(run.meta),
      is_starred: Boolean(run.experimentTag),
      experiment_tag: run.experimentTag || '',
      experiment_tag_label: run.experimentTag ? formatExperimentTag(run.experimentTag) : '',
      status: run.meta?.status || '',
      completed_steps: run.meta?.completed_steps,
      planned_total_steps: run.meta?.planned_total_steps,
      started_at: run.meta?.started_at,
      finished_at: run.meta?.finished_at || run.meta?.archived_at,
      data_root: run.dataRoot,
      storage: getRunRecordStorageLabel(run),
      archive_url: buildRunArchiveUrl(run),
      files: buildRunRecordFileLinks(run),
      meta: run.meta,
    })),
  });

  const downloadRunRecords = (runs) => {
    if (!runs.length) {
      return;
    }
    const firstScenarioId = getScenarioIdFromMeta(runs[0].meta) || 'mixed';
    downloadJson(
      `${sanitizeDownloadPart(firstScenarioId)}_${runs.length}_run_records.json`,
      buildRunRecordExportPayload(runs)
    );
  };

  const downloadRunArchives = (runs) => {
    runs.forEach((run, index) => {
      const archiveUrl = buildRunArchiveUrl(run);
      if (!archiveUrl) {
        return;
      }
      window.setTimeout(() => downloadUrl(archiveUrl), index * 250);
    });
  };

  const handleAnalysisRunChange = (runId) => {
    const runEntry = availableRuns.find((run) => run.id === runId) || null;
    if (!runEntry) {
      return;
    }
    cancelViewReads();setLoading(true);setError(null);
    if(runId===activeRunId&&viewMode==='analysis')loadAnalysisData(runId);
    const nextAnalysisScope = getAnalysisScopeFromMeta(runEntry?.meta);
    setSelectedRunId(runId);
    setSelectedRunMeta(runEntry?.meta || null);
    setActiveRunId(runId);
    setActiveRunMeta(runEntry?.meta || null);
    setAnalysisScope(nextAnalysisScope);
    setScenarioPage(detectScenarioPage(runEntry?.meta) || 'bullwhip');
    setCurrentPage(hasScenarioAnalysisPage(runEntry?.meta) && nextAnalysisScope === 'multi' ? 'scenario' : 'daily');
    setSelectedExchangeId(null);
  };

  const handleSingleCaseFilterChange = (filterValue) => {
    setSingleCaseFilter(filterValue);
    const nextRuns = availableRuns.filter((run) => (
      getAnalysisScopeFromMeta(run.meta) === 'single'
      && (filterValue === 'all' || getSingleCaseIdFromMeta(run.meta) === filterValue)
      && (
        analysisControlModeFilter === 'all'
        || getRunControlModeFromMeta(run.meta) === analysisControlModeFilter
      )
    ));
    const currentRunId = activeRunId || selectedRunId;
    if (nextRuns.length > 0 && !nextRuns.some((run) => run.id === currentRunId)) {
      handleAnalysisRunChange(nextRuns[0].id);
    }
  };

  const handleMultiScenarioFilterChange = (filterValue) => {
    setMultiScenarioFilter(filterValue);
    const nextRuns = availableRuns.filter((run) => (
      getAnalysisScopeFromMeta(run.meta) === 'multi'
      && (filterValue === 'all' || detectScenarioPage(run.meta) === filterValue)
      && (
        analysisControlModeFilter === 'all'
        || getRunControlModeFromMeta(run.meta) === analysisControlModeFilter
      )
    ));
    const currentRunId = activeRunId || selectedRunId;
    if (nextRuns.length > 0 && !nextRuns.some((run) => run.id === currentRunId)) {
      handleAnalysisRunChange(nextRuns[0].id);
    }
  };

  const handleAnalysisControlModeFilterChange = (filterValue) => {
    setAnalysisControlModeFilter(filterValue);
    const nextRuns = availableRuns.filter((run) => (
      getAnalysisScopeFromMeta(run.meta) === analysisScope
      && (
        filterValue === 'all'
        || getRunControlModeFromMeta(run.meta) === filterValue
      )
      && (
        analysisScope !== 'single'
        || singleCaseFilter === 'all'
        || getSingleCaseIdFromMeta(run.meta) === singleCaseFilter
      )
      && (
        analysisScope !== 'multi'
        || multiScenarioFilter === 'all'
        || detectScenarioPage(run.meta) === multiScenarioFilter
      )
    ));
    const currentRunId = activeRunId || selectedRunId;
    if (nextRuns.length > 0 && !nextRuns.some((run) => run.id === currentRunId)) {
      handleAnalysisRunChange(nextRuns[0].id);
    }
  };

  const handleAnalysisScopeChange = (nextScope) => {
    setAnalysisScope(nextScope);
    let nextRuns = availableRuns.filter((run) => (
      getAnalysisScopeFromMeta(run.meta) === nextScope
      && (
        analysisControlModeFilter === 'all'
        || getRunControlModeFromMeta(run.meta) === analysisControlModeFilter
      )
      && (
        nextScope !== 'single'
        || singleCaseFilter === 'all'
        || getSingleCaseIdFromMeta(run.meta) === singleCaseFilter
      )
      && (
        nextScope !== 'multi'
        || multiScenarioFilter === 'all'
        || detectScenarioPage(run.meta) === multiScenarioFilter
      )
    ));
    if (nextScope === 'single' && nextRuns.length === 0 && singleCaseFilter !== 'all') {
      setSingleCaseFilter('all');
      nextRuns = availableRuns.filter((run) => (
        getAnalysisScopeFromMeta(run.meta) === nextScope
        && (
          analysisControlModeFilter === 'all'
          || getRunControlModeFromMeta(run.meta) === analysisControlModeFilter
        )
      ));
    }
    if (nextScope === 'multi' && nextRuns.length === 0 && multiScenarioFilter !== 'all') {
      setMultiScenarioFilter('all');
      nextRuns = availableRuns.filter((run) => (
        getAnalysisScopeFromMeta(run.meta) === nextScope
        && (
          analysisControlModeFilter === 'all'
          || getRunControlModeFromMeta(run.meta) === analysisControlModeFilter
        )
      ));
    }
    if (nextRuns.length === 0 && analysisControlModeFilter !== 'all') {
      setAnalysisControlModeFilter('all');
      nextRuns = availableRuns.filter((run) => (
        getAnalysisScopeFromMeta(run.meta) === nextScope
        && (
          nextScope !== 'single'
          || singleCaseFilter === 'all'
          || getSingleCaseIdFromMeta(run.meta) === singleCaseFilter
        )
      ));
    }
    if (nextRuns.length > 0 && !nextRuns.some((run) => run.id === activeRunId)) {
      const preferredRun = pickPreferredRun(nextRuns, {
        keepCandidate: false,
        preferredScope: nextScope,
      });
      handleAnalysisRunChange(preferredRun?.id || nextRuns[0].id);
      return;
    }
    if (nextScope === 'single' && currentPage === 'scenario') {
      setCurrentPage('daily');
    }
  };

  const handleScenarioPageChange = (pageId) => {
    setScenarioPage(pageId);
    setMultiScenarioFilter(pageId);
    const matchingRuns = availableRuns.filter((run) => (
      getAnalysisScopeFromMeta(run.meta) === 'multi'
      &&
      detectScenarioPage(run.meta) === pageId
      && (
        analysisControlModeFilter === 'all'
        || getRunControlModeFromMeta(run.meta) === analysisControlModeFilter
      )
    ));
    const currentRunStillMatches = matchingRuns.some((run) => run.id === activeRunId);
    if (!currentRunStillMatches && matchingRuns[0]) {
      handleAnalysisRunChange(matchingRuns[0].id);
    }
  };

  const detectMaxDay = async (rootPath, runMeta = null) => {
    if (Array.isArray(runMeta?.available_rounds)) return Math.max(-1, ...runMeta.available_rounds);
    if (runMeta?._visualization_max_day != null) return Number(runMeta._visualization_max_day);
    const steps = runMeta?.completed_steps ?? runMeta?.total_steps;
    if (steps != null && Number(steps) === 0) return -1;
    // Use public exchange snapshots as the turn source so Vite's HTML fallback is not mistaken for data.
    let maxDay = -1;
    for (let i = 0; i < Math.max(200, Number(steps) || 0); i++) {
      try {
        const data = await safeFetchJson(buildDataUrl(rootPath, `public/exchange/day${i}/exchange.json`));
        if (data?.status !== 'success' || !data?.data) {
          break;
        }
        maxDay = i;
      } catch (e) {
        break;
      }
    }

    // If no exchange snapshots are available, infer the latest turn from enterprise records.
    if (maxDay === -1) {
      try {
        const runEnterpriseId = Array.isArray(runMeta?.enterprise_ids) ? runMeta.enterprise_ids[0] : '';
        const runEnterpriseSpec = (
          runMeta?.scenario_config?.enterprise_specs?.[0]
          || runMeta?.scenario_config?.enterprise_configs?.[0]
          || {}
        );
        const fallbackEnterpriseId = (
          runEnterpriseId
          || runEnterpriseSpec.enterprise_id
          || runEnterpriseSpec.id
          || selectedEnterpriseSpecs[0]?.enterprise_id
          || selectedEnterpriseSpecs[0]?.id
          || ''
        );
        const data = await safeFetchJson(buildDataUrl(rootPath, `enterprises/${fallbackEnterpriseId}/records/`));
        if (data) {
          const days = Object.keys(data).filter(key => key.startsWith('day'));
          if (days.length > 0) {
            maxDay = Math.max(...days.map(day => parseInt(day.replace('day', ''))));
          }
        }
      } catch (e) {
        // Ignore fallback lookup errors.
      }
    }

    return maxDay;
  };

  const initCompanyNetwork = () => {
    setTopologyLoading(true);
    let cancelled = false;
    let removeResize = () => {};
    import('./utils/echartsRuntime').then((echarts) => {
      if (cancelled) return;
    if (!chartRef.current) return;

    if (chartInstance.current) {
      chartInstance.current.dispose();
    }

    chartInstance.current = echarts.init(chartRef.current);
    const topology = buildTopologyGraph(
      activeEnterpriseSpecs,
      currentCompany,
      companyNameMap,
      dailyExchangeData
    );

    const option = {
      title: {
        text: '',
        textStyle: {
          fontSize: 14,
          fontWeight: 'bold'
        }
      },
      tooltip: {
        trigger: 'item'
      },
      grid: { containLabel: true },
      animationDurationUpdate: 1500,
      animationEasingUpdate: 'quinticInOut',
      series: [
        {
          type: 'graph',
          layout: 'none',
          roam: false,
          draggable: false,
          label: {
            show: true,
            fontSize: 12,
            fontWeight: 'bold'
          },
          edgeSymbol: ['circle', 'arrow'],
          edgeSymbolSize: [4, 10],
          edgeLabel: {
            show: true,
            fontSize: 10,
            color: '#3d4b5c',
            backgroundColor: 'rgba(255,255,255,0.82)',
            borderRadius: 4,
            padding: [2, 4],
          },
          data: topology.nodes,
          links: topology.links,
          lineStyle: {
            opacity: 0.9,
            width: 2,
            curveness: 0.08
          },
          emphasis: {
            focus: 'adjacency',
            lineStyle: {
              width: 4
            }
          }
        }
      ]
    };

    setReviewChart(chartInstance.current, option);setTopologyLoading(false);

    // Select a topology node to switch the active enterprise.
    chartInstance.current.on('click', function(params) {
      if (params.dataType === 'node') {
        const company = params.data.name;
        setCurrentCompany(company);
      }
    });

    const handleResize = () => {
      chartInstance.current?.resize();
    };

    window.addEventListener('resize', handleResize);

    removeResize = () => window.removeEventListener('resize', handleResize);
    }).catch(()=>{if(!cancelled){setTopologyLoading(false);if(chartRef.current)chartRef.current.textContent='The topology diagram could not be loaded. Other records remain available.';}});
    return () => {cancelled=true;removeResize();chartInstance.current?.dispose();chartInstance.current=null;};
  };

  if (viewMode === 'welcome') {
    return (
      <div className="welcome-shell"><ViewerActivity/>
        <div className="welcome-hero"><PlatformAccessBadge />
          <div className="welcome-badge">Supply Chain Simulation</div>
          <h1>Supply Chain Simulation Review</h1>
          <p>
            Explore archived enterprise decisions, operating outcomes, and experiment designs. Authorized operators can run simulations on a configured installation.
          </p>
        </div>

        <section className="welcome-entry-grid">
          <button
            type="button"
            className="welcome-entry-card result"
            onClick={handleEnterAnalysis}
            disabled={loading || !selectedRunId}
          >
            <span>Results Review</span>
            <strong>Review Simulation Results</strong>
            <p>Inspect enterprise states, transaction paths, global dynamics, and scenario-specific economic outcomes from completed runs.</p>
          </button>
          <button type="button" className="welcome-entry-card operations" onClick={handleEnterExperimentOps}>
            <span>Experiment Ops</span>
            <strong>Browse Experiment Designs</strong>
            <p>Inspect scenario configurations and available results. Experiment execution requires operator access.</p>
          </button>
        </section>

        <div className="welcome-panel">
          {loading ? (
            <LoadingState label="Loading available experiments…" variant="metrics"/>
          ) : error ? (
            <div className="error" role="alert">{error}<button onClick={()=>{clearDataSourceCache();loadRunCatalog();}}>Retry run list</button></div>
          ) : (
            <>
            <section className="welcome-block welcome-selection-block">
                <div className="welcome-panel-header">
                  <div>
                    <h2>Select a Simulation Run</h2>
                    <span>Browse the active workspace and archived operations or legacy runs.</span>
                  </div>
                  <button type="button" className="welcome-refresh-btn" onClick={()=>{clearDataSourceCache();loadRunCatalog();}}>
                    Refresh Run List
                  </button>
                </div>
                <div className="welcome-filter-block">
                  <label htmlFor="scenario-filter">Scenario Filter</label>
                  <div className="welcome-filter-tabs">
                    <button
                      type="button"
                      className={scenarioFilter === 'all' ? 'active' : ''}
                      onClick={() => handleScenarioFilterChange('all')}
                    >
                      All
                    </button>
                    {RUN_CATEGORY_CONFIGS.map((page) => (
                      <button
                        type="button"
                        key={page.id}
                        className={scenarioFilter === page.id ? 'active' : ''}
                        onClick={() => handleScenarioFilterChange(page.id)}
                      >
                        {page.label}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="welcome-selector-block">
                  <label htmlFor="run-selector">Simulation Run</label>
                  <select
                    id="run-selector"
                    value={selectedRunId}
                    onChange={(event) => setSelectedRunId(event.target.value)}
                  >
                    {filteredAvailableRuns.length > 0 ? (
                      filteredAvailableRuns.map((run) => (
                        <option key={run.id} value={run.id}>
                          {run.label}
                        </option>
                      ))
                    ) : (
                      <option value={selectedRunId} disabled>
                        No runs match the current filters
                      </option>
                    )}
                  </select>
                  {filteredAvailableRuns.length === 0 && (
                    <small className="welcome-selector-hint">{availableRuns.length === 0 ? "No published results are loaded. The host can publish existing runs; self-hosted users can import the included FD4 example." : "No runs match the current filters."}</small>
                  )}
                </div>

                {filteredAvailableRuns.some((run) => run.experimentTag) && (
                  <div className="welcome-key-runs">
                    <div className="welcome-key-runs-header">
                      <strong>Key Experiment Tags</strong>
                      <span>Runs with an experiment prefix or a suffix after the timestamp are highlighted here.</span>
                    </div>
                    <div className="welcome-key-run-list">
                      {filteredAvailableRuns
                        .filter((run) => run.experimentTag)
                        .map((run) => (
                          <button
                            type="button"
                            key={run.id}
                            className={`welcome-key-run ${selectedRunId === run.id ? 'active' : ''}`}
                            onClick={() => setSelectedRunId(run.id)}
                          >
                            <span>{getScenarioCategoryLabel(run.meta)}</span>
                            <strong>{formatExperimentTag(run.experimentTag)}</strong>
                            <small>{run.id}</small>
                          </button>
                        ))}
                    </div>
                  </div>
                )}

                <div className="welcome-meta-grid">
                  <div className="welcome-meta-card accent">
                    <span>Detected Scenario</span>
                    <strong>{selectedRunMeta ? getScenarioCategoryLabel(selectedRunMeta) : 'No result loaded'}</strong>
                  </div>
                  {selectedRunEntry?.experimentTag && (
                    <div className="welcome-meta-card key-run">
                      <span>Experiment Tag</span>
                      <strong>{formatExperimentTag(selectedRunEntry.experimentTag)}</strong>
                    </div>
                  )}
                  <div className="welcome-meta-card">
                    <span>Run ID</span>
                    <strong>{renderRunMetaValue(selectedRunEntry?.sourceLabel || selectedRunEntry?.resolvedRunId || selectedRunMeta?.run_id || selectedRunId)}</strong>
                  </div>
                  <div className="welcome-meta-card">
                    <span>Scenario ID</span>
                    <strong>{renderRunMetaValue(selectedRunMeta?.scenario_id)}</strong>
                  </div>
                  <div className="welcome-meta-card">
                    <span>Scenario Name</span>
                    <strong>{renderRunMetaValue(selectedRunMeta?.scenario_meta?.name)}</strong>
                  </div>
                  <div className="welcome-meta-card">
                    <span>Completed / Planned Turns</span>
                    <strong>{`${selectedRunMeta?.completed_steps ?? selectedRunMeta?.total_steps ?? 'Not recorded'} / ${selectedRunMeta?.planned_total_steps ?? 'Not recorded'}`}</strong>
                  </div>
                  <div className="welcome-meta-card">
                    <span>Started</span>
                    <strong>{renderRunTimeValue(selectedRunMeta?.started_at)}</strong>
                  </div>
                  <div className="welcome-meta-card">
                    <span>Finished</span>
                    <strong>{renderRunTimeValue(selectedRunMeta?.finished_at || selectedRunMeta?.archived_at)}</strong>
                  </div>
                  <div className="welcome-meta-card">
                    <span>Demand Mode</span>
                    <strong>{renderRunMetaValue(getMarketModeFromMeta(selectedRunMeta))}</strong>
                  </div>
                  <div className="welcome-meta-card">
                    <span>Status</span>
                    <strong>{renderRunMetaValue(selectedRunMeta?.status)}</strong>
                  </div>
                </div>
              </section>

              <section className="welcome-block run-record-library">
                <div className="welcome-panel-header">
                  <div>
                    <h2>Run Archive</h2>
                    <span>Search all records, export selections, and open commonly used artifacts from their storage locations.</span>
                  </div>
                  <div className="run-record-actions">
                    <button
                      type="button"
                      className="welcome-refresh-btn"
                      onClick={selectFilteredRunRecords}
                      disabled={filteredRunRecords.length === 0}
                    >
                      Select Filtered
                    </button>
                    <button
                      type="button"
                      className="welcome-refresh-btn"
                      onClick={clearRunRecordSelection}
                      disabled={selectedRunRecordIds.size === 0}
                    >
                      Clear Selection
                    </button>
                    <button
                      type="button"
                      className="welcome-enter-btn"
                      onClick={() => downloadRunRecords(selectedRunRecords)}
                      disabled={selectedRunRecords.length === 0}
                    >
                      Download Manifest
                    </button>
                    <button
                      type="button"
                      className="welcome-enter-btn"
                      onClick={() => downloadRunArchives(selectedRunRecords)}
                      disabled={selectedRunRecords.length === 0}
                    >
                      Download Selected ZIPs
                    </button>
                  </div>
                </div>

                <div className="run-record-filter-grid">
                  <label>
                    <span>Scope</span>
                    <select
                      value={runRecordFilters.scope}
                      onChange={(event) => updateRunRecordFilter('scope', event.target.value)}
                    >
                      <option value="all">All</option>
                      {ANALYSIS_SCOPE_CONFIGS.map((config) => (
                        <option key={config.id} value={config.id}>{config.label}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    <span>Scenario</span>
                    <select
                      value={runRecordFilters.category}
                      onChange={(event) => updateRunRecordFilter('category', event.target.value)}
                    >
                      <option value="all">All Scenarios</option>
                      {RUN_CATEGORY_CONFIGS.map((config) => (
                        <option key={config.id} value={config.id}>{config.label}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    <span>Scenario ID</span>
                    <select
                      value={runRecordFilters.scenarioId}
                      onChange={(event) => updateRunRecordFilter('scenarioId', event.target.value)}
                    >
                      <option value="all">All IDs</option>
                      {runRecordScenarioIds.map((scenarioId) => (
                        <option key={scenarioId} value={scenarioId}>{scenarioId}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    <span>Decision Mode</span>
                    <select
                      value={runRecordFilters.mode}
                      onChange={(event) => updateRunRecordFilter('mode', event.target.value)}
                    >
                      <option value="all">All Modes</option>
                      <option value="agent">Agent Skill</option>
                      <option value="scripted">Scripted</option>
                    </select>
                  </label>
                  <label>
                    <span>Start Date</span>
                    <input
                      type="date"
                      value={runRecordFilters.dateFrom}
                      onChange={(event) => updateRunRecordFilter('dateFrom', event.target.value)}
                    />
                  </label>
                  <label>
                    <span>End Date</span>
                    <input
                      type="date"
                      value={runRecordFilters.dateTo}
                      onChange={(event) => updateRunRecordFilter('dateTo', event.target.value)}
                    />
                  </label>
                  <button type="button" className="run-record-reset" onClick={resetRunRecordFilters}>
                    Reset Filters
                  </button>
                </div>

                <div className="run-record-summary">
                  <strong>{filteredRunRecords.length}</strong>
                  <span>matching records; {selectedRunRecordIds.size} selected.</span>
                </div>

                <div className="run-record-table">
                  {filteredRunRecords.length > 0 ? (
                    filteredRunRecords.map((run) => {
                      const scenarioId = getScenarioIdFromMeta(run.meta) || 'unknown_scenario';
                      const fileLinks = buildRunRecordFileLinks(run);
                      return (
                        <article
                          key={run.id}
                          className={`run-record-row ${selectedRunRecordIds.has(run.id) ? 'selected' : ''} ${run.experimentTag ? 'starred' : ''}`}
                        >
                          <label className="run-record-check">
                            <input
                              type="checkbox"
                              checked={selectedRunRecordIds.has(run.id)}
                              onChange={() => toggleRunRecordSelection(run.id)}
                            />
                          </label>
                          <div className="run-record-main">
                            <div className="run-record-title">
                              <strong>{scenarioId}</strong>
                              {run.experimentTag && (
                                <span className="run-record-star">★ {formatExperimentTag(run.experimentTag)}</span>
                              )}
                              <span>{getRunControlModeLabel(run.meta)}</span>
                              <span>{getScenarioCategoryLabel(run.meta)}</span>
                              <span>{getAnalysisScopeFromMeta(run.meta) === 'multi' ? 'Multi-enterprise' : 'Enterprise capability'}</span>
                            </div>
                            <div className="run-record-meta">
                              <span>{formatDisplayTime(getRunRecordTimeValue(run))}</span>
                              <span>{renderRunMetaValue(run.meta?.status)}</span>
                              <span>{renderRunMetaValue(run.meta?.completed_steps || run.meta?.planned_total_steps)} steps</span>
                              <code>{getRunRecordStorageLabel(run)}</code>
                            </div>
                            <div className="run-record-files">
                              {fileLinks.map((file) => (
                                <a key={file.relativePath} href={file.url} target="_blank" rel="noreferrer" download>
                                  {file.label}
                                </a>
                              ))}
                            </div>
                          </div>
                          <div className="run-record-row-actions">
                            <button
                              type="button"
                              onClick={() => {
                                setSelectedRunId(run.id);
                                setSelectedRunMeta(run.meta || null);
                              }}
                            >
                              Set Active
                            </button>
                            <button type="button" onClick={() => downloadRunRecords([run])}>
                              Download Record
                            </button>
                            {buildRunArchiveUrl(run) && (
                              <a href={buildRunArchiveUrl(run)} download>
                                Full ZIP
                              </a>
                            )}
                          </div>
                        </article>
                      );
                    })
                  ) : (
                    <div className="run-record-empty">No run records match the current filters.</div>
                  )}
                </div>
              </section>

              {selectedRunMeta && <section className="welcome-block welcome-config-block">
                <div className="welcome-config-header">
                  <h3>Configuration Details</h3>
                  <span>{renderRunMetaValue(selectedRunMeta?.scenario_meta?.summary)}</span>
                </div>
                <div className="welcome-config-card">
                  {renderScenarioConfigSummary(selectedRunMeta)}
                </div>
              </section>}
              {selectedRunRecords.length>1 && <RunComparison runs={selectedRunRecords} />}
            </>
          )}
        </div>
        <div className="welcome-sticky-action">
          <button type="button" className="welcome-enter-panel secondary" onClick={handleEnterExperimentOps}>
            <strong>Open Experiment Operations</strong>
          </button>
          <button
            type="button"
            className="welcome-enter-panel"
            onClick={handleEnterAnalysis}
            disabled={loading || !selectedRunId}
          >
            <strong>Review Results</strong>
          </button>
        </div>
      </div>
    );
  }

  if (viewMode === 'experimentOps') {
    return (
      <div className="app experiment-ops-shell"><ViewerActivity/>
        <div className="app-header experiment-ops-app-header">
          <PlatformAccessBadge /><div className="analysis-toolbar">
            <button type="button" className="back-to-welcome-btn" onClick={handleBackToWelcome}>
              Back to Start
            </button>
            <button type="button" className="back-to-welcome-btn alt" onClick={handleEnterAnalysis}>
              Review Results
            </button>
          </div>
          <h1>Experiment Operations</h1>
          <p>
            Create, start, stop, and track simulation jobs independently of the results workspace.
          </p>
        </div>
        <div className="main-content-wrapper">
          <ViewBoundary resetKey="operations"><React.Suspense fallback={<ViewLoading label="Opening experiment operations…"/>}><ExperimentOperationsView
            currentRunId={activeRunMeta?.run_id || activeRunEntry?.id || activeRunId || selectedRunId}
            currentRunMeta={activeRunMeta || selectedRunMeta}
            currentRunSourceLabel={activeRunEntry?.sourceLabel || selectedRunEntry?.sourceLabel || dataRoot}
            availableRuns={availableRuns}
          /></React.Suspense></ViewBoundary>
        </div>
      </div>
    );
  }

  return (
    <div className={`app results-app ${currentPage==='daily'?'has-round-dock':''}`}><ViewerActivity/>
      <div className="app-header analysis-header">
        <div className="analysis-brand-row">
          <h1>Supply Chain Enterprise Simulation</h1>
          <PlatformAccessBadge/>
          <div className="analysis-toolbar">
          <button type="button" aria-label="Back to Start" className="back-to-welcome-btn" onClick={handleBackToWelcome}>
            <span className="toolbar-label-full">Back to Start</span><span className="toolbar-label-short">Back</span>
          </button>
          <button type="button" aria-label="Experiment Operations" className="back-to-welcome-btn alt" onClick={handleEnterExperimentOps}>
            <span className="toolbar-label-full">Experiment Operations</span><span className="toolbar-label-short">Experiments</span>
          </button>
        </div>
        </div>
        <p className="analysis-run-caption" title={`${activeRunMeta?.run_id||''} · ${activeRunMeta?.scenario_id||''}`}>
          {activeRunMeta?.run_id ? `Run: ${activeRunMeta.run_id}` : 'Results review'}
          {activeRunMeta?.scenario_id ? ` · ${activeRunMeta.scenario_id}` : ''}
        </p>
        <div id="analysis-run-controls" className={`analysis-header-controls ${runFiltersOpen?'filters-open':'filters-collapsed'}`}>
          <div className="analysis-scope-switcher">
            {ANALYSIS_SCOPE_CONFIGS.map((scope) => (
              <button
                type="button"
                key={scope.id}
                title={`${scope.label}: ${scope.description}`}
                aria-label={scope.label}
                aria-pressed={analysisScope===scope.id}
                className={analysisScope === scope.id ? 'active' : ''}
                onClick={() => handleAnalysisScopeChange(scope.id)}
              >
                <strong>{scope.id==='multi'?'Multi-enterprise':'Single-enterprise'}</strong>
                <small>{scope.description}</small>
              </button>
            ))}
          </div>
          {analysisScope === 'single' && (
            <label className="analysis-run-switcher case-filter">
              <span>Case</span>
              <select
                value={singleCaseFilter}
                onChange={(event) => handleSingleCaseFilterChange(event.target.value)}
              >
                {SINGLE_CASE_FILTER_CONFIGS.map((config) => (
                  <option
                    key={config.id}
                    value={config.id}
                    disabled={config.id !== 'all' && !availableSingleCaseFilterIds.has(config.id)}
                  >
                    {config.label}
                  </option>
                ))}
              </select>
            </label>
          )}
          {analysisScope === 'multi' && (
            <label className="analysis-run-switcher case-filter">
              <span>Scenario</span>
              <select
                value={multiScenarioFilter}
                onChange={(event) => handleMultiScenarioFilterChange(event.target.value)}
              >
                {MULTI_SCENARIO_FILTER_CONFIGS.map((config) => (
                  <option
                    key={config.id}
                    value={config.id}
                    disabled={config.id !== 'all' && !availableMultiScenarioFilterIds.has(config.id)}
                  >
                    {config.label}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="analysis-run-switcher case-filter">
            <span>Mode</span>
            <select
              value={analysisControlModeFilter}
              onChange={(event) => handleAnalysisControlModeFilterChange(event.target.value)}
            >
              {RUN_CONTROL_MODE_FILTER_CONFIGS.map((config) => (
                <option
                  key={config.id}
                  value={config.id}
                  disabled={config.id !== 'all' && !availableAnalysisControlModeIds.has(config.id)}
                >
                  {config.label}
                </option>
              ))}
            </select>
          </label>
          <label className="analysis-run-switcher">
            <span>Run</span>
            <select
              value={analysisScopeRuns.some((run) => run.id === (activeRunId || selectedRunId)) ? (activeRunId || selectedRunId) : ''}
              onChange={(event) => handleAnalysisRunChange(event.target.value)}
              disabled={analysisScopeRuns.length === 0}
            >
              {analysisScopeRuns.length > 0 ? (
                analysisScopeRuns.map((run) => (
                  <option key={run.id} value={run.id}>
                    {formatScenarioRunOptionLabel(run)}
                  </option>
                ))
              ) : (
                <option value="">No runs are available for this scope</option>
              )}
            </select>
          </label>
        </div>

        <div className="analysis-view-controls">
          <div className={`page-switcher ${visibleAnalysisPages.length===1?'single-page':''}`}>
            {visibleAnalysisPages.map((page) => {
              const scenarioPageDisabled = page.id === 'scenario' && !activeRunHasScenarioAnalysis;
              return (
                <button
                  type="button"
                  key={page.id}
                  className={`page-tab ${currentPage === page.id ? 'active' : ''} ${scenarioPageDisabled ? 'disabled' : ''}`}
                  disabled={scenarioPageDisabled}
                  onClick={() => {
                    if(currentPage===page.id)return;
                    cancelViewReads();
                    if (page.id === 'scenario') {
                      const nextScenarioPage = detectScenarioPage(activeRunMeta || selectedRunMeta);
                      if (!nextScenarioPage) {
                        return;
                      }
                      setScenarioPage(nextScenarioPage);
                    }
                    setCurrentPage(page.id);
                  }}
                >
                  {scenarioPageDisabled ? 'Scenario Analysis Unavailable' : page.label}
                </button>
              );
            })}
          </div>
          <button className="mobile-controls-toggle" aria-expanded={runFiltersOpen} aria-controls="analysis-run-controls" onClick={()=>setRunFiltersOpen(v=>!v)}>{runFiltersOpen?'Hide run filters':'Show run filters'}</button>
        {analysisScope === 'multi' && <DisplayOptions activeCount={Number(productEquivalentMode)+Number(includeUpstreamExternalSupplierMode)+Number(includeDownstreamExternalMarketMode)}>
          <div className="analysis-external-toggle-stack">
            <label
              className={`analysis-quantity-toggle compact ${productEquivalentMode ? 'active' : ''}`}
            >
              <input
                type="checkbox"
                checked={productEquivalentMode}
                onChange={(event) => setProductEquivalentMode(event.target.checked)}
              />
              <span className="analysis-quantity-copy">
                <strong>Finished-Goods Equivalent</strong>
              </span>
              <span className="analysis-quantity-switch" aria-hidden="true">
                <span className="analysis-quantity-switch-knob" />
              </span>
            </label>

            <label
              className={`analysis-quantity-toggle medium ${includeUpstreamExternalSupplierMode ? 'active' : ''}`}
            >
              <input
                type="checkbox"
                checked={includeUpstreamExternalSupplierMode}
                onChange={(event) => setIncludeUpstreamExternalSupplierMode(event.target.checked)}
              />
              <span className="analysis-quantity-copy">
                <strong>External Upstream Supplier</strong>
              </span>
              <span className="analysis-quantity-switch" aria-hidden="true">
                <span className="analysis-quantity-switch-knob" />
              </span>
            </label>

            <label
              className={`analysis-quantity-toggle wide ${includeDownstreamExternalMarketMode ? 'active' : ''}`}
            >
              <input
                type="checkbox"
                checked={includeDownstreamExternalMarketMode}
                onChange={(event) => setIncludeDownstreamExternalMarketMode(event.target.checked)}
              />
              <span className="analysis-quantity-copy">
                <strong>External Downstream Market</strong>
              </span>
              <span className="analysis-quantity-switch" aria-hidden="true">
                <span className="analysis-quantity-switch-knob" />
              </span>
            </label>
          </div>
        </DisplayOptions>}
        </div>
      </div>

      <div className="main-content-wrapper" aria-busy={loading}>
        {loading?<ViewLoading label="Opening selected archive…"/>:error?<section className="view-error" role="alert"><h3>Archive unavailable</h3><p>{error}</p><button onClick={()=>{clearDataSourceCache();loadAnalysisData(activeRunId);}}>Retry archive</button><button onClick={handleBackToWelcome}>Back to Start</button></section>:<>
        <ViewBoundary resetKey={`${dataRoot}:${currentCompany}:${currentDay}`}><RunEvidencePanel dataRoot={dataRoot} company={currentCompany} round={availableDays.length ? currentDay : undefined} /></ViewBoundary>
        <ViewBoundary resetKey={`${dataRoot}:${currentPage}:${scenarioPage}:${currentCompany}:${currentDay}`}><React.Suspense fallback={<ViewLoading/>}>
        {currentPage === 'daily' ? (
          <div className="daily-page">
            {analysisScope === 'single' ? (
              <>
                <RoundEnterpriseBoard
                  enterpriseSpecs={activeEnterpriseSpecs}
                  currentCompany={currentCompany}
                  currentDay={currentDay}
                  dataRoot={dataRoot}
                  onCompanyChange={company=>{if(company!==currentCompany)cancelViewReads();setCurrentCompany(company);}}
                  companyNameMap={companyNameMap}
                  includeSingleCaseSeedActions
                />

                <div className="single-enterprise-workbench">
                  <aside className="single-workbench-left">
                    <div className="column-section compact-section">
                      <CompanySelector
                        companies={companies}
                        companyNameMap={companyNameMap}
                        currentCompany={currentCompany}
                        onCompanyChange={company=>{if(company!==currentCompany)cancelViewReads();setCurrentCompany(company);}}
                      />
                    </div>

                    <div className="column-section compact-section">
                      <h3>Enterprise Profile</h3>
                      <CompanyInfo company={currentCompany} day={currentDay} dataRoot={dataRoot} />
                    </div>

                    <div className="column-section compact-section ceo-compact-section">
                      <h3>Executive Analysis</h3>
                      <CEOAnalysis company={currentCompany} day={currentDay} dataRoot={dataRoot} />
                    </div>

                    <SingleEnterpriseActionList
                      company={currentCompany}
                      currentDay={currentDay}
                      dataRoot={dataRoot}
                    />
                  </aside>

                  <SingleEnterprisePerformanceView
                    availableDays={availableDays}
                    currentDay={currentDay}
                    company={currentCompany}
                    dataRoot={dataRoot}
                    viewMode={singlePerformanceMode}
                    onViewModeChange={(nextMode) => {
                      setSinglePerformanceMode(nextMode);
                      if (nextMode === 'trend' && availableDays.length > 0) {
                        setCurrentDay(availableDays[availableDays.length - 1]);
                      }
                    }}
                  />
                </div>
              </>
            ) : (
              <>
                <div className="daily-hero-layout">
                  <aside className="daily-side-panel daily-left-panel">
                    <div className="column-section compact-section">
                      <CompanySelector
                        companies={companies}
                        companyNameMap={companyNameMap}
                        currentCompany={currentCompany}
                        onCompanyChange={company=>{if(company!==currentCompany)cancelViewReads();setCurrentCompany(company);}}
                      />
                    </div>

                    <div className="column-section compact-section">
                      <h3>Enterprise Profile</h3>
                      <CompanyInfo company={currentCompany} day={currentDay} dataRoot={dataRoot} />
                    </div>

                    <div className="column-section compact-section ceo-compact-section">
                      <h3>Executive Analysis</h3>
                      <CEOAnalysis company={currentCompany} day={currentDay} dataRoot={dataRoot} />
                    </div>
                  </aside>

                  <section className="daily-topology-section">
                    <div className="daily-topology-header">
                      <div>
                        <strong>Supply-Chain Topology</strong>
                      </div>
                    </div>
                    <div className="topology-frame"><div className="chart-container daily-topology-chart" ref={chartRef}/>{topologyLoading&&<div className="topology-loading-overlay"><LoadingState label="Preparing enterprise network…" variant="compact"/></div>}</div>
                  </section>

                  <aside className="daily-side-panel daily-right-panel">
                    <ExchangeEnterprisePanel
                      day={currentDay}
                      coreCompany={currentCompany}
                      dataRoot={dataRoot}
                    />
                  </aside>
                </div>

                <RoundEnterpriseBoard
                  enterpriseSpecs={activeEnterpriseSpecs}
                  currentCompany={currentCompany}
                  currentDay={currentDay}
                  dataRoot={dataRoot}
                  onCompanyChange={company=>{if(company!==currentCompany)cancelViewReads();setCurrentCompany(company);}}
                  companyNameMap={companyNameMap}
                />

                <div className="daily-lower-layout">
                  <div className="column-section">
                    <h3>Exchange Activity</h3>
                    <ExchangeInfo
                      day={currentDay}
                      dataRoot={dataRoot}
                      quantityView={quantityView}
                      selectedExchangeId={selectedExchangeId}
                      onSelectExchange={setSelectedExchangeId}
                    />
                  </div>
                </div>
              </>
            )}


          </div>
        ) : currentPage === 'propagation' ? (
          <div className="propagation-page">
            <div className="flow-production-band">
              <div className="column-section">
                <h3>Material Flow and Production</h3>
                <FlowProductionView
                  availableDays={availableDays}
                  currentDay={currentDay}
                  dataRoot={dataRoot}
                  quantityView={quantityView}
                  enterpriseSpecs={activeEnterpriseSpecs}
                  includeUpstreamExternalSupplierMode={includeUpstreamExternalSupplierMode}
                  includeDownstreamExternalMarketMode={includeDownstreamExternalMarketMode}
                />
              </div>
            </div>

            <div className="column-section">
              <h3>Run Overview</h3>
              <RunSummary
                availableDays={availableDays}
                dataRoot={dataRoot}
                quantityView={quantityView}
                includeUpstreamExternalSupplierMode={includeUpstreamExternalSupplierMode}
                includeDownstreamExternalMarketMode={includeDownstreamExternalMarketMode}
                enterpriseSpecs={activeEnterpriseSpecs}
              />
            </div>

            <div className="demand-propagation-band">
              <div className="column-section">
                <DemandPropagationView
                  availableDays={availableDays}
                  currentDay={currentDay}
                  dataRoot={dataRoot}
                  quantityView={quantityView}
                  enterpriseSpecs={activeEnterpriseSpecs}
                />
              </div>
            </div>
          </div>
        ) : currentPage === 'scenario' ? (
          <div className="scenario-analysis-page">
            <section className="scenario-analysis-control">
              <div className="scenario-analysis-copy">
                <span>Scenario Analysis</span>
                <strong>{getScenarioPageConfig(scenarioPage).label}</strong>
                <small>{getScenarioPageConfig(scenarioPage).description}</small>
              </div>
              <div className="scenario-inner-tabs">
                {SCENARIO_ANALYSIS_PAGES.map((page) => {
                  const isDetected = detectScenarioPage(activeRunMeta || selectedRunMeta) === page.id;
                  return (
                    <button
                      type="button"
                      key={page.id}
                      className={`${scenarioPage === page.id ? 'active' : ''} ${isDetected ? 'detected' : ''}`}
                      onClick={() => handleScenarioPageChange(page.id)}
                    >
                      <strong>{page.label}</strong>
                      <small>{isDetected ? 'Current Run' : page.description}</small>
                    </button>
                  );
                })}
              </div>
            </section>

            {scenarioPage === 'evolution' ? (
              <EvolutionExperimentView
                availableDays={availableDays}
                dataRoot={dataRoot}
                enterpriseSpecs={activeEnterpriseSpecs}
              />
            ) : scenarioPage === 'bullwhip' ? (
              <BullwhipEffectView
                availableDays={availableDays}
                dataRoot={dataRoot}
                quantityView={quantityView}
                enterpriseSpecs={activeEnterpriseSpecs}
                includeUpstreamExternalSupplierMode={includeUpstreamExternalSupplierMode}
                includeDownstreamExternalMarketMode={includeDownstreamExternalMarketMode}
                currentRunId={activeRunMeta?.run_id || activeRunEntry?.id || activeRunId}
                currentRunSourceLabel={activeRunEntry?.sourceLabel || dataRoot}
              />
            ) : scenarioPage === 'cobweb' ? (
              <CobwebModelValidationView
                availableDays={availableDays}
                dataRoot={dataRoot}
                enterpriseSpecs={activeEnterpriseSpecs}
                currentRunId={activeRunMeta?.run_id || activeRunEntry?.id || activeRunId}
                currentRunSourceLabel={activeRunEntry?.sourceLabel || dataRoot}
              />
            ) : scenarioPage === 'commons' ? (
              <CommonsTragedyView
                availableDays={availableDays}
                dataRoot={dataRoot}
                enterpriseSpecs={activeEnterpriseSpecs}
                currentRunId={activeRunMeta?.run_id || activeRunEntry?.id || activeRunId}
                currentRunSourceLabel={activeRunEntry?.sourceLabel || dataRoot}
              />
            ) : (
              <HerdingEffectView
                availableDays={availableDays}
                dataRoot={dataRoot}
                enterpriseSpecs={activeEnterpriseSpecs}
                currentRunId={activeRunMeta?.run_id || activeRunEntry?.id || activeRunId}
                currentRunSourceLabel={activeRunEntry?.sourceLabel || dataRoot}
              />
            )}
          </div>
        ) : currentPage === 'operations' ? (
          <OperationalReviewView
            availableDays={availableDays}
            dataRoot={dataRoot}
            quantityView={quantityView}
            enterpriseSpecs={activeEnterpriseSpecs}
          />
        ) : (
          <GlobalSimulationView
            availableDays={availableDays}
            dataRoot={dataRoot}
            quantityView={quantityView}
            enterpriseSpecs={activeEnterpriseSpecs}
            includeUpstreamExternalSupplierMode={includeUpstreamExternalSupplierMode}
            includeDownstreamExternalMarketMode={includeDownstreamExternalMarketMode}
          />
        )}
        </React.Suspense></ViewBoundary>
        </>}
      </div>
      {currentPage === 'daily' && <DaySelector floating disabled={loading||Boolean(error)} availableDays={availableDays} currentDay={currentDay} onDayChange={round => {if(round!==currentDay)cancelViewReads();setCurrentDay(round); if(analysisScope==='single')setSinglePerformanceMode('snapshot');}} />}
    </div>
  );
}

export default App;
