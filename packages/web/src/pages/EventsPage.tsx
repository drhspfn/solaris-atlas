import '../styles/events.css';

import {
  ChevronDown,
  Clock3,
  ExternalLink,
  Gift,
  Image as ImageIcon,
  Sparkles,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import { api } from '../api/client';

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
  rewards: { id: number; value?: number }[] | null;
  banner_path: string | null;
  banner_url?: string | null;
  game_path: string | null;
  source_url: string;
};

type EventsResponse = { events: GameEvent[]; source_url: string };
type Server = 'asia' | 'europe' | 'america';
type TimelineRange = 'current' | 'all';
const serverLabels: Record<Server, string> = { asia: 'Asia', europe: 'Europe', america: 'America' };
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
const monthFormatter = new Intl.DateTimeFormat(undefined, { month: 'short', year: 'numeric' });

function formatDate(value: string | null) {
  return value ? dateFormatter.format(new Date(value)) : 'Date not recorded';
}

function EventArtwork({ event, compact = false }: { event: GameEvent; compact?: boolean }) {
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const failed = event.banner_url === failedUrl;

  return (
    <div className={`events-artwork ${compact ? 'compact' : ''}`}>
      {event.banner_url && !failed ? (
        <img
          src={event.banner_url}
          alt=""
          loading="lazy"
          onError={() => setFailedUrl(event.banner_url ?? null)}
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

function App() {
  const [server, setServer] = useState<Server>('europe');
  const [kind, setKind] = useState<GameEvent['kind'] | 'all'>('all');
  const [events, setEvents] = useState<GameEvent[]>([]);
  const [selected, setSelected] = useState<GameEvent | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [timelineRange, setTimelineRange] = useState<TimelineRange>('current');

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    void api<EventsResponse>(`/events?server=${server}`, { signal: controller.signal })
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
    () => events.filter((event) => kind === 'all' || event.kind === kind),
    [events, kind],
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
    if (timelineRange === 'all' || !event.starts_at) return true;
    const start = new Date(event.starts_at).getTime();
    const end = event.ends_at ? new Date(event.ends_at).getTime() : start;
    return end >= rangeStart && start <= rangeEnd;
  });
  const span = Math.max(latest - earliest, 1);
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
            <span>schedule entries</span>
          </div>
        </div>
        <div className="events-controls">
          <label>
            <span>Server schedule</span>
            <span className="events-select-wrap">
              <select value={server} onChange={(e) => setServer(e.target.value as Server)}>
                {Object.entries(serverLabels).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
              <ChevronDown size={14} />
            </span>
          </label>
          <label>
            <span>Show</span>
            <span className="events-select-wrap">
              <select value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}>
                <option value="all">All event types</option>
                {Object.entries(kindLabels).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
              <ChevronDown size={14} />
            </span>
          </label>
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
              {selected.game_version ? `VERSION ${selected.game_version}` : 'HISTORICAL SCHEDULE'}
            </span>
          </div>
          <div className="events-detail-copy">
            <span className={`events-kind kind-${selected.kind}`}>{kindLabels[selected.kind]}</span>
            <h2>{selected.title}</h2>
            <p>
              {selected.description ??
                (selected.season
                  ? `${selected.season.cycle?.weeks ? `Repeats every ${selected.season.cycle.weeks} weeks. ` : ''}Season ${selected.season.start ?? 'schedule'}.`
                  : 'Historical schedule entry from the game event archive.')}
            </p>
            <div className="events-detail-dates">
              <span>{formatDate(selected.starts_at)}</span>
              <b>→</b>
              <span>
                {selected.ends_at
                  ? formatDate(selected.ends_at)
                  : selected.kind === 'permanent'
                    ? 'Permanent'
                    : 'End date unavailable'}
              </span>
            </div>
            {selected.rewards?.length ? (
              <div className="events-rewards">
                <strong>
                  <Gift size={15} /> Reward records
                </strong>
                <div>
                  {selected.rewards.map((reward, i) => (
                    <span key={`${reward.id}-${i}`}>
                      #{reward.id}
                      {reward.value ? ` ×${reward.value}` : ''}
                    </span>
                  ))}
                </div>
              </div>
            ) : null}
            {selected.banner_path && !selected.banner_url && (
              <small className="events-art-note">
                Artwork is listed in the game data but has not been published yet.
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
            <label>
              <span>Timeline</span>
              <span className="events-select-wrap">
                <select
                  value={timelineRange}
                  onChange={(e) => setTimelineRange(e.target.value as TimelineRange)}
                >
                  <option value="current">Current cycle</option>
                  <option value="all">All history</option>
                </select>
                <ChevronDown size={14} />
              </span>
            </label>
            <p>
              {dated.length && Number.isFinite(earliest) && Number.isFinite(latest)
                ? `${monthFormatter.format(new Date(earliest))} — ${monthFormatter.format(new Date(latest))}`
                : 'Historical schedule'}
            </p>
          </div>
        </header>
        {loading ? (
          <div className="events-empty">Reading the archive…</div>
        ) : visibleEvents.length === 0 ? (
          <div className="events-empty">
            No event schedules have been imported yet. An administrator can import the archive from
            Data Operations.
          </div>
        ) : (
          <div className="events-chart-scroll" tabIndex={0} aria-label="Scrollable event timeline">
            <div
              className="events-chart"
              style={{ minWidth: Math.max(900, Math.ceil(span / (30.5 * 86400000)) * 165 + 300) }}
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
          Schedule data is community-maintained and compiled from game client data. Missing dates
          are left blank.
        </span>
        <a href="https://github.com/Sanma5657/wuwa-wiki-public" target="_blank" rel="noreferrer">
          Data source <ExternalLink size={13} />
        </a>
      </footer>
    </main>
  );
}

export function EventsPage() {
  return <App />;
}
