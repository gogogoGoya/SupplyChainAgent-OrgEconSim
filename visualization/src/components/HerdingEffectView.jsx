import {enlargeSvgChart} from '../utils/chartZoom.mjs';
import LoadingState from './LoadingState';
import React, { useEffect, useMemo, useState } from 'react';
import { buildDataUrl, safeFetchJson } from '../utils/dataSource';

const ENTERPRISE_COLORS = {
  Sensor_A: '#2c6f7d',
  Sensor_B: '#d0862c',
  Sensor_C: '#bc4f38',
  Sensor_D: '#5f7d42',
};

const HERDING_COLORS = {
  heat: '#f59e0b',
  demand: '#0f172a',
  marketHeat: '#7c3aed',
  planned: '#a93f32',
  inventory: '#6f5d8f',
  sync: '#007c89',
  herding: '#e11d48',
  threshold: '#8a979d',
};

const SVG_WIDTH = 920;
const SVG_HEIGHT = 320;
const PADDING = { top: 34, right: 28, bottom: 44, left: 58 };

const toNumber = (value, fallback = null) => {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
};

const average = (values) => {
  const finite = values.map(Number).filter(Number.isFinite);
  if (finite.length === 0) {
    return null;
  }
  return finite.reduce((sum, value) => sum + value, 0) / finite.length;
};

const formatNumber = (value, digits = 2) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return '-';
  }
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: digits }).format(numeric);
};

const formatPercent = (value, digits = 1) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return '-';
  }
  return `${formatNumber(numeric * 100, digits)}%`;
};

const getEnterpriseId = (spec) => spec?.enterprise_id || spec?.id || spec?.name;

const normalizeMetricRow = (payload) => {
  const source = payload?.data || payload || {};
  const enterprises = Object.fromEntries(
    Object.entries(source.enterprises || {}).map(([enterpriseId, record]) => ([
      enterpriseId,
      {
        ...record,
        planned_quantity: toNumber(record?.planned_quantity, 0),
        ending_inventory: toNumber(record?.ending_inventory, 0),
        cash_balance: toNumber(record?.cash_balance, null),
      },
    ]))
  );

  return {
    ...source,
    round: toNumber(source.round, 0),
    true_demand_quantity: toNumber(source.true_demand_quantity, 0),
    visible_demand_signal: toNumber(source.visible_demand_signal, 0),
    market_heat: toNumber(source.market_heat, 0),
    total_planned_quantity: toNumber(source.total_planned_quantity, 0),
    overproduction_ratio: toNumber(source.overproduction_ratio, 0),
    overproduction_gap: toNumber(source.overproduction_gap, 0),
    synchronization_index: toNumber(source.synchronization_index, 0),
    herding_index: toNumber(source.herding_index, 0),
    inventory_pressure: toNumber(source.inventory_pressure, 0),
    peer_visibility_enabled: Boolean(source.peer_visibility_enabled),
    enterprises,
    externalDemandByEnterprise: {},
    salesByEnterprise: {},
  };
};

const loadMetricsForDays = async (dataRoot, availableDays) => {
  const rows = [];
  await Promise.all(availableDays.map(async (day) => {
    const payload = await safeFetchJson(
      buildDataUrl(dataRoot, `public/exchange/day${day}/end_of_day/herding_metrics.json`)
    ) || await safeFetchJson(
      buildDataUrl(dataRoot, `public/exchange/day${day}/herding_metrics.json`)
    );
    if (payload) {
      rows.push(normalizeMetricRow(payload));
    }
  }));
  return rows.sort((a, b) => Number(a.round) - Number(b.round));
};

const normalizeExternalDemandRow = (payload, day) => {
  const source = payload?.data || payload || {};
  const dayNumber = toNumber(day, day);
  const records = Array.isArray(source.history)
    ? source.history
    : Array.isArray(source.records)
      ? source.records
      : Array.isArray(source.demands)
        ? source.demands
        : [];
  const byEnterprise = {};

  records.forEach((record) => {
    const recordRound = toNumber(record?.round ?? record?.day, dayNumber);
    if (recordRound !== dayNumber) {
      return;
    }
    const enterpriseId = record?.enterprise_id || record?.enterpriseId || record?.seller_id || record?.target_enterprise_id;
    if (!enterpriseId) {
      return;
    }
    const existing = byEnterprise[enterpriseId] || {
      quantity: 0,
      plannedQuantity: 0,
      trueDemandQuantity: 0,
      visibleDemandSignal: 0,
      count: 0,
    };
    byEnterprise[enterpriseId] = {
      quantity: existing.quantity + toNumber(record?.quantity ?? record?.demand_quantity, 0),
      plannedQuantity: existing.plannedQuantity + toNumber(record?.planned_quantity, 0),
      trueDemandQuantity: existing.trueDemandQuantity + toNumber(record?.true_demand_quantity, 0),
      visibleDemandSignal: existing.visibleDemandSignal + toNumber(record?.visible_demand_signal, 0),
      count: existing.count + 1,
    };
  });

  return byEnterprise;
};

const loadExternalDemandForDays = async (dataRoot, availableDays) => {
  const byRound = {};
  await Promise.all(availableDays.map(async (day) => {
    const payload = await safeFetchJson(
      buildDataUrl(dataRoot, `public/exchange/day${day}/end_of_day/external_demand.json`)
    ) || await safeFetchJson(
      buildDataUrl(dataRoot, `public/exchange/day${day}/external_demand.json`)
    );
    if (payload) {
      byRound[day] = normalizeExternalDemandRow(payload, day);
    }
  }));
  return byRound;
};

