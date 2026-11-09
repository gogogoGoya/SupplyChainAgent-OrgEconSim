// Shared read-only transport: bounded parsing, cancellation, deduplication and cache.
const queue=[];const running=new Set();const pending=new Map();const cache=new Map();
const activityListeners=new Set();let activity=Object.freeze({pending:0});
export const getReadActivity=()=>activity;
export function subscribeReadActivity(listener){activityListeners.add(listener);return()=>activityListeners.delete(listener);}
function publishActivity(){const count=queue.length+running.size;if(count===activity.pending)return;activity=Object.freeze({pending:count});for(const listener of activityListeners)listener();}
const LIMIT=8,MAX_CACHE_BYTES=24*1024*1024;let bytes=0;
const failure=(code,error,status=0)=>({ok:false,code,error,status,data:null});
function pump(){
 while(running.size<LIMIT&&queue.length){
  const job=queue.shift();if(job.controller.signal.aborted){job.resolve(failure('cancelled','Request cancelled'));continue;}
  running.add(job);job.run().then(job.resolve,error=>job.resolve(failure('network_error',error?.message||'The request failed'))).finally(()=>{running.delete(job);pump();publishActivity();});
 }
}
function cached(key){const value=cache.get(key);if(!value)return null;if(value.expires<Date.now()){cache.delete(key);bytes-=value.bytes;return null;}cache.delete(key);cache.set(key,value);return value.result;}
function remember(key,result,size,ttl){
 if(!ttl||size>2*1024*1024)return;
 if(cache.has(key)){bytes-=cache.get(key).bytes;cache.delete(key);}
 cache.set(key,{result,bytes:size,expires:Date.now()+ttl});bytes+=size;
 while(bytes>MAX_CACHE_BYTES||cache.size>192){const old=cache.keys().next().value;bytes-=cache.get(old).bytes;cache.delete(old);}
}
export function clearReadCache(){cache.clear();bytes=0;}
export function cancelViewReads(){
 for(const job of [...queue,...running])if(job.group==='view'){job.controller.abort();pending.delete(job.key);}
}
export function readResource(url,{format='json',headers={},identity=0,group='view',timeoutMs=20000,
 cacheTtlMs=0,forceRefresh=false,retries=0,signal,priority=0}={}){
 const key=`${identity}:${format}:${url}`;
 if(!forceRefresh){const hit=cached(key);if(hit)return Promise.resolve(hit);if(pending.has(key))return pending.get(key);}
 const controller=new AbortController();const abort=()=>controller.abort();signal?.addEventListener('abort',abort,{once:true});
 if(signal?.aborted)controller.abort();
 let timedOut=false;const timer=setTimeout(()=>{timedOut=true;controller.abort();},timeoutMs);
 let resolve;const promise=new Promise(r=>{resolve=r;});
 const job={controller,group,resolve,key,run:async()=>{
  let result;
  for(let attempt=0;attempt<=retries;attempt++){
   try{
    const response=await fetch(url,{headers,signal:controller.signal,cache:'no-cache'});
    if(!response.ok){result=failure('http_error',`Request failed (${response.status})`,response.status);if([502,503,504].includes(response.status)&&attempt<retries)continue;return result;}
    const text=await response.text();let data;
    try{data=format==='json'?JSON.parse(text):text;}catch{return failure('invalid_json','The server returned unreadable JSON',response.status);}
    result={ok:true,status:response.status,data,code:'available',error:''};remember(key,result,text.length*2,cacheTtlMs);return result;
   }catch(error){
    if(controller.signal.aborted)return failure(timedOut?'timeout':'cancelled',timedOut?'The request timed out. Retry or choose another view.':'Request cancelled');
    result=failure('network_error',error?.message||'The server could not be reached');
    if(attempt===retries)return result;
   }
  }
  return result;
 }};
 const finish=promise.then(value=>value).finally(()=>{
  clearTimeout(timer);signal?.removeEventListener('abort',abort);
  if(pending.get(key)===finish)pending.delete(key);
 });
 // Resolve queued cancellation immediately instead of waiting behind old archive reads.
 controller.signal.addEventListener('abort',()=>{const index=queue.indexOf(job);if(index>=0){queue.splice(index,1);resolve(failure(timedOut?'timeout':'cancelled',timedOut?'The request timed out':'Request cancelled'));publishActivity();}},{once:true});
 pending.set(key,finish);priority>0?queue.unshift(job):queue.push(job);pump();publishActivity();return finish;
}
