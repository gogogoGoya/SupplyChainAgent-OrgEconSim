import LoadingState from './LoadingState';
import React,{useEffect,useMemo,useState} from 'react';
import {getArtifactReview} from '../utils/operationsApi';
import {displayMetric,reviewLocation,displayArm} from '../utils/reviewDisplay';

const columns=[['quantity_fill_rate','Quantity fill','metrics'],['order_completion_rate','Order completion','metrics'],
 ['backlog','Backlog','metrics'],['cash','Cash','metrics'],['first_attempt_rate','Valid first attempts','execution'],
 ['retry_recovery_rate','Retry recovery','execution'],['business_rejections','Business rejections','execution'],
 ['repair_submissions','Repair submissions','execution'],['executed_replacements','Executed replacement chains','execution'],
 ['withdrawals','Withdrawn chains','execution'],['unknown_receipts','Unknown receipts','execution']];

export default function RunComparison({runs=[]}){
 const [reviews,setReviews]=useState([]),[errors,setErrors]=useState([]),[loading,setLoading]=useState(false);
 const [enterprise,setEnterprise]=useState(''),[period,setPeriod]=useState('all'),[phase,setPhase]=useState('all');
 const [metricGroup,setMetricGroup]=useState('quality');
 const visibleColumns=columns.filter(([, ,group])=>metricGroup==='all'||metricGroup==='outcomes'&&group==='metrics'||metricGroup==='quality'&&group==='execution');
 const showTiming=metricGroup==='timing'||metricGroup==='all';
 const runKey=runs.map(r=>r.id).join('|');
 const firms=useMemo(()=>{
  const sets=runs.map(r=>r.meta?.enterprise_ids||[]);
  return sets.length?sets[0].filter(f=>sets.every(s=>s.includes(f))):[];
 },[runKey]);
 const selectedEnterprise=firms.includes(enterprise)?enterprise:firms[0]||'';
 useEffect(()=>{
  let active=true;setLoading(true);setErrors([]);setReviews([]);
  if(runs.length>1&&!selectedEnterprise){setErrors(['These runs have no enterprise in common. Select runs with a shared enterprise to compare.']);setLoading(false);return()=>{active=false;};}
  Promise.all(runs.map(async run=>{
   const {source,name}=reviewLocation(run.dataRoot);
   const boundary=Number(run.meta?.prewarm_rounds??run.meta?.handoff_day??0);
   const end=Number(run.meta?.completed_steps??run.meta?.total_steps)-1;
   const options={decision_phase:phase,...(Number.isFinite(end)?{start:period==='formal'?boundary:0,end}:{})};
   const result=await getArtifactReview(source,name,selectedEnterprise,null,options);
   return {run,result};
  })).then(results=>{if(active){setReviews(results.filter(r=>r.result.data).map(r=>r.result.data));setErrors(results.filter(r=>!r.result.data).map(r=>`${r.run.resolvedRunId||r.run.id}: ${r.result.error||'Review unavailable'}`));setLoading(false);}});
  return()=>{active=false;};
 },[runKey,selectedEnterprise,period,phase]);
 const economics=new Set(reviews.map(r=>r.comparison.economic_contract_sha256).filter(Boolean));
 const provenance=new Set(reviews.map(r=>r.execution_provenance?.source_sha256).filter(Boolean));
 const execution=new Set(reviews.map(r=>JSON.stringify([r.execution_provenance?.configured_models,r.execution_provenance?.configured_route,r.execution_provenance?.dependencies,r.execution_provenance?.cli,r.execution_provenance?.execution_protocol])));
 const windows=new Set(reviews.map(r=>JSON.stringify(r.execution?.window)));
 const matched=reviews.length>1&&economics.size===1&&provenance.size===1&&execution.size===1&&windows.size===1&&reviews.every(r=>r.comparison.economic_contract_sha256&&r.execution_provenance?.source_sha256&&r.execution_provenance?.execution_protocol);
 const download=()=>{
  const data={schema_version:'run_comparison.v1',enterprise:selectedEnterprise,period,decision_phase:phase,display_group:metricGroup,matching_recorded_conditions:matched,reviews};
  const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));
  const link=document.createElement('a');link.href=url;link.download='run-comparison.json';link.click();URL.revokeObjectURL(url);
 };
 return <section className="run-comparison"><div className="review-section-heading"><h3>Selected Run Comparison</h3><button onClick={download} disabled={loading||!reviews.length}>Export comparison JSON</button></div>
  <div className="review-controls"><label>Enterprise <select value={selectedEnterprise} onChange={e=>setEnterprise(e.target.value)} disabled={!firms.length}>{firms.map(f=><option key={f}>{f}</option>)}{!firms.length&&<option value="">No shared enterprise</option>}</select></label>
   <label>Quality window <select value={period} onChange={e=>setPeriod(e.target.value)}><option value="all">All recorded rounds</option><option value="formal">After warmup</option></select></label>
   <label>Decision phase <select value={phase} onChange={e=>setPhase(e.target.value)}><option value="all">All phases</option><option value="decision">Decision</option><option value="trade">Trade</option></select></label><label>Metrics <select value={metricGroup} onChange={e=>setMetricGroup(e.target.value)}><option value="quality">Execution quality</option><option value="outcomes">Operating outcomes</option><option value="timing">Timing</option><option value="all">All metrics</option></select></label></div>
  <p>{matched?'Matching recorded economic, source, model-route, protocol and window fingerprints; decision regimes differ by arm.':'Descriptive comparison · execution conditions are not fully matched in every record.'}</p>
   <p>Operating outcomes use each run’s final snapshot. Quality uses the selected window and enterprise; run duration uses the archived start and finish timestamps.</p>
  {loading&&<LoadingState label="Loading selected run comparisons…" variant="metrics"/>}{errors.map(error=><p role="alert" key={error}>{error}</p>)}
  <div className="review-table-scroll"><table><thead><tr><th>Run / enterprise</th><th>Arm / window</th>{visibleColumns.map(([key,label])=><th key={key}>{label}</th>)}{showTiming&&<><th>Whole-run duration</th><th>Summed phase durations</th></>}</tr></thead><tbody>
   {reviews.map(r=><tr key={`${r.run_id}:${r.enterprise}`}><td data-label="Run / enterprise"><span>{r.run_id}<br/>{r.enterprise}</span></td><td data-label="Arm / window"><span>{displayArm(r.arm)}<br/>R{r.execution?.window.start}–R{r.execution?.window.end}</span></td>{visibleColumns.map(([key,label,group])=><td key={key} data-label={label}><span>{displayMetric(group==='metrics'?r.metrics[key]:r.execution?.metrics[key])}</span></td>)}{showTiming&&<><td data-label="Whole-run duration"><span>{displayMetric(r.execution?.timing.run_wall)}<small className="review-help">{r.execution?.timing.basis}</small></span></td><td data-label="Summed phase durations"><span>{displayMetric(r.execution?.timing.phase_elapsed_sum)}</span></td></>}</tr>)}
  </tbody></table></div>
 </section>;
}
