import React,{useEffect,useState} from 'react';
import {getOperationsAccess} from '../utils/operationsApi';
export default function PlatformAccessBadge(){
 const [access,setAccess]=useState(null);
 useEffect(()=>{
  let active=true;
  const refresh=()=>getOperationsAccess().then(result=>{if(active)setAccess(result.available?result.data:null);});
  refresh();window.addEventListener('platform-access-changed',refresh);
  return()=>{active=false;window.removeEventListener('platform-access-changed',refresh);};
 },[]);
 return <div className="platform-global-access" role="status">
   <strong>{access?.can_operate?'Operator':access?'Visitor · read only':'Connecting to results service'}</strong>
   <span>Public release · {access?.release_version || '2026.11.public.1'}</span>
 </div>;
}
