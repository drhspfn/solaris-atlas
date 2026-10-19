'use client';
import { Image as ImageIcon } from 'lucide-react';
import { useState } from 'react';
import type { GameEvent } from './calendar';

export function EventArtwork({ event }: { event: GameEvent }) {
  const [failed, setFailed] = useState('');
  const url = event.preview_url || event.banner_url;
  return (
    <span
      className={`events-artwork ${event.preview_url && event.kind === 'banner' && !event.preview_provenance?.includes('notice') ? 'is-subject' : ''}`}
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
