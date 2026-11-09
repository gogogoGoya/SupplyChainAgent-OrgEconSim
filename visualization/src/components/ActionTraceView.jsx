import React,{useState} from 'react';
import LazyDetails from './LazyDetails';
import {buildDataUrl} from '../utils/dataSource';

export default function ActionTraceView({trace,dataRoot}){
 const [filter,setFilter]=useState('all');
 const rows=(trace?.actions||[]).filter(a=>filter==='all'||filter==='issues'&&['rejected','unknown','partial','normalized_noop'].includes(a.status)||filter==='repairs'&&a.parent_ids.length);
 return <section className="execution-trace">
  <div className="review-section-heading"><h4>Action execution and repair trace</h4>
   <label>Show <select value={filter} onChange={e=>setFilter(e.target.value)}><option value="all">All submissions</option><option value="issues">Rejected or unresolved</option><option value="repairs">Linked replacements</option></select></label></div>
  {!trace?<p>No structured execution trace was published. Department proposal and receipt files remain available below.</p>:<>
   <p>{rows.length} of {trace.actions.length} recorded submissions. Parent links identify replacements; older records without links retain their recorded order.</p>
   <ol className="execution-trace-list">{rows.map((a,i)=><li key={a.id+':'+i} className={`trace-outcome-${a.outcome}`}>
    <LazyDetails summary={<><span>{a.department} · {a.action_name||'Recorded action'}</span><strong>{a.outcome.replaceAll('_',' ')}</strong><small>{a.phase}{a.attempt!=null?` · attempt ${a.attempt}`:''}</small></>}>{()=> <>
     <dl><dt>Action ID</dt><dd><code>{a.id}</code></dd><dt>Replaces</dt><dd>{a.parent_ids.length?a.parent_ids.join(', '):a.lineage_recorded?'Original submission':'Parent linkage not recorded'}</dd><dt>Source</dt><dd>{a.source}</dd></dl>
     {a.feedback.length>0&&<div className="trace-feedback"><strong>Execution feedback</strong>{a.feedback.map((m,index)=><p key={index}>{m}</p>)}</div>}
     <LazyDetails summary="Proposal and actual receipt">{()=> <pre>{JSON.stringify({proposal:a.proposal,receipts:a.receipts},null,2)}</pre>}</LazyDetails>
    </>}</LazyDetails>
   </li>)}</ol>
   <a href={buildDataUrl(dataRoot,trace.end_state_source)} target="_blank" rel="noreferrer">View this round’s end-of-day state</a>
   <small className="review-help">The state snapshot covers all actions and settlement in the round.</small>
  </>}
 </section>;
}