const normalizeSalesRow = (payload) => {
  const source = payload?.data || payload || {};
  return {
    totalQuantitySold: toNumber(
      source.total_quantity_sold ?? source.total_sold_quantity ?? source.fulfilled_quantity,
      null
    ),
    backlogQuantity: toNumber(
      source.backlog_quantity ?? source.confirmed_order_backlog_quantity,
      null
    ),
    lostSalesQuantity: toNumber(source.lost_sales_quantity, null),
  };
};

const loadSalesForDays = async (dataRoot, availableDays, enterpriseIds) => {
  const byRound = {};
  await Promise.all(availableDays.map(async (day) => {
    const roundSales = {};
    await Promise.all(enterpriseIds.map(async (enterpriseId) => {
      const payload = await safeFetchJson(
        buildDataUrl(dataRoot, `enterprises/${enterpriseId}/department/sales/day${day}/sales.json`)
      );
      if (payload) {
        roundSales[enterpriseId] = normalizeSalesRow(payload);
      }
    }));
    byRound[day] = roundSales;
  }));
  return byRound;
};

const loadRunBundle = async (dataRoot, availableDays) => {
  const [runMeta, metrics, externalDemandByRound] = await Promise.all([
    safeFetchJson(buildDataUrl(dataRoot, 'run_meta.json')),
    loadMetricsForDays(dataRoot, availableDays),
    loadExternalDemandForDays(dataRoot, availableDays),
  ]);
  const enterpriseIds = Object.keys(metrics[0]?.enterprises || {});
  const salesByRound = await loadSalesForDays(dataRoot, availableDays, enterpriseIds);
  const enrichedMetrics = metrics.map((row) => ({
    ...row,
    externalDemandByEnterprise: externalDemandByRound[row.round] || {},
    salesByEnterprise: salesByRound[row.round] || {},
  }));
  return { runMeta, metrics: enrichedMetrics };
};

const getStrategyMap = (runMeta, enterpriseSpecs) => {
  const sources = [
    ...(enterpriseSpecs || []),
    ...(runMeta?.scenario_config?.enterprise_specs || []),
    ...(runMeta?.scenario_config?.enterprise_configs || []),
  ];
  return Object.fromEntries(
    sources
      .map((spec) => [getEnterpriseId(spec), spec?.strategy_profile || {}])
      .filter(([enterpriseId]) => Boolean(enterpriseId))
  );
};

const buildScale = (values, {
  min = null,
  max = null,
  floorZero = true,
  nice = true,
} = {}) => {
  const finite = values.map(Number).filter(Number.isFinite);
  if (finite.length === 0) {
    return { min: 0, max: 1 };
  }
  const rawMin = min ?? (floorZero ? Math.min(0, ...finite) : Math.min(...finite));
  const rawMax = max ?? Math.max(1, ...finite);
  if (!nice) {
    return { min: rawMin, max: rawMax || 1 };
  }
  const span = rawMax - rawMin;
  const padding = span === 0 ? Math.abs(rawMax) * 0.1 || 1 : span * 0.08;
  return {
    min: floorZero ? Math.max(0, rawMin - padding) : rawMin - padding,
    max: rawMax + padding,
  };
};

const createPointHelpers = (rows, scale) => {
  const width = SVG_WIDTH - PADDING.left - PADDING.right;
  const height = SVG_HEIGHT - PADDING.top - PADDING.bottom;
  const x = (index) => PADDING.left + (rows.length <= 1 ? width / 2 : (index / (rows.length - 1)) * width);
  const y = (value) => {
    const numeric = toNumber(value, 0);
    const range = scale.max - scale.min || 1;
    return PADDING.top + height - ((numeric - scale.min) / range) * height;
  };
  return { x, y, width, height };
};

