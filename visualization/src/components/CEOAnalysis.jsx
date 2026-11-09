import LoadingState from './LoadingState';
import React, { useState, useEffect } from 'react';
import { buildDataUrl, getDataRequestState, safeFetchJson } from '../utils/dataSource';

const CEOAnalysis = ({ company, day, dataRoot }) => {
  const [analysis, setAnalysis] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [warmup, setWarmup] = useState(false);

  useEffect(() => {
    let active = true;
    const load = async () => {
      setLoading(true); setError(null); setAnalysis(null); setWarmup(false);
      const url = buildDataUrl(dataRoot, `enterprises/${company}/records/day${day}/analysis.json`);
      try {
        const [data, meta] = await Promise.all([
          safeFetchJson(url), safeFetchJson(buildDataUrl(dataRoot, 'run_meta.json')),
        ]);
        if (!active) return;
        setWarmup(day < Number(meta?.prewarm_rounds || 0));
        if (!data && getDataRequestState(url).availability === 'failed_to_load') {
          setError('The recorded CEO analysis could not be loaded.');
        }
        setAnalysis(data);
      } catch (error) {
        if (active) setError('The recorded CEO analysis could not be loaded: ' + error.message);
      } finally {
        if (active) setLoading(false);
      }
    };
    load();
    return () => { active = false; };
  }, [company, day, dataRoot]);

  if (loading) {
    return <LoadingState label="Loading enterprise analysis…" variant="compact"/>;
  }

  if (error) {
    return <div className="error">{error}</div>;
  }

  if (!analysis) {
    return <div className="empty-state">{warmup ? 'Scripted warmup · ' : ''}No CEO analysis was published for R{day}.</div>;
  }

  return (
    <div className="ceo-analysis">
      <h4>CEO Analysis</h4>
      <div className="ceo-summary">
        {analysis.enterprise_summarys}
      </div>
      {analysis.department_targets && (
        <div className="department-targets">
          <h5>Department Objectives</h5>
          {Object.entries(analysis.department_targets).map(([dept, target]) => (
            <div className="target-item" key={dept}>
              <div className="target-header">{dept.toUpperCase()}</div>
              <div className="target-content">{target.target}</div>
              <div className="target-content">Evaluation: {target.evaluation}</div>
              <div className="target-reason">Rationale: {target.reason}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default CEOAnalysis;
