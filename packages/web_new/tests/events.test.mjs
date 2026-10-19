import assert from 'node:assert/strict';
import test from 'node:test';
import {
  DAY,
  DAY_WIDTH,
  groupEvents,
  layoutRuns,
  countdown,
  shiftMonth,
  timePosition,
  timeZoneLabel,
  compareEventGroups,
  matchesEventType,
  artworkGroups,
} from '../src/components/events/calendar.ts';

test('calendar groups characters, weapons and modes; soonest closing runs come first', () => {
  const event = (id, kind, title, end) => ({
    id,
    kind,
    title,
    starts_at: '2026-10-01T00:00:00Z',
    ends_at: end,
  });
  const character = event(1, 'banner', 'Iuno · Resonator Convene', '2026-10-22T00:00:00Z');
  const weapon = event(2, 'banner', 'Moongazer · Weapon Convene', '2026-10-22T00:00:00Z');
  const limited = event(3, 'limited', 'Long event', '2026-10-29T00:00:00Z');
  const short = event(4, 'limited', 'Short event', '2026-10-21T00:00:00Z');
  const rows = [[limited], [weapon], [short], [character]];
  rows.sort((a, b) => compareEventGroups(a, b, Date.parse('2026-10-01'), Date.parse('2026-11-01')));
  assert.deepEqual(
    rows.map(([row]) => row.id),
    [1, 2, 4, 3],
  );
  assert(matchesEventType(character, 'characters'));
  assert(!matchesEventType(weapon, 'characters'));
  assert(matchesEventType(limited, 'activities'));
  assert(!matchesEventType(weapon, 'activities'));
});

test('gallery deduplicates published images and separates interface sprites from artwork', () => {
  const result = artworkGroups({
    artwork: [
      { path: 'small', role: 'featured_weapon_small', url: 'small.png' },
      { path: 'full', role: 'featured_weapon', url: 'weapon.png' },
      { path: 'same-bytes', role: 'featured_weapon', url: 'weapon.png' },
      { path: 'border', role: 'prefab_texture', url: 'border.png' },
      { path: 'missing', role: 'content', url: null },
    ],
  });
  assert.deepEqual(
    result.artwork.map((image) => image.path),
    ['full'],
  );
  assert.deepEqual(
    result.resources.map((image) => image.path),
    ['border'],
  );
});

const run = (id, occurrence, start, end, kind = 'limited') => ({
  id,
  occurrence_id: occurrence,
  starts_at: start,
  ends_at: end,
  kind,
});

test('timezone labels show UTC offsets and follow Moldova daylight saving', () => {
  assert.equal(timeZoneLabel('Europe/Chisinau', Date.parse('2026-10-19T12:00:00Z')), 'UTC+3');
  assert.equal(timeZoneLabel('Europe/Chisinau', Date.parse('2026-12-19T12:00:00Z')), 'UTC+2');
  assert.equal(timeZoneLabel('UTC', 0), 'UTC');
});
test('reruns share one event row while retaining separate clickable occurrences', () => {
  const first = run(1, 10, '2026-10-01T00:00:00Z', '2026-10-10T00:00:00Z');
  const second = run(1, 11, '2026-11-01T00:00:00Z', '2026-11-10T00:00:00Z');
  const groups = groupEvents([second, first]);
  assert.deepEqual(groups, [[first, second]]);
  assert.equal(
    layoutRuns(groups[0], Date.parse(first.starts_at), Date.parse(second.ends_at) + DAY).length,
    2,
  );
});
test('bars clip to the viewport; overlapping runs occupy separate lanes', () => {
  const start = Date.parse('2026-10-01T00:00:00Z');
  const bars = layoutRuns(
    groupEvents([
      run(1, 1, '2026-09-01T00:00:00Z', '2026-10-05T00:00:00Z'),
      run(1, 2, '2026-10-02T00:00:00Z', '2026-10-08T00:00:00Z'),
      run(1, 3, null, null),
    ])[0],
    start,
    start + 7 * DAY,
  );
  assert.equal(bars[0].left, 0);
  assert.equal(bars[0].width, 4 * DAY_WIDTH);
  assert.equal(bars[1].lane, 1);
  assert.equal(bars[1].width, 6 * DAY_WIDTH);
});
test('unknown end is not permanent unless the event is permanent', () => {
  assert.equal(countdown(run(1, 1, null, null), Date.now()), 'Server schedule unavailable');
  assert.equal(
    countdown(run(1, 1, '2026-01-01', null, 'permanent'), Date.parse('2026-10-01')),
    'Available permanently',
  );
  assert.equal(shiftMonth('2026-12', 1), '2027-01');
  assert.equal(shiftMonth('2026-01', -1), '2025-12');
  assert.equal(
    countdown(run(1, 1, '2026-01-01', null), Date.parse('2026-10-01')),
    'Started · end date unavailable',
  );
});
test('local calendar days have equal width across daylight saving changes', () => {
  const boundaries = [0, 23 * 3600000, 47 * 3600000];
  assert.equal(timePosition(boundaries[1], boundaries), DAY_WIDTH);
  assert.equal(timePosition(boundaries[2], boundaries), 2 * DAY_WIDTH);
});
