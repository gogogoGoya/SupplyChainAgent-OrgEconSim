import LoadingState from './LoadingState';
import React, { useEffect, useMemo, useState } from 'react';
import { buildDataUrl, safeFetchJson } from '../utils/dataSource';
import { convertQuantityByItem, getDisplayItemLabel, getQuantityColumnLabel } from '../utils/productEquivalent';

const SIGNAL_ACTIONS = new Set(['adjust_sales_demand', 'create_replenishment_order', 'create_purchase_demand']);

const toArray = (value) => {
  if (!value) {
    return [];
  }
  if (Array.isArray(value)) {
    return value;
  }
  return [value];
};

const formatNumber = (value) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return '-';
  }
  return Number(value).toLocaleString('en-US', { maximumFractionDigits: 2 });
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
  const companyIndexMap = Object.fromEntries(companies.map((company, index) => [company, index]));
  return { companies, companyNameMap, companyIndexMap };
};

const getLaneLabel = (signal, flowMeta) => {
  const index = flowMeta.companyIndexMap[signal.company];
  if (!Number.isFinite(index)) {
    return signal.company;
  }

  if (signal.actionType === 'adjust_sales_demand') {
    if (index >= flowMeta.companies.length - 1) {
      return `${flowMeta.companyNameMap[signal.company] || signal.company} -> External`;
    }
    const downstreamId = flowMeta.companies[index + 1];
    return `${flowMeta.companyNameMap[signal.company] || signal.company} -> ${flowMeta.companyNameMap[downstreamId] || downstreamId}`;
  }

  if (index <= 0) {
    return `${flowMeta.companyNameMap[signal.company] || signal.company} -> External`;
  }
  const upstreamId = flowMeta.companies[index - 1];
  return `${flowMeta.companyNameMap[signal.company] || signal.company} -> ${flowMeta.companyNameMap[upstreamId] || upstreamId}`;
};

const getSignalKindLabel = (actionType) => {
  if (actionType === 'adjust_sales_demand') return 'Sales Supply';
  if (actionType === 'create_replenishment_order') return 'Replenishment Request';
  if (actionType === 'create_purchase_demand') return 'Manual Procurement';
  return actionType;
};

const getSignalQuantity = (entry) => (
  entry?.data?.requested_quantity
  ?? entry?.data?.quantity
  ?? entry?.params?.quantity
  ?? 0
);

const getSignalItemId = (entry) => (
  entry?.data?.material_id
  ?? entry?.params?.material_id
  ?? entry?.data?.product_id
  ?? entry?.params?.product_id
  ?? '-'
);

const normalizeSignalEntry = ({ company, day, phase, dept, entry }) => {
  const actionType = entry?.action_type;
  if (!SIGNAL_ACTIONS.has(actionType)) {
    return null;
  }

  const requestId = entry?.data?.request_id || null;
  const requestCreated = actionType === 'create_replenishment_order'
    ? Boolean(entry?.data?.created_request)
    : Boolean(requestId);

  return {
    id: `${company}-${day}-${phase}-${dept}-${actionType}-${requestId || getSignalItemId(entry)}`,
    company,
    day,
    phase,
    dept,
    actionType,
    kindLabel: getSignalKindLabel(actionType),
    itemId: getSignalItemId(entry),
    quantity: getSignalQuantity(entry),
    requestId,
    requestCreated,
    message: entry?.message || '',
    params: entry?.params || {},
    data: entry?.data || {},
  };
};

const buildStatusSummary = (items, key = 'status') => {
  return items.reduce((summary, item) => {
    const status = item?.[key] || 'unknown';
    summary[status] = (summary[status] || 0) + 1;
    return summary;
  }, {});
};

