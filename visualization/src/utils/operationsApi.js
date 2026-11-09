import {readResource,clearReadCache,cancelViewReads} from './readClient.mjs';
const OPERATIONS_BASE = '/operations';

// Operator access is kept in memory only, never in URLs or browser storage.
let operatorToken = '';
let accessEpoch = 0;
export const getAccessEpoch = () => accessEpoch;
export const operatorHeaders = () => operatorToken ? {Authorization:`Bearer ${operatorToken}`} : {};
export const setOperatorToken = (token = '') => {
  operatorToken = String(token).trim(); accessEpoch += 1;cancelViewReads();clearReadCache();
  if (typeof window !== 'undefined') window.dispatchEvent(new Event('platform-access-changed'));
};


const buildQuery = (params = {}) => {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      query.set(key, String(value));
    }
  });
  const text = query.toString();
  return text ? `?${text}` : '';
};

const readOperationsJson = async (response) => {
  const contentType = response.headers.get('content-type') || '';
  if (!contentType.includes('application/json')) {
    return {
      available: false,
      data: null,
      error: `Operations endpoint returned ${contentType || 'non-json response'}`,
    };
  }

  const payload = await response.json();
  if (!response.ok || payload?.ok === false || payload?.status === 'error') {
    return {
      available: response.ok,
      data: null,
      error: payload?.error?.message || payload?.detail || payload?.message || response.statusText,
    };
  }

  return {
    available: true,
    data: payload?.data ?? payload,
    error: '',
  };
};

const requestOperations = async (path, options = {}) => {
  try {
    const headers=new Headers(options.headers||{});
    if(operatorToken)headers.set('Authorization',`Bearer ${operatorToken}`);
    if(!options.method||options.method==='GET'){
      const result=await readResource(`${OPERATIONS_BASE}${path}`,{headers,identity:accessEpoch,format:'json',
        group:path.startsWith('/artifacts/review')||path.startsWith('/artifacts/trend')||path.startsWith('/artifacts/view')?'view':'control',
        timeoutMs:20000,retries:1,cacheTtlMs:path.startsWith('/artifacts/')?5000:0,forceRefresh:options.forceRefresh,
        priority:path.startsWith('/artifacts/review')?1:0,signal:options.signal});
      if(!result.ok)return {available:false,data:null,error:result.error,status:result.status,code:result.code};
      const payload=result.data;
      if(payload===null||typeof payload!=='object')return {available:false,data:null,error:'The server returned an unexpected response format',code:'invalid_response'};
      if(payload?.ok===false||payload?.status==='error')return {available:true,data:null,error:payload?.error?.message||payload?.detail||payload?.message||'The request could not be completed'};
      return {available:true,data:payload?.data??payload,error:'',status:result.status};
    }
    const response=await fetch(`${OPERATIONS_BASE}${path}`,{...options,headers});
    return await readOperationsJson(response);
  }catch(error){return {available:false,data:null,error:error?.message||'The server could not be reached'};}
};

export const listOperationsJobs = async ({ activeOnly = false, status = '', limit = 100 } = {}) => {
  const result = await requestOperations(`/jobs${buildQuery({
    active_only: activeOnly ? 'true' : '',
    status,
    limit,
  })}`);
  return {
    available: result.available,
    jobs: Array.isArray(result.data) ? result.data : [],
    error: result.error,
  };
};

export const listArtifactRuns = async ({
  includeWorkspaceCurrent = true,
  includeWorkspaceJobs = true,
  includeSimulationRuns = true,
  includeOperationsJobs = true,
  limit = 500,
  offset = 0,
} = {}) => {
  const result = await requestOperations(`/artifacts/runs${buildQuery({
    include_workspace_current: includeWorkspaceCurrent ? 'true' : 'false',
    include_workspace_jobs: includeWorkspaceJobs ? 'true' : 'false',
    include_simulation_runs: includeSimulationRuns ? 'true' : 'false',
    include_operations_jobs: includeOperationsJobs ? 'true' : 'false',
    limit,
    offset,
  })}`);
  if(!result.error&&result.available&&(!Array.isArray(result.data?.entries)||!Number.isFinite(Number(result.data?.count))))return {available:false,entries:[],count:0,error:'The server returned an invalid run catalog',status:result.status};
  return {
    status:result.status,
    available: result.available,
    entries: Array.isArray(result.data?.entries) ? result.data.entries : [],
    count: Number(result.data?.count || 0),
    error: result.error,
  };
};

