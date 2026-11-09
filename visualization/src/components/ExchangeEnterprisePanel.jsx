import LoadingState from './LoadingState';
import React, { useEffect, useState } from 'react';
import { buildDataUrl, safeFetchJson } from '../utils/dataSource';

const DEPARTMENTS = ['finance', 'sales', 'procurement', 'inventory', 'hr', 'production'];
const DEPARTMENT_LABELS = {
  finance: 'Finance',
  sales: 'Sales',
  procurement: 'Procurement',
  inventory: 'Inventory',
  hr: 'Human Resources',
  production: 'Production'
};

const formatNumber = (value) => {
  if (value === null || value === undefined || value === '') {
    return '-';
  }
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return '-';
  }
  return new Intl.NumberFormat('en-US', {
    maximumFractionDigits: 2
  }).format(numeric);
};

const formatMoney = (value) => `SCU ${formatNumber(value)}`;

const formatValue = (value) => {
  if (value === null || value === undefined || value === '') {
    return '-';
  }
  if (typeof value === 'object') {
    return JSON.stringify(value);
  }
  return String(value);
};

const getExchangeCompanies = (exchange, direction) => {
  if (!exchange) {
    return [];
  }

  if (direction === 'upstream') {
    return exchange.enterprises?.upstream_enterprises || [];
  }

  return exchange.enterprises?.downstream_enterprises || [];
};

const getProcurementFlowSummary = (companyName, exchange) => {
  if (!exchange) {
    return {
      title: 'Upstream Procurement',
      metrics: []
    };
  }

  const upstreamPartners = getExchangeCompanies(exchange, 'upstream');
  const ownRequests = (exchange.buy_requests?.list || []).filter(
    (request) => request.buyer_company_id === companyName
  );
  const ownOrders = (exchange.orders?.list || []).filter(
    (order) => order.buyer_company_id === companyName
  );
  const upstreamOffers = (exchange.sell_requests?.list || []).filter(
    (request) => upstreamPartners.includes(request.seller_company_id)
  );

  return {
    title: `Upstream Procurement · ${exchange.id}`,
    metrics: [
      { label: 'Buy Requests', value: ownRequests.length },
      { label: 'Upstream Offers', value: upstreamOffers.length },
      { label: 'Matched Orders', value: ownOrders.length },
      {
        label: 'Purchased Quantity',
        value: formatNumber(ownOrders.reduce((sum, order) => sum + Number(order.quantity || 0), 0))
      }
    ]
  };
};

const getSalesFlowSummary = (companyName, exchange) => {
  if (!exchange) {
    return {
      title: 'Downstream Sales',
      metrics: []
    };
  }

  const downstreamPartners = getExchangeCompanies(exchange, 'downstream');
  const ownSalesRequests = (exchange.sell_requests?.list || []).filter(
    (request) => request.seller_company_id === companyName
  );
  const ownOrders = (exchange.orders?.list || []).filter(
    (order) => order.seller_company_id === companyName
  );
  const downstreamRequests = (exchange.buy_requests?.list || []).filter(
    (request) => downstreamPartners.includes(request.buyer_company_id)
  );

  return {
    title: `Downstream Sales · ${exchange.id}`,
    metrics: [
      { label: 'Sell Requests', value: ownSalesRequests.length },
      { label: 'Downstream Demand', value: downstreamRequests.length },
      { label: 'Matched Orders', value: ownOrders.length },
      {
        label: 'Sold Quantity',
        value: formatNumber(ownOrders.reduce((sum, order) => sum + Number(order.quantity || 0), 0))
      }
    ]
  };
};

const renderMetrics = (metrics) => (
  <div className="metrics-grid">
    {metrics.map((metric, index) => (
      <div key={index} className="metric-card">
        <div className="metric-label">{metric.label}</div>
        <div className="metric-value">{metric.value}</div>
      </div>
    ))}
  </div>
);

const normalizeActionItems = (payload) => {
  if (!Array.isArray(payload)) {
    return [];
  }

  if (payload.length === 1 && Array.isArray(payload[0])) {
    return payload[0];
  }

  return payload.filter((item) => item && typeof item === 'object');
};

const flattenResultEntries = (resultPayload) => {
  const result = resultPayload?.result;
  if (!result || typeof result !== 'object') {
    return [];
  }

  return Object.entries(result).flatMap(([statusKey, value]) => {
    const entries = Array.isArray(value) ? value : [value];
    return entries
      .filter((entry) => entry && typeof entry === 'object')
      .map((entry) => ({ ...entry, _statusKey: statusKey }));
  });
};

const getActionName = (actionItem) => String(actionItem?.action?.action_name || '');

