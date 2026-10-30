import assert from 'node:assert/strict';
import test from 'node:test';
import { storyChapterGroups, storyMapPage, storyPageForHash, storyPageNumber } from '../src/components/story/storyMapPaging.ts';

const node = (id, title = `Quest ${id}`) => ({ id, title, source_kind: 'quest_prerequisite',
  previous_node_ids: [], quests: [{ game_quest_id: id, title, first_observed_game_version: '1.0.0' }] });
const chapter = (id, title, count, first, version = '1.0.0', act = title) => ({
  id, title, act_title: act, snapshot_game_version: version, tree_available: false,
  nodes: Array.from({ length: count }, (_, i) => node(first + i)),
});
const chapters = [chapter(1, 'Prelude', 5, 1),
  chapter(2, 'Long chapter', 344, 6, '1.0.0', 'Act I'),
  chapter(3, 'Long chapter', 20, 350, '1.0.0', 'Act II'),
  chapter(4, 'Later chapter', 3, 370, '2.0.0')];
const groups = storyChapterGroups(chapters);
const ids = (page) => page.visibleGroups.flatMap((group) => group.acts.flatMap((act) => act.nodes.map((n) => n.id)));

test('sixty-node pages split a 344-node chapter and repeat its original chapter/act context', () => {
  const first = storyMapPage(groups, '', 1);
  assert.equal(first.pages, 7);
  assert.equal(ids(first).length, 60);
  assert.deepEqual(first.visibleGroups.map((group) => group.index), [0, 1]);
  const second = storyMapPage(groups, '', 2);
  assert.deepEqual(ids(second), Array.from({ length: 60 }, (_, i) => i + 61));
  assert.equal(second.visibleGroups[0].index, 1);
  assert.equal(second.visibleGroups[0].count, 364);
  assert.equal(second.visibleGroups[0].acts[0].act_title, 'Act I');
  assert.equal(second.visibleGroups[0].acts[0].start, 56);
  const boundary = storyMapPage(groups, '', 6);
  assert.equal(ids(boundary).length, 60);
  assert.deepEqual(boundary.visibleGroups[0].acts.map((act) => act.act_title), ['Act I', 'Act II']);
  assert.equal(boundary.visibleGroups[0].acts[1].start, 1);
  const last = storyMapPage(groups, '', 7);
  assert.deepEqual(last.visibleGroups.map((group) => [group.index, group.version]), [[1, '1.0.0'], [2, '2.0.0']]);
  const all = Array.from({ length: first.pages }, (_, i) => ids(storyMapPage(groups, '', i + 1))).flat();
  assert.deepEqual(all, chapters.flatMap((chapter) => chapter.nodes.map((node) => node.id)));
});

test('search spans all pages and versions while preserving original chapter IDs/numbers', () => {
  const found = storyMapPage(groups, 'Quest 372', 1);
  assert.deepEqual(ids(found), [372]);
  assert.equal(found.visibleGroups[0].index, 2);
  assert.equal(found.visibleGroups[0].version, '2.0.0');
  assert.equal(storyMapPage(groups, 'Long chapter', 1).total, 364);
  assert.deepEqual(ids(storyMapPage(groups, '2.0.0', 1)), [370, 371, 372]);
  assert.equal(storyMapPage(groups, '  quest 372  ', 99).page, 1);
  assert.equal(storyMapPage(groups, 'missing', 9).total, 0);
  assert.equal(storyMapPage(groups, 'missing', 9).start, 0);
});

test('invalid page syntax defaults to one and out-of-range integers clamp to the final page', () => {
  for (const value of [null, undefined, '', 'bad', '-3', '0', '2.5', '1e3', 'Infinity', '9'.repeat(30)])
    assert.equal(storyMapPage(groups, '', value).page, 1, String(value));
  assert.equal(storyMapPage(groups, '', '999').page, 7);
  assert.equal(storyPageNumber('02'), 2);
  assert.equal(storyMapPage([], '', 5).page, 1);
});

test('chapter hashes resolve against global chapter identity, including after search', () => {
  assert.equal(storyPageForHash(groups, '#story-chapter-group-2'), 7);
  assert.equal(storyPageForHash(groups, '#story-chapter-group-1'), 1);
  const filtered = storyMapPage(groups, 'Quest 372', 1).matches;
  assert.equal(storyPageForHash(filtered, '#story-chapter-group-2'), 1);
  assert.equal(storyPageForHash(filtered, '#story-chapter-group-1'), null);
  for (const hash of ['#story-chapter-group-9999', '#story-chapter-group--1', '#other', '#%broken'])
    assert.equal(storyPageForHash(groups, hash), null);
});

test('empty chapters do not shift the existing chapter numbering', () => {
  const withEmpty = storyChapterGroups([chapter(8, 'Empty', 0, 1), ...chapters]);
  assert.deepEqual(withEmpty.map((group) => [group.index, group.title]), groups.map((group) => [group.index, group.title]));
});
