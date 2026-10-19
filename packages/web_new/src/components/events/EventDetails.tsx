'use client';
import { ExternalLink, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { EventArtwork } from './EventArtwork';
import { kindLabels } from './calendar';
import { countdown, type GameEvent } from './calendar';

export function EventDetails({
  event,
  history,
  timeZone,
  now,
  onSelect,
  onClose,
  returnFocus,
}: {
  event: GameEvent;
  history: GameEvent[];
  timeZone: string;
  now: number | null;
  onSelect: (event: GameEvent) => void;
  onClose: () => void;
  returnFocus: HTMLElement | null;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [clock, setClock] = useState(now);
  useEffect(() => {
    const element = dialog.current;
    const overflow = document.body.style.overflow;
    element?.showModal();
    document.body.style.overflow = 'hidden';
    setClock(Date.now());
    const timer = setInterval(() => setClock(Date.now()), 1000);
    return () => {
      element?.close();
      document.body.style.overflow = overflow;
      clearInterval(timer);
      if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true });
    };
  }, []);
  const formatter = new Intl.DateTimeFormat('en', {
    timeZone,
    dateStyle: 'medium',
    timeStyle: 'short',
  });
  const format = (date: string) => formatter.format(new Date(date));
  return (
    <dialog
      ref={dialog}
      className="events-modal"
      aria-labelledby="event-modal-title"
      onCancel={onClose}
      onClick={(click) => {
        if (click.target !== click.currentTarget) return;
        const rect = click.currentTarget.getBoundingClientRect();
        if (
          click.clientX < rect.left ||
          click.clientX > rect.right ||
          click.clientY < rect.top ||
          click.clientY > rect.bottom
        )
          onClose();
      }}
    >
      <button
        className="events-detail-close"
        onClick={onClose}
        aria-label="Close event details"
      >
        <X size={22} />
      </button>
      <EventArtwork event={event} />
      {event.preview_provenance === 'current_notice' && <small className="events-art-caption">Illustration from the current official event announcement.</small>}
      <div className="events-detail-copy">
        <span className="events-kicker">
          {kindLabels[event.kind]}
          {event.game_version ? ` · v${event.game_version}` : ''}
        </span>
        <h2 id="event-modal-title">{event.title}</h2>
        <p className="events-detail-dates">
          {event.starts_at
            ? `${format(event.starts_at)} → ${event.ends_at ? format(event.ends_at) : event.kind === 'permanent' ? 'Permanent' : 'End date unavailable'} (${timeZone})`
            : 'Opening dates are supplied by the game server, not the client catalog.'}
        </p>
        <p className="events-description">
          {event.description || 'No description is included in the client catalog.'}
        </p>
        {!!event.featured_names?.length && <p>Featured: {event.featured_names.join(', ')}</p>}
        {!!event.up_names?.length && <p>Also featured: {event.up_names.join(', ')}</p>}
        {!!event.rewards?.length && (
          <section className="events-rewards">
            <h3>Reward preview</h3>
            <ul>
              {event.rewards.map((reward, index) => (
                <li key={`${reward.id}-${index}`}>
                  {reward.name || 'Unnamed reward'}
                  {reward.value ? ` ×${reward.value}` : ''}
                </li>
              ))}
            </ul>
          </section>
        )}
        {!!event.season?.cycle?.weeks && <p>Cycle: {event.season.cycle.weeks} weeks</p>}
        {event.official_url && (
          <a
            className="events-official-link"
            href={event.official_url}
            target="_blank"
            rel="noopener noreferrer"
          >
            Event announcement from Kuro
            <ExternalLink size={15} />
          </a>
        )}
        {!!event.notices?.length && (
          <section className="events-history">
            <h3>Related announcements</h3>
            {event.notices.map((notice) => (
              <a className="events-official-link" key={notice.id} href={notice.url}>
                {notice.title}
                <ExternalLink size={15} />
              </a>
            ))}
          </section>
        )}
        <p className="events-countdown" role="timer">
          {clock === null ? 'Recorded schedule' : countdown(event, clock)}
        </p>
        {!!event.artwork?.some((image) => image.url) && (
          <details className="events-gallery">
            <summary>
              All game artwork · {event.artwork.filter((image) => image.url).length} images
            </summary>
            <div>
              {event.artwork
                .filter((image) => image.url)
                .map((image, index) => (
                  <button
                    key={image.path}
                    aria-label={`Preview artwork ${index + 1}`}
                    onClick={() =>
                      onSelect({
                        ...event,
                        preview_url: image.url,
                        banner_url: image.url,
                      })
                    }
                  >
                    <img src={image.url!} alt="" loading="lazy" />
                  </button>
                ))}
            </div>
          </details>
        )}
        {history.length > 1 && (
          <section className="events-history">
            <h3>All recorded runs on this server</h3>
            {history.map((run) => (
              <button
                key={run.occurrence_id ?? `${run.id}-${run.starts_at}`}
                aria-pressed={
                  run.occurrence_id === event.occurrence_id && run.starts_at === event.starts_at
                }
                onClick={() => onSelect(run)}
              >
                {run.starts_at
                  ? `${format(run.starts_at)} → ${run.ends_at ? format(run.ends_at) : 'Open end'}`
                  : `Dates not recorded · Client ${run.catalog_version ?? 'unknown'}`}
              </button>
            ))}
          </section>
        )}
      </div>
    </dialog>
  );
}
