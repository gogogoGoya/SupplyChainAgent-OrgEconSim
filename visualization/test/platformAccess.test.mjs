import assert from 'node:assert/strict';
import { test } from 'node:test';
import { getOperationsAccess, setOperatorToken } from '../src/utils/operationsApi.js';

test('operator credentials are optional, sent as a header, and removable', async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, authorization: options.headers.get('Authorization') });
    return { ok: true, headers: new Headers({ 'content-type': 'application/json' }),
      text: async () => JSON.stringify({ ok: true, data: { mode: 'visitor', can_operate: false } }) };
  };
  try {
    setOperatorToken('');
    await getOperationsAccess();
    setOperatorToken('fixture-access-only');
    await getOperationsAccess();
    setOperatorToken('');
    await getOperationsAccess();
    assert.deepEqual(calls.map((call) => call.authorization), [null, 'Bearer fixture-access-only', null]);
    assert.ok(calls.every((call) => call.url === '/operations/access'));
  } finally {
    setOperatorToken('');
    globalThis.fetch = originalFetch;
  }
});
