import React from 'react';
import {displayMetric} from '../utils/reviewDisplay';

const labels={model_tasks:'Recorded model tasks',first_attempt_rate:'Valid first attempts',retry_recovery_rate:'Recovery of invalid first attempts',
 available_phases:'Available recorded phases',recovered_tasks:'Recovered model tasks',attempts:'Model attempts',timeouts:'Timed-out attempts',
 business_rejections:'Business rejections',repair_submissions:'Repair submissions',executed_replacements:'Executed replacement chains',
 withdrawals:'Withdrawn action chains',unresolved_rejections:'Unresolved rejections',unknown_receipts:'Unknown receipts',all_agent_timeout_rounds:'All-agent timeout rounds'};

export default function ExecutionQualityPanel({execution}){
 if(!execution)return <p>No execution-quality projection is available.</p>;
 return <section className="execution-quality"><h4>Execution quality</h4>
  <p>R{execution.window.start}–R{execution.window.end} · {execution.phase==='all'?'All decision phases':execution.phase} · task summaries and legacy Skill audits retain their recorded task denominators.</p>
  <div className="review-metric-grid">{Object.entries(labels).map(([key,label])=>{
   const metric=execution.metrics[key];return <article key={key} title={metric?.source}><span>{label}</span><strong>{displayMetric(metric)}</strong>{metric?.denominator!=null&&<small>{metric.numerator??metric.value??'Not recorded'} / {metric.denominator}</small>}</article>;
  })}</div>
  <p>Repair submissions count attempts to replace a rejected action. Executed replacements and withdrawals use explicit parent links and actual receipts.</p>
  <div className="review-metric-grid"><article><span>Whole-run duration</span><strong>{displayMetric(execution.timing.run_wall)}</strong><small>{execution.timing.basis}</small></article><article><span>Summed phase durations</span><strong>{displayMetric(execution.timing.phase_elapsed_sum)}</strong><small>May include concurrent phases; this is not wall time.</small></article></div>
  {execution.health_coverage&&<p>Runtime-health coverage: {execution.health_coverage.recorded} / {execution.health_coverage.expected} rounds · all-agent timeout counts cover all enterprises.</p>}
  {execution.unsealed_phases>0&&<p role="status">{execution.unsealed_phases} phase records were not sealed.</p>}
 </section>;
}
