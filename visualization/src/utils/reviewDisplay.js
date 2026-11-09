export function displayMetric(metric){
 if(!metric)return 'Not recorded';
 if(metric.availability==='not_applicable')return 'Not applicable';
 if(metric.availability==='changed_since_publication')return 'Changed since publication';
 if(metric.availability==='failed_to_load')return 'Could not load';
 if(metric.value==null)return 'Not recorded';
 if(metric.availability&&metric.availability!=='available')return 'Unavailable';
 const value=metric.unit==='ratio'?metric.value*100:metric.value;
 if(metric.unit==='seconds'){
  const seconds=Math.round(value);return `${Math.floor(seconds/3600)}h ${Math.floor(seconds%3600/60)}m ${seconds%60}s`;
 }
 return `${value.toLocaleString('en-US',{maximumFractionDigits:2})}${['ratio','percent'].includes(metric.unit)?'%':` ${metric.unit}`}`;
}
export function reviewLocation(dataRoot=''){
 const [source,...parts]=dataRoot.replace(/^\//,'').split('/');
 return {source,name:parts.map(decodeURIComponent).join('/')};
}

export function displayArm(value){
 const names={ma:'MA',na:'NA',nc:'NC',sa:'SA',organizational:'MA',centralized:'SA'};
 return names[value] || value || 'Scenario controller';
}
