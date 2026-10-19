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
} from '../src/components/events/calendar.ts';

const run = (id, occurrence, start, end, kind = 'limited') => ({
  id,
  occurrence_id: occurrence,
  starts_at: start,
  ends_at: end,
  kind,
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
