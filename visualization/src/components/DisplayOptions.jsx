import React,{useEffect,useRef,useState} from 'react';

/** Show chart controls beside navigation when space permits, with a compact menu below that width. */
export default function DisplayOptions({activeCount=0,children}){
 const ref=useRef(null);
 const [inline,setInline]=useState(()=>typeof window!=='undefined'&&window.matchMedia('(min-width:1360px)').matches);
 useEffect(()=>{
  const query=window.matchMedia('(min-width:1360px)');
  const update=()=>setInline(query.matches);
  query.addEventListener('change',update);update();
  return()=>query.removeEventListener('change',update);
 },[]);
 useEffect(()=>{
  const outside=event=>{const menu=ref.current;if(menu?.open&&!menu.contains(event.target))menu.open=false;};
  const escape=event=>{const menu=ref.current;if(event.key==='Escape'&&menu?.open){menu.open=false;menu.querySelector('summary')?.focus();}};
  document.addEventListener('pointerdown',outside);document.addEventListener('keydown',escape);
  return()=>{document.removeEventListener('pointerdown',outside);document.removeEventListener('keydown',escape);};
 },[]);
 if(inline)return <div className="analysis-display-inline" role="group" aria-label="Chart display options">{children}</div>;
 return <details className="analysis-display-options" ref={ref}>
  <summary>Display options{activeCount>0&&<span className="display-options-count">{activeCount} on</span>}</summary>
  <div className="analysis-toggle-anchor" role="group" aria-label="Chart display options">{children}</div>
 </details>;
}
