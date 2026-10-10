import assert from 'node:assert/strict';
import test from 'node:test';
import { cutsceneAnchor } from '../src/components/story/cutsceneAnchor.ts';
test('scene occurrences have stable fragment-safe anchors', () => {
  const event = {
    flow_state: '\u5267\u60c5_3_7&\u540e\u65e5\u8c08',
    action_index: 1,
    reference: 'cutscene:M0389',
  };
  const anchor = cutsceneAnchor(event);
  assert.match(anchor, /^[a-z0-9-]+$/);
  assert.equal(decodeURIComponent(anchor), anchor);
  assert.equal(cutsceneAnchor({ ...event }), anchor);
  assert.notEqual(cutsceneAnchor({ ...event, action_index: 2 }), anchor);
});
