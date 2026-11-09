// A visitor's development server must not expose raw repository files via /@fs.
const fs=require('node:fs');const path=require('node:path');const {spawn}=require('node:child_process');const assert=require('node:assert/strict');
const viewer=path.resolve(__dirname,'..');const root=path.dirname(viewer);
const probe=path.join(root,`.viewer-boundary-${process.pid}.json`);const marker='synthetic-private-boundary-marker';
const port=process.env.VITE_BOUNDARY_TEST_PORT || '18775';const base=`http://127.0.0.1:${port}`;
fs.writeFileSync(probe,JSON.stringify({synthetic_private:marker}),{flag:'wx'});
const child=spawn(process.execPath,[path.join(viewer,'node_modules/vite/bin/vite.js'),'--host','127.0.0.1','--port',port,'--strictPort'],{cwd:viewer,stdio:['ignore','pipe','pipe']});
let log='';child.stdout.on('data',b=>{log+=b;});child.stderr.on('data',b=>{log+=b;});
(async()=>{
 for(let i=0;i<100;i++){
  if(child.exitCode!==null)throw Error(`Development viewer exited: ${log}`);
  try{if((await fetch(base)).ok)break;}catch{}
  await new Promise(resolve=>setTimeout(resolve,100));
 }
 const response=await fetch(base+'/@fs'+probe);
 const body=await response.text();assert.equal(response.status,403);assert.equal(body.includes(marker),false);
 console.log('Development filesystem boundary passed; raw repository probe denied.');
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(()=>{child.kill('SIGTERM');fs.unlinkSync(probe);});
