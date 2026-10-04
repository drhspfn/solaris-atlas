import assert from 'node:assert/strict';
import test from 'node:test';

import { questPassagePath } from '../src/data/story.ts';

test('quest links preserve exact dialogue identity, snapshot and language', () => {
  const key = 'talk_item:剧情_3_7_心相迷宫&后日谈_2_12:1:17%/#';
  const url = new URL(questPassagePath(139000025, key, 'ja', '3.7.0'), 'http://localhost');
  assert.equal(url.pathname, '/quests/139000025');
  assert.equal(url.searchParams.get('line'), key);
  assert.equal(url.searchParams.get('locale'), 'ja');
  assert.equal(url.searchParams.get('game_version'), '3.7.0');
  assert.equal(url.hash, '');
  assert.equal(
    new URL(questPassagePath(1, key, 'en'), url).searchParams.has('game_version'),
    false,
  );
});
