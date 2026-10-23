'use client';
import { Image as ImageIcon } from 'lucide-react';
import { useState } from 'react';
import type { GameEvent } from './calendar';

export function EventArtwork({ event }: { event: GameEvent }) {
  const [failed, setFailed] = useState('');
  const url = event.preview_url || event.banner_url;
  const subject =
    !!event.preview_url &&
    event.kind === 'banner' &&
    !event.preview_provenance?.includes('notice') &&
    !!event.artwork?.some((image) => image.url === url && image.role.startsWith('featured_'));
  const background = subject
    ? event.artwork?.find(
        (image) =>
          ['content_background', 'background'].includes(image.role) &&
          image.url &&
          image.url !== url,
      )?.url
    : null;
  return (
    <span
      className={`events-artwork ${subject ? 'is-subject' : ''}`}
      style={
        background
          ? {
              backgroundImage: `url(${JSON.stringify(background)})`,
              backgroundSize: 'cover',
              backgroundPosition: 'center',
            }
          : undefined
      }
    >
      {url && url !== failed ? (
        <img src={url} alt="" loading="lazy" onError={() => setFailed(url)} />
      ) : (
        <span className="events-artwork-fallback">
          <ImageIcon aria-hidden="true" />
          <span>Artwork unavailable</span>
        </span>
      )}
    </span>
  );
}
