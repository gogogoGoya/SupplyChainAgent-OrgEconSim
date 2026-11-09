import {test} from 'node:test';
import assert from 'node:assert/strict';
import {normalizeReviewState,metric} from '../src/utils/reviewMetrics.mjs';

test('recruitment batches and people have distinct denominators',()=>{
  const result=normalizeReviewState({recruitment_status:{total_recruited:4,recruitment_history:[2,2,1,1].map((num_people,index)=>({num_people,record_id:`REC_${index}`,status:'onboarded'}))}},'hr');
  assert.equal(result.recruitment_status.completed_recruitment_batches,4);
  assert.equal(result.recruitment_status.recruited_people,6);
  const partial=normalizeReviewState({recruitment_status:{total_recruited:100,recruitment_history:[{num_people:2}]}},'hr');
  assert.equal(partial.recruitment_status.recruited_people,null);
});
test('sales exposes quantity fill and actual product backlog',()=>{
  const result=normalizeReviewState({service_level_summary:{fill_rate:3160/4720},demand_backlog:{by_product:{PRODUCT_1:{backlog_quantity:1560}}}},'sales');
  assert.equal(result.review_backlog[0].backlog_quantity,1560);
  assert.ok(Math.abs(result.review_fill_rate*100-66.94915)<0.0001);
  assert.equal(metric(null,'SCU','fixture').availability,'missing');
});