export const resumeArtifactRun = async (payload = {}) => {
  const result = await requestOperations('/artifacts/resume', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return {
    available: result.available,
    job: result.data || null,
    error: result.error,
  };
};

export const getOperationsJob = async (jobId) => {
  if (!jobId) {
    return { available: false, job: null, error: 'jobId is required' };
  }
  const result = await requestOperations(`/jobs/${encodeURIComponent(jobId)}`);
  return {
    available: result.available,
    job: result.data || null,
    error: result.error,
  };
};

export const getOperationsJobSummary = async (jobId) => {
  if (!jobId) {
    return { available: false, summary: null, error: 'jobId is required' };
  }
  const result = await requestOperations(`/jobs/${encodeURIComponent(jobId)}/summary`);
  return {
    available: result.available,
    summary: result.data || null,
    error: result.error,
  };
};

export const createOperationsJob = async (payload) => {
  const result = await requestOperations('/jobs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {}),
  });
  return {
    available: result.available,
    job: result.data || null,
    error: result.error,
  };
};

export const startOperationsJob = async (jobId, payload = {}) => {
  const result = await requestOperations(`/jobs/${encodeURIComponent(jobId)}/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return {
    available: result.available,
    job: result.data || null,
    error: result.error,
  };
};

export const runOperationsJob = async (jobId, payload = {}) => {
  const result = await requestOperations(`/jobs/${encodeURIComponent(jobId)}/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return {
    available: result.available,
    result: result.data || null,
    error: result.error,
  };
};

export const stopOperationsJob = async (jobId, payload = {}) => {
  const result = await requestOperations(`/jobs/${encodeURIComponent(jobId)}/stop`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return {
    available: result.available,
    job: result.data || null,
    error: result.error,
  };
};

export const resumeOperationsJob = async (jobId, payload = {}) => {
  const result = await requestOperations(`/jobs/${encodeURIComponent(jobId)}/resume`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return {
    available: result.available,
    job: result.data || null,
    error: result.error,
  };
};

export const deleteOperationsJob = async (jobId, payload = {}) => {
  if (!jobId) {
    return { available: false, job: null, error: 'jobId is required' };
  }
  const result = await requestOperations(`/jobs/${encodeURIComponent(jobId)}/delete`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ force: true, ...payload }),
  });
  return {
    available: result.available,
    job: result.data || null,
    error: result.error,
  };
};

export const listOperationScenarios = async () => {
  const result = await requestOperations('/scenarios');
  return {
    available: result.available,
    scenarios: Array.isArray(result.data) ? result.data : [],
    error: result.error,
  };
};

export const listOperationsBatches = () => requestOperations('/batches');
export const createOperationsBatch = (payload) => requestOperations('/batches', {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
});
export const controlOperationsBatch = (batchId, action) => requestOperations(
  `/batches/${encodeURIComponent(batchId)}/${action}`, { method: 'POST' }
);

export const getOperationsAccess = () => requestOperations('/access');

export const reconcileOperationsJobs = () => requestOperations('/jobs/reconcile', {method:'POST'});
export const getArtifactReview = (source,name,enterprise='',round=null,options={}) => {const {signal,forceRefresh,...query}=options;return requestOperations(`/artifacts/review${buildQuery({source,name,enterprise,round,...query})}`,{signal,forceRefresh});};
export const getArtifactTrend=(source,name,enterprise='',options={})=>{const {signal,forceRefresh,...query}=options;return requestOperations(`/artifacts/trend${buildQuery({source,name,enterprise,...query})}`,{signal,forceRefresh});};
export const getArtifactView=(source,name,view,options={})=>requestOperations(`/artifacts/view${buildQuery({source,name,view})}`,options);

export const listAllArtifactRuns = async (options={}) => {
  const entries=[];let offset=0;let total=0;
  do {
    const page=await listArtifactRuns({...options,limit:200,offset});
    if(!page.available || page.error)return {...page,entries:[],error:page.error || 'Run catalog unavailable'};
    total=page.count;
    if(!page.entries.length)break;
    entries.push(...page.entries);offset+=page.entries.length;
  }while(offset<total);
  return {available:true,entries,count:total,error:''};
};
