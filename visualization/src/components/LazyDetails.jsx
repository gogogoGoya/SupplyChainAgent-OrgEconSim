import React,{useState} from 'react';
export default function LazyDetails({summary,children,className=''}){
 const [open,setOpen]=useState(false);
 return <details className={className} onToggle={event=>setOpen(event.currentTarget.open)}><summary>{summary}</summary>{open?(typeof children==='function'?children():children):null}</details>;
}
