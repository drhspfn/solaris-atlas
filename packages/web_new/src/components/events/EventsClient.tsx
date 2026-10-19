'use client';

import '@/styles/events.css';

import { Clock3, ExternalLink, Gift, Image as ImageIcon, Sparkles } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import { api } from '@/api/client';
import { Select } from '@/components/ui/Select';

type GameEvent = {
  id: number;
  source_id: string;
  occurrence_id: number | null;
  title: string;
  description: string | null;
  kind: 'banner' | 'limited' | 'recurring' | 'permanent';
  game_version: string | null;
  starts_at: string | null;
  ends_at: string | null;
  season: { start?: number; cycle?: { weeks?: number } } | null;
  rewards: { id: number; value?: number; name?: string | null }[] | null;
  featured_names?: string[];
  up_names?: string[];
  banner_path: string | null;
  banner_url?: string | null;
  preview_url?: string | null;
  game_path: string | null;
  source_url: string;
  catalog_version?: string;
  schedule_status?: string;
  artwork?: { path: string; url: string | null; role: string }[];
};

type EventsResponse = { events: GameEvent[]; source_url: string };
type Server = 'asia' | 'europe' | 'america';
type TimelineRange = 'current' | 'all';
const serverLabels: Record<Server, string> = {
  asia: 'Asia',
  europe: 'Europe',
  america: 'America',
};
const kindLabels: Record<GameEvent['kind'], string> = {
  banner: 'Convenes',
  limited: 'Limited events',
  recurring: 'Recurring modes',
  permanent: 'Permanent',
};

const dateFormatter = new Intl.DateTimeFormat(undefined, {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
});
const monthFormatter = new Intl.DateTimeFormat(undefined, {
  month: 'short',
  year: 'numeric',
});

function formatDate(value: string | null) {
  return value ? dateFormatter.format(new Date(value)) : 'Date not recorded';
}

function EventArtwork({ event, compact = false }: { event: GameEvent; compact?: boolean }) {
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const displayUrl = event.preview_url || event.banner_url;
  const failed = displayUrl === failedUrl;

  return (
    <div
      className={`events-artwork ${compact ? 'compact' : ''} ${event.kind === 'banner' && event.preview_url ? 'is-subject' : ''}`}
    >
      {displayUrl && !failed ? (
        <img
          src={displayUrl}
          alt=""
          loading="lazy"
          onError={() => setFailedUrl(displayUrl ?? null)}
        />
      ) : (
        <div className="events-artwork-fallback" aria-hidden="true">
          <ImageIcon size={compact ? 14 : 27} />
          {!compact && <span>Artwork not available</span>}
        </div>
      )}
    </div>
  );
}

