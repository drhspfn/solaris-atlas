'use client';

import { useState } from 'react';
import { useNarrativePreferences } from '../../preferences/NarrativePreferences';
import { Select } from '../ui/Select';

export type Illustration = {
  id: string;
  kind: 'image' | 'clear' | 'spine';
  resource_id?: number;
  images: Array<{ rover: 'male' | 'female' | null; engine_path: string; url: string | null }>;
};

export function QuestIllustration({ illustration }: { illustration: Illustration }) {
  const { preferredRover } = useNarrativePreferences();
  const [choice, setChoice] = useState(preferredRover === 'male' ? 'male' : 'female');
  if (illustration.kind === 'clear') return null;
  const selected = illustration.images.find((image) => image.rover === choice) || illustration.images[0];
  return <figure className="quest-illustration">
    {selected?.url ? <img src={selected.url} alt="Story illustration" loading="lazy" decoding="async" /> :
      <p>{illustration.kind === 'spine' ? 'Animated illustration · source recorded, playback unavailable.' : 'Story illustration · awaiting image export.'}</p>}
    <figcaption>
      <span>ILLUSTRATED STORY</span>
      {illustration.images.length > 1 && <Select label="Rover illustration" value={choice} onChange={setChoice}
        options={illustration.images.map((image) => ({ value: image.rover!, label: image.rover === 'male' ? 'Male Rover' : 'Female Rover' }))} />}
      <details><summary>Image source</summary>
        {illustration.resource_id && <small>Animated background #{illustration.resource_id}</small>}
        {illustration.images.map((image) => <small key={image.engine_path}>{image.engine_path}</small>)}
      </details>
    </figcaption>
  </figure>;
}
