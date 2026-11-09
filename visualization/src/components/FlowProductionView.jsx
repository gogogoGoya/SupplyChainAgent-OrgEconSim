import LoadingState from './LoadingState';
import {setReviewChart} from '../utils/chartReview';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import * as echarts from '../utils/echartsRuntime';
import {clearReadCache} from '../utils/readClient.mjs';
import { buildDataUrl, safeFetchJson } from '../utils/dataSource';
import {
  convertQuantityByItem,
  getDisplayItemLabel,
  getQuantityAxisLabel,
  isConvertibleRawMaterial,
} from '../utils/productEquivalent';
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

const groupByProduct = (orders) => {
  const grouped = {};
  orders.forEach((order) => {
    const key = `${order.seller_company_id}->${order.buyer_company_id}:${order.product_id}`;
    if (!grouped[key]) {
      grouped[key] = {
        source: order.seller_company_id,
        target: order.buyer_company_id,
        product: order.product_id,
        quantity: 0,
        originalQuantity: 0,
      };
    }
    grouped[key].quantity += Number(order.displayQuantity || 0);
    grouped[key].originalQuantity += Number(order.quantity || 0);
  });
  return Object.values(grouped);
};

const getDailyProduction = (currentProduction, previousProduction) => {
  const currentTotal = Number(currentProduction?.self_state?.production_metrics?.total_production || 0);
  const previousTotal = Number(previousProduction?.self_state?.production_metrics?.total_production || 0);
  return Math.max(0, currentTotal - previousTotal);
};

const toArray = (value) => {
  if (!value) {
    return [];
  }
  return Array.isArray(value) ? value : [value];
};

const buildFlowMeta = (enterpriseSpecs = []) => {
  const sorted = [...enterpriseSpecs]
    .filter((spec) => spec?.enterprise_id || spec?.id)
    .sort((left, right) => {
      const tierGap = Number(left?.tier ?? 0) - Number(right?.tier ?? 0);
      if (tierGap !== 0) {
        return tierGap;
      }
      return String(left?.enterprise_id || left?.id || '').localeCompare(String(right?.enterprise_id || right?.id || ''));
    });
  const companies = sorted.map((spec) => spec.enterprise_id || spec.id);
  const companyNameMap = Object.fromEntries(
    sorted.map((spec) => [
      spec.enterprise_id || spec.id,
      spec.enterprise_name || spec.name || spec.enterprise_id || spec.id
    ])
  );
  const exchangeEntries = companies.slice(0, -1).map((companyId, index) => ({
    exchangeId: `${index}-${index + 1}_exchange`,
    sourceId: companyId,
    targetId: companies[index + 1],
    label: `${companyNameMap[companyId] || companyId} → ${companyNameMap[companies[index + 1]] || companies[index + 1]}`,
  }));
  const productionSpec =
    sorted.find((spec) => toArray(spec?.enabled_functions).includes('production'))
    || sorted.find((spec) => toArray(spec?.role_tags).includes('finished_goods_manufacturer'))
    || null;
  return {
    companies,
    companyNameMap,
    exchangeEntries,
    topCompanyId: companies[0] || '',
    bottomCompanyId: companies[companies.length - 1] || '',
    topCompanyName: companyNameMap[companies[0]] || companies[0] || 'Top tier',
    bottomCompanyName: companyNameMap[companies[companies.length - 1]] || companies[companies.length - 1] || 'Bottom tier',
    productionNodeId: productionSpec?.enterprise_id || productionSpec?.id || '',
    productionNodeName: productionSpec?.enterprise_name || productionSpec?.name || productionSpec?.enterprise_id || productionSpec?.id || '',
  };
};

