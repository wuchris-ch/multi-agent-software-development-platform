import assert from 'node:assert/strict';
import test from 'node:test';
import { createGateway } from './provider.mjs';

test('agent provider accepts only the local broker and declared roles', () => {
  for (const url of ['https://example.invalid/v1', 'http://localhost:8000/v1', 'http://127.0.0.1:8000/other', 'http://key@127.0.0.1/v1']) {
    assert.throws(() => createGateway(url, 'coder'));
  }
  assert.throws(() => createGateway('http://127.0.0.1:8000/v1', 'publisher'));
  assert.ok(createGateway('http://127.0.0.1:8000/v1', 'coder'));
  assert.ok(createGateway('http://127.0.0.1:8000/v1', 'reviewer'));
});
