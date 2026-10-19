export const DAY = 86_400_000;
export const DAY_WIDTH = 44;
export function timeZoneLabel(timeZone: string, time: number) {
  return new Intl.DateTimeFormat('en', { timeZone, timeZoneName: 'shortOffset' })
    .formatToParts(time)
    .find((part) => part.type === 'timeZoneName')!
    .value.replace('GMT', 'UTC')
    .replace(/^UTC[+-]0$/, 'UTC');
}
export type EventKind = 'banner' | 'limited' | 'recurring' | 'permanent';
export const kindLabels: Record<EventKind, string> = {
  banner: 'Convenes',
  limited: 'Limited events',
  recurring: 'Recurring modes',
  permanent: 'Permanent',
};
export type GameEvent = {
  id: number;
  source_id: string;
  slug?: string | null;
  href?: string;
  occurrence_id: number | null;
  title: string;
  description: string | null;
  kind: EventKind;
  game_version: string | null;
  catalog_version?: string;
  starts_at: string | null;
  ends_at: string | null;
  season: { start?: number; cycle?: { weeks?: number } } | null;
  rewards: { id: number; value?: number; name?: string | null; href?: string; slug?: string | null }[] | null;
  featured_names?: string[];
  up_names?: string[];
  banner_path: string | null;
  banner_url?: string | null;
  preview_url?: string | null;
  preview_provenance?: 'notice_snapshot' | 'current_notice' | 'game_client';
  artwork?: { path: string; url: string | null; role: string }[];
  source_url: string;
  official_url?: string | null;
  notices?: { id: number; title: string; url: string }[];
};
export function startTime(event: GameEvent) {
  return event.starts_at ? Date.parse(event.starts_at) : Number.NaN;
}
export function endTime(event: GameEvent) {
  if (event.ends_at) return Date.parse(event.ends_at);
  return event.kind === 'permanent' ? Infinity : Number.NaN;
}
function displayEnd(event: GameEvent) {
  const end = endTime(event);
  return Number.isNaN(end) ? startTime(event) : end;
}
export function timePosition(time: number, boundaries: number[]) {
  let day = boundaries.findIndex((end, index) => index > 0 && time <= end) - 1;
  if (day < 0) day = time < boundaries[0] ? 0 : boundaries.length - 2;
  return (day + (time - boundaries[day]) / (boundaries[day + 1] - boundaries[day])) * DAY_WIDTH;
}
export function groupEvents(events: GameEvent[]) {
  const groups = new Map<number, GameEvent[]>();
  for (const event of events) {
    const group = groups.get(event.id) ?? [];
    group.push(event);
    groups.set(event.id, group);
  }
  return [...groups.values()].map((runs) =>
    runs.sort(
      (a, b) =>
        (Number.isFinite(startTime(a)) ? startTime(a) : Infinity) -
        (Number.isFinite(startTime(b)) ? startTime(b) : Infinity),
    ),
  );
}
// Assign simultaneous runs separate lanes; a rerun retains the same event identity.
export function layoutRuns(events: GameEvent[], start: number, end: number, boundaries?: number[]) {
  const laneEnds: number[] = [];
  return events
    .filter(
      (event) =>
        Number.isFinite(startTime(event)) && startTime(event) < end && displayEnd(event) >= start,
    )
    .map((event) => {
      const left = Math.max(start, startTime(event));
      const right = Math.min(end, displayEnd(event));
      let lane = laneEnds.findIndex((last) => last < left);
      if (lane < 0) lane = laneEnds.length;
      laneEnds[lane] = right;
      return {
        event,
        lane,
        left: boundaries ? timePosition(left, boundaries) : ((left - start) / DAY) * DAY_WIDTH,
        width: Math.max(
          12,
          boundaries
            ? timePosition(right, boundaries) - timePosition(left, boundaries)
            : ((right - left) / DAY) * DAY_WIDTH,
        ),
      };
    });
}
export function eventColor(id: number) {
  // Restrained, dark fills keep white text readable without depending on the artwork.
  return ['#593b68', '#245861', '#704032', '#3c4878', '#315946', '#69454f'][Math.abs(id) % 6];
}
export function monthKey(time: number) {
  return new Date(time).toISOString().slice(0, 7);
}
export function monthStart(month: string) {
  return Date.parse(`${month}-01T00:00:00Z`);
}
export function shiftMonth(month: string, amount: number) {
  const date = new Date(monthStart(month));
  date.setUTCMonth(date.getUTCMonth() + amount);
  return monthKey(date.getTime());
}
export function countdown(event: GameEvent, now: number) {
  const start = startTime(event),
    end = endTime(event);
  if (!Number.isFinite(start)) return 'Server schedule unavailable';
  if (now > end) return 'Ended';
  if (now >= start && end === Infinity) return 'Available permanently';
  if (now >= start && Number.isNaN(end)) return 'Started · end date unavailable';
  const seconds = Math.max(0, Math.floor(((now < start ? start : end) - now) / 1000));
  const days = Math.floor(seconds / 86400);
  const hours = String(Math.floor(seconds / 3600) % 24).padStart(2, '0');
  const minutes = String(Math.floor(seconds / 60) % 60).padStart(2, '0');
  return `${now < start ? 'Starts in' : 'Ends in'} ${days}d ${hours}:${minutes}:${String(seconds % 60).padStart(2, '0')}`;
}
