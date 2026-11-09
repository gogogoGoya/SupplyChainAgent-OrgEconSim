/* Fault injection verifies visible recovery, retained navigation, and retry behavior. */
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'@playwright/test');
const {spawn,spawnSync}=require('node:child_process');
const fs=require('node:fs');const os=require('node:os');const path=require('node:path');const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../..');
const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'sca-viewer-recovery-'));
const python=process.env.PYTHON_BIN||'python3';
const base=process.env.BROWSER_BASE_URL||`http://127.0.0.1:${process.env.BROWSER_RECOVERY_PORT||'18775'}`;
const outputs=path.join(process.env.BROWSER_TEST_OUTPUT||temporary,'recovery');fs.mkdirSync(outputs,{recursive:true});
const checks=[];const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));let server;
(async()=>{
 if(!process.env.BROWSER_BASE_URL){
  const fixture=spawnSync(python,['test/viewer_fixture.py',temporary],{cwd:root,encoding:'utf8'});assert.equal(fixture.status,0,fixture.stderr);
  const env={...process.env,SUPPLY_CHAIN_PLATFORM_MODE:'visitor',SUPPLY_CHAIN_OPERATOR_TOKEN:'',
   SUPPLY_CHAIN_PUBLIC_RESULTS_ROOT:path.join(temporary,'public'),SUPPLY_CHAIN_RUNTIME_ROOT:path.join(temporary,'runtime'),
   SIMULATION_OPERATIONS_SQLITE_PATH:path.join(temporary,'runtime/operations.sqlite'),
   SUPPLY_CHAIN_FRONTEND_DIST:process.env.SUPPLY_CHAIN_FRONTEND_DIST||path.join(root,'visualization/dist')};
  server=spawn(python,['-m','uvicorn','simulate.simulation_server:app','--host','127.0.0.1','--port',new URL(base).port],{cwd:root,env,stdio:['ignore','pipe','pipe']});
  const log=fs.createWriteStream(path.join(outputs,'server.log'));server.stdout.pipe(log);server.stderr.pipe(log);
  let ready=false;
  for(let i=0;i<100;i++){
   if(server.exitCode!==null)throw Error(`Viewer exited: ${server.exitCode}`);
   try{if((await fetch(base+'/health')).ok){ready=true;break;}}catch{}
   await pause(200);
  }
  assert.ok(ready,'Recovery test backend is ready');
 }
 const browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_EXECUTABLE_PATH||undefined,args:['--no-sandbox','--disable-dev-shm-usage']});
 async function scenario(name,test){
  const context=await browser.newContext({viewport:{width:1024,height:768}});const page=await context.newPage();page.setDefaultTimeout(30000);
  try{await test(page);await page.screenshot({path:path.join(outputs,name+'.png')});checks.push(name);console.log('PASS '+name);}
  catch(error){await page.screenshot({path:path.join(outputs,name+'-failed.png')}).catch(()=>{});throw error;}
  finally{await context.close();}
 }
 const catalog=/\/operations\/artifacts\/runs(?:\?|$)/;
 const ready=async page=>{await page.goto(base,{waitUntil:'networkidle'});await page.locator('.platform-global-access').waitFor();await page.waitForFunction(()=>document.querySelector('#run-selector')?.options.length>0);};
 const enter=async page=>{const needle=process.env.BROWSER_RUN_NEEDLE||'synthetic-viewer-check';const selected=await page.locator('#run-selector option').evaluateAll((options,n)=>options.find(o=>o.textContent.includes(n))?.value,needle);assert.ok(selected,'An archived run is available');await page.selectOption('#run-selector',selected);await page.locator('.welcome-entry-card.result').click();};
 try{
  await scenario('initial-bundle-failure',async page=>{
   await page.route('**/assets/index-*.js',route=>route.abort('failed'));
   await page.goto(base,{waitUntil:'domcontentloaded'});
   await page.getByText('The viewer could not be loaded. Reload to try again.',{exact:true}).waitFor();
   await page.getByRole('button',{name:'Reload viewer',exact:true}).first().waitFor();
   assert.equal(await page.locator('#root .viewer-boot').getAttribute('aria-busy'),'false');
  });
  await scenario('slow-initial-bundle',async page=>{
   let release;const gate=new Promise(resolve=>{release=resolve;});
   await page.route('**/assets/index-*.js',async route=>{await gate;await route.continue();});
   await page.goto(base,{waitUntil:'commit'});await page.locator('#root .viewer-boot').waitFor();
   assert.equal(await page.locator('#root .viewer-boot').getAttribute('aria-busy'),'true');
   release();await page.locator('.platform-global-access').waitFor();
  });
  for(const [name,body,contentType,status] of [
   ['catalog-service-failure','Unavailable','text/plain',503],
   ['catalog-invalid-json','{invalid','application/json',200],
  ])await scenario(name,async page=>{
   await page.route(catalog,route=>route.fulfill({status,contentType,body}));
   await page.goto(base,{waitUntil:'networkidle'});await page.getByText(/Failed to load the run catalog:/).waitFor();
   await page.unroute(catalog);await page.getByRole('button',{name:'Retry run list',exact:true}).click();
   await page.waitForFunction(()=>document.querySelector('#run-selector')?.options.length>0);
   assert.equal(await page.locator('#viewer-recovery').isVisible(),false);
  });
  for(const [name,status,body] of [
   ['stale-view-module',503,'Unavailable'],
   ['view-object-exception',200,'throw {message:{invalid:true}}; export default function View() {}'],
   ['view-null-exception',200,'throw null; export default function View() {}'],
  ])await scenario(name,async page=>{
   await ready(page);await page.route('**/assets/ExperimentOperationsView-*.js',route=>route.fulfill({status,contentType:'text/javascript',body}));
   await page.locator('.welcome-entry-card.operations').click();
   await page.getByRole('alert').filter({hasText:'This view could not be displayed'}).waitFor();
   await page.getByRole('button',{name:'Reload viewer',exact:true}).first().waitFor();
   assert.equal(await page.locator('#viewer-recovery').isVisible(),false);
   await page.getByRole('button',{name:'Back to Start',exact:true}).click();await page.locator('#run-selector').waitFor();
  });
  await scenario('slow-view-module',async page=>{
   await ready(page);let release;const gate=new Promise(resolve=>{release=resolve;});
   await page.route('**/assets/ExperimentOperationsView-*.js',async route=>{await gate;await route.continue();});
   await page.locator('.welcome-entry-card.operations').click();
   await page.locator('.view-loading').waitFor();await page.getByRole('button',{name:'Back to Start',exact:true}).waitFor();
   release();await page.getByText('Visitor access · read only',{exact:true}).waitFor();
  });
  await scenario('archive-metadata-failure',async page=>{
   await page.route('**/run_meta.json',route=>route.fulfill({status:404,contentType:'application/json',body:'{}'}));await ready(page);
   await enter(page);await page.getByRole('heading',{name:'Archive unavailable',exact:true}).waitFor();
   await page.unroute('**/run_meta.json');await page.getByRole('button',{name:'Retry archive',exact:true}).click();
   await page.locator('.run-evidence-panel .review-metric-grid strong').first().waitFor();
  });
  for(const event of ['error','unhandledrejection'])await scenario('empty-root-'+event,async page=>{
   await ready(page);
   await page.evaluate(type=>{
    document.getElementById('root').replaceChildren();
    const event=type==='error'?new ErrorEvent('error',{message:'Injected fatal error'}):new PromiseRejectionEvent('unhandledrejection',{promise:Promise.resolve(),reason:new Error('Injected fatal rejection')});
    window.dispatchEvent(event);
   },event);
   await page.locator('#viewer-recovery').waitFor();await page.locator('#viewer-recovery').getByRole('button',{name:'Reload viewer',exact:true}).click();
   await page.locator('.platform-global-access').waitFor();assert.equal(await page.locator('#viewer-recovery').isVisible(),false);
  });
  await scenario('handled-page-error-retains-view',async page=>{
   await ready(page);await page.evaluate(()=>window.dispatchEvent(new ErrorEvent('error',{message:'Injected nonfatal error'})));
   await page.waitForTimeout(50);assert.equal(await page.locator('#viewer-recovery').isVisible(),false);assert.equal(await page.locator('#run-selector').isVisible(),true);
  });
 }finally{await browser.close();}
 console.log(JSON.stringify({passed:true,checks,model_calls:0,outputs}));
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(()=>server?.kill('SIGTERM'));
