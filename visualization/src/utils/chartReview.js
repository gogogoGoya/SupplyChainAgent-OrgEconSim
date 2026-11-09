import {openChartZoom} from './chartZoom.mjs';

const chartBindings=new WeakMap();
// Apply shared presentation rules while preserving data and formatter logic.
const labels={'Turn':'Round','Day':'Round','百万元':'million SCU','万元':'10,000 SCU','元':'SCU','现金':'Cash','收入':'Revenue','利润':'Profit','成本':'Cost','产量':'Output','产能':'Capacity','库存':'Inventory','天':'Rounds'};
const label=value=>typeof value==='string'?(labels[value] || value.replace(/^Day\s+(\d+)$/,'R$1')):value;
export function setReviewChart(chart, option, ...args){
 if(!chart||chart.isDisposed?.())return;
 const normalized={...option};
 for(const key of ['xAxis','yAxis'])if(normalized[key]){
  const items=Array.isArray(normalized[key])?normalized[key]:[normalized[key]];
  const axes=items.map(axis=>({...axis,name:label(axis.name),data:Array.isArray(axis.data)?axis.data.map(label):axis.data,nameGap:axis.nameGap??32}));
  normalized[key]=Array.isArray(normalized[key])?axes:axes[0];
 }
 if(normalized.legend&&!Array.isArray(normalized.legend))normalized.legend={...normalized.legend,type:'scroll',left:'center',width:'90%'};
 if(normalized.tooltip){
  const original=normalized.tooltip.formatter;
  normalized.tooltip={...normalized.tooltip,confine:true,renderMode:'richText'};
  if(typeof original==='function')normalized.tooltip.formatter=(...params)=>String(original(...params)).replace(/<br\s*\/?>/gi,'\n').replace(/<[^>]*>/g,'');
 }
 const axis=Array.isArray(normalized.xAxis)?normalized.xAxis[0]:normalized.xAxis;
 const categories=axis?.data?.length || 0;
 if(Array.isArray(normalized.series)) normalized.series=normalized.series.map(series=>series.type==='heatmap'&&categories>chart.getWidth()/28?{...series,label:{...series.label,show:false}}:series);
 // ECharts media rules also apply when an already-open chart is resized.
 const mobile={};
 if(normalized.grid&&!Array.isArray(normalized.grid))mobile.grid={...normalized.grid,containLabel:true,left:12,right:16};
 if(normalized.xAxis){
  const axes=Array.isArray(normalized.xAxis)?normalized.xAxis:[normalized.xAxis];
  const compact=axes.map(axis=>({...axis,axisLabel:{...axis.axisLabel,fontSize:10,hideOverlap:true,
   ...(!axis.axisLabel?.formatter&&axis.type==='category'?{formatter:value=>String(value).replace(/^(Round|Day)\s+/,'R')}:{}),showMaxLabel:true}}));
  mobile.xAxis=Array.isArray(normalized.xAxis)?compact:compact[0];
 }
 if(normalized.legend&&!Array.isArray(normalized.legend))mobile.legend={...normalized.legend,textStyle:{...normalized.legend.textStyle,fontSize:11,width:150,overflow:'truncate'}};
 if(normalized.series?.some(series=>series.type==='sankey'))mobile.series=normalized.series.map(series=>series.type==='sankey'?{...series,orient:'vertical',left:24,right:24,top:32,bottom:24,label:{...series.label,position:'top',fontSize:11,width:160,overflow:'truncate'}}:series);
 const adaptive=Object.keys(mobile).length?{baseOption:normalized,media:[{query:{maxWidth:500},option:mobile}]}:normalized;
 chart.setOption(adaptive,...args);
 const host=chart.getDom();
 const binding=chartBindings.get(host)||{};binding.chart=chart;chartBindings.set(host,binding);
 if(typeof document!=='undefined'&&host.parentElement){
  if(!binding.button?.isConnected){
   const button=document.createElement('button');button.type='button';button.className='chart-expand-control';button.textContent='Enlarge chart';
   host.parentElement.insertBefore(button,host);binding.button=button;
  }
  // Redraws replace ECharts instances; the button must resolve the latest binding.
  binding.button.onclick=()=>openChartZoom({
   title:host.parentElement.querySelector('.flow-card-header strong,[class$="card-title"],h3,h4')?.textContent?.trim()||'Chart detail',
   trigger:binding.button,
   render:async(plot,session)=>{
    const current=chartBindings.get(host)?.chart;
    if(!current||current.isDisposed())throw new Error('The source chart is no longer available. Close this window and reopen the current view.');
    const option=current.getOption();
    const runtime=await import('./echartsRuntime');if(session.closed)return;
    const target=document.createElement('div');target.className='chart-zoom-canvas';plot.replaceChildren(target);
    const enlarged=runtime.init(target);let observer;
    const resize=()=>{if(!session.closed&&!enlarged.isDisposed())enlarged.resize();};
    try{
     enlarged.setOption(option);
     if(typeof ResizeObserver!=='undefined'){observer=new ResizeObserver(resize);observer.observe(target);}else window.addEventListener('resize',resize);
    }catch(error){enlarged.dispose();throw error;}
    return()=>{observer?.disconnect();window.removeEventListener('resize',resize);if(!enlarged.isDisposed())enlarged.dispose();};
   },
  });
 }
}
