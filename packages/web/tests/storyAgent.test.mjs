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
