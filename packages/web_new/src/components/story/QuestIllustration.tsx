'use client';

import { useEffect, useRef, useState } from 'react';
import { Archive, Download, ImageOff } from 'lucide-react';
import { useNarrativePreferences } from '../../preferences/NarrativePreferences';
import { Select } from '../ui/Select';

export type Illustration = {
  id: string;
  kind: 'image' | 'clear' | 'spine';
  resource_id?: number;
  bundle?: {
    url: string;
    asset_version?: string;
    size_bytes?: number;
    file_id?: number;
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
  const variants = illustration.images.some((image) => image.rover);
  useEffect(() => {
    // The image can fail before React hydrates the server-rendered page.
    if (selected?.url && image.current?.complete && !image.current.naturalWidth)
      setFailedUrl(selected.url);
  }, [selected?.url, retry]);
  if (illustration.kind === 'clear') return null;
  return (
    <figure
      className={`quest-illustration${presentation === 'background' ? ' is-background' : ''}`}
    >
      {selected?.url && failedUrl !== selected.url ? (
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
                  ? 'Animated scene archived'
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
              ...illustration.images
                .filter((image) => image.rover)
                .map((image) => ({
                  value: image.rover!,
                  label: image.rover === 'male' ? 'Male Rover' : 'Female Rover',
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
          {illustration.images.map((image) => (
            <small key={image.engine_path}>{image.engine_path}</small>
          ))}
        </details>
      </figcaption>
    </figure>
  );
}
