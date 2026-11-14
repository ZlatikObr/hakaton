import test from 'node:test';
import assert from 'node:assert/strict';
import { apply } from './adapter.mjs';
const config = { jobId: 'a'.repeat(32), approvedDigest: 'b'.repeat(64), token: 's'.repeat(32) };
function context() {
  const state = {};
  state.tools = {
    register(tool) { state.tool = tool; },
    restrict(policy) { state.policy = policy; },
    guard(fn) { state.guard = fn; },
  };
  return state;
}
test('SDK tool returns only counts, hides private report and secrets', async () => {
  const ctx = context();
  let calls = 0;
  apply(ctx, config, async (url, options) => {
    calls++;
    assert.equal(url.hostname, '127.0.0.1');
    assert.equal(JSON.parse(options.body).approved_digest, config.approvedDigest);
    return { ok: true, json: async () => ({ review: { findings: [{ quote: 'secret@example.com' }] } }) };
  });
  const result = await ctx.tool.execute({}, { signal: new AbortController().signal });
  assert.deepEqual(result, { status: 'complete', findings: 1 });
  assert.equal(JSON.stringify(result).includes('secret'), false);
  assert.equal(ctx.guard({ name: 'shell' }), 'Tool denied by procurement policy');
  assert.equal(ctx.guard({ name: 'run_procurement_review' }), undefined);
  await ctx.tool.execute({}, { signal: new AbortController().signal });
  assert.equal(calls, 1);
});
test('nonlocal backend and missing host approval are rejected', () => {
  assert.throws(() => apply(context(), { ...config, baseUrl: 'https://evil.example' }));
  assert.throws(() => apply(context(), { ...config, approvedDigest: undefined }));
  assert.throws(() => apply({ tools: {} }, config));
});
test('error response never enters the harness', async () => {
  const ctx = context();
  apply(ctx, config, async () => { throw new Error('private credentials'); });
  assert.deepEqual(await ctx.tool.execute({}, { signal: new AbortController().signal }), { status: 'request_failed', findings: 0 });
});