const getResultStatus = (actionItem, resultEntries) => {
  const actionName = getActionName(actionItem);
  if (actionName === 'action_pass') {
    return {
      label: 'Skipped',
      className: 'pass',
      message: actionItem?.action_reason || actionItem?.action?.action_param || 'No action executed'
    };
  }

  const matched = resultEntries.find((entry) => String(entry.action_type || '') === actionName);
  if (!matched) {
    return {
      label: 'Unmatched Result',
      className: 'unknown',
      message: 'No corresponding execution result was found'
    };
  }

  if (matched.success === true || matched.status === 'success' || matched._statusKey === 'success') {
    return {
      label: 'Success',
      className: 'success',
      message: matched.message || 'Execution succeeded'
    };
  }

  return {
    label: 'Failure',
    className: 'failed',
    message: matched.message || matched.errors?.[0]?.message || 'Execution failed'
  };
};

const ExchangeEnterprisePanel = ({ day, coreCompany, dataRoot }) => {
  const [panelData, setPanelData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [expandedDept, setExpandedDept] = useState({});
  const [expandedMessage, setExpandedMessage] = useState({});

  const toggleDeptExpansion = (dept) => {
    setExpandedDept(prev => ({
      ...prev,
      [dept]: !prev[dept]
    }));
  };

  const toggleMessageExpansion = (dept, index) => {
    setExpandedMessage(prev => ({
      ...prev,
      [`${dept}-${index}`]: !prev[`${dept}-${index}`]
    }));
  };

  useEffect(() => {
    loadPanelData();
  }, [day, coreCompany, dataRoot]);

  const loadPanelData = async () => {
    try {
      setLoading(true);
      setError(null);

      const exchangeData = await safeFetchJson(buildDataUrl(dataRoot, `public/exchange/day${day}/exchange.json`));

      const departmentData = {};
      for (const dept of DEPARTMENTS) {
        const response = await safeFetchJson(buildDataUrl(dataRoot, `enterprises/${coreCompany}/department/${dept}/day${day}/${dept}.json`));
        if (response) {
          departmentData[dept] = response;
        }
      }

      const messageDepartments = ['analyst', 'sales', 'procurement', 'production'];
      const messagesData = {};
      for (const dept of messageDepartments) {
        const response = await safeFetchJson(buildDataUrl(dataRoot, `enterprises/${coreCompany}/model_messages/day${day}/${dept}_messages.json`));
        if (response) {
          messagesData[dept] = response;
        }
      }

      const actionData = {};
      for (const dept of DEPARTMENTS) {
        const phases = [];
        const [preActionData, actionDataItem] = await Promise.all([
          safeFetchJson(buildDataUrl(dataRoot, `enterprises/${coreCompany}/department/${dept}/day${day}/pre_${dept}_action.json`)),
          safeFetchJson(buildDataUrl(dataRoot, `enterprises/${coreCompany}/department/${dept}/day${day}/${dept}_action.json`)),
        ]);

        if (preActionData) {
          phases.push({
            label: 'Pre-execution',
            payload: preActionData,
            result: await safeFetchJson(buildDataUrl(dataRoot, `enterprises/${coreCompany}/department/${dept}/day${day}/pre_${dept}_result.json`))
          });
        }
        if (actionDataItem) {
          phases.push({
            label: 'Execution',
            payload: actionDataItem,
            result: await safeFetchJson(buildDataUrl(dataRoot, `enterprises/${coreCompany}/department/${dept}/day${day}/${dept}_result.json`))
          });
        }
        if (phases.length > 0) {
          actionData[dept] = phases;
        }
      }

      setPanelData({
        exchangeData,
        departmentData,
        messagesData,
        actionData
      });
    } catch (err) {
      console.error('Failed to load panel data:', err);
      setError('Failed to load panel data: ' + err.message);
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return <LoadingState label="Loading trading partners…" variant="compact"/>;
  }

  if (error) {
    return <div className="error">{error}</div>;
  }

  if (!panelData) {
    return <div className="loading">No data available</div>;
  }

  const { exchangeData, departmentData, messagesData, actionData } = panelData;
  const exchanges = exchangeData?.data?.exchanges || {};
  const coreExchanges = Object.values(exchanges).filter(exchange =>
    getExchangeCompanies(exchange, 'upstream').includes(coreCompany) ||
    getExchangeCompanies(exchange, 'downstream').includes(coreCompany)
  );

  const getDeptSummary = (dept, data) => {
    if (!data) return null;
    const state = data.self_state || {};

    switch (dept) {
      case 'finance':
        return {
          metrics: [
            { label: 'Cash', value: formatMoney(state.cash) },
            { label: 'Total Revenue', value: formatMoney(state.total_revenue) },
            { label: 'Net Profit', value: formatMoney(state.financial_indicators?.net_profit) }
          ]
        };
      case 'sales':
        const salesMetrics = state.sales_metrics || {};
        return {
          metrics: [
            { label: 'Orders', value: salesMetrics.total_orders || 0 },
            { label: 'Revenue', value: formatMoney(salesMetrics.total_revenue) },
            { label: 'Quantity Sold', value: formatNumber(salesMetrics.total_quantity_sold) }
          ]
        };
      case 'procurement':
        const metrics = state.procurement_metrics || {};
        return {
          metrics: [
            { label: 'Orders', value: metrics.total_orders || 0 },
            { label: 'Completed', value: metrics.completed_orders || 0 },
            { label: 'Total Cost', value: formatMoney(metrics.total_cost) }
          ]
        };
      case 'inventory':
        return {
          metrics: [
            { label: 'Capacity', value: formatNumber(state.warehouse_capacity) },
            { label: 'Used', value: formatNumber(state.used_capacity) },
            { label: 'Item Types', value: (state.inventory_items || []).length }
          ]
        };
      case 'hr':
        const totalEmployees = (state.employees || []).reduce((sum, e) => sum + (e.count || 0), 0);
        return {
          metrics: [
            { label: 'Employees', value: totalEmployees },
            { label: 'Payroll', value: formatMoney(state.total_payroll) }
          ]
        };
      case 'production':
        const prodLines = state.production_lines || {};
        const prodMetrics = state.production_metrics || {};
        return {
          metrics: [
            { label: 'Capacity', value: formatNumber(prodLines.total_capacity) },
            { label: 'Available', value: formatNumber(prodLines.available_capacity) },
            { label: 'Total Output', value: formatNumber(prodMetrics.total_production) }
          ]
        };
      default:
        return { metrics: [] };
    }
  };

  const renderActionDetails = (phase) => {
    const actionDataList = phase.payload;
    const actionType = phase.label;
    const actions = normalizeActionItems(actionDataList);
    const resultEntries = flattenResultEntries(phase.result);

    if (actions.length === 0) {
      return <div className="detail-row">No action information available</div>;
    }

    return (
      <div className="action-details">
        <div className="detail-header">{actionType}</div>
        {actions.map((action, idx) => {
          if (action.pass_reason) {
            return (
              <div key={idx} className="pass-reason-item">
                <div className="pass-reason-label">Reason Not Executed</div>
                <div className="pass-reason-content">{action.pass_reason}</div>
              </div>
            );
          }
          if (action.action) {
            const actionParam = action.action.action_param;
            const hasObjectParams = actionParam && typeof actionParam === 'object' && !Array.isArray(actionParam);
            const resultStatus = getResultStatus(action, resultEntries);
            return (
              <div key={idx} className="action-item-detail">
                <div className="action-title-row">
                  <div className="action-name">{action.action.action_name}</div>
                  <span className={`action-result-badge ${resultStatus.className}`}>{resultStatus.label}</span>
                </div>
                {hasObjectParams ? (
                  <div className="action-params">
                    {Object.entries(actionParam).map(([key, value]) => (
                      <span key={key} className="action-param">{key}: {formatValue(value)}</span>
                    ))}
                  </div>
                ) : actionParam ? (
                  <div className="action-reason">{formatValue(actionParam)}</div>
                ) : null}
                {action.module_type && (
                  <div className="action-meta">
                    <span className="module-type">Module: {action.module_type}</span>
                    <span className="executor">Executor: {action.executor_id || '-'}</span>
                  </div>
                )}
                {action.action_reason && <div className="action-reason">{action.action_reason}</div>}
                {resultStatus.message && <div className="action-result-message">{resultStatus.message}</div>}
              </div>
            );
          }
          return null;
        })}
      </div>
    );
  };

  const renderMessages = (dept) => {
    const msgs = messagesData[dept];
    if (!Array.isArray(msgs) || msgs.length === 0) return null;

    const processedMsgs = msgs.filter(msg => {
      const content = String(msg.content || '');
      return content.includes('AssistantMessage') ||
             content.includes('ResultMessage') ||
             content.includes('reason') ||
             content.includes('analysis') ||
             content.length > 100;
    }).slice(-3);

    if (processedMsgs.length === 0) return null;

    return (
      <div className="messages-section">
        <div className="section-title">Department Reasoning</div>
        {processedMsgs.map((msg, idx) => {
          const messageKey = `${dept}-${idx}`;
          const isExpanded = expandedMessage[messageKey];
          let content = String(msg.content || '');

          if (content.includes('ResultMessage')) {
            try {
              const match = content.match(/result='([^']+)'/);
              if (match) content = match[1];
            } catch (e) {
              // Fall back to the original content when parsing fails.
            }
          } else if (content.includes('AssistantMessage')) {
            try {
              const textMatch = content.match(/TextBlock\(text='([^']+)'\)/);
              if (textMatch) content = textMatch[1];
            } catch (e) {
              // Fall back to the original content when parsing fails.
            }
          }

          if (content.length > 200 && !isExpanded) {
            content = content.substring(0, 200) + '...';
          }

          return (
            <div
              key={idx}
              className={`message-card ${isExpanded ? 'expanded' : ''}`}
              onClick={() => toggleMessageExpansion(dept, idx)}
            >
              <div className="message-content">{content}</div>
            </div>
          );
        })}
      </div>
    );
  };

  return (
    <div className="exchange-enterprise-panel">
      {/* Exchange summary */}
      {coreExchanges.map((exchange, index) => (
        <div key={index} className="exchange-summary">
          <div className="exchange-flow">
            {getExchangeCompanies(exchange, 'downstream').includes(coreCompany) && (
              <div className="flow-section">
                <h4>{getProcurementFlowSummary(coreCompany, exchange).title}</h4>
                {renderMetrics(getProcurementFlowSummary(coreCompany, exchange).metrics)}
              </div>
            )}
            {getExchangeCompanies(exchange, 'upstream').includes(coreCompany) && (
              <div className="flow-section">
                <h4>{getSalesFlowSummary(coreCompany, exchange).title}</h4>
                {renderMetrics(getSalesFlowSummary(coreCompany, exchange).metrics)}
              </div>
            )}
          </div>
        </div>
      ))}

      {/* Consolidated department information */}
      <div className="departments-container">
        {DEPARTMENTS.map(dept => {
          const data = departmentData[dept];
          const actions = actionData[dept];

          if (!data && !actions) return null;

          const summary = getDeptSummary(dept, data);
          const isExpanded = expandedDept[dept];

          const hasPreAction = Array.isArray(actions) && actions.some((phase) => phase.label === 'Pre-execution');
          const hasRegularAction = Array.isArray(actions) && actions.some((phase) => phase.label === 'Execution');

          return (
            <div key={dept} className={`dept-card ${isExpanded ? 'expanded' : ''}`}>
              <div className="dept-header" onClick={() => toggleDeptExpansion(dept)}>
                <div className="dept-name">
                  {DEPARTMENT_LABELS[dept]} Department
                  {(hasPreAction || hasRegularAction) && (
                    <span className="action-indicator">
                      {hasPreAction && hasRegularAction ? 'Pre + Exec' : hasPreAction ? 'Pre' : 'Exec'}
                    </span>
                  )}
                </div>
                <div className="expand-icon">{isExpanded ? '▲' : '▼'}</div>
              </div>

              <div className="dept-summary">
                {summary && summary.metrics && renderMetrics(summary.metrics)}
              </div>

              {isExpanded && (
                <div className="dept-details">
                  {/* Department actions */}
                  {actions && (
                    <div className="details-section">
                      <div className="section-title">Department Actions</div>
                      {Array.isArray(actions) && actions.map((phase) => (
                        <React.Fragment key={phase.label}>
                          {renderActionDetails(phase)}
                        </React.Fragment>
                      ))}
                    </div>
                  )}

                  {/* Department reasoning */}
                  {(dept === 'sales' || dept === 'procurement' || dept === 'production') && renderMessages(dept)}
                  {dept === 'finance' && renderMessages('analyst')}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Analyst reasoning */}
      {messagesData.analyst && (
        <div className="analyst-section">
          <div className="section-title">Analyst Reasoning</div>
          {messagesData.analyst.slice(-3).map((msg, idx) => {
            const messageKey = `analyst-${idx}`;
            const isExpanded = expandedMessage[messageKey];
            let content = String(msg.content || '');

            if (content.includes('ResultMessage')) {
              const match = content.match(/result='([^']+)'/);
              if (match) content = match[1];
            } else if (content.includes('AssistantMessage')) {
              const textMatch = content.match(/TextBlock\(text='([^']+)'\)/);
              if (textMatch) content = textMatch[1];
            }

            if (content.length > 200 && !isExpanded) {
              content = content.substring(0, 200) + '...';
            }

            return (
              <div
                key={idx}
                className={`message-card ${isExpanded ? 'expanded' : ''}`}
                onClick={() => toggleMessageExpansion('analyst', idx)}
              >
                <div className="message-content">{content}</div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default ExchangeEnterprisePanel;
