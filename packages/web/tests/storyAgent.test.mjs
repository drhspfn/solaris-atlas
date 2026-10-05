import assert from 'node:assert/strict';
import { test } from 'node:test';

import { resumeBody, resumePolicy } from '../src/data/storyAgent.ts';

const job = { status: 'paused_steps', limits: { max_steps: 16, provider: 'responses' } };
test('step resume sends only a bounded extension', () => {
  assert.deepEqual(resumeBody(job, 16), { extra_steps: 16 });
  for (const count of [0, -1, 85, NaN, 1.5]) assert.throws(() => resumeBody(job, count));
  const exhausted = { ...job, limits: { ...job.limits, max_steps: 100 } };
  assert.equal(resumePolicy(exhausted).allowed, false);
  assert.throws(() => resumeBody(exhausted, 1));
});
test('context recovery preserves the input and spending bounds', () => {
  assert.deepEqual(resumeBody({ ...job, status: 'paused_context' }, 16), { compact_context: true });
  assert.equal(
    resumePolicy({ ...job, status: 'paused_context', limits: { ...job.limits, provider: 'chat' } })
      .allowed,
    false,
  );
  assert.deepEqual(resumeBody({ ...job, status: 'paused_budget' }, 16), {});
  assert.deepEqual(resumeBody({ ...job, status: 'paused_rate_limit' }, 16), {});
});
test('uncertain charges, active runs and terminal failures cannot be restarted', () => {
  for (const status of ['paused_uncertain', 'running', 'queued', 'completed', 'stale', 'failed']) {
    assert.equal(resumePolicy({ ...job, status }).allowed, false);
    assert.throws(() => resumeBody({ ...job, status }, 16));
  }
});
test('validated historical tool overflow replays with the existing bound', () => {
  const recoverable = {
    ...job,
    status: 'failed',
    recovery: 'recorded_tools',
    limits: { ...job.limits, max_tool_calls_per_step: 20 },
  };
  assert.equal(resumePolicy(recoverable).allowed, true);
  assert.deepEqual(resumeBody(recoverable, 0), { tool_calls_per_step: 20 });
  assert.equal(resumePolicy({ ...recoverable, recovery: null }).allowed, false);
});
test('output recovery raises only the response bound and adds a step when required', () => {
  const truncated = {
    ...job,
    status: 'paused_output',
    step: 15,
    limits: { ...job.limits, max_output_tokens: 4096 },
  };
  assert.equal(resumePolicy(truncated).allowed, true);
  assert.deepEqual(resumeBody(truncated, 16), { output_tokens: 16384, extra_steps: 1 });
  assert.deepEqual(resumeBody({ ...truncated, step: 4 }, 16), {
    output_tokens: 16384,
    extra_steps: 0,
  });
  assert.deepEqual(
    resumeBody({ ...truncated, limits: { ...truncated.limits, max_output_tokens: 16384 } }, 16),
    { output_tokens: 32000, extra_steps: 1 },
  );
  for (const exhausted of [
    { ...truncated, step: 99 },
    { ...truncated, limits: { ...truncated.limits, max_output_tokens: 32000 } },
  ]) {
    assert.equal(resumePolicy(exhausted).allowed, false);
    assert.throws(() => resumeBody(exhausted, 16));
  }
});
