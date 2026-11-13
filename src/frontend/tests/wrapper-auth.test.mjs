import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readWrapperAuthError } from '../.test-build/wrapper-auth.js';
const read = body => readWrapperAuthError(new Response(JSON.stringify(body)), 'Login failed', 'Code', 'Check Apple network');

test('shows code and fixed network guidance without native error/account fields', async () => {
  assert.equal(await read({ detail: { error_code: 8, hint: 'check_apple_network', message: 'secret@example.com', error: 'token=secret' } }),
    'Login failed (Code: 8). Check Apple network');
});
test('rejects raw messages, malformed JSON, non-integer and out-of-range codes', async () => {
  for (const detail of ['token=secret', null, [], { error_code: '8' }, { error_code: true }, { error_code: -1234 }, { error_code: 65536 }, { error_code: 1.5 }]) {
    assert.equal(await read({ detail }), 'Login failed');
  }
  assert.equal(await readWrapperAuthError(new Response('not JSON'), 'Login failed', 'Code', 'Check Apple network'), 'Login failed');
});
test('does not forward arbitrary hint text or apply code-8 guidance to another code', async () => {
  assert.equal(await read({ detail: { error_code: 9, hint: 'secret' } }), 'Login failed (Code: 9)');
  assert.equal(await read({ detail: { error_code: 9, hint: 'check_apple_network' } }), 'Login failed (Code: 9)');
});
