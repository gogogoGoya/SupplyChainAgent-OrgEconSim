import test from 'node:test';
import assert from 'node:assert/strict';
import {extractRunExperimentTag, getRunExperimentTag} from '../src/utils/runNaming.mjs';

test('published names retain scenario, architecture arm, and condition tags', () => {
  const cases = [
    ['FD1_Market-Development__AG__run_55addd212e89', 'FD1_Market-Development__AG'],
    ['FD2_Procurement-Arrival__NA__run_17c499f18e2f', 'FD2_Procurement-Arrival__NA'],
    ['FD3_Capacity-Investment__NC__run_4a7232d8573e', 'FD3_Capacity-Investment__NC'],
    ['FD4_Staffing-Recovery__RB__run_9a97da677b84', 'FD4_Staffing-Recovery__RB'],
    ['FD5_Cash-Sensitive-Operations__SA__run_436ed12b0bfe', 'FD5_Cash-Sensitive-Operations__SA'],
    ['EM-BW_Bullwhip__AG-OC__run_5ce2a8bfd692', 'EM-BW_Bullwhip__AG-OC'],
    ['EM-CM_Commons__AG__run_16a5f008fc24', 'EM-CM_Commons__AG'],
    ['EM-CW_Cobweb__RB__Bounded-Divergent__run_33134dfcdcf0', 'EM-CW_Cobweb__RB__Bounded-Divergent'],
    ['EM-HD_Herding__SA__Peer-Hidden__run_13d0b72ea16c', 'EM-HD_Herding__SA__Peer-Hidden'],
    ['EM-HD_Herding__RB__Peer-Visible__Synchronized-Expansion__run_c6f7d9863ac4', 'EM-HD_Herding__RB__Peer-Visible__Synchronized-Expansion'],
    ['LH-SS_Sequential-Shocks__AG__200-Rounds__run_f3ca897020cb', 'LH-SS_Sequential-Shocks__AG__200-Rounds'],
  ];
  for (const [name, expected] of cases) {
    assert.equal(extractRunExperimentTag(name), expected);
    assert.equal(extractRunExperimentTag(`/workspace_jobs/${name}/`), expected);
  }
});

test('both historical suffix formats keep their exact tag values', () => {
  assert.equal(extractRunExperimentTag('run_abc123__started_2026-10-20T15_49_08_paired_ma'), 'paired_ma');
  assert.equal(extractRunExperimentTag('run_2026-07-14_103000_beer_game_C3'), 'beer_game_C3');
  assert.equal(extractRunExperimentTag('/simulation_runs/run_2026-07-14_103000_peer_visible/'), 'peer_visible');
  assert.equal(extractRunExperimentTag('run_abc123__started_2026-10-20T15_49_08_tag__run_xyz'), 'tag__run_xyz');
});

test('untagged and malformed names are not promoted to highlighted experiments', () => {
  for (const value of [undefined, null, '', '/', 'run_abc123',
    'run_abc123__started_2026-10-20T15_49_08', 'run_2026-07-14_103000',
    'FD4_Staffing-Recovery__AG', 'FD4_Staffing-Recovery__AG__run_',
    'FD4_Staffing-Recovery__UNKNOWN__run_abc', 'FD4__AG__run_abc',
    'FD4_Staffing-Recovery__AG__run_abc.json', 'unrelated__run_abc']) {
    assert.equal(extractRunExperimentTag(value), '', String(value));
  }
});

test('catalog tag lookup preserves source precedence and metadata fallbacks', () => {
  const prefix = 'EM-HD_Herding__SA__Peer-Hidden__run_13d0b72ea16c';
  const legacy = 'run_2026-07-14_103000_peer_visible';
  assert.equal(getRunExperimentTag(prefix, legacy), 'EM-HD_Herding__SA__Peer-Hidden');
  assert.equal(getRunExperimentTag('run_13d0b72ea16c', legacy), 'peer_visible');
  assert.equal(getRunExperimentTag('', 'run_13d0b72ea16c', `/workspace_jobs/${prefix}`), 'EM-HD_Herding__SA__Peer-Hidden');
  assert.equal(getRunExperimentTag('run_13d0b72ea16c'), '');
});
