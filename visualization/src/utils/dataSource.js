import {operatorHeaders,getAccessEpoch} from './operationsApi.js';
import {readResource,clearReadCache,cancelViewReads} from './readClient.mjs';
export {cancelViewReads};
export const LATEST_RUN_ID='latest';
export const resolveDataRoot=(id=LATEST_RUN_ID)=>id===LATEST_RUN_ID?'/workspace_multi':`/simulation_runs/${encodeURIComponent(id)}`;
export const resolveWorkspaceJobRoot=name=>`/workspace_jobs/${encodeURIComponent(name)}`;
const roots=new Set(),manifests=new Map(),states=new Map();
export const buildDataUrl=(root='/workspace_multi',relative='')=>{root=(root||'/workspace_multi').replace(/\/$/,'');roots.add(root);return `${root}/${String(relative).replace(/^\//,'')}`;};
const key=url=>`${getAccessEpoch()}:${url}`;
export const getDataRequestState=url=>states.get(key(url))||{availability:'not_loaded'};
async function fetchManifest(root,options){
 const k=key(root);const old=manifests.get(k);
 if(old&&old.expires>Date.now()&&!options.forceRefresh)return old.promise;
 const promise=readResource(`${root}/public_manifest.json`,{identity:getAccessEpoch(),headers:operatorHeaders(),group:'view',priority:1,timeoutMs:options.timeoutMs||20000,signal:options.signal}).then(result=>{
  if(!result.ok){manifests.delete(k);return result;}return result;
 });
 manifests.set(k,{promise,expires:Date.now()+60000});
 while(manifests.size>6)manifests.delete(manifests.keys().next().value);
 return promise;
}
async function read(url,format,options={}){
 const k=key(url);states.set(k,{availability:'loading'});
 const root=[...roots].filter(r=>String(url).startsWith(r+'/')).sort((a,b)=>b.length-a.length)[0];
 if(root&&!url.endsWith('/public_manifest.json')&&!url.endsWith('/run_meta.json')){
  const manifest=await fetchManifest(root,options);
  if(!manifest.ok&&manifest.status!==404){states.set(k,{availability:manifest.code==='cancelled'?'cancelled':'failed_to_load',reason:manifest.error});return null;}
  const relative=String(url).slice(root.length+1);
  if(manifest.data?.files&&!(relative in manifest.data.files)&&!url.endsWith('/')){states.set(k,{availability:'missing',source:'publication_manifest'});return null;}
 }
 const result=await readResource(url,{format,identity:getAccessEpoch(),headers:{...operatorHeaders(),...options.headers},group:'view',
  timeoutMs:options.timeoutMs||20000,cacheTtlMs:options.cacheTtlMs??60000,forceRefresh:options.forceRefresh,retries:1,signal:options.signal});
 states.set(k,{availability:result.ok?'available':result.status===404?'missing':result.code==='cancelled'?'cancelled':'failed_to_load',reason:result.error,status:result.status});
 if(states.size>1024)states.delete(states.keys().next().value);
 return result.ok?result.data:null;
}
export const safeFetchJson=(url,options={})=>read(url,'json',options);
export const safeFetchText=(url,options={})=>read(url,'text',options);
export function clearDataSourceCache(){cancelViewReads();clearReadCache();manifests.clear();states.clear();}