const DemandPropagationView = ({
  availableDays = [],
  currentDay = null,
  dataRoot,
  quantityView,
  enterpriseSpecs = [],
}) => {
  const [signals, setSignals] = useState([]);
  const [exchangeSnapshot, setExchangeSnapshot] = useState(null);
  const [loading, setLoading] = useState(true);
  const flowMeta = useMemo(() => buildFlowMeta(enterpriseSpecs), [enterpriseSpecs]);

  useEffect(() => {
    let alive = true;

    const loadData = async () => {
      setLoading(true);
      const latestDay = availableDays[availableDays.length - 1];
      const signalRows = [];

      for (const company of flowMeta.companies) {
        for (const day of availableDays) {
          const resultFiles = [
            { dept: 'sales', phase: 'Pre-execution', file: 'pre_sales_result.json' },
            { dept: 'sales', phase: 'Execution', file: 'sales_result.json' },
            { dept: 'procurement', phase: 'Pre-execution', file: 'pre_procurement_result.json' },
            { dept: 'procurement', phase: 'Execution', file: 'procurement_result.json' },
          ];

          for (const { dept, phase, file } of resultFiles) {
            const resultPayload = await safeFetchJson(
              buildDataUrl(dataRoot, `enterprises/${company}/department/${dept}/day${day}/${file}`)
            );
            const successRows = toArray(resultPayload?.result?.success);
            for (const entry of successRows) {
              const normalized = normalizeSignalEntry({ company, day, phase, dept, entry });
              if (normalized) {
                signalRows.push(normalized);
              }
            }
          }
        }
      }

      const exchangeData = latestDay === undefined
        ? null
        : await safeFetchJson(buildDataUrl(dataRoot, `public/exchange/day${latestDay}/exchange.json`));

      if (!alive) {
        return;
      }

      setSignals(signalRows);
      setExchangeSnapshot(exchangeData?.data?.exchanges || {});
      setLoading(false);
    };

    loadData();

    return () => {
      alive = false;
    };
  }, [availableDays.join(','), dataRoot, flowMeta.companies.join(',')]);

  const linkMaps = useMemo(() => {
    const proposalsByBuyRequest = new Map();
    const proposalsBySellRequest = new Map();
    const ordersByProposal = new Map();

    for (const exchange of Object.values(exchangeSnapshot || {})) {
      for (const proposal of exchange?.proposals?.list || []) {
        if (proposal?.buy_request_id) {
          if (!proposalsByBuyRequest.has(proposal.buy_request_id)) {
            proposalsByBuyRequest.set(proposal.buy_request_id, []);
          }
          proposalsByBuyRequest.get(proposal.buy_request_id).push(proposal);
        }
        if (proposal?.sell_request_id) {
          if (!proposalsBySellRequest.has(proposal.sell_request_id)) {
            proposalsBySellRequest.set(proposal.sell_request_id, []);
          }
          proposalsBySellRequest.get(proposal.sell_request_id).push(proposal);
        }
      }

      for (const order of exchange?.orders?.list || []) {
        if (!order?.proposal_id) continue;
        if (!ordersByProposal.has(order.proposal_id)) {
          ordersByProposal.set(order.proposal_id, []);
        }
        ordersByProposal.get(order.proposal_id).push(order);
      }
    }

    return { proposalsByBuyRequest, proposalsBySellRequest, ordersByProposal };
  }, [exchangeSnapshot]);

  const enrichedSignals = useMemo(() => {
    return signals.map((signal) => {
      const proposalSource = signal.actionType === 'adjust_sales_demand'
        ? linkMaps.proposalsBySellRequest
        : linkMaps.proposalsByBuyRequest;
      const linkedProposals = signal.requestId ? (proposalSource.get(signal.requestId) || []) : [];
      const linkedOrders = linkedProposals.flatMap((proposal) => linkMaps.ordersByProposal.get(proposal.proposal_id) || []);

      let stageLabel = 'Request Not Created';
      if (signal.requestCreated && signal.requestId) {
        stageLabel = 'Request Created';
      }
      if (linkedProposals.length > 0) {
        const confirmedCount = linkedProposals.filter((proposal) => proposal.status === 'confirmed').length;
        const pendingCount = linkedProposals.filter((proposal) => proposal.status === 'pending').length;
        const rejectedCount = linkedProposals.filter((proposal) => proposal.status === 'rejected').length;
        if (linkedOrders.length > 0) {
          stageLabel = 'Order Created';
        } else if (confirmedCount > 0) {
          stageLabel = 'Proposal Confirmed';
        } else if (pendingCount > 0) {
          stageLabel = 'Proposal Pending';
        } else if (rejectedCount > 0) {
          stageLabel = 'Proposal Rejected';
        } else {
          stageLabel = 'Proposal Generated';
        }
      }

      return {
        ...signal,
        laneLabel: getLaneLabel(signal, flowMeta),
        displayCompany: flowMeta.companyNameMap[signal.company] || signal.company,
        displayItemId: getDisplayItemLabel(signal.itemId, quantityView),
        displayQuantity: convertQuantityByItem(signal.quantity, signal.itemId, quantityView),
        linkedProposals,
        linkedOrders,
        proposalStatusSummary: buildStatusSummary(linkedProposals),
        orderStatusSummary: buildStatusSummary(linkedOrders),
        stageLabel,
      };
    });
  }, [signals, linkMaps, quantityView, flowMeta]);

  const daySummaries = useMemo(() => {
    return availableDays.map((day) => {
      const daySignals = enrichedSignals.filter((signal) => signal.day === day);
      const proposalIds = new Set();
      const orderIds = new Set();

      for (const signal of daySignals) {
        for (const proposal of signal.linkedProposals) {
          proposalIds.add(proposal.proposal_id);
        }
        for (const order of signal.linkedOrders) {
          orderIds.add(order.order_id);
        }
      }

      return {
        day,
        signalCount: daySignals.length,
        sellSignalCount: daySignals.filter((signal) => signal.actionType === 'adjust_sales_demand' && signal.requestCreated).length,
        procurementSignalCount: daySignals.filter((signal) => signal.actionType !== 'adjust_sales_demand' && signal.requestCreated).length,
        blockedSignalCount: daySignals.filter((signal) => !signal.requestCreated).length,
        proposalCount: proposalIds.size,
        orderCount: orderIds.size,
      };
    });
  }, [availableDays, enrichedSignals]);

  const visibleSignals = useMemo(
    () => enrichedSignals
      .slice()
      .sort((a, b) => {
        const left = `${String(a.day).padStart(4, '0')}-${a.company}-${a.phase}-${a.kindLabel}`;
        const right = `${String(b.day).padStart(4, '0')}-${b.company}-${b.phase}-${b.kindLabel}`;
        return left.localeCompare(right);
      }),
    [enrichedSignals]
  );

  if (loading) {
    return <LoadingState label="Loading demand-propagation trace…" variant="chart"/>;
  }

  return (
    <div className="demand-propagation-view">
      <div className="demand-propagation-header">
        <div>
          <h3>Demand-Propagation Trace</h3>
          <p>Links each turn's `adjust_sales_demand`, `create_replenishment_order`, and `create_purchase_demand` actions to downstream proposals and orders.</p>
        </div>
      </div>

      <div className="demand-propagation-summary-grid">
        {daySummaries.map((summary) => (
          <div
            key={summary.day}
            className="demand-day-card"
          >
            <div className="demand-day-title">Round {summary.day}</div>
            <div className="demand-day-metrics">
              <span>Signals {summary.signalCount}</span>
              <span>Sales {summary.sellSignalCount}</span>
              <span>Procurement {summary.procurementSignalCount}</span>
              <span>Proposals {summary.proposalCount}</span>
              <span>Orders {summary.orderCount}</span>
            </div>
            {summary.blockedSignalCount > 0 ? (
              <div className="demand-day-warning">Requests Not Created {summary.blockedSignalCount}</div>
            ) : null}
          </div>
        ))}
      </div>

      <div className="demand-propagation-scroll-hint">
        Scroll horizontally to inspect all fields and vertically to browse turns. The header and first two columns remain fixed for comparison.
      </div>

      <div className="demand-propagation-table-wrapper">
        <table className="demand-propagation-table">
          <thead>
            <tr>
              <th>Round</th>
              <th>Enterprise</th>
              <th>Phase</th>
              <th>Link</th>
              <th>Action</th>
              <th>Material</th>
              <th>{getQuantityColumnLabel('Quantity', quantityView)}</th>
              <th>Request ID</th>
              <th>Proposals</th>
              <th>Orders</th>
              <th>Current Status</th>
            </tr>
          </thead>
          <tbody>
            {visibleSignals.length === 0 ? (
              <tr>
                <td colSpan="11" className="demand-empty-cell">No demand-propagation actions were captured in this run</td>
              </tr>
            ) : visibleSignals.map((signal) => (
              <tr key={signal.id}>
                <td>{signal.day}</td>
                <td>{signal.displayCompany}</td>
                <td>{signal.phase}</td>
                <td>{signal.laneLabel}</td>
                <td>{signal.kindLabel}</td>
                <td>{signal.displayItemId}</td>
                <td>{formatNumber(signal.displayQuantity)}</td>
                <td>{signal.requestId || '-'}</td>
                <td>
                  {signal.linkedProposals.length > 0 ? (
                    <div className="demand-stage-cell">
                      <span>{signal.linkedProposals.length}</span>
                      <span className="demand-stage-meta">
                        {Object.entries(signal.proposalStatusSummary).map(([status, count]) => `${status}:${count}`).join(' / ')}
                      </span>
                    </div>
                  ) : signal.requestCreated ? '0' : 'Not created'}
                </td>
                <td>
                  {signal.linkedOrders.length > 0 ? (
                    <div className="demand-stage-cell">
                      <span>{signal.linkedOrders.length}</span>
                      <span className="demand-stage-meta">
                        {Object.entries(signal.orderStatusSummary).map(([status, count]) => `${status}:${count}`).join(' / ')}
                      </span>
                    </div>
                  ) : '0'}
                </td>
                <td>
                  <span className={`demand-stage-badge ${signal.linkedOrders.length > 0 ? 'order' : signal.linkedProposals.length > 0 ? 'proposal' : signal.requestCreated ? 'request' : 'blocked'}`}>
                    {signal.stageLabel}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default DemandPropagationView;
