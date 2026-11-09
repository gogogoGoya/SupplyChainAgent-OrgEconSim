import assert from 'node:assert/strict';
import { test } from 'node:test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { isPublicArtifact } from '../publicArtifacts.mjs';

test('public artifact access excludes secrets, private state and escaping symlinks', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'sca-public-'));
  const root = path.join(directory, 'run');
  fs.mkdirSync(root);
  try {
    fs.writeFileSync(path.join(root, 'run_meta.json'), '{}');
    assert.equal(isPublicArtifact(path.join(root, 'run_meta.json'), root), true);
    for (const name of ['.env', 'auth.private.json', 'worker.agent_env.json', 'state.pkl', 'storage.sqlite', 'worker.log']) {
      const file = path.join(root, name);
      fs.writeFileSync(file, 'private');
      assert.equal(isPublicArtifact(file, root), false, name);
    }
    const outside = path.join(directory, 'outside.json');
    fs.writeFileSync(outside, 'private');
    fs.symlinkSync(outside, path.join(root, 'escape.json'));
    assert.equal(isPublicArtifact(path.join(root, 'escape.json'), root), false);
    assert.equal(isPublicArtifact(outside, root), false);
    fs.mkdirSync(path.join(root, '_operations'));
    assert.equal(isPublicArtifact(path.join(root, '_operations'), root), false);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});
