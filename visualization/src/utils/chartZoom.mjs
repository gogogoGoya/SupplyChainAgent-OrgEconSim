// A viewport-sized dialog keeps loading, errors and close controls outside the renderer.
let activeZoom=null;
let sequence=0;

export function openChartZoom({title='Chart detail',trigger,render}){
 activeZoom?.close();
 const dialog=document.createElement('dialog');dialog.className='chart-zoom-dialog';
 const toolbar=document.createElement('div');toolbar.className='chart-zoom-toolbar';
 const heading=document.createElement('h2');heading.id=`chart-zoom-title-${++sequence}`;heading.textContent=title;
 dialog.setAttribute('aria-labelledby',heading.id);
 const closeButton=document.createElement('button');closeButton.type='button';closeButton.className='chart-zoom-close';closeButton.textContent='Close chart';
 toolbar.append(heading,closeButton);
 const plot=document.createElement('div');plot.className='chart-zoom-plot';plot.setAttribute('aria-busy','true');
 const status=document.createElement('div');status.className='chart-zoom-status';status.setAttribute('role','status');status.textContent='Preparing chart…';plot.append(status);
 dialog.append(toolbar,plot);
 const previousOverflow=document.body.style.overflow;let closed=false;let dispose=()=>{};let backdrop;
 const onKey=event=>{if(event.key==='Escape'){event.preventDefault();close();}};
 const close=()=>{
  if(closed)return;closed=true;
  document.removeEventListener('keydown',onKey);
  try{dispose();}catch{/* Closing remains available even if a renderer is already disposed. */}
  if(dialog.open&&typeof dialog.close==='function')dialog.close();
  dialog.remove();backdrop?.remove();document.body.style.overflow=previousOverflow;
  if(activeZoom===session)activeZoom=null;
  if(trigger?.isConnected)trigger.focus({preventScroll:true});
 };
 const session={close,get closed(){return closed;}};activeZoom=session;
 closeButton.addEventListener('click',close);
 dialog.addEventListener('cancel',event=>{event.preventDefault();close();});
 dialog.addEventListener('close',close);
 dialog.addEventListener('click',event=>{if(event.target===dialog)close();});
 document.addEventListener('keydown',onKey);
 document.body.append(dialog);document.body.style.overflow='hidden';
 try{
  if(typeof dialog.showModal!=='function')throw new Error('Dialog API unavailable');
  dialog.showModal();
 }catch{
  backdrop=document.createElement('div');backdrop.className='chart-zoom-backdrop';backdrop.addEventListener('click',close);
  document.body.insertBefore(backdrop,dialog);dialog.classList.add('chart-zoom-dialog--fallback');dialog.setAttribute('open','');dialog.setAttribute('role','dialog');dialog.setAttribute('aria-modal','true');
 }
 closeButton.focus({preventScroll:true});
 Promise.resolve().then(()=>closed?null:render(plot,session)).then(cleanup=>{
  if(typeof cleanup==='function'){if(closed)cleanup();else dispose=cleanup;}
  if(!closed)plot.setAttribute('aria-busy','false');
 }).catch(error=>{
  if(closed)return;
  const message=document.createElement('div');message.className='chart-zoom-error';message.setAttribute('role','alert');
  const text=document.createElement('strong');text.textContent='This chart could not be enlarged.';
  const detail=document.createElement('p');detail.textContent=error?.message||'Close this window and try the current view again.';
  message.append(text,detail);plot.replaceChildren(message);plot.setAttribute('aria-busy','false');
 });
 return session;
}

export function enlargeSvgChart(frame,trigger){
 const card=frame.closest('article');const title=card?.querySelector('h3,h4,[class$="card-title"]')?.textContent?.trim()||'Chart detail';
 return openChartZoom({title,trigger,render:plot=>{
  const original=frame.querySelector('svg');if(!original)throw new Error('No SVG chart is available in this view.');
  const content=document.createElement('div');content.className='chart-zoom-svg';
  const legend=frame.parentElement.querySelector('.herding-svg-legend,.commons-svg-legend,.herding-mini-legend');
  if(legend)content.append(legend.cloneNode(true));
  const stage=document.createElement('div');stage.className='chart-zoom-svg-stage';stage.append(original.cloneNode(true));content.append(stage);plot.replaceChildren(content);
 }});
}
