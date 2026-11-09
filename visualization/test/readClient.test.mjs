import test from 'node:test';
import assert from 'node:assert/strict';
import {readResource,cancelViewReads,clearReadCache,getReadActivity,subscribeReadActivity} from '../src/utils/readClient.mjs';

const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
test('read transport keeps text and JSON separate, deduplicates reads and bounds body parsing',async()=>{
 const original=global.fetch;let calls=0,active=0,peak=0;
 try{
  clearReadCache();global.fetch=async()=>{calls++;active++;peak=Math.max(peak,active);return {ok:true,status:200,text:async()=>{await wait(5);active--;return '{"value":7}';}};};
  const [json,text,copy]=await Promise.all([readResource('/same',{cacheTtlMs:1000}),readResource('/same',{format:'text'}),readResource('/same')]);
  assert.deepEqual(json.data,{value:7});assert.equal(text.data,'{"value":7}');assert.equal(copy,json);assert.equal(calls,2);
  const cached=await readResource('/same');assert.deepEqual(cached.data,{value:7});assert.equal(calls,2);
  await Promise.all(Array.from({length:24},(_,i)=>readResource('/many/'+i)));
  assert.ok(peak<=8,`Concurrent bodies: ${peak}`);
 }finally{global.fetch=original;clearReadCache();}
});
test('malformed JSON, transient GET failures and deadlines have explicit results',async()=>{
 const original=global.fetch;
 try{
  global.fetch=async()=>new Response('<html>unavailable</html>');assert.equal((await readResource('/invalid')).code,'invalid_json');
  let calls=0;global.fetch=async()=>++calls===1?new Response('',{status:503}):new Response('{"ready":true}');
  assert.equal((await readResource('/retry',{retries:1})).data.ready,true);assert.equal(calls,2);
  global.fetch=async(url,{signal})=>new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(new Error('aborted'))));
  assert.equal((await readResource('/slow',{timeoutMs:15})).code,'timeout');
 }finally{global.fetch=original;clearReadCache();}
});
test('leaving a view cancels queued and running reads without blocking the next view',async()=>{
 const original=global.fetch;
 try{
  global.fetch=async(url,{signal})=>url==='/new'?new Response('{}'):new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(new Error('aborted'))));
  const old=Array.from({length:20},(_,i)=>readResource('/old/'+i));cancelViewReads();
  assert.equal((await readResource('/new')).ok,true);assert.ok((await Promise.all(old)).every(r=>r.code==='cancelled'));
 }finally{global.fetch=original;clearReadCache();}
});

test('loading activity settles after running and queued reads are cancelled',async()=>{
 const original=global.fetch;const counts=[];const unsubscribe=subscribeReadActivity(()=>counts.push(getReadActivity().pending));
 try{
  global.fetch=async(url,{signal})=>new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(new Error('aborted'))));
  const reads=Array.from({length:14},(_,index)=>readResource('/activity/'+index));
  assert.equal(getReadActivity().pending,14);cancelViewReads();await Promise.all(reads);await wait(0);
  assert.equal(getReadActivity().pending,0);assert.equal(counts.at(-1),0);
  global.fetch=async()=>new Response('unreadable');assert.equal((await readResource('/activity-invalid')).code,'invalid_json');await wait(0);
  assert.equal(getReadActivity().pending,0);
 }finally{unsubscribe();global.fetch=original;clearReadCache();}
});
