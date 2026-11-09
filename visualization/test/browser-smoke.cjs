/* Exercise the production viewer with synthetic data and no model configuration. */
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
const {spawn,spawnSync}=require('node:child_process');
const fs=require('node:fs');const os=require('node:os');const path=require('node:path');const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../..');
const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'sca-browser-'));
const python=process.env.PYTHON_BIN || 'python3';const port=process.env.BROWSER_TEST_PORT || '18774';
const base=process.env.BROWSER_BASE_URL || `http://127.0.0.1:${port}`;
const outputs=process.env.BROWSER_TEST_OUTPUT || path.join(temporary,'screenshots');fs.mkdirSync(outputs,{recursive:true});
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let server;
(async()=>{
 if(!process.env.BROWSER_BASE_URL){
  const env={...process.env,SUPPLY_CHAIN_PLATFORM_MODE:'visitor',SUPPLY_CHAIN_OPERATOR_TOKEN:'',
   SUPPLY_CHAIN_PUBLIC_RESULTS_ROOT:path.join(temporary,'public'),SUPPLY_CHAIN_RUNTIME_ROOT:path.join(temporary,'runtime'),
   SIMULATION_OPERATIONS_SQLITE_PATH:path.join(temporary,'runtime/operations.sqlite'),
   SUPPLY_CHAIN_FRONTEND_DIST:process.env.SUPPLY_CHAIN_FRONTEND_DIST||path.join(root,'visualization/dist')};
  server=spawn(python,['-m','uvicorn','simulate.simulation_server:app','--host','127.0.0.1','--port',port],{cwd:root,env,stdio:['ignore','pipe','pipe']});
  const log=fs.createWriteStream(path.join(outputs,'server.log'));server.stdout.pipe(log);server.stderr.pipe(log);
  for(let attempt=0;attempt<100;attempt++){
   if(server.exitCode!==null)throw Error(`Viewer exited: ${server.exitCode}`);
   try{if((await fetch(base+'/health')).ok)break;}catch{}
   await pause(200);
  }
 }
 const browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_EXECUTABLE_PATH||undefined,args:['--no-sandbox','--disable-dev-shm-usage']});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000},locale:'en-US'});const errors=[];
  page.on('pageerror',e=>errors.push(String(e)));
  await page.goto(base,{waitUntil:'networkidle'});
  await page.locator('.platform-global-access').waitFor();
  assert.match(await page.locator('.platform-global-access').innerText(),/Visitor/);
  const denied=await page.request.post(base+'/operations/jobs',{data:{scenario_id:'beer_game_scripted'}});
  assert.equal(denied.status(),403);
  if(!process.env.BROWSER_BASE_URL){
   assert.match(await page.locator('body').innerText(),/No (published |archived |recorded )?results|No .*run|No .*data/i);
   const fixture=spawnSync(python,['test/viewer_fixture.py',temporary],{cwd:root,encoding:'utf8'});
   assert.equal(fixture.status,0,fixture.stderr);await page.reload({waitUntil:'networkidle'});
  }
  const needle=process.env.BROWSER_RUN_NEEDLE || 'synthetic-viewer-check';
  await page.waitForFunction(()=>document.querySelector('#run-selector')?.options.length>0);
  const options=await page.locator('#run-selector option').evaluateAll(nodes=>nodes.map(n=>({text:n.textContent,value:n.value})));
  const selected=options.find(o=>o.text.includes(needle));assert.ok(selected,`No run matching ${needle}`);
  await page.selectOption('#run-selector',selected.value);await page.locator('.welcome-entry-card.result').click();
  await page.locator('.run-evidence-panel .review-metric-grid strong').first().waitFor({timeout:60000});
  if(!process.env.BROWSER_BASE_URL){
   await page.locator('.review-full-metrics > summary').click();
   const text=await page.locator('.run-evidence-panel').innerText();
   for(const pattern of [/79\.91%/,/95\.88%/,/66\.95%/,/6 people/,/4 batches/,/1,560/])assert.match(text,pattern);
   await page.locator('.review-full-metrics > summary').click();
  }
  for(const [width,height] of [[1440,1000],[1024,768],[390,844]]){
   await page.setViewportSize({width,height});await pause(350);await page.evaluate(()=>scrollTo(0,0));
   const dock=page.locator('.round-navigation-dock');
   if(await dock.count()){
    assert.equal(await dock.evaluate(el=>getComputedStyle(el).position),'fixed');
    const bounds=await dock.boundingBox();
    assert.ok(bounds.x>=0&&bounds.x+bounds.width<=width&&bounds.y+bounds.height<=height,'Round controls stay inside the viewport');
   }
   const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+3);
   assert.equal(overflow,false,`Horizontal page overflow at ${width}px`);
   await page.screenshot({path:path.join(outputs,`viewer-${width}.png`),fullPage:true});
   // A chart must remain closable and render at dialog size on every viewport.
   const enlarge=page.locator('.chart-expand-control:visible').first();await enlarge.waitFor();
   for(const closeWithEscape of [true,false]){
    await enlarge.click();const dialog=page.locator('.chart-zoom-dialog');await dialog.waitFor();
    await page.waitForFunction(()=>document.querySelector('.chart-zoom-plot')?.getAttribute('aria-busy')==='false');
    assert.equal(await dialog.locator('.chart-zoom-error').count(),0);
    const canvas=dialog.locator('canvas').first();await canvas.waitFor();
    const bounds=await canvas.boundingBox();assert.ok(bounds.width>width-100&&bounds.height>height/2,'Enlarged renderer fills the dialog');
    if(closeWithEscape)await page.keyboard.press('Escape');else await dialog.getByRole('button',{name:'Close chart',exact:true}).click();
    await dialog.waitFor({state:'detached'});
    assert.equal(await page.evaluate(()=>document.body.style.overflow),'');
    assert.equal(await enlarge.evaluate(el=>document.activeElement===el),true,'Focus returns to the source chart');
   }

  }
  // Inject only the standalone dialog utility to exercise failure and cancellation paths.
  await page.route('**/__chart-zoom-regression.mjs',route=>route.fulfill({contentType:'text/javascript',body:fs.readFileSync(path.join(__dirname,'../src/utils/chartZoom.mjs'),'utf8')}));
  await page.evaluate(async()=>{
   const {openChartZoom}=await import('/__chart-zoom-regression.mjs');
   openChartZoom({render:()=>{throw new Error('Regression: renderer unavailable');}});
  });
  await page.getByRole('alert').filter({hasText:'Regression: renderer unavailable'}).waitFor();
  await page.getByRole('button',{name:'Close chart',exact:true}).click();
  const cancellation=await page.evaluate(async()=>{
   const {openChartZoom}=await import('/__chart-zoom-regression.mjs');
   let complete;let disposed=0;
   const session=openChartZoom({render:()=>new Promise(resolve=>{complete=()=>resolve(()=>{disposed+=1;});})});
   await Promise.resolve();session.close();complete();await new Promise(resolve=>setTimeout(resolve,0));
   return {disposed,dialogs:document.querySelectorAll('.chart-zoom-dialog').length,overflow:document.body.style.overflow};
  });
  assert.deepEqual(cancellation,{disposed:1,dialogs:0,overflow:''});
  await page.evaluate(async()=>{
   const {openChartZoom}=await import('/__chart-zoom-regression.mjs');
   const original=HTMLDialogElement.prototype.showModal;
   try{HTMLDialogElement.prototype.showModal=undefined;openChartZoom({render:plot=>{plot.textContent='Fallback chart';}});}
   finally{HTMLDialogElement.prototype.showModal=original;}
  });
  await page.locator('.chart-zoom-dialog--fallback').getByText('Fallback chart').waitFor();
  await page.keyboard.press('Escape');assert.equal(await page.locator('.chart-zoom-dialog').count(),0);
  assert.deepEqual(errors,[]);console.log(JSON.stringify({passed:true,viewports:3,chart_dialog_checks:9,model_calls:0,outputs}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(()=>{server?.kill('SIGTERM');});