export function EventsPage({ initialData }: { initialData?: EventsResponse | null }) {
  const [server, setServer] = useState<Server>('europe');
  const [kind, setKind] = useState<GameEvent['kind'] | 'all'>('all');
  const [events, setEvents] = useState<GameEvent[]>(initialData?.events ?? []);
  const [selected, setSelected] = useState<GameEvent | null>(null);
  const [loading, setLoading] = useState(!initialData);
  const [error, setError] = useState('');
  const [timelineRange, setTimelineRange] = useState<TimelineRange>('all');
  const [search, setSearch] = useState('');

  useEffect(() => {
    if (initialData && server === 'europe' && events === initialData.events) {
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    void api<EventsResponse>(`/events?server=${server}`, {
      signal: controller.signal,
    })
      .then((data) => {
        setEvents(data.events);
        setSelected((current) => data.events.find((event) => event.id === current?.id) ?? null);
        setError('');
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : 'Could not load the event archive.');
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [server]);

  const visibleEvents = useMemo(
    () =>
      events.filter(
        (event) =>
          (kind === 'all' || event.kind === kind) &&
          event.title.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()),
      ),
    [events, kind, search],
  );
  const dated = visibleEvents.filter(
    (event) => event.starts_at && Number.isFinite(new Date(event.starts_at).getTime()),
  );
  const allEarliest = dated.reduce(
    (value, event) => Math.min(value, new Date(event.starts_at!).getTime()),
    Infinity,
  );
  const allLatest = dated.reduce(
    (value, event) => Math.max(value, new Date(event.ends_at ?? event.starts_at!).getTime()),
    -Infinity,
  );
  const now = Date.now();
  const rangeStart = timelineRange === 'current' ? now - 45 * 86400000 : allEarliest;
  const rangeEnd = timelineRange === 'current' ? now + 75 * 86400000 : allLatest;
  const earliest = timelineRange === 'current' ? rangeStart : allEarliest;
  const latest = timelineRange === 'current' ? rangeEnd : allLatest;
  const timelineEvents = visibleEvents.filter((event) => {
    if (timelineRange === 'all') return true;
    if (!event.starts_at) return false;
    const start = new Date(event.starts_at).getTime();
    const end = event.ends_at ? new Date(event.ends_at).getTime() : start;
    return end >= rangeStart && start <= rangeEnd;
  });
  const span = Number.isFinite(latest - earliest) ? Math.max(latest - earliest, 1) : 1;
  const monthTicks = useMemo(() => {
    if (!Number.isFinite(earliest) || !Number.isFinite(latest)) return [];
    const ticks: { label: string; left: number }[] = [];
    const date = new Date(earliest);
    ticks.push({ label: monthFormatter.format(date), left: 0 });
    date.setDate(1);
    date.setHours(0, 0, 0, 0);
    date.setMonth(date.getMonth() + 1);
    while (date.getTime() <= latest && ticks.length < 36) {
      ticks.push({
        label: monthFormatter.format(date),
        left: ((date.getTime() - earliest) / span) * 100,
      });
      date.setMonth(date.getMonth() + 1);
    }
    const chartWidth = Math.max(900, Math.ceil(span / (30.5 * 86400000)) * 165 + 300);
    const trackWidth = chartWidth * 0.69;
    return ticks.filter(
      (tick, index) => index === 0 || (tick.left - ticks[index - 1].left) * trackWidth >= 96,
    );
  }, [earliest, latest, span]);

  return (
    <main className="events-page">
      <section className="events-hero">
        <div className="events-kicker">
          <Sparkles size={14} /> EVENT ARCHIVE
        </div>
        <div className="events-hero-row">
          <div>
            <h1>Every cycle leaves a trace.</h1>
            <p>Explore event windows, recurring challenges and permanent modes across versions.</p>
          </div>
          <div className="events-total">
            <strong>{visibleEvents.length.toLocaleString()}</strong>
            <span>catalog entries</span>
          </div>
        </div>
        <div className="events-controls">
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
              options={Object.entries(serverLabels).map(([value, label]) => ({
                value,
                label,
              }))}
            />
          </div>
          <div className="events-filter">
            <span>Show</span>
            <Select
              label="Event type"
              value={kind}
              onChange={(value) => setKind(value as typeof kind)}
              options={[
                { value: 'all', label: 'All event types' },
                ...Object.entries(kindLabels).map(([value, label]) => ({
                  value,
                  label,
                })),
              ]}
            />
          </div>
          <span className="events-date-note">
            <Clock3 size={14} /> Times shown in your local timezone
          </span>
        </div>
      </section>

      {error && (
        <p className="events-error" role="alert">
          {error}
        </p>
      )}
      {selected && (
        <section className="events-detail" aria-live="polite">
          <button
            className="events-detail-close"
            onClick={() => setSelected(null)}
            aria-label="Close event details"
          >
            ×
          </button>
          <div className="events-detail-art">
            <EventArtwork event={selected} />
            <span>
              {selected.catalog_version
                ? `CLIENT ${selected.catalog_version}`
                : selected.game_version
                  ? `VERSION ${selected.game_version}`
                  : 'GAME CLIENT'}
            </span>
          </div>
          <div className="events-detail-copy">
            <span className={`events-kind kind-${selected.kind}`}>{kindLabels[selected.kind]}</span>
            <h2>{selected.title}</h2>
            <p>
              {selected.description ??
                (selected.season
                  ? `${selected.season.cycle?.weeks ? `Repeats every ${selected.season.cycle.weeks} weeks. ` : ''}Season ${selected.season.start ?? 'schedule'}.`
                  : 'No description is included in the client catalog.')}
            </p>
            <div className="events-detail-dates">
              {!selected.starts_at ? (
                <span>Opening dates are supplied by the game server, not the client catalog.</span>
              ) : (
                <>
                  <span>{formatDate(selected.starts_at)}</span>
                  <b>→</b>
                  <span>
                    {selected.ends_at
                      ? formatDate(selected.ends_at)
                      : selected.kind === 'permanent'
                        ? 'Permanent'
                        : 'End date unavailable'}
                  </span>
                </>
              )}
            </div>
            {selected.artwork?.some((image) => image.url) && (
              <details className="events-gallery">
                <summary>
                  Game artwork · {selected.artwork.filter((image) => image.url).length} images
                </summary>
                <div>
                  {selected.artwork
                    .filter((image) => image.url)
                    .map((image, index) => (
                      <button
                        key={image.path}
                        type="button"
                        aria-label={`Preview game image ${index + 1}`}
                        onClick={() =>
                          setSelected({
                            ...selected,
                            banner_url: image.url,
                            preview_url: image.url,
                          })
                        }
                      >
                        <img src={image.url!} alt="" loading="lazy" />
                      </button>
                    ))}
                </div>
              </details>
            )}
            {selected.up_names?.length ? (
              <p>Also featured: {selected.up_names.join(', ')}</p>
            ) : null}
            {selected.rewards?.length ? (
              <div className="events-rewards">
                <strong>
                  <Gift size={15} /> Reward preview
                </strong>
                <div>
                  {selected.rewards.map((reward, i) => (
                    <span key={`${reward.id}-${i}`}>
                      {reward.name || 'Unnamed reward'}
                      {reward.value ? ` ×${reward.value}` : ''}
                    </span>
                  ))}
                </div>
              </div>
            ) : null}
            {selected.banner_path && !selected.banner_url && !selected.preview_url && (
              <small className="events-art-note">
                No decoded image is available yet. Check the event media tasks in Data Operations.
              </small>
            )}
          </div>
        </section>
      )}

      <section className="events-timeline" aria-label="Events timeline">
        <header className="events-timeline-heading">
          <div>
            <span>THE LONG VIEW</span>
            <h2>Event timeline</h2>
          </div>
          <div className="events-timeline-tools">
            <div className="events-filter">
              <span>Timeline</span>
              <Select
                label="Timeline"
                value={timelineRange}
                onChange={(value) => setTimelineRange(value as TimelineRange)}
                options={[
                  { value: 'current', label: 'Nearby dates' },
                  { value: 'all', label: 'Full catalog' },
                ]}
              />
            </div>
            <p>
              {dated.length && Number.isFinite(earliest) && Number.isFinite(latest)
                ? `${monthFormatter.format(new Date(earliest))} — ${monthFormatter.format(new Date(latest))}`
                : 'Server opening dates are not included in this catalog'}
            </p>
          </div>
        </header>
        {loading ? (
          <div className="events-empty">Reading the archive…</div>
        ) : timelineEvents.length === 0 ? (
          <div className="events-empty">
            {events.length
              ? 'No events match these filters or date range.'
              : 'No client events have been imported yet. An administrator can import the game catalog from Data Operations.'}
          </div>
        ) : (
          <div className="events-chart-scroll" tabIndex={0} aria-label="Scrollable event timeline">
            <div
              className="events-chart"
              style={{
                minWidth: Math.max(900, Math.ceil(span / (30.5 * 86400000)) * 165 + 300),
              }}
            >
              <div className="events-axis">
                <span>EVENT</span>
                <div className="events-axis-track">
                  {monthTicks.map((tick) => (
                    <span key={tick.label} style={{ left: `${tick.left}%` }}>
                      {tick.label}
                    </span>
                  ))}
                </div>
              </div>
              {timelineEvents.map((event) => {
                const start = event.starts_at ? new Date(event.starts_at).getTime() : Number.NaN;
                const end = event.ends_at ? new Date(event.ends_at).getTime() : start;
                const left = Number.isFinite(start)
                  ? Math.min(100, Math.max(0, ((start - earliest) / span) * 100))
                  : 0;
                const right = Number.isFinite(end)
                  ? Math.min(100, Math.max(0, ((end - earliest) / span) * 100))
                  : left;
                const width = Math.max(0.8, right - left);
                return (
                  <button
                    className={`events-row ${selected?.id === event.id ? 'selected' : ''}`}
                    key={event.occurrence_id ?? event.id}
                    onClick={() => setSelected(event)}
                  >
                    <span className="events-row-label">
                      <span className={`events-dot kind-${event.kind}`} />
                      <EventArtwork event={event} compact />
                      <span>
                        <strong>{event.title}</strong>
                        <small>
                          {kindLabels[event.kind]}
                          {event.game_version ? ` · v${event.game_version}` : ''}
                        </small>
                      </span>
                    </span>
                    <span className="events-row-track">
                      {!event.starts_at && (
                        <span className="events-no-dates">Server schedule unavailable</span>
                      )}
                      {Number.isFinite(start) && (
                        <span
                          className={`events-range kind-${event.kind}`}
                          style={{ left: `${left}%`, width: `${width}%` }}
                          title={`${formatDate(event.starts_at)} — ${formatDate(event.ends_at)}`}
                        />
                      )}
                      {event.ends_at && Number.isFinite(end) && (
                        <span className="events-end-marker" style={{ left: `${right}%` }} />
                      )}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        )}
      </section>

      <footer className="events-source">
        <span>
          Names and artwork come from the game client. Existing historical dates are preserved;
          opening dates supplied by the game server are not inferred from client files.
        </span>
        <a href="https://wutheringwaves.kurogames.com/" target="_blank" rel="noreferrer">
          Wuthering Waves <ExternalLink size={13} />
        </a>
      </footer>
    </main>
  );
}
