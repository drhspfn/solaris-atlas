'use client';
import '@/styles/events.css';
import { ChevronLeft, ChevronRight, X } from 'lucide-react';
import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { api } from '@/api/client';
import { Select } from '@/components/ui/Select';
import { EventDetails } from './EventDetails';
import { EventArtwork } from './EventArtwork';
import { EventArchiveHeader } from './EventArchiveHeader';
import {
  DAY,
  DAY_WIDTH,
  countdown,
  eventColor,
  groupEvents,
  compareEventGroups,
  matchesEventType,
  kindLabels,
  layoutTimeline,
  isPermanentListing,
  monthKey,
  monthStart,
  shiftMonth,
  startTime,
  timePosition,
  timeZoneLabel,
  type GameEvent,
  type EventFilter,
} from './calendar';

type EventsResponse = { events: GameEvent[]; source_url: string };
type Server = 'asia' | 'europe' | 'america';
const servers = {
  asia: 'Asia · UTC+8',
  europe: 'Europe · UTC+1',
  america: 'America · UTC−5',
};
const zones = { asia: 'Etc/GMT-8', europe: 'Etc/GMT-1', america: 'Etc/GMT+5' };
const monthFormat = new Intl.DateTimeFormat('en', {
  month: 'long',
  year: 'numeric',
  timeZone: 'UTC',
});

