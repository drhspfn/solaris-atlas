import assert from 'node:assert/strict';
import test from 'node:test';

import { requestCredentials } from '../src/api/requestPolicy.ts';

test('public game reads omit account cookies so signed-in users can use shared caching', () => {
  assert.equal(requestCredentials('/quests/139000025/transcript?locale=ja', 'GET'), 'omit');
  assert.equal(requestCredentials('/maps/102/markers?type=chest', 'get'), 'omit');
  assert.equal(requestCredentials('/items/item%3A41100012/profile', 'GET'), 'omit');
  assert.equal(requestCredentials('/story-analysis/search?q=why', 'GET'), 'omit');
});

test('account, admin, unknown routes and mutations retain session credentials', () => {
  for (const path of [
    '/auth/me',
    '/auth/csrf',
    '/admin/media-jobs/1',
    '/admin/story-agent/jobs',
    '/health',
    '/catalog-private',
  ]) {
    assert.equal(requestCredentials(path, 'GET'), 'include');
  }
  for (const method of ['POST', 'DELETE', 'PATCH']) {
    assert.equal(requestCredentials('/catalog', method), 'include');
  }
});
