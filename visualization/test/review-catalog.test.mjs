import test from 'node:test';
import assert from 'node:assert/strict';
import {listAllArtifactRuns,getArtifactReview} from '../src/utils/operationsApi.js';
import {displayMetric} from '../src/utils/reviewDisplay.js';

test('catalog pagination reaches records beyond the first 500',async()=>{
 const original=global.fetch;const offsets=[];
 try{
  global.fetch=async(url)=>{
   const u=new URL(url,'http://localhost');const offset=Number(u.searchParams.get('offset'));offsets.push(offset);
   return new Response(JSON.stringify({ok:true,data:{count:503,entries:Array.from({length:Math.min(200,503-offset)},(_,i)=>({id:offset+i}))}}),{headers:{'Content-Type':'application/json'}});
  };
  const result=await listAllArtifactRuns();assert.equal(result.entries.length,503);assert.deepEqual(offsets,[0,200,400]);
 }finally{global.fetch=original;}
});

test('comparison passes explicit enterprise and quality window',async()=>{
 const original=global.fetch;let requested;
 try{
  global.fetch=async(url)=>{requested=new URL(url,'http://localhost');return new Response('{"ok":true,"data":{}}',{headers:{'Content-Type':'application/json'}});};
  await getArtifactReview('workspace_jobs','run','Manufacturer',19,{start:10,end:19,decision_phase:'decision'});
  assert.equal(requested.searchParams.get('enterprise'),'Manufacturer');assert.equal(requested.searchParams.get('start'),'10');assert.equal(requested.searchParams.get('end'),'19');
 }finally{global.fetch=original;}
});

test('confirmed zero duration and missing metrics remain distinct',()=>{
 assert.equal(displayMetric({value:0,unit:'seconds'}),'0h 0m 0s');assert.equal(displayMetric({value:null,unit:'count'}),'Not recorded');
});
