import LoadingState from './LoadingState';
import {setReviewChart} from '../utils/chartReview';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import * as echarts from '../utils/echartsRuntime';
import { buildDataUrl, safeFetchJson } from '../utils/dataSource';
import { buildExternalFlowSeries } from '../utils/externalMarketFlows';

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

const formatMoney = (value) => `SCU ${formatNumber(value)}`;

const RunSummary = ({
  availableDays,
  currentDay = null,
  dataRoot,
  quantityView,
  enterpriseSpecs = [],
  includeUpstreamExternalSupplierMode = false,
  includeDownstreamExternalMarketMode = false,
}) => {
  const [exchangeRows, setExchangeRows] = useState([]);
  const [companyRows, setCompanyRows] = useState([]);
  const [externalRows, setExternalRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const chartRef = useRef(null);
  const chartInstanceRef = useRef(null);

  const latestDay = useMemo(
    () => (availableDays.length > 0 ? availableDays[availableDays.length - 1] : 0),
    [availableDays]
  );

  const effectiveDay = currentDay ?? latestDay;
  const companyIds = useMemo(
    () => enterpriseSpecs.map((spec) => spec.enterprise_id || spec.id).filter(Boolean),
    [enterpriseSpecs]
  );
  const companyNameMap = useMemo(
    () => Object.fromEntries(
      enterpriseSpecs.map((spec) => [
        spec.enterprise_id || spec.id,
        spec.enterprise_name || spec.name || spec.enterprise_id || spec.id,
      ])
    ),
    [enterpriseSpecs]
  );
  const topEnterpriseId = companyIds[0] || '';
  const bottomEnterpriseId = companyIds[companyIds.length - 1] || '';
  const topEnterpriseName = companyNameMap[topEnterpriseId] || topEnterpriseId || 'Top tier';
  const bottomEnterpriseName = companyNameMap[bottomEnterpriseId] || bottomEnterpriseId || 'Bottom tier';

  const daysToLoad = useMemo(
    () => availableDays.filter((day) => day <= effectiveDay),
    [availableDays, effectiveDay]
  );

  useEffect(() => {
    loadSummary();
  }, [daysToLoad.join(','), effectiveDay, dataRoot, includeUpstreamExternalSupplierMode, includeDownstreamExternalMarketMode, quantityView]);

  useEffect(() => {
    if (!chartRef.current || exchangeRows.length === 0) {
      return undefined;
    }

    if (chartInstanceRef.current) {
      chartInstanceRef.current.dispose();
    }

    chartInstanceRef.current = echarts.init(chartRef.current);
    const exchangeIds = [...new Set(exchangeRows.flatMap((row) => Object.keys(row.exchanges)))];
    const xAxisData = exchangeRows.map((row) => `Round ${row.day}`);
    const series = exchangeIds.flatMap((exchangeId) => [
      {
        name: `${exchangeId} Orders`,
        type: 'line',
        smooth: true,
        symbolSize: 5,
        data: exchangeRows.map((row) => row.exchanges[exchangeId]?.orders || 0)
      },
      {
        name: `${exchangeId} Proposals`,
        type: 'line',
        smooth: true,
        symbolSize: 5,
        lineStyle: { type: 'dashed' },
        data: exchangeRows.map((row) => row.exchanges[exchangeId]?.proposals || 0)
      }
    ]);

    if (includeUpstreamExternalSupplierMode) {
      series.push(
        {
          name: `Upstream Market → ${topEnterpriseName} Orders`,
          type: 'line',
          smooth: true,
          symbolSize: 5,
          data: externalRows.map((row) => row.upstreamCount || 0)
        }
      );
    }

    if (includeDownstreamExternalMarketMode) {
      series.push(
        {
          name: `${bottomEnterpriseName} → External Market Orders`,
          type: 'line',
          smooth: true,
          symbolSize: 5,
          data: externalRows.map((row) => row.downstreamCount || 0)
        }
      );
    }

    setReviewChart(chartInstanceRef.current, {
      tooltip: { trigger: 'axis' },
      legend: {
        type: 'scroll',
        bottom: 0,
        textStyle: { fontSize: 10 }
      },
      grid: {
        left: 32,
        right: 16,
        top: 24,
        bottom: 52
      },
      xAxis: {
        type: 'category',
        data: xAxisData
      },
      yAxis: {
        type: 'value',
        minInterval: 1
      },
      series
    });

    const handleResize = () => chartInstanceRef.current?.resize();
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      chartInstanceRef.current?.dispose();
      chartInstanceRef.current = null;
    };
  }, [exchangeRows, externalRows, includeUpstreamExternalSupplierMode, includeDownstreamExternalMarketMode]);

  const loadSummary = async () => {
    setLoading(true);

    const exchangeData = [];
    const nextExternalRows = [];
    for (const day of daysToLoad) {
      const [data, topProcurement, bottomSales] = await Promise.all([
        safeFetchJson(buildDataUrl(dataRoot, `public/exchange/day${day}/exchange.json`)),
        safeFetchJson(buildDataUrl(dataRoot, `enterprises/${topEnterpriseId}/department/procurement/day${day}/procurement.json`)),
        safeFetchJson(buildDataUrl(dataRoot, `enterprises/${bottomEnterpriseId}/department/sales/day${day}/sales.json`))
      ]);
      const exchanges = {};
      Object.entries(data?.data?.exchanges || {}).forEach(([exchangeId, exchange]) => {
        exchanges[exchangeId] = {
          orders: exchange.orders?.count || 0,
          proposals: exchange.proposals?.count || 0,
          orderQuantity: exchange.orders?.total_quantity || 0,
          orderValue: exchange.orders?.total_value || 0
        };
      });
      exchangeData.push({ day, exchanges });

      const externalFlows = buildExternalFlowSeries({
        availableDays: [day],
        quantityView,
        procurementState: topProcurement?.self_state || {},
        salesState: bottomSales?.self_state || {},
      });
      nextExternalRows.push({
        day,
        upstreamCount: externalFlows.upstreamCountSeries[0] || 0,
        downstreamCount: externalFlows.downstreamCountSeries[0] || 0,
      });
    }

    const companies = [];
    for (const company of companyIds) {
      const [finance, sales, inventory] = await Promise.all([
        safeFetchJson(buildDataUrl(dataRoot, `enterprises/${company}/department/finance/day${effectiveDay}/finance.json`)),
        safeFetchJson(buildDataUrl(dataRoot, `enterprises/${company}/department/sales/day${effectiveDay}/sales.json`)),
        safeFetchJson(buildDataUrl(dataRoot, `enterprises/${company}/department/inventory/day${effectiveDay}/inventory.json`))
      ]);

      companies.push({
        company,
        companyName: companyNameMap[company] || company,
        cash: finance?.self_state?.cash,
        revenue: finance?.self_state?.total_revenue,
        cost: finance?.self_state?.total_cost,
        orders: sales?.self_state?.sales_metrics?.total_orders,
        completedOrders: sales?.self_state?.sales_metrics?.completed_orders,
        inventoryUsed: inventory?.self_state?.used_capacity,
        inventoryCapacity: inventory?.self_state?.warehouse_capacity
      });
    }

    setExchangeRows(exchangeData);
    setExternalRows(nextExternalRows);
    setCompanyRows(companies);
    setLoading(false);
  };

  if (loading) {
    return <LoadingState label="Loading run overview…" variant="chart"/>;
  }

  return (
    <div className="run-summary">
      <div className="run-summary-chart" ref={chartRef} />
      <div className="company-kpi-table">
        {companyRows.map((row) => (
          <div className="company-kpi-row" key={row.company}>
            <strong>{row.companyName || row.company}</strong>
            <span>Cash {formatMoney(row.cash)}</span>
            <span>Revenue {formatMoney(row.revenue)}</span>
            <span>Orders {formatNumber(row.orders)} / Completed {formatNumber(row.completedOrders)}</span>
            <span>Inventory {formatNumber(row.inventoryUsed)} / {formatNumber(row.inventoryCapacity)}</span>
          </div>
        ))}
      </div>
    </div>
  );
};

export default RunSummary;
