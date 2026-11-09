import LoadingState from './LoadingState';
import React, { useState, useEffect } from 'react';
import { buildDataUrl, safeFetchText, safeFetchJson } from '../utils/dataSource';

const CompanyInfo = ({ company, day, dataRoot }) => {
  const [info, setInfo] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let active = true;
    const load = async () => {
      setLoading(true); setError(null); setInfo(null);
      try {
        const text = await safeFetchText(buildDataUrl(dataRoot, `enterprises/${company}/observations/observation_day${day}.txt`));
        let data = text ? JSON.parse(text) : null;
        if (!data) {
          const meta = await safeFetchJson(buildDataUrl(dataRoot, 'run_meta.json'));
          const stage = day < Number(meta?.prewarm_rounds || 0) ? 'prewarm_end_of_day' : 'end_of_day';
          const snapshot = await safeFetchJson(buildDataUrl(dataRoot, `public/observer_state/day${day}/${stage}/${company}.json`));
          data = snapshot?.observation || snapshot?.data?.observation;
        }
        if (!data) {
          const finance = await safeFetchJson(buildDataUrl(dataRoot, `enterprises/${company}/department/finance/day${day}/finance.json`));
          if (finance?.self_state) data = {finance: finance.self_state};
        }
        if (active) setInfo(data);
      } catch (error) {
        if (active) setError('The recorded observation could not be parsed: ' + error.message);
      } finally {
        if (active) setLoading(false);
      }
    };
    load();
    return () => {active = false;};
  }, [company, day, dataRoot]);

  const formatCurrency = (value) => {
    if (value === null || value === undefined || value === '') {
      return 'Not recorded';
    }
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) {
      return 'Not recorded';
    }
    return `SCU ${new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(numeric)}`;
  };

  if (loading) {
    return <LoadingState label="Loading enterprise state…" variant="compact"/>;
  }

  if (error) {
    return <div className="error">{error}</div>;
  }

  if (!info) {
    return <div className="loading">No enterprise observation was published for this round.</div>;
  }

  const finance = info.finance?.self_state || info.finance || {};
  const financialIndicators = finance.financial_indicators || {};

  return (
    <div className="company-info">
      <div className="metric-card">
        <div className="metric-label">Enterprise</div>
        <div className="metric-value">{company}</div>
      </div>
      <div className="metric-card">
        <div className="metric-label">Cash balance</div>
        <div className="metric-value">{formatCurrency(finance.cash)}</div>
      </div>
      <div className="metric-card">
        <div className="metric-label">Total revenue</div>
        <div className="metric-value">{formatCurrency(finance.total_revenue)}</div>
      </div>
      <div className="metric-card">
        <div className="metric-label">Total cost</div>
        <div className="metric-value">{formatCurrency(finance.total_cost)}</div>
      </div>
      {Object.keys(financialIndicators).length > 0 && (
        <div className="metric-card">
          <div className="metric-label">Net profit</div>
          <div className="metric-value">{formatCurrency(financialIndicators.net_profit)}</div>
        </div>
      )}
    </div>
  );
};

export default CompanyInfo;
