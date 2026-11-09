import LoadingState from './LoadingState';
import React,{useEffect,useState} from 'react';
import {getArtifactReview} from '../utils/operationsApi';

import {displayMetric,reviewLocation,displayArm} from '../utils/reviewDisplay';
import ExecutionQualityPanel from './ExecutionQualityPanel';
import ActionTraceView from './ActionTraceView';
import LazyDetails from './LazyDetails';
export {default as RunComparison} from './RunComparison';

const headlineMetrics = new Set(['cash','quantity_fill_rate','order_completion_rate','backlog']);

export default function RunEvidencePanel({dataRoot,company,round}){
 const [reload,setReload]=useState(0);const [review,setReview]=useState(null);const [error,setError]=useState('');
 const [qualityPeriod,setQualityPeriod]=useState('all');const [phase,setPhase]=useState('all');
 const [formalStart,setFormalStart]=useState(0);const [refreshing,setRefreshing]=useState(false);
 const qualityStart=qualityPeriod==='formal' && Number(round)>=formalStart?formalStart:0;
 useEffect(()=>{setReview(null);},[dataRoot,company,round]);
 useEffect(()=>{let active=true;setRefreshing(true);setError('');const {source,name}=reviewLocation(dataRoot);
  getArtifactReview(source,name,company,round,{start:qualityStart,end:round,decision_phase:phase,forceRefresh:reload>0}).then(r=>{if(active){setReview(r.data);setError(r.error);setRefreshing(false);if(r.data)setFormalStart(r.data.windows?.formal_start||0);}});
  return()=>{active=false;};
 },[dataRoot,company,round,qualityStart,phase,reload]);
 return <section className="run-evidence-panel" aria-busy={refreshing}>
  <div className="review-evidence-summary">
   <div className="review-context">
    <h3>Run Evidence and Operating Outcomes</h3>
    {review&&!error&&<p>{review.round < 0 ? 'No recorded rounds' : `R${review.round}`} · {displayArm(review.arm)} · {review.phase === 'prewarm_end_of_day' ? 'Scripted warmup · end-of-day observation' : 'Archived department snapshot'} · {review.current_snapshot_complete?'Six current department states available':'Current state evidence incomplete'}</p>}
   </div>
   {error?<div role="alert"><p>Review unavailable: {error}</p><button onClick={()=>setReload(v=>v+1)}>Retry evidence</button></div>:!review?<LoadingState label="Loading operating outcomes…" variant="metrics"/>:
    <div className="review-metric-grid">{Object.entries(review.metrics).filter(([name])=>headlineMetrics.has(name)).map(([name,m])=><article key={name} title={`Source: ${m.source}`}>
     <span>{name.replaceAll('_',' ')}</span><strong>{displayMetric(m)}</strong>
     {m.denominator!=null&&<small>{m.numerator} / {m.denominator}</small>}
    </article>)}</div>}
  </div>
  {review&&!error&&<>
   {refreshing&&<LoadingState label="Updating execution window…" variant="compact"/>}
   <LazyDetails className="review-full-metrics" summary="All operating metrics, decision quality and source details">{()=> <>
    {review.display_coverage && <p>Display coverage checked: {review.display_coverage.integrity.declared_completed_steps} rounds across {review.display_coverage.enterprise_ids.length} enterprises · {review.display_coverage.snapshot_coverage_complete ? 'All six formal department snapshots present' : 'Some formal department snapshots missing or invalid'}.</p>}
    <div className="review-metric-grid">{Object.entries(review.metrics).filter(([name])=>!headlineMetrics.has(name)).map(([name,m])=><article key={name} title={`Source: ${m.source}`}><span>{name.replaceAll('_',' ')}</span><strong>{displayMetric(m)}</strong></article>)}</div>
    <p>Decision prose can refer to an earlier input. Numeric cards identify their archived snapshot source and stage; warmup cards use end-of-day observations.</p>
    <div className="review-controls"><label>Quality window <select value={Number(round)<formalStart?'all':qualityPeriod} onChange={e=>setQualityPeriod(e.target.value)}><option value="all">From R0</option><option value="formal" disabled={round<formalStart}>After warmup</option></select></label><label>Decision phase <select value={phase} onChange={e=>setPhase(e.target.value)}><option value="all">All phases</option><option value="decision">Decision</option><option value="trade">Trade</option></select></label></div>
    <ExecutionQualityPanel execution={review.execution}/>
    <ActionTraceView trace={review.action_trace} dataRoot={dataRoot}/>
    {Object.entries(review.departments).map(([dept,d])=><LazyDetails key={dept} summary={`${dept} · ${d.execution_status} · ${d.record_count} business records`}>
      {()=> <pre>{JSON.stringify({source:d.source,proposals:d.proposals,receipts:d.receipts},null,2)}</pre>}</LazyDetails>)}
   </>}</LazyDetails>
  </>}
 </section>;
}
