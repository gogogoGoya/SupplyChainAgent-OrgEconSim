import React, { useEffect, useState } from 'react';
import { buildDataUrl, safeFetchJson } from '../utils/dataSource';
import { getActionDisplayName } from '../utils/actionLabels';

const DEPARTMENTS = ['finance', 'production', 'sales', 'inventory', 'procurement', 'hr'];

const DEPARTMENT_LABELS = {
  finance: 'Finance',
  production: 'Production',
  sales: 'Sales',
  inventory: 'Inventory',
  procurement: 'Procurement',
  hr: 'Human Resources',
};

const STATUS_LABELS = {
  failed: 'Issue',
  success: 'Success',
  generated: 'Generated',
  empty: 'No Action',
  unknown: 'Receipt unresolved',
};

const normalizeActions = (payload) => {
  if (!Array.isArray(payload)) {
    return [];
  }
  if (payload.length === 1 && Array.isArray(payload[0])) {
    return payload[0];
  }
  return payload.filter((item) => item && typeof item === 'object');
};

const normalizeResultItems = (payload, key) => {
  const result = payload?.result || payload?.data || payload || {};
  const items = result?.[key];
  if (Array.isArray(items)) {
    return items;
  }
  return [];
};

const getActionName = (entry) => (
  getActionDisplayName(
    entry?.action?.action_name
    || entry?.action_name
    || entry?.name
    || (entry?.pass_reason ? 'action_pass' : 'unknown_action')
  )
);

const getActionParams = (entry) => (
  entry?.action?.action_param
  || entry?.action_param
  || entry?.params
  || null
);

const getActionReason = (entry) => (
  entry?.action_reason
  || entry?.reason
  || entry?.pass_reason
  || ''
);

const hasPayload = (payload) => {
  if (!payload) {
    return false;
  }
  if (Array.isArray(payload)) {
    return payload.length > 0;
  }
  if (typeof payload === 'object') {
    return Object.keys(payload).length > 0;
  }
  return true;
};

const stringifyCompact = (value, maxLength = 220) => {
  if (value === null || value === undefined || value === '') {
    return '-';
  }
  const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  return text.length > maxLength ? `${text.slice(0, maxLength)}...` : text;
};

const extractMessageText = (message) => {
  const content = message?.content ?? message?.text ?? message?.message ?? '';
  if (Array.isArray(content)) {
    return content.map((item) => item?.text || item?.content || stringifyCompact(item, 120)).join('\n');
  }
  return stringifyCompact(content, 360);
};

const normalizeMessages = (payload, source) => {
  if (!Array.isArray(payload)) {
    return [];
  }
  return payload.map((message, index) => ({
    id: `${source}-${index}`,
    source,
    role: message?.role || source,
    content: extractMessageText(message),
  })).filter((message) => message.content && message.content !== '-');
};

const flattenErrorMessages = (payload) => {
  if (!hasPayload(payload)) {
    return [];
  }
  if (Array.isArray(payload)) {
    return payload.flatMap((item) => flattenErrorMessages(item));
  }
  if (typeof payload === 'object') {
    const message = payload.message || payload.error || payload.detail || payload.reason;
    if (message) {
      return [String(message)];
    }
    return [stringifyCompact(payload, 260)];
  }
  return [String(payload)];
};

const loadFirstAvailable = async (dataRoot, candidates) => {
  for (const relativePath of candidates) {
    const payload = await safeFetchJson(buildDataUrl(dataRoot, relativePath));
    if (hasPayload(payload)) {
      return payload;
    }
  }
  return null;
};

const Section = ({ title, children, emptyText }) => (
  <section className="single-action-section">
    <h4>{title}</h4>
    {children || <p>{emptyText || 'No records available'}</p>}
  </section>
);

