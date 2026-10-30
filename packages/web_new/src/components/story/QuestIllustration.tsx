'use client';

import { useEffect, useRef, useState } from 'react';
import { Archive, Download, ImageOff } from 'lucide-react';
import { useNarrativePreferences } from '../../preferences/NarrativePreferences';
import { Select } from '../ui/Select';
import { QuestAnimatedBackdrop, type OpenPlayback } from './QuestAnimatedBackdrop';

export type Illustration = {
  id: string;
  kind: 'image' | 'clear' | 'spine';
  resource_id?: number;
  loop?: boolean;
  bundle?: {
    url: string;
    asset_version?: string;
    size_bytes?: number;
    file_id?: number;
    playback?: OpenPlayback | null;
  } | null;
  images: Array<{
    rover: 'male' | 'female' | null;
    engine_path: string;
    url: string | null;
  }>;
};

export function QuestIllustration({
  illustration,
  presentation = 'inline',
}: {
  illustration: Illustration;
  presentation?: 'inline' | 'background';
}) {
  const { preferredRover } = useNarrativePreferences();
  const [choice, setChoice] = useState(preferredRover === 'ask' ? '' : preferredRover);
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const image = useRef<HTMLImageElement>(null);
  const selected =
    illustration.images.find((image) => image.rover === choice) ||
    illustration.images.find((image) => !image.rover);
  const playback = illustration.bundle?.playback;
  const animated =
    playback?.variants.find((variant) => variant.rover === choice) ||
    playback?.variants.find((variant) => !variant.rover);
  const rovers = [
    ...new Set(
      [
        ...illustration.images.map((image) => image.rover),
        ...(playback?.variants || []).map((variant) => variant.rover),
      ].filter(Boolean),
    ),
  ];
  const variants = rovers.length > 0;
  useEffect(() => {
    // The image can fail before React hydrates the server-rendered page.
    if (selected?.url && image.current?.complete && !image.current.naturalWidth)
      setFailedUrl(selected.url);
  }, [selected?.url, retry]);
  if (illustration.kind === 'clear') return null;
  return (
    <figure
      className={`quest-illustration${presentation === 'background' ? ' is-background' : ''}${animated ? ' has-animation' : ''}`}
    >
      {animated ? (
        <QuestAnimatedBackdrop
          key={animated.scene_url}
          variant={animated}
          loop={!!illustration.loop}
        />
      ) : selected?.url && failedUrl !== selected.url ? (
        <img
          ref={image}
          key={`${selected.url}:${retry}`}
          src={selected.url}
          alt="Story illustration"
          loading="lazy"
          decoding="async"
          onError={() => setFailedUrl(selected.url)}
        />
      ) : (
        <div className="quest-illustration-placeholder">
          <ImageOff size={24} aria-hidden="true" />
          <p>
            {failedUrl && failedUrl === selected?.url
              ? 'Illustration could not load.'
              : illustration.kind === 'spine'
                ? illustration.bundle
                  ? variants && !choice
                    ? 'Choose Rover to view this scene.'
                    : 'Animated scene archived · playback unavailable'
                  : 'Animated scene · awaiting export'
                : variants && !choice
                  ? 'Choose Rover to view this illustration.'
                  : 'Illustration · awaiting image export.'}
          </p>
          {failedUrl === selected?.url && selected?.url && (
            <button
              type="button"
              onClick={() => {
                setFailedUrl(null);
                setRetry(retry + 1);
              }}
            >
              Retry illustration
            </button>
          )}
        </div>
      )}
      <figcaption>
        <span>ILLUSTRATED STORY</span>
        {variants && (
          <Select
            label="Rover illustration"
            value={choice}
            onChange={setChoice}
            options={[
              { value: '', label: 'Choose Rover' },
              ...rovers.map((rover) => ({
                value: rover!,
                label: rover === 'male' ? 'Male Rover' : 'Female Rover',
              })),
            ]}
          />
        )}
        {illustration.bundle && (
          <a className="quest-spine-download" href={illustration.bundle.url} download>
            <Download size={14} />
            Download animated scene
            {!!illustration.bundle.size_bytes && (
              <small>{(illustration.bundle.size_bytes / 1024 / 1024).toFixed(1)} MiB</small>
            )}
          </a>
        )}
        <details>
          <summary>Image source</summary>
          {illustration.resource_id && (
            <small>Animated background #{illustration.resource_id}</small>
          )}
          {illustration.bundle && (
            <small>
              <Archive size={12} /> Skeleton, atlas, textures and original game packages · client{' '}
              {illustration.bundle.asset_version}
            </small>
          )}
          {playback?.unsupported.map((item) => (
            <small key={item.prefab}>{item.reason}</small>
          ))}
          {illustration.images.map((image) => (
            <small key={image.engine_path}>{image.engine_path}</small>
          ))}
        </details>
      </figcaption>
    </figure>
  );
}
