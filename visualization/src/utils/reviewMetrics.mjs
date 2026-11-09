// Normalize archived observations without changing their source files.
export const entriesOf = (value) => Array.isArray(value) ? value : Object.values(value || {});
export const finiteValue = (value) => value === null || value === undefined || value === '' || !Number.isFinite(Number(value)) ? null : Number(value);
export const metric = (value, unit, source, numerator = null, denominator = null) => ({
  value: finiteValue(value), unit, source, numerator, denominator,
  availability: finiteValue(value) === null ? 'missing' : 'available',
});

export function normalizeReviewState(input, department) {
  const state = {...(input || {})};
  if (department === 'hr') {
    const status = {...state.recruitment_status};
    const history = status.recruitment_history || [];
    const complete = status.total_recruited != null && history.length === Number(status.total_recruited)
      && new Set(history.map(r=>r.record_id)).size === history.length
      && history.every(r => ['onboarded','completed','已到岗'].includes(r.status) && r.num_people != null && Number.isFinite(Number(r.num_people)));
    status.completed_recruitment_batches ??= status.total_recruited;
    status.recruited_people ??= complete ? history.reduce((s,r)=>s+Number(r.num_people),0) : null;
    state.recruitment_status = status;
  }
  if (department === 'inventory') {
    const items = state.inventory_items || [];
    state.total_inventory ??= items.length ? items.reduce((sum,item)=>sum+(finiteValue(item.quantity) ?? 0),0) : null;
  }
  if (department === 'sales') {
    state.orders_summary = {total:state.sales_metrics?.total_orders,...state.orders_summary};
    state.markets_summary = {active:state.markets ? entriesOf(state.markets).filter(m=>m.status==='active').length : null,...state.markets_summary};
    state.review_backlog = Object.entries(state.demand_backlog?.by_product || {}).map(([product,v])=>({product,...v}));
    state.review_fill_rate = state.service_level_summary?.fill_rate ?? (
      state.sales_metrics?.total_downstream_demand > 0
        ? state.sales_metrics.fulfilled_downstream_demand / state.sales_metrics.total_downstream_demand : null);
  }
  if (department === 'production') {
    const lines = state.production_lines || {};
    const active = Object.entries(lines.by_status || {}).filter(([,count])=>Number(count)>0);
    const commonStatus = active.length===1 && Number(active[0][1])===Number(lines.total) ? active[0][0] : null;
    state.production_lines = {...lines,details:entriesOf(lines.details).map(line=>({...line,status:line.status ?? commonStatus}))};
  }
  return state;
}

export function reviewQuality(quality = {}) {
  return [
    ['Valid first attempts',quality.first_attempt_valid_tasks,quality.model_task_count],
    ['Available decision phases',quality.available_phases,quality.phase_count],
    ['Timeouts',quality.timeout_attempts,null],
    ['Recovered tasks',quality.recovered_tasks,null],
    ['Business rejections',quality.business_rejections,null],
    ['Business repairs',quality.business_repairs,null],
    ['Unresolved rejections',quality.unresolved_rejections,null],
    ['Unknown receipts',quality.unknown_execution_results,null],
  ].map(([label,value,total])=>({label,value:finiteValue(value),total:finiteValue(total)}));
}

export function englishDepartmentLabel(value){
 const names={'生产部门':'Production','采购部门':'Procurement','销售部门':'Sales','仓储部门':'Inventory','人力资源部门':'Human Resources','财务管理部门':'Finance'};
 return names[value] || value;
}