const FlowProductionView = ({
  availableDays,
  currentDay = null,
  dataRoot,
  quantityView,
  includeUpstreamExternalSupplierMode = false,
  includeDownstreamExternalMarketMode = false,
  enterpriseSpecs = [],
}) => {
  const [flowRows, setFlowRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error,setError]=useState('');const [reload,setReload]=useState(0);const generation=useRef(0);
  const sankeyRef = useRef(null);
  const trendRef = useRef(null);
  const sankeyInstanceRef = useRef(null);
  const trendInstanceRef = useRef(null);
  const flowMeta = useMemo(() => buildFlowMeta(enterpriseSpecs), [enterpriseSpecs]);

  const latestDay = useMemo(
    () => (availableDays.length > 0 ? availableDays[availableDays.length - 1] : 0),
    [availableDays]
  );

  const effectiveDay = currentDay ?? latestDay;

  const daysToLoad = useMemo(
    () => availableDays.filter((day) => day <= effectiveDay),
    [availableDays, effectiveDay]
  );

  useEffect(() => {
    const request=++generation.current;
    loadFlowData(request).catch(error=>{if(request===generation.current){setError(error.message);setLoading(false);}});
    return()=>{generation.current+=1;};
  }, [
    daysToLoad.join(','),
    effectiveDay,
    dataRoot,
    quantityView,
    includeUpstreamExternalSupplierMode,
    includeDownstreamExternalMarketMode,
    flowMeta.companies.join(','),
    reload,
  ]);

  useEffect(() => {
    if(loading)return;
    renderSankey();
    renderTrend();

    const handleResize = () => {
      sankeyInstanceRef.current?.resize();
      trendInstanceRef.current?.resize();
    };
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      sankeyInstanceRef.current?.dispose();trendInstanceRef.current?.dispose();
      sankeyInstanceRef.current=null;trendInstanceRef.current=null;
    };
  }, [loading,flowRows, effectiveDay, flowMeta, includeUpstreamExternalSupplierMode, includeDownstreamExternalMarketMode]);

  const loadFlowData = async (request) => {
    setLoading(true);setError('');
    const rows = [];

    for (const day of daysToLoad) {
      if(request!==generation.current)return;
      const [
        exchangeData,
        productionData,
        topProcurement,
        bottomSales,
        previousProductionData,
      ] = await Promise.all([
        safeFetchJson(buildDataUrl(dataRoot, `public/exchange/day${day}/exchange.json`)),
        flowMeta.productionNodeId
          ? safeFetchJson(buildDataUrl(dataRoot, `enterprises/${flowMeta.productionNodeId}/department/production/day${day}/production.json`))
          : null,
        flowMeta.topCompanyId
          ? safeFetchJson(buildDataUrl(dataRoot, `enterprises/${flowMeta.topCompanyId}/department/procurement/day${day}/procurement.json`))
          : null,
        flowMeta.bottomCompanyId
          ? safeFetchJson(buildDataUrl(dataRoot, `enterprises/${flowMeta.bottomCompanyId}/department/sales/day${day}/sales.json`))
          : null,
        day > 0 && flowMeta.productionNodeId
          ? safeFetchJson(buildDataUrl(dataRoot, `enterprises/${flowMeta.productionNodeId}/department/production/day${day - 1}/production.json`))
          : null,
      ]);
      const exchanges = exchangeData?.data?.exchanges || {};
      const currentOrders = Object.entries(exchanges).flatMap(([exchangeId, exchange]) => (
        (exchange.orders?.list || [])
          .filter((order) => Number(order.created_round) === Number(day))
          .map((order) => ({
            ...order,
            exchangeId,
            displayQuantity: convertQuantityByItem(order.quantity, order.product_id, quantityView),
          }))
      ));
      const externalFlows = buildExternalFlowSeries({
        availableDays: [day],
        quantityView,
        procurementState: topProcurement?.self_state || {},
        salesState: bottomSales?.self_state || {},
      });

      const dailyProduction = flowMeta.productionNodeId
        ? getDailyProduction(productionData, previousProductionData)
        : 0;
      const capacity = Number(productionData?.self_state?.production_lines?.total_capacity || 0);

      rows.push({
        day,
        currentOrders,
        groupedFlows: groupByProduct(currentOrders),
        upstreamExternalQuantity: externalFlows.upstreamQuantitySeries[0] || 0,
        downstreamExternalQuantity: externalFlows.downstreamQuantitySeries[0] || 0,
        upstreamExternalOrders: externalFlows.upstreamProcurementOrders,
        downstreamExternalOrders: externalFlows.downstreamSalesOrders,
        dailyProduction,
        cumulativeProduction: Number(productionData?.self_state?.production_metrics?.total_production || 0),
        capacity,
      });
    }

    if(request!==generation.current)return;
    setFlowRows(rows);
    setLoading(false);
  };

  const getCurrentRow = () => flowRows.find((row) => row.day === effectiveDay);

  const renderSankey = () => {
    if (!sankeyRef.current || flowRows.length === 0) {
      return;
    }

    if (sankeyInstanceRef.current) {
      sankeyInstanceRef.current.dispose();
    }
    sankeyInstanceRef.current = echarts.init(sankeyRef.current);

    const currentRow = getCurrentRow();
    const links = (currentRow?.groupedFlows || []).map((flow) => ({
      source: flowMeta.companyNameMap[flow.source] || flow.source,
      target: flowMeta.companyNameMap[flow.target] || flow.target,
      value: Math.max(flow.quantity, 0.01),
      product: flow.product,
      rawQuantity: flow.quantity,
      originalQuantity: flow.originalQuantity,
      converted: isConvertibleRawMaterial(flow.product, quantityView),
      lineStyle: {
        color: flow.product === 'beer' ? '#2f80ed' : '#8a6f3d',
        opacity: 0.55,
      },
    }));

    if (flowMeta.productionNodeId && (currentRow?.dailyProduction || 0) > 0) {
      links.push({
        source: flowMeta.productionNodeName,
        target: `${flowMeta.productionNodeName} Production`,
        value: currentRow.dailyProduction,
        product: 'produced_goods',
        rawQuantity: currentRow.dailyProduction,
        lineStyle: { color: '#21a67a', opacity: 0.6 },
      });
    }

    if (includeUpstreamExternalSupplierMode && (currentRow?.upstreamExternalQuantity || 0) > 0) {
      links.push({
        source: 'External Upstream',
        target: flowMeta.topCompanyName,
        value: Math.max(currentRow.upstreamExternalQuantity, 0.01),
        product: quantityView?.enabled ? `${quantityView.productId} equivalent` : 'external procurement',
        rawQuantity: currentRow.upstreamExternalQuantity,
        converted: Boolean(quantityView?.enabled),
        lineStyle: { color: '#3c8f5f', opacity: 0.55 },
      });
    }

    if (includeDownstreamExternalMarketMode && (currentRow?.downstreamExternalQuantity || 0) > 0) {
      links.push({
        source: flowMeta.bottomCompanyName,
        target: 'External Market',
        value: Math.max(currentRow.downstreamExternalQuantity, 0.01),
        product: 'beer',
        rawQuantity: currentRow.downstreamExternalQuantity,
        lineStyle: { color: '#d14d72', opacity: 0.55 },
      });
    }

    if(links.length===0){
      sankeyRef.current.setAttribute('role','img');
      sankeyRef.current.setAttribute('aria-label',`No recorded flow in R${effectiveDay}`);
      setReviewChart(sankeyInstanceRef.current,{
        title:{text:`No recorded flow in R${effectiveDay}`,subtext:'No order or production quantities\nin the selected flow view.\nChoose another round in Round View.',left:'center',top:'middle',
          itemGap:16,textStyle:{fontSize:15,lineHeight:24,color:'#475569'},subtextStyle:{fontSize:12,lineHeight:18,color:'#64748b'}},
        series:[],
      });
      return;
    }
    sankeyRef.current.setAttribute('role','img');
    sankeyRef.current.setAttribute('aria-label',`Order and production flow in R${effectiveDay}`);
    const nodeNames=[...new Set(links.flatMap(link=>[link.source,link.target]))];

    setReviewChart(sankeyInstanceRef.current, {
      tooltip: {
        trigger: 'item',
        formatter: (params) => {
          if (params.dataType === 'edge') {
            const productLabel = getDisplayItemLabel(params.data.product, quantityView);
            if (params.data.converted) {
              return `${params.data.source} → ${params.data.target}<br/>${productLabel}: ${formatNumber(params.data.rawQuantity)}<br/>Original quantity: ${formatNumber(params.data.originalQuantity)}`;
            }
            return `${params.data.source} → ${params.data.target}<br/>${productLabel}: ${formatNumber(params.data.rawQuantity)}`;
          }
          return params.name;
        },
      },
      series: [
        {
          type: 'sankey',
          nodeWidth: 18,
          nodeGap: 14,
          draggable: false,
          emphasis: { focus: 'adjacency' },
          label: { fontSize: 12 },
          data: nodeNames.map((name) => ({ name })),
          links,
        },
      ],
    });
  };

  const renderTrend = () => {
    if (!trendRef.current || flowRows.length === 0) {
      return;
    }

    if (trendInstanceRef.current) {
      trendInstanceRef.current.dispose();
    }
    trendInstanceRef.current = echarts.init(trendRef.current);

    const days = flowRows.map((row) => `Round ${row.day}`);
    const exchangeSeries = flowMeta.exchangeEntries.map(({ exchangeId, label }) => ({
      name: label,
      type: 'bar',
      stack: 'flow',
      data: flowRows.map((row) => row.currentOrders
        .filter((order) => order.exchangeId === exchangeId)
        .reduce((sum, order) => sum + Number(order.displayQuantity || 0), 0)),
    }));

    if (includeUpstreamExternalSupplierMode) {
      exchangeSeries.push({
        name: `External Upstream → ${flowMeta.topCompanyName}`,
        type: 'bar',
        stack: 'flow',
        data: flowRows.map((row) => row.upstreamExternalQuantity || 0),
      });
    }

    if (includeDownstreamExternalMarketMode) {
      exchangeSeries.push({
        name: `${flowMeta.bottomCompanyName} → External Market`,
        type: 'bar',
        stack: 'flow',
        data: flowRows.map((row) => row.downstreamExternalQuantity || 0),
      });
    }

    setReviewChart(trendInstanceRef.current, {
      tooltip: { trigger: 'axis' },
      legend: {
        type: 'scroll',
        bottom: 0,
        textStyle: { fontSize: 10 },
      },
      grid: {
        left: 36,
        right: 18,
        top: 28,
        bottom: 54,
      },
      xAxis: { type: 'category', data: days },
      yAxis: { type: 'value', name: getQuantityAxisLabel('Quantity', quantityView) },
      series: [
        ...exchangeSeries,
        ...(flowMeta.productionNodeId ? [{
          name: `${flowMeta.productionNodeName} daily production`,
          type: 'line',
          smooth: true,
          symbolSize: 6,
          data: flowRows.map((row) => row.dailyProduction),
          lineStyle: { width: 3, color: '#21a67a' },
          itemStyle: { color: '#21a67a' },
        }] : []),
      ],
    });
  };

  if(error)return <section className="view-error" role="alert"><h3>Flow review unavailable</h3><p>{error}</p><button onClick={()=>{clearReadCache();setReload(value=>value+1);}}>Retry flow review</button></section>;
  if (loading) {
    return <LoadingState label="Loading flow and production data…" variant="chart"/>;
  }

  const externalLabels = [
    includeUpstreamExternalSupplierMode ? 'upstream external procurement' : null,
    includeDownstreamExternalMarketMode ? 'downstream external sales' : null,
  ].filter(Boolean);

  return (
    <div className="flow-production-view">
      <div className="flow-chart-card">
        <div className="flow-card-header">
          <strong>{currentDay === null ? 'Final-Round Flow Snapshot' : 'Current-Round Flow'}</strong>
          <span>Round {effectiveDay}</span>
        </div>
        <div className="flow-sankey-chart" ref={sankeyRef} />
      </div>

      <div className="flow-chart-card">
        <div className="flow-card-header">
          <strong>{quantityView?.enabled ? 'Flow and Production Trends (Raw Materials as Finished-Goods Equivalents)' : 'Flow and Production Trends'}</strong>
          <span>{externalLabels.length > 0 ? `Order flow + ${externalLabels.join(' + ')}${flowMeta.productionNodeId ? ' + daily output' : ''}` : `Order flow${flowMeta.productionNodeId ? ' + daily output' : ''}`}</span>
        </div>
        <div className="flow-trend-chart" ref={trendRef} />
      </div>
    </div>
  );
};

export default FlowProductionView;