const SingleEnterpriseActionList = ({ company, currentDay = 0, dataRoot }) => {
  const [rows, setRows] = useState([]);
  const [expandedDept, setExpandedDept] = useState(null);

  useEffect(() => {
    let cancelled = false;
    const loadRows = async () => {
      if (!company) {
        setRows([]);
        return;
      }

      const nextRows = await Promise.all(DEPARTMENTS.map(async (dept) => {
        const basePath = `enterprises/${company}/department/${dept}/day${currentDay}`;
        const messageBasePath = `enterprises/${company}/model_messages/day${currentDay}`;
        const [
          preAction,
          action,
          marketSeedAction,
          preResult,
          result,
          marketSeedResult,
          error,
          marketSeedError,
          firstRetryError,
          retryError,
          secondRetryError,
          skillAudit,
          tradeAudit,
          analystAudit,
          normalMessages,
          scriptedMessages,
        ] = await Promise.all([
          safeFetchJson(buildDataUrl(dataRoot, `${basePath}/pre_${dept}_action.json`)),
          safeFetchJson(buildDataUrl(dataRoot, `${basePath}/${dept}_action.json`)),
          dept === 'sales'
            ? safeFetchJson(buildDataUrl(dataRoot, `${basePath}/sales_market_seed_action.json`))
            : Promise.resolve(null),
          safeFetchJson(buildDataUrl(dataRoot, `${basePath}/pre_${dept}_result.json`)),
          safeFetchJson(buildDataUrl(dataRoot, `${basePath}/${dept}_result.json`)),
          dept === 'sales'
            ? safeFetchJson(buildDataUrl(dataRoot, `${basePath}/sales_market_seed_result.json`))
            : Promise.resolve(null),
          safeFetchJson(buildDataUrl(dataRoot, `${basePath}/${dept}_error.json`)),
          dept === 'sales'
            ? safeFetchJson(buildDataUrl(dataRoot, `${basePath}/sales_market_seed_error.json`))
            : Promise.resolve(null),
          safeFetchJson(buildDataUrl(dataRoot, `${basePath}/${dept}_error_0.json`)),
          safeFetchJson(buildDataUrl(dataRoot, `${basePath}/${dept}_error_1.json`)),
          safeFetchJson(buildDataUrl(dataRoot, `${basePath}/${dept}_error_2.json`)),
          safeFetchJson(buildDataUrl(dataRoot, `${basePath}/skill_execution_audit.json`)),
          safeFetchJson(buildDataUrl(dataRoot, `${basePath}/trade_skill_execution_audit.json`)),
          safeFetchJson(buildDataUrl(dataRoot, `${basePath}/analyst_execution_audit.json`)),
          loadFirstAvailable(dataRoot, [
            `${messageBasePath}/${dept}_messages.json`,
            `${messageBasePath}/${dept}_message.json`,
          ]),
          loadFirstAvailable(dataRoot, [
            `${messageBasePath}/scripted_${dept}_messages.json`,
            `${messageBasePath}/scripted_${dept}_message.json`,
          ]),
        ]);

        const actions = [
          ...normalizeActions(preAction).map((item) => ({ ...item, phase: 'Pre-execution' })),
          ...normalizeActions(action).map((item) => ({ ...item, phase: 'Execution' })),
          ...normalizeActions(marketSeedAction).map((item) => ({ ...item, phase: 'Warm-up Seed' })),
        ];
        const successResults = [
          ...normalizeResultItems(preResult, 'success'),
          ...normalizeResultItems(result, 'success'),
          ...normalizeResultItems(marketSeedResult, 'success'),
        ];
        const failedResults = [
          ...normalizeResultItems(preResult, 'failed'),
          ...normalizeResultItems(result, 'failed'),
          ...normalizeResultItems(marketSeedResult, 'failed'),
          ...normalizeResultItems(preResult, 'error'),
          ...normalizeResultItems(result, 'error'),
          ...normalizeResultItems(marketSeedResult, 'error'),
        ];
        const errors = [
          ...flattenErrorMessages(error),
          ...flattenErrorMessages(marketSeedError),
          ...flattenErrorMessages(firstRetryError),
          ...flattenErrorMessages(retryError),
          ...flattenErrorMessages(secondRetryError),
        ];
        const audits = [skillAudit, tradeAudit, analystAudit].filter(hasPayload);
        const messages = [
          ...normalizeMessages(normalMessages, dept),
          ...normalizeMessages(scriptedMessages, `scripted_${dept}`),
        ];
        const failed = errors.length > 0 || failedResults.length > 0 || audits.some((audit) => audit?.status === 'failed' || audit?.ok === false);
        const succeeded = successResults.length > 0;
        const receiptPresent = hasPayload(preResult) || hasPayload(result);

        return {
          department: dept,
          departmentLabel: DEPARTMENT_LABELS[dept] || dept,
          status: failed ? 'failed' : succeeded ? 'success' : receiptPresent && actions.length > 0 ? 'unknown' : actions.length > 0 ? 'generated' : 'empty',
          rawReceipts: [preResult,result,marketSeedResult].filter(Boolean),
          rawMessages: [normalMessages,scriptedMessages].filter(Boolean),
          actions,
          successResults,
          failedResults,
          errors,
          audits,
          messages,
        };
      }));

      if (!cancelled) {
        setRows(nextRows);
        setExpandedDept((previousDept) => {
          if (previousDept && nextRows.some((row) => row.department === previousDept)) {
            return previousDept;
          }
          if (nextRows.length > 0) {
            const firstActiveRow = nextRows.find((row) => row.status !== 'empty') || nextRows[0];
            return firstActiveRow.department;
          }
          return null;
        });
      }
    };
    loadRows();
    return () => {
      cancelled = true;
    };
  }, [company, currentDay, dataRoot]);

  return (
    <aside className="single-action-panel">
      <div className="single-action-header">
        <span>Department Trace</span>
        <strong>Round {currentDay} Department Actions and Reasoning</strong>
      </div>
      <div className="single-action-list">
        {rows.map((row) => {
          const expanded = expandedDept === row.department;
          return (
            <section key={row.department} className={`single-action-card ${row.status}`}>
              <header>
                <div>
                  <strong>{row.departmentLabel}</strong>
                  <small>
                    Actions {row.actions.length} · Successes {row.successResults.length} · Failures {row.failedResults.length + row.errors.length} · Messages {row.messages.length}
                  </small>
                </div>
                <button
                  type="button"
                  className="single-action-detail-toggle"
                  onClick={() => setExpandedDept(expanded ? null : row.department)}
                >
                  <span>{STATUS_LABELS[row.status] || row.status}</span>
                  <b>{expanded ? 'Collapse' : 'Details'}</b>
                </button>
              </header>

              {row.actions.length > 0 ? (
                <div className="single-action-items">
                  {row.actions.slice(0, expanded ? row.actions.length : 2).map((action, index) => (
                    <article key={`${row.department}-${index}`}>
                      <b>{action.phase} · {getActionName(action)}</b>
                      <small>{getActionReason(action) || 'No action rationale recorded'}</small>
                    </article>
                  ))}
                  {!expanded && row.actions.length > 2 && (
                    <p>{row.actions.length - 2} more actions. Expand to view the complete trace.</p>
                  )}
                </div>
              ) : (
                <p>No action was recorded for this department in the current turn.</p>
              )}

              {expanded && (
                <div className="single-action-detail-panel">
                  <details className="review-raw-evidence"><summary>Full archived proposal and receipt data</summary><pre>{JSON.stringify({actions:row.actions,receipts:row.rawReceipts,messages:row.rawMessages,audits:row.audits},null,2)}</pre></details>
                  <Section title="Action Plan">
                    {row.actions.length > 0 ? (
                      row.actions.map((action, index) => (
                        <article key={`plan-${row.department}-${index}`} className="single-action-detail-item">
                          <b>{action.phase} · {getActionName(action)}</b>
                          <dl>
                            <dt>Parameters</dt>
                            <dd>{stringifyCompact(getActionParams(action), 360)}</dd>
                            <dt>Rationale</dt>
                            <dd>{getActionReason(action) || 'Not recorded'}</dd>
                          </dl>
                        </article>
                      ))
                    ) : null}
                  </Section>

                  <Section title="Execution Results">
                    {(row.successResults.length > 0 || row.failedResults.length > 0) ? (
                      <div className="single-action-result-grid">
                        <div>
                          <strong>Success</strong>
                          {row.successResults.length > 0
                            ? row.successResults.slice(0, 4).map((item, index) => <p key={`success-${index}`}>{stringifyCompact(item, 180)}</p>)
                            : <p>No successful results</p>}
                        </div>
                        <div>
                          <strong>Failure</strong>
                          {row.failedResults.length > 0
                            ? row.failedResults.slice(0, 4).map((item, index) => <p key={`failed-${index}`}>{stringifyCompact(item, 180)}</p>)
                            : <p>No failed results</p>}
                        </div>
                      </div>
                    ) : null}
                  </Section>

                  <Section title="Errors and Validation">
                    {(row.errors.length > 0 || row.audits.length > 0) ? (
                      <>
                        {row.errors.map((message, index) => (
                          <p key={`error-${row.department}-${index}`} className="single-action-error-line">{message}</p>
                        ))}
                        {row.audits.map((audit, index) => (
                          <p key={`audit-${row.department}-${index}`}>{stringifyCompact(audit, 260)}</p>
                        ))}
                      </>
                    ) : null}
                  </Section>

                  <Section title="Reasoning and Messages">
                    {row.messages.length > 0 ? (
                      <div className="single-action-message-list">
                        {row.messages.slice(-4).map((message) => (
                          <article key={message.id}>
                            <strong>{message.role}</strong>
                            <p>{message.content}</p>
                          </article>
                        ))}
                      </div>
                    ) : null}
                  </Section>
                </div>
              )}
            </section>
          );
        })}
      </div>
    </aside>
  );
};

export default SingleEnterpriseActionList;
