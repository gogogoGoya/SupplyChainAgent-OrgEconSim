import React,{useEffect,useState} from 'react';

/** Indeterminate loading feedback; no percentage is shown without measured progress. */
export default function LoadingState({label='Loading this view…',description,variant='panel',className=''}){
 const [slow,setSlow]=useState(false);
 useEffect(()=>{const timer=setTimeout(()=>setSlow(true),8000);return()=>clearTimeout(timer);},[]);
 return <section className={`loading-state loading-state--${variant} ${className}`} role="status" aria-live="polite" aria-busy="true">
  <div className="loading-state-heading"><span className="loading-state-spinner" aria-hidden="true"/><strong>{label}</strong></div>
  <div className="loading-state-track" aria-hidden="true"><span/></div>
  {(description||slow)&&<p>{slow?'Still loading this archive. Navigation remains available.':description}</p>}
  {variant!=='compact'&&<div className={`loading-state-skeleton ${variant==='metrics'?'loading-state-skeleton--metrics':''}`} aria-hidden="true">
   {Array.from({length:variant==='metrics'?4:3},(_,index)=><div key={index}><i/><i/></div>)}
   {variant==='chart'&&<div className="loading-state-chart"><span/><span/><span/><span/><span/></div>}
  </div>}
 </section>;
}