export function EventsPage({
  initialData,
  initialEventIds = [],
}: {
  initialData?: EventsResponse | null;
  initialEventIds?: number[];
}) {
  const [server, setServer] = useState<Server>('europe');
  const [kind, setKind] = useState<EventFilter>('all');
  const [events, setEvents] = useState(initialData?.events ?? []);
  const [selected, setSelected] = useState<GameEvent | null>(null);
  const [chosen, setChosen] = useState<number[]>(initialEventIds);
  const [loading, setLoading] = useState(!initialData);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [month, setMonth] = useState(monthKey(Date.now()));
  const [now, setNow] = useState<number | null>(null);
  const [localTime, setLocalTime] = useState(false);
  const [localZone, setLocalZone] = useState('UTC');
  const [catalogLimit, setCatalogLimit] = useState(24);
  const chart = useRef<HTMLDivElement>(null);
  const pendingTime = useRef<number | null>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const openEvent = (event: GameEvent, trigger: HTMLElement) => {
    returnFocus.current = trigger;
    setSelected(event);
  };
  const firstFetch = useRef(true);
  const timeZone = localTime ? localZone : zones[server];
  useEffect(() => {
    setLocalZone(Intl.DateTimeFormat().resolvedOptions().timeZone);
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 60000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    if (firstFetch.current && initialData && server === 'europe') {
      firstFetch.current = false;
      return;
    }
    firstFetch.current = false;
    const controller = new AbortController();
    setLoading(true);
    setSelected(null);
    void api<EventsResponse>(`/events?server=${server}&summary=true`, {
      signal: controller.signal,
    })
      .then((data) => {
        if (!controller.signal.aborted) {
          setEvents(data.events);
          setError('');
        }
      })
      .catch((reason) => {
        if (!controller.signal.aborted)
          setError(reason instanceof Error ? reason.message : 'Could not load events.');
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [server, initialData]);
  useEffect(() => {
    setCatalogLimit(24);
  }, [search, kind, chosen]);

  const groups = useMemo(() => groupEvents(events), [events]);
  const matching = groups.filter((runs) =>
    runs.some(
      (event) =>
        matchesEventType(event, kind) &&
        event.title.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()),
    ),
  );
  const visible = matching.filter((runs) => !chosen.length || chosen.includes(runs[0].id));
  const zoneOffset = (time: number) => {
    const parts = new Intl.DateTimeFormat('en', {
      timeZone,
      timeZoneName: 'longOffset',
    }).formatToParts(time);
    const offset = parts
      .find((part) => part.type === 'timeZoneName')
      ?.value.match(/GMT([+−-])(\d{2}):(\d{2})/);
    return offset
      ? (offset[1] === '+' ? 1 : -1) * (Number(offset[2]) * 60 + Number(offset[3])) * 60000
      : 0;
  };
  const boundaries = Array.from({ length: 43 }, (_, index) => {
    const midnight = monthStart(month) + index * DAY;
    return midnight - zoneOffset(midnight);
  });
  const start = boundaries[0],
    end = boundaries[42];
  visible.sort((a, b) => compareEventGroups(a, b, start, end, now ?? start));
  const timeline = layoutTimeline(visible.flat(), start, end, boundaries);
  const permanent = visible
    .map((runs) => runs.filter(isPermanentListing))
    .filter((runs) => runs.length);
  const catalog = visible
    .map((runs) =>
      runs.filter((event) => !Number.isFinite(startTime(event)) && !isPermanentListing(event)),
    )
    .filter((runs) => runs.length);
  const recorded = visible.flatMap((runs) =>
    runs.filter((event) => Number.isFinite(startTime(event))),
  );
  const availableMonths = [
    ...new Set([
      month,
      monthKey(Date.now()),
      ...recorded.map((event) => monthKey(startTime(event))),
    ]),
  ]
    .sort()
    .reverse();
  const dateFormat = new Intl.DateTimeFormat('en', {
    timeZone,
    dateStyle: 'medium',
    timeStyle: 'short',
  });
  const formatDate = (date: string | null) =>
    date ? dateFormat.format(new Date(date)) : 'Date not recorded';
  const jumpTo = (event: GameEvent) => {
    if (Number.isFinite(startTime(event))) {
      pendingTime.current = startTime(event);
      setMonth(monthKey(startTime(event)));
      if (chart.current)
        chart.current.scrollLeft = Math.max(
          0,
          timePosition(startTime(event), boundaries) - DAY_WIDTH * 2,
        );
    }
  };
  const toggle = (id: number) =>
    setChosen((ids) => (ids.includes(id) ? ids.filter((value) => value !== id) : [...ids, id]));
  const today = now === null ? -1 : timePosition(now, boundaries);
  const changeMonth = (value: string) => {
    setMonth(value);
    if (chart.current) chart.current.scrollLeft = 0;
  };
  useEffect(() => {
    if (!chart.current || loading) return;
    const time = pendingTime.current ?? Date.now();
    chart.current.scrollLeft =
      time >= start && time < end ? Math.max(0, timePosition(time, boundaries) - DAY_WIDTH * 2) : 0;
    pendingTime.current = null;
  }, [month, timeZone, loading]);

  return (
    <main className="events-page">
      <EventArchiveHeader active="calendar" />
      <section className="events-controls" aria-label="Event filters">
        <label className="events-filter">
          <span>Find an event</span>
          <input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search event names"
          />
        </label>
        <div className="events-filter">
          <span>Server schedule</span>
          <Select
            label="Server schedule"
            value={server}
            onChange={(value) => setServer(value as Server)}
            options={Object.entries(servers).map(([value, label]) => ({
              value,
              label,
            }))}
          />
        </div>
        <div className="events-filter">
          <span>Event type</span>
          <Select
            label="Event type"
            value={kind}
            onChange={(value) => setKind(value as typeof kind)}
            options={[
              { value: 'all', label: 'All types' },
              { value: 'characters', label: 'Character convenes' },
              { value: 'weapons', label: 'Weapon convenes' },
              { value: 'activities', label: 'Events and modes' },
              ...Object.entries(kindLabels).map(([value, label]) => ({
                value,
                label,
              })),
            ]}
          />
        </div>
        <label className="events-zone">
          <input
            type="checkbox"
            checked={localTime}
            onChange={(event) => setLocalTime(event.target.checked)}
          />
          Local time <small>({timeZoneLabel(timeZone, now ?? 0)})</small>
        </label>
      </section>
      <details className="events-picker">
        <summary>
          Choose events {chosen.length ? `· ${chosen.length} selected` : '· all events'}
        </summary>
        <div className="events-picker-actions">
          <span>Select several events to compare their history.</span>
          <button onClick={() => setChosen([])} disabled={!chosen.length}>
            Show all
          </button>
        </div>
        <div className="events-picker-list">
          {matching.map((runs) => (
            <label key={runs[0].id}>
              <input
                type="checkbox"
                checked={chosen.includes(runs[0].id)}
                onChange={() => toggle(runs[0].id)}
              />
              <span>{runs[runs.length - 1].title}</span>
              <small>{runs.filter((event) => event.starts_at).length} runs</small>
            </label>
          ))}
        </div>
        {!matching.length && <p>No event names match your search.</p>}
      </details>
      {!!chosen.length && (
        <div className="events-selection" aria-label="Selected events">
          {groups
            .filter((runs) => chosen.includes(runs[0].id))
            .map((runs) => (
              <button
                key={runs[0].id}
                onClick={() => toggle(runs[0].id)}
                aria-label={`Remove ${runs[0].title} from selection`}
              >
                {runs[0].title}
                <X size={14} />
              </button>
            ))}
        </div>
      )}
      {error && (
        <p className="events-error" role="alert">
          {error}
        </p>
      )}
      <section className="events-timeline" aria-label="Events timeline" aria-busy={loading}>
        <header className="events-calendar-tools">
          <h2>Event calendar</h2>
          <div>
            <button aria-label="Previous month" onClick={() => changeMonth(shiftMonth(month, -1))}>
              <ChevronLeft size={18} />
            </button>
            <Select
              label="Calendar month"
              value={month}
              onChange={changeMonth}
              options={availableMonths.map((value) => ({
                value,
                label: monthFormat.format(monthStart(value)),
              }))}
            />
            <button aria-label="Next month" onClick={() => changeMonth(shiftMonth(month, 1))}>
              <ChevronRight size={18} />
            </button>
            <button
              onClick={() => {
                pendingTime.current = Date.now();
                changeMonth(monthKey(Date.now()));
                if (chart.current)
                  chart.current.scrollLeft = Math.max(
                    0,
                    timePosition(Date.now(), boundaries) - DAY_WIDTH * 2,
                  );
              }}
            >
              Today
            </button>
          </div>
        </header>
        <p className="events-calendar-help">
          Scroll across for dates and down for events. Select a bar for details. Times:{' '}
          {timeZoneLabel(timeZone, now ?? 0)}.
        </p>
        {loading ? (
          <p className="events-empty" role="status">
            Reading the archive…
          </p>
        ) : (
          <>
            <div
              ref={chart}
              className="events-chart-scroll"
              tabIndex={0}
              aria-label="Scrollable daily event calendar"
            >
              <div
                className="events-chart"
                style={
                  {
                    width: 42 * DAY_WIDTH,
                    '--day-width': `${DAY_WIDTH}px`,
                  } as CSSProperties
                }
              >
                <div className="events-axis">
                  {Array.from({ length: 42 }, (_, index) => {
                    const day = new Date(monthStart(month) + index * DAY);
                    return (
                      <div
                        key={index}
                        className={
                          now &&
                          new Date(now + zoneOffset(now)).toISOString().slice(0, 10) ===
                            day.toISOString().slice(0, 10)
                            ? 'is-today'
                            : ''
                        }
                      >
                        <span>
                          {day.getUTCDate() === 1 || index === 0
                            ? day.toLocaleDateString('en', {
                                month: 'short',
                                timeZone: 'UTC',
                              })
                            : ''}
                        </span>
                        <small>
                          {day.toLocaleDateString('en', {
                            weekday: 'short',
                            timeZone: 'UTC',
                          })}
                        </small>
                        <strong>{day.getUTCDate()}</strong>
                      </div>
                    );
                  })}
                </div>
                {today >= 0 && today < 42 * DAY_WIDTH && (
                  <div className="events-now" style={{ left: today }}>
                    <span>Now</span>
                  </div>
                )}
                {timeline.map(({ group, bars, lanes }) => (
                  <section
                    className="events-calendar-group"
                    key={group}
                    aria-label={
                      group === 'characters'
                        ? 'Character convenes'
                        : group === 'weapons'
                          ? 'Weapon convenes'
                          : kindLabels[group as keyof typeof kindLabels]
                    }
                  >
                    <h3>
                      {group === 'characters'
                        ? 'Character convenes'
                        : group === 'weapons'
                          ? 'Weapon convenes'
                          : kindLabels[group as keyof typeof kindLabels]}
                    </h3>
                    <div className="events-calendar-row" style={{ height: lanes * 60 }}>
                      {bars.map(({ event, lane, left, width }) => (
                        <button
                          key={event.occurrence_id ?? `${event.id}-${event.starts_at}`}
                          className="events-bar"
                          style={
                            {
                              left,
                              width,
                              top: lane * 60 + 8,
                              '--event-color': eventColor(event.id),
                            } as CSSProperties
                          }
                          onClick={(click) => openEvent(event, click.currentTarget)}
                          aria-label={`${event.title}, ${formatDate(event.starts_at)} to ${event.ends_at ? formatDate(event.ends_at) : 'end date unavailable'}`}
                        >
                          <EventArtwork event={event} />
                          <span className="events-bar-copy">
                            <strong>{event.title}</strong>
                            <small>
                              {event.game_version ? `v${event.game_version} · ` : ''}
                              {now === null
                                ? kindLabels[event.kind]
                                : countdown(event, now).replace(/:\d{2}$/, '')}
                            </small>
                          </span>
                        </button>
                      ))}
                    </div>
                  </section>
                ))}
              </div>
            </div>
            {!timeline.length && (
              <div className="events-empty">
                No recorded runs in this date range. Choose another month or an event below.
              </div>
            )}
          </>
        )}
      </section>
      {!!recorded.length && (
        <section className="events-run-index">
          <h2>Run history</h2>
          <p>Jump to any recorded run, including previous cycles.</p>
          <div>
            {recorded.map((event) => (
              <button
                key={event.occurrence_id ?? `${event.id}-${event.starts_at}`}
                onClick={(click) => {
                  jumpTo(event);
                  openEvent(event, click.currentTarget);
                }}
              >
                <span>{event.title}</span>
                <small>
                  {formatDate(event.starts_at)} →{' '}
                  {event.ends_at
                    ? formatDate(event.ends_at)
                    : event.kind === 'permanent'
                      ? 'Permanent'
                      : 'End date unavailable'}
                </small>
              </button>
            ))}
          </div>
        </section>
      )}
      {[
        {
          title: 'Permanent content',
          rows: permanent,
          description: 'Permanent modes and events without a closing date.',
        },
        {
          title: 'Dates not recorded',
          rows: catalog,
          description:
            'These events are in the game catalog. Server schedule unavailable; their presence does not confirm that they are currently running.',
        },
      ].map(
        ({ title, rows, description }) =>
          !!rows.length && (
            <section className="events-undated" key={title}>
              <h2>{title}</h2>
              <p>{description}</p>
              <div className="events-catalog-grid">
                {rows.slice(0, catalogLimit).map((runs) => {
                  const event = runs[runs.length - 1];
                  return (
                    <button
                      key={event.id}
                      onClick={(click) => openEvent(event, click.currentTarget)}
                    >
                      <EventArtwork event={event} />
                      <span>
                        <strong>{event.title}</strong>
                        <small>
                          {kindLabels[event.kind]}
                          {event.catalog_version ? ` · Client ${event.catalog_version}` : ''}
                        </small>
                      </span>
                    </button>
                  );
                })}
              </div>
              {rows.length > catalogLimit && (
                <button
                  className="events-load-more"
                  onClick={() => setCatalogLimit((value) => value + 24)}
                >
                  Show more events ({rows.length - catalogLimit} remaining)
                </button>
              )}
            </section>
          ),
      )}
      {!loading && !visible.length && (
        <p className="events-empty">
          {events.length
            ? 'No events match these filters. Clear your search or selection.'
            : 'No events have been imported yet.'}
        </p>
      )}
      {selected && (
        <EventDetails
          returnFocus={returnFocus.current}
          fallbackFocus={chart.current}
          event={selected}
          history={groups.find((runs) => runs[0].id === selected.id) ?? []}
          timeZone={timeZone}
          now={now}
          onSelect={(event) => {
            setSelected(event);
            jumpTo(event);
          }}
          onClose={() => setSelected(null)}
        />
      )}
      <footer className="events-source">
        Names and artwork come from the game client and official Notice board. Only recorded opening
        dates appear on the calendar.{' '}
        <a href="https://wutheringwaves.kurogames.com/" target="_blank" rel="noopener noreferrer">
          Wuthering Waves ↗
        </a>
      </footer>
    </main>
  );
}
