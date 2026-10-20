'use client';

import { useState } from 'react';
import { AudioPlayer } from './AudioPlayer';
import { Select } from './Select';

export function TrackPlayer({
  src,
  tracks,
  defaultTrack,
  label = 'Audio',
}: {
  src?: string | null;
  tracks?: Record<string, string>;
  defaultTrack?: string;
  label?: string;
}) {
  const [choice, setChoice] = useState<string | null>(null);
  if (!tracks || !Object.keys(tracks).length) return <AudioPlayer src={src} label={label} />;
  const selected = choice ?? defaultTrack ?? Object.keys(tracks)[0];
  const source = choice ? tracks[choice] : (tracks[selected] ?? src);
  const options = Object.keys(tracks).map((name) => ({ value: name, label: name }));
  if (!(selected in tracks))
    options.unshift({ value: selected, label: `${selected} · unavailable` });
  return (
    <div className="audio-track-player">
      <div className="audio-track-select">
        <span>Track</span>
        <Select
          label={`Audio track: ${label}`}
          value={selected}
          options={options}
          onChange={setChoice}
        />
      </div>
      <AudioPlayer src={source} label={label} />
    </div>
  );
}
