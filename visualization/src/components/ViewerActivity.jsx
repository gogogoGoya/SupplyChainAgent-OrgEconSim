import React,{useSyncExternalStore} from 'react';
import {getReadActivity,subscribeReadActivity} from '../utils/readClient.mjs';

export default function ViewerActivity(){
 const activity=useSyncExternalStore(subscribeReadActivity,getReadActivity,getReadActivity);
 if(!activity.pending)return null;
 return <div className="viewer-activity" role="status" aria-live="polite">
  <span className="viewer-activity-label">Loading archived data…</span>
  <span className="viewer-activity-track" aria-hidden="true"><span/></span>
 </div>;
}