const linePath = (points) => points
  .map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`)
  .join(' ');

const Axis = ({ rows, scale, formatter = (value) => formatNumber(value, 0) }) => {
  const { x, y, width, height } = createPointHelpers(rows, scale);
  const ticks = [scale.min, scale.min + (scale.max - scale.min) / 2, scale.max];
  const labelIndexes = rows.length <= 8
    ? rows.map((_, index) => index)
    : [0, Math.floor((rows.length - 1) / 2), rows.length - 1];

  return (
    <g className="herding-svg-axis">
      <line x1={PADDING.left} y1={PADDING.top + height} x2={PADDING.left + width} y2={PADDING.top + height} />
      <line x1={PADDING.left} y1={PADDING.top} x2={PADDING.left} y2={PADDING.top + height} />
      {ticks.map((tick) => (
        <g key={tick}>
          <line x1={PADDING.left} y1={y(tick)} x2={PADDING.left + width} y2={y(tick)} className="herding-svg-grid-line" />
          <text x={PADDING.left - 8} y={y(tick) + 4} textAnchor="end">{formatter(tick)}</text>
        </g>
      ))}
      {labelIndexes.map((index) => (
        <text key={index} x={x(index)} y={PADDING.top + height + 24} textAnchor="middle">
          D{rows[index]?.round}
        </text>
      ))}
    </g>
  );
};

const Legend = ({ items }) => (
  <div className="herding-svg-legend">
    {items.map((item) => (
      <span key={item.name}>
        <i style={{ background: item.color }} />
        {item.name}
      </span>
    ))}
  </div>
);

const EmptyChart = ({ message = 'No chart data available', detail = null }) => (
  <div className="herding-svg-empty">
    <strong>{message}</strong>
    {detail && <span>{detail}</span>}
  </div>
);

const LineSvgChart = ({
  rows,
  series,
  max = null,
  min = null,
  percent = false,
  floorZero = true,
  emptyMessage = 'No chart data available',
  emptyDetail = null,
}) => {
  if (!rows.length || !series.length) {
    return <EmptyChart message={emptyMessage} detail={emptyDetail} />;
  }
  const values = series.flatMap((entry) => rows.map((row) => entry.value(row)));
  const scale = buildScale(values, { min, max, floorZero, nice: max === null || min === null });
  const helpers = createPointHelpers(rows, scale);

  return (
    <div className="herding-svg-chart-wrap">
      <Legend items={series} />
      <div className="svg-review-frame"><button type="button" className="svg-expand-control" onClick={event=>enlargeSvgChart(event.currentTarget.parentElement,event.currentTarget)}>Enlarge chart</button><svg className="herding-svg-chart" viewBox={`0 0 ${SVG_WIDTH} ${SVG_HEIGHT}`} role="img">
        <Axis
          rows={rows}
          scale={scale}
          formatter={(value) => (percent ? formatPercent(value, 0) : formatNumber(value, 0))}
        />
        {series.map((entry) => {
          const points = rows.map((row, index) => ({
            x: helpers.x(index),
            y: helpers.y(entry.value(row)),
          }));
          return (
            <g key={entry.name}>
              <path
                d={linePath(points)}
                fill="none"
                stroke={entry.color}
                strokeWidth={entry.bold ? '4' : '3'}
                strokeDasharray={entry.dashed ? '7 6' : undefined}
                opacity={entry.opacity ?? 1}
              />
              {!entry.dashed && points.map((point, index) => (
                <circle key={index} cx={point.x} cy={point.y} r="3.6" fill={entry.color}>
                  <title>
                    {`${entry.name} D${rows[index].round}: ${percent ? formatPercent(entry.value(rows[index])) : formatNumber(entry.value(rows[index]), 2)}`}
                  </title>
                </circle>
              ))}
            </g>
          );
        })}
      </svg></div>
    </div>
  );
};

const BarLineSvgChart = ({ rows }) => {
  if (!rows.length) {
    return <EmptyChart />;
  }
  const values = rows.flatMap((row) => [
    row.total_planned_quantity,
    row.true_demand_quantity,
    row.visible_demand_signal,
  ]);
  const scale = buildScale(values);
  const helpers = createPointHelpers(rows, scale);
  const band = helpers.width / Math.max(rows.length, 1);
  const barWidth = Math.max(6, Math.min(26, band * 0.58));
  const chartBottom = PADDING.top + helpers.height;

  return (
    <div className="herding-svg-chart-wrap">
      <Legend
        items={[
          { name: 'Total Planned Quantity', color: HERDING_COLORS.planned },
          { name: 'Actual Demand', color: HERDING_COLORS.demand },
          { name: 'Visible Demand Signal', color: HERDING_COLORS.heat },
        ]}
      />
      <div className="svg-review-frame"><button type="button" className="svg-expand-control" onClick={event=>enlargeSvgChart(event.currentTarget.parentElement,event.currentTarget)}>Enlarge chart</button><svg className="herding-svg-chart" viewBox={`0 0 ${SVG_WIDTH} ${SVG_HEIGHT}`} role="img">
        <Axis rows={rows} scale={scale} />
        {rows.map((row, index) => {
          const y = helpers.y(row.total_planned_quantity);
          const height = chartBottom - y;
          return (
            <rect
              key={row.round}
              x={helpers.x(index) - barWidth / 2}
              y={y}
              width={barWidth}
              height={Math.max(0, height)}
              rx="4"
              fill={HERDING_COLORS.planned}
              opacity="0.72"
            >
              <title>{`D${row.round} total planned quantity: ${formatNumber(row.total_planned_quantity, 2)}`}</title>
            </rect>
          );
        })}
        {[
          { name: 'Actual Demand', color: HERDING_COLORS.demand, value: (row) => row.true_demand_quantity },
          { name: 'Visible Demand Signal', color: HERDING_COLORS.heat, value: (row) => row.visible_demand_signal, dashed: true },
        ].map((entry) => {
          const points = rows.map((row, index) => ({
            x: helpers.x(index),
            y: helpers.y(entry.value(row)),
          }));
          return (
            <path
              key={entry.name}
              d={linePath(points)}
              fill="none"
              stroke={entry.color}
              strokeWidth="3"
              strokeDasharray={entry.dashed ? '7 6' : undefined}
            />
          );
        })}
      </svg></div>
    </div>
  );
};

const EnterprisePlanChart = ({ rows, enterpriseIds }) => (
  <LineSvgChart
    rows={rows}
    series={enterpriseIds.map((enterpriseId) => ({
      name: enterpriseId,
      color: ENTERPRISE_COLORS[enterpriseId] || '#52616a',
      value: (row) => row?.enterprises?.[enterpriseId]?.planned_quantity,
    }))}
  />
);

const buildEnterpriseConvergenceRows = (metrics, enterpriseIds) => (
  metrics.map((row) => {
    const values = enterpriseIds.map((enterpriseId) => (
      Number(row?.enterprises?.[enterpriseId]?.planned_quantity || 0)
    ));
    const mean = average(values) ?? 0;
    const variance = average(values.map((value) => (value - mean) ** 2)) ?? 0;
    const coefficientVariation = mean > 0 ? Math.sqrt(variance) / mean : 0;
    const range = values.length > 0 ? Math.max(...values) - Math.min(...values) : 0;
    const rangeRatio = mean > 0 ? range / mean : 0;
    return {
      ...row,
      productionDifference: Math.max(0, Math.min(1, coefficientVariation)),
      productionSpread: Math.max(0, Math.min(1, rangeRatio / 2)),
    };
  })
);

const EnterpriseConvergenceChart = ({ rows, enterpriseIds }) => {
  const convergenceRows = buildEnterpriseConvergenceRows(rows, enterpriseIds);
  return (
    <LineSvgChart
      rows={convergenceRows}
      max={1}
      min={0}
      percent
      series={[
        {
          name: 'Production Dispersion',
          color: HERDING_COLORS.herding,
          value: (row) => row.productionDifference,
          bold: true,
        },
        {
          name: 'Production Range',
          color: HERDING_COLORS.heat,
          value: (row) => row.productionSpread,
          dashed: true,
        },
        {
          name: 'Behavioral Synchronization',
          color: HERDING_COLORS.sync,
          value: (row) => row.synchronization_index,
          bold: true,
        },
      ]}
    />
  );
};

const MiniOperationTrend = ({ rows, enterpriseId, enterpriseCount }) => {
  const hasSalesData = rows.some((row) => {
    const value = row?.salesByEnterprise?.[enterpriseId]?.totalQuantitySold;
    return value !== null && value !== undefined && Number.isFinite(Number(value));
  });
  const points = rows.map((row) => {
    const directDemand = row?.externalDemandByEnterprise?.[enterpriseId]?.quantity;
    return {
      round: row.round,
      planned: toNumber(row?.enterprises?.[enterpriseId]?.planned_quantity, 0),
      salesOrDemand: hasSalesData
        ? toNumber(row?.salesByEnterprise?.[enterpriseId]?.totalQuantitySold, 0)
        : toNumber(directDemand, Number(row.true_demand_quantity || 0) / Math.max(enterpriseCount, 1)),
      inventory: toNumber(row?.enterprises?.[enterpriseId]?.ending_inventory, 0),
    };
  });
  const series = [
    { key: 'planned', name: 'Production Plan', color: HERDING_COLORS.planned },
    { key: 'salesOrDemand', name: hasSalesData ? 'Completed Sales' : 'Demand Input', color: HERDING_COLORS.demand },
    { key: 'inventory', name: 'Ending Inventory', color: HERDING_COLORS.inventory },
  ];
  const values = points.flatMap((point) => series.map((entry) => point[entry.key]));
  const scale = buildScale(values);
  const width = 360;
  const height = 118;
  const padding = { top: 12, right: 12, bottom: 20, left: 34 };
  const innerWidth = width - padding.left - padding.right;
  const innerHeight = height - padding.top - padding.bottom;
  const x = (index) => padding.left + (points.length <= 1 ? innerWidth / 2 : (index / (points.length - 1)) * innerWidth);
  const y = (value) => padding.top + innerHeight - ((Number(value || 0) - scale.min) / (scale.max - scale.min || 1)) * innerHeight;
  const labelIndexes = points.length <= 6
    ? points.map((_, index) => index)
    : [0, Math.floor((points.length - 1) / 2), points.length - 1];

  return (
    <div className="herding-mini-trend">
      <div className="herding-mini-legend">
        {series.map((entry) => (
          <span key={entry.key}>
            <i style={{ background: entry.color }} />
            {entry.name}
          </span>
        ))}
      </div>
      <div className="svg-review-frame"><button type="button" className="svg-expand-control" onClick={event=>enlargeSvgChart(event.currentTarget.parentElement,event.currentTarget)}>Enlarge chart</button><svg viewBox={`0 0 ${width} ${height}`} role="img">
        <line x1={padding.left} y1={padding.top + innerHeight} x2={padding.left + innerWidth} y2={padding.top + innerHeight} />
        <line x1={padding.left} y1={padding.top} x2={padding.left} y2={padding.top + innerHeight} />
        {[scale.min, scale.max].map((tick) => (
          <g key={tick}>
            <line x1={padding.left} y1={y(tick)} x2={padding.left + innerWidth} y2={y(tick)} className="herding-mini-grid" />
            <text x={padding.left - 7} y={y(tick) + 4} textAnchor="end">{formatNumber(tick, 0)}</text>
          </g>
        ))}
        {series.map((entry) => {
          const path = linePath(points.map((point, index) => ({
            x: x(index),
            y: y(point[entry.key]),
          })));
          return (
            <path
              key={entry.key}
              d={path}
              fill="none"
              stroke={entry.color}
              strokeWidth={entry.key === 'planned' ? '3' : '2.3'}
              opacity={entry.key === 'inventory' ? 0.78 : 1}
            />
          );
        })}
        {labelIndexes.map((index) => (
          <text key={index} x={x(index)} y={height - 4} textAnchor="middle">D{points[index]?.round}</text>
        ))}
      </svg></div>
    </div>
  );
};

const EnterpriseOperationMatrix = ({ rows, enterpriseIds, strategyMap }) => {
  if (!rows.length || !enterpriseIds.length) {
    return <EmptyChart message="No enterprise operating data" />;
  }
  return (
    <div className="herding-operation-matrix">
      {enterpriseIds.map((enterpriseId) => {
        const finalRow = rows[rows.length - 1] || {};
        const cumulativePlanned = rows.reduce(
          (sum, row) => sum + Number(row?.enterprises?.[enterpriseId]?.planned_quantity || 0),
          0
        );
        const cumulativeDemand = rows.reduce((sum, row) => (
          sum + Number(
            row?.salesByEnterprise?.[enterpriseId]?.totalQuantitySold
            ?? row?.externalDemandByEnterprise?.[enterpriseId]?.quantity
            ?? 0
          )
        ), 0);
        const finalInventory = finalRow?.enterprises?.[enterpriseId]?.ending_inventory;
        const profile = strategyMap[enterpriseId] || {};
        return (
          <div className="herding-operation-card" key={enterpriseId}>
            <div className="herding-operation-head">
              <span>
                <i style={{ background: ENTERPRISE_COLORS[enterpriseId] || '#52616a' }} />
                {enterpriseId}
              </span>
              <strong>{profile?.label || profile?.profile_id || 'Strategy not specified'}</strong>
            </div>
            <MiniOperationTrend rows={rows} enterpriseId={enterpriseId} enterpriseCount={enterpriseIds.length} />
            <div className="herding-operation-values">
              <span>Cumulative production plan {formatNumber(cumulativePlanned, 0)}</span>
              <span>Cumulative sales/demand {formatNumber(cumulativeDemand, 0)}</span>
              <span>Ending inventory {formatNumber(finalInventory, 0)}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
};

const buildWindowStats = (rows, windows) => (
  windows.map((window) => {
    const selected = rows.filter((row) => row.round >= window.start && row.round <= window.end);
    return {
      ...window,
      herding: average(selected.map((row) => row.herding_index)),
      sync: average(selected.map((row) => row.synchronization_index)),
      overRatio: average(selected.map((row) => row.overproduction_ratio)),
      total: average(selected.map((row) => row.total_planned_quantity)),
      inventoryPressure: average(selected.map((row) => row.inventory_pressure)),
    };
  })
);

const buildEnterpriseBehaviorRows = (metrics, enterpriseIds, strategyMap) => {
  const totalPlannedAll = metrics.reduce((sum, row) => sum + Number(row.total_planned_quantity || 0), 0);
  return enterpriseIds.map((enterpriseId) => {
    const values = metrics.map((row) => Number(row?.enterprises?.[enterpriseId]?.planned_quantity || 0));
    const cumulativePlanned = values.reduce((sum, value) => sum + value, 0);
    const activeRounds = values.filter((value) => value > 0).length;
    const highOutputRounds = metrics.filter((row) => (
      Number(row?.enterprises?.[enterpriseId]?.planned_quantity || 0) > Number(row.true_demand_quantity || 0) / Math.max(enterpriseIds.length, 1)
    )).length;
    const avgDemandShareGap = average(metrics.map((row) => {
      const demandShare = Number(row.true_demand_quantity || 0) / Math.max(enterpriseIds.length, 1);
      return Number(row?.enterprises?.[enterpriseId]?.planned_quantity || 0) - demandShare;
    }));
    const profile = strategyMap[enterpriseId] || {};
    return {
      enterpriseId,
      profile,
      cumulativePlanned,
      averagePlanned: average(values),
      maxPlanned: Math.max(...values, 0),
      activeRounds,
      highOutputRounds,
      avgDemandShareGap,
      totalShare: totalPlannedAll > 0 ? cumulativePlanned / totalPlannedAll : 0,
      finalInventory: metrics[metrics.length - 1]?.enterprises?.[enterpriseId]?.ending_inventory,
    };
  }).sort((a, b) => Number(b.cumulativePlanned || 0) - Number(a.cumulativePlanned || 0));
};

const buildSummary = (metrics, enterpriseIds, behaviorRows) => {
  const windows = [
    { key: 'heatRise', label: 'Rising Attention', start: 3, end: 5 },
    { key: 'herding', label: 'Herding Window', start: 6, end: 9 },
    { key: 'late', label: 'Late Low-Attention Phase', start: 10, end: 16 },
  ];
  const windowStats = buildWindowStats(metrics, windows);
  const last = metrics[metrics.length - 1] || {};
  const leading = behaviorRows[0] || null;
  const cautious = behaviorRows.reduce((best, row) => (
    !best || Number(row.profile?.inventory_risk_sensitivity || 0) > Number(best.profile?.inventory_risk_sensitivity || 0)
      ? row
      : best
  ), null);

  return {
    roundCount: metrics.length,
    finalRound: last.round,
    productId: metrics[0]?.product_id || 'smart_sensor',
    peerVisible: metrics[0]?.peer_visibility_enabled === true,
    avgSync: average(metrics.slice(3).map((row) => row.synchronization_index)),
    avgHerding: average(metrics.slice(3).map((row) => row.herding_index)),
    maxOverRatio: Math.max(...metrics.map((row) => Number(row.overproduction_ratio || 0)), 0),
    overProductionRounds: metrics.filter((row) => Number(row.overproduction_ratio || 0) > 1.15).length,
    finalInventoryPressure: last.inventory_pressure,
    windowStats,
    leading,
    cautious,
    enterpriseCount: enterpriseIds.length,
  };
};

const StrategySensitivityBars = ({ profile }) => {
  const items = [
    ['Market Attention', profile?.market_heat_sensitivity],
    ['Peer Signal', profile?.peer_signal_sensitivity],
    ['Inventory Risk', profile?.inventory_risk_sensitivity],
    ['Cash Risk', profile?.cash_risk_sensitivity],
  ];
  return (
    <div className="herding-sensitivity-bars">
      {items.map(([label, value]) => (
        <div className="herding-sensitivity-row" key={label}>
          <span>{label}</span>
          <b>
            <i style={{ width: `${Math.max(2, Math.min(1, Number(value || 0)) * 100)}%` }} />
          </b>
          <em>{formatNumber(value, 2)}</em>
        </div>
      ))}
    </div>
  );
};

const StrategyBehaviorPanel = ({ rows }) => {
  if (!rows.length) {
    return <EmptyChart message="No enterprise strategy data" detail="The run metadata does not contain strategy_profile." />;
  }
  const maxPlanned = Math.max(...rows.map((row) => Number(row.cumulativePlanned || 0)), 1);
  return (
    <div className="herding-strategy-panel">
      {rows.map((row) => (
        <div className="herding-strategy-card" key={row.enterpriseId}>
          <div className="herding-strategy-head">
            <span>
              <i style={{ background: ENTERPRISE_COLORS[row.enterpriseId] || '#52616a' }} />
              {row.enterpriseId}
            </span>
            <strong>{row.profile?.label || row.profile?.profile_id || 'Strategy not specified'}</strong>
          </div>
          <StrategySensitivityBars profile={row.profile} />
          <div className="herding-strategy-output">
            <div className="herding-strategy-track">
              <b
                style={{
                  width: `${Math.max(3, (Number(row.cumulativePlanned || 0) / maxPlanned) * 100)}%`,
                  background: ENTERPRISE_COLORS[row.enterpriseId] || '#52616a',
                }}
              />
            </div>
            <div className="herding-strategy-values">
              <span>Cumulative plan {formatNumber(row.cumulativePlanned, 0)}</span>
              <span>Peak {formatNumber(row.maxPlanned, 0)}</span>
              <span>Above demand share in {row.highOutputRounds} turns</span>
              <span>Ending inventory {formatNumber(row.finalInventory, 0)}</span>
            </div>
          </div>
          {row.profile?.decision_note && (
            <p>{row.profile.decision_note}</p>
          )}
        </div>
      ))}
    </div>
  );
};

const EnterpriseContributionRecords = ({ rows }) => {
  if (!rows.length) {
    return null;
  }
  return (
    <section className="herding-contribution-records" aria-label="Cumulative expansion contribution by enterprise">
      <div className="herding-records-title">
        <span>Cumulative Expansion Contribution by Enterprise</span>
        <small>Derived from each enterprise's per-turn production plans to identify which strategy contributes most to expansion.</small>
      </div>
      <div className="herding-record-grid">
        {rows.map((row) => (
          <div className="herding-record-card" key={row.enterpriseId}>
            <div className="herding-record-head">
              <span>
                <i style={{ background: ENTERPRISE_COLORS[row.enterpriseId] || '#52616a' }} />
                {row.enterpriseId}
              </span>
              <strong>{formatPercent(row.totalShare)}</strong>
            </div>
            <dl>
              <dt>Cumulative Plan</dt>
              <dd>{formatNumber(row.cumulativePlanned, 0)}</dd>
              <dt>Mean Plan</dt>
              <dd>{formatNumber(row.averagePlanned, 1)}</dd>
              <dt>Demand-Share Gap</dt>
              <dd className={Number(row.avgDemandShareGap || 0) > 0 ? 'warning' : ''}>
                {formatNumber(row.avgDemandShareGap, 1)}
              </dd>
              <dt>Ending Inventory</dt>
              <dd>{formatNumber(row.finalInventory, 0)}</dd>
            </dl>
            <small>{row.profile?.label || row.profile?.profile_id || 'Strategy not specified'}</small>
          </div>
        ))}
      </div>
    </section>
  );
};

const WindowStatsPanel = ({ rows }) => (
  <div className="herding-window-panel">
    {rows.map((row) => (
      <div className="herding-window-card" key={row.key}>
        <div>
          <span>{row.label}</span>
          <strong>D{row.start}-D{row.end}</strong>
        </div>
        <dl>
          <dt>Synchronization</dt>
          <dd>{formatNumber(row.sync, 3)}</dd>
          <dt>Herding Index</dt>
          <dd>{formatNumber(row.herding, 3)}</dd>
          <dt>Overproduction Ratio</dt>
          <dd>{formatNumber(row.overRatio, 2)}</dd>
          <dt>Mean Total Plan</dt>
          <dd>{formatNumber(row.total, 1)}</dd>
        </dl>
      </div>
    ))}
  </div>
);

const EnterpriseHeatTable = ({ rows, enterpriseIds }) => {
  if (!rows.length || !enterpriseIds.length) {
    return <EmptyChart message="No enterprise planning data" />;
  }
  const maxPlan = Math.max(
    ...rows.flatMap((row) => enterpriseIds.map((enterpriseId) => Number(row?.enterprises?.[enterpriseId]?.planned_quantity || 0))),
    1
  );
  return (
    <div className="herding-table-wrapper">
      <table className="herding-table">
        <thead>
          <tr>
            <th>Round</th>
            {enterpriseIds.map((enterpriseId) => (
              <th key={enterpriseId}>{enterpriseId}</th>
            ))}
            <th>Synchronization</th>
            <th>Total Plan / Actual Demand</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.round}>
              <td>D{row.round}</td>
              {enterpriseIds.map((enterpriseId) => {
                const value = Number(row?.enterprises?.[enterpriseId]?.planned_quantity || 0);
                const alpha = 0.12 + (value / maxPlan) * 0.7;
                return (
                  <td key={enterpriseId}>
                    <span
                      className="herding-heat-cell"
                      style={{
                        background: `rgba(31, 111, 122, ${alpha})`,
                        color: alpha > 0.5 ? '#fff' : '#1c2d34',
                      }}
                    >
                      {formatNumber(value, 0)}
                    </span>
                  </td>
                );
              })}
              <td className={row.synchronization_index >= 0.72 ? 'herding-strong-cell' : ''}>
                {formatNumber(row.synchronization_index, 3)}
              </td>
              <td className={row.overproduction_ratio > 1.15 ? 'herding-warning-cell' : ''}>
                {formatNumber(row.overproduction_ratio, 2)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

function HerdingEffectView({
  availableDays = [],
  dataRoot,
  enterpriseSpecs = [],
  currentRunId,
  currentRunSourceLabel,
}) {
  const [bundle, setBundle] = useState({ runMeta: null, metrics: [] });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const days = useMemo(() => (
    availableDays.length > 0 ? availableDays : Array.from({ length: 18 }, (_, index) => index)
  ), [availableDays]);

  useEffect(() => {
    let cancelled = false;
    const loadData = async () => {
      setLoading(true);
      setError(null);
      try {
        const current = await loadRunBundle(dataRoot, days);
        if (!cancelled) {
          setBundle(current);
        }
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError.message || 'Failed to load herding-effect data');
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };
    loadData();
    return () => {
      cancelled = true;
    };
  }, [days, dataRoot]);

  const enterpriseIds = useMemo(() => {
    const fromSpecs = enterpriseSpecs.map(getEnterpriseId).filter(Boolean);
    if (fromSpecs.length > 0) {
      return fromSpecs;
    }
    return Object.keys(bundle.metrics[0]?.enterprises || {});
  }, [enterpriseSpecs, bundle.metrics]);

  const strategyMap = useMemo(
    () => getStrategyMap(bundle.runMeta, enterpriseSpecs),
    [bundle.runMeta, enterpriseSpecs]
  );

  const behaviorRows = useMemo(
    () => buildEnterpriseBehaviorRows(bundle.metrics, enterpriseIds, strategyMap),
    [bundle.metrics, enterpriseIds, strategyMap]
  );

  const summary = useMemo(
    () => buildSummary(bundle.metrics, enterpriseIds, behaviorRows),
    [bundle.metrics, enterpriseIds, behaviorRows]
  );

  const scenarioId = bundle.runMeta?.scenario_id || bundle.runMeta?.scenario_config?.meta?.scenario_id;
  const herdingConfig = bundle.runMeta?.scenario_config?.simulation?.herding_config || {};
  const peerVisibleLabel = summary.peerVisible ? 'Peer Summary Visible' : 'Market Attention Only';

  if (loading) {
    return <LoadingState label="Loading herding-effect experiment data…" variant="chart"/>;
  }

  if (error) {
    return <div className="herding-empty-state warning">{error}</div>;
  }

  if (!bundle.metrics.length) {
    return (
      <div className="herding-empty-state warning">
        The current run does not contain `herding_metrics.json`. Select a herding_market scenario.
      </div>
    );
  }

  return (
    <div className="herding-page">
      <section className="herding-hero">
        <div className="herding-hero-copy">
          <span className="herding-eyebrow">Herding Effect</span>
          <h2>Herding-Effect Run Analysis</h2>
          <p>
            This view analyzes the selected run's market signals, production synchronization, overproduction, and strategy heterogeneity. Evidence of herding combines peer-signal visibility, persistent synchronization after attention declines, and differentiated expansion across enterprise strategies.
          </p>
          <div className="herding-source-strip">
            <span>Current run: {currentRunId || bundle.runMeta?.run_id || '-'}</span>
            <span>Data source: {currentRunSourceLabel || dataRoot}</span>
            <span>Scenario: {scenarioId || '-'}</span>
            <span>Product: {herdingConfig.product_id || summary.productId}</span>
            <span>Peer signal: {peerVisibleLabel}</span>
          </div>
        </div>
        <aside className="herding-proof-card">
          <span>Run Summary</span>
          <strong>{summary.peerVisible ? 'Herding Formation Observable' : 'Peer-Hidden Reference'}</strong>
          <p>
            Mean post-D3 synchronization is {formatNumber(summary.avgSync, 3)}, and mean herding index is {formatNumber(summary.avgHerding, 3)}. Overproduction occurs in {summary.overProductionRounds}/{summary.roundCount} turns, with a maximum ratio of {formatNumber(summary.maxOverRatio, 2)}.
          </p>
        </aside>
      </section>

      <section className="herding-kpi-grid">
        <div className="herding-kpi-card accent">
          <span>Current Scenario Type</span>
          <strong>{peerVisibleLabel}</strong>
          <small>{scenarioId || '-'}</small>
        </div>
        <div className="herding-kpi-card">
          <span>Mean Synchronization after D3</span>
          <strong>{formatNumber(summary.avgSync, 3)}</strong>
          <small>Values above 0.72 indicate strong convergence</small>
        </div>
        <div className="herding-kpi-card">
          <span>Mean Herding Index after D3</span>
          <strong>{formatNumber(summary.avgHerding, 3)}</strong>
          <small>Combines synchronization, attention, and overproduction</small>
        </div>
        <div className="herding-kpi-card warning">
          <span>Overproduction Turns</span>
          <strong>{summary.overProductionRounds}/{summary.roundCount}</strong>
          <small>Threshold: total plan / actual demand &gt; 1.15</small>
        </div>
        <div className="herding-kpi-card">
          <span>Leading Expansion Enterprise</span>
          <strong>{summary.leading?.enterpriseId || '-'}</strong>
          <small>Cumulative plan {formatNumber(summary.leading?.cumulativePlanned, 0)}</small>
        </div>
        <div className="herding-kpi-card accent">
          <span>Highest Risk Sensitivity</span>
          <strong>{summary.cautious?.enterpriseId || '-'}</strong>
          <small>{summary.cautious?.profile?.label || 'Strategy not specified'}</small>
        </div>
      </section>

      <EnterpriseContributionRecords rows={behaviorRows} />

      <section className="herding-layout">
        <article className="herding-chart-card evidence-card">
          <div className="herding-card-title">Market Context: Actual Demand and Visible Signals</div>
          <p className="herding-card-note">
            Actual demand, the signal visible to agents, and market attention provide the external reference for the enterprise-level trajectories.
          </p>
          <LineSvgChart
            rows={bundle.metrics}
            series={[
              { name: 'Actual Demand', color: HERDING_COLORS.demand, value: (row) => row.true_demand_quantity, bold: true },
              { name: 'Visible Demand Signal', color: HERDING_COLORS.heat, value: (row) => row.visible_demand_signal, dashed: true },
              { name: 'Market Attention ×100', color: HERDING_COLORS.marketHeat, value: (row) => Number(row.market_heat || 0) * 100, opacity: 0.82 },
            ]}
          />
        </article>

        <article className="herding-chart-card evidence-card">
          <div className="herding-card-title">Enterprise Production–Sales–Inventory Differences</div>
          <p className="herding-card-note">
            Each panel traces one enterprise's production plan, completed sales or demand input, and ending inventory to reveal strategy-dependent operating trajectories.
          </p>
          <EnterpriseOperationMatrix rows={bundle.metrics} enterpriseIds={enterpriseIds} strategyMap={strategyMap} />
        </article>

        <article className="herding-chart-card">
          <div className="herding-card-title">Synchronization and Herding Index</div>
          <p className="herding-card-note">
            Synchronization measures convergence in enterprise plans; the herding index combines synchronized expansion, market attention, and overproduction.
          </p>
          <LineSvgChart
            rows={bundle.metrics}
            max={1}
            min={0}
            percent
            series={[
              { name: 'Synchronization', color: HERDING_COLORS.sync, value: (row) => row.synchronization_index, bold: true },
              { name: 'Herding Index', color: HERDING_COLORS.herding, value: (row) => row.herding_index },
              { name: 'Synchronization Threshold 0.72', color: HERDING_COLORS.threshold, value: () => 0.72, dashed: true },
            ]}
          />
        </article>

        <article className="herding-chart-card">
          <div className="herding-card-title">Path from Heterogeneity to Convergence</div>
          <p className="herding-card-note">
            Production-plan dispersion and behavioral synchronization jointly reveal whether initially heterogeneous firms converge over time.
          </p>
          <EnterpriseConvergenceChart rows={bundle.metrics} enterpriseIds={enterpriseIds} />
        </article>

        <article className="herding-chart-card">
          <div className="herding-card-title">Phase-Window Metrics</div>
          <p className="herding-card-note">
            Rising attention, the herding window, and the late low-attention phase distinguish transient common signals from persistent behavioral inertia.
          </p>
          <WindowStatsPanel rows={summary.windowStats} />
        </article>

        <article className="herding-chart-card">
          <div className="herding-card-title">Enterprise Production Plans</div>
          <p className="herding-card-note">
            Converging curves indicate similar behavior; divergence reflects differentiated responses to strategy, inventory, or risk constraints.
          </p>
          <EnterprisePlanChart rows={bundle.metrics} enterpriseIds={enterpriseIds} />
        </article>

        <article className="herding-chart-card wide">
          <div className="herding-card-title">Strategy Profiles vs. Observed Production</div>
          <p className="herding-card-note">
            Configured strategy preferences are compared with observed planning behavior to assess whether profiles produce distinct enterprise responses.
          </p>
          <StrategyBehaviorPanel rows={behaviorRows} />
        </article>

        <article className="herding-chart-card">
          <div className="herding-card-title">Inventory Pressure and Overproduction</div>
          <p className="herding-card-note">
            A rise in inventory pressure after overproduction indicates that synchronized expansion has propagated into operating outcomes.
          </p>
          <LineSvgChart
            rows={bundle.metrics}
            series={[
              { name: 'Overproduction Ratio', color: HERDING_COLORS.planned, value: (row) => row.overproduction_ratio },
              { name: 'Inventory Pressure', color: HERDING_COLORS.inventory, value: (row) => row.inventory_pressure },
              { name: 'Overproduction Threshold 1.15', color: HERDING_COLORS.threshold, value: () => 1.15, dashed: true },
            ]}
          />
        </article>

        <article className="herding-chart-card">
          <div className="herding-card-title">Per-Round Enterprise Plan Heatmap</div>
          <p className="herding-card-note">
            Darker cells indicate larger plans; synchronization and overproduction highlights locate the herding window.
          </p>
          <EnterpriseHeatTable rows={bundle.metrics} enterpriseIds={enterpriseIds} />
        </article>
      </section>
    </div>
  );
}

export default HerdingEffectView;
