"use client";

import { Button } from '../ui/Button';
import { useMediaViewer } from '../ui/MediaViewerProvider';
import type { MediaItem } from '../ui/MediaViewer';
import { mediaViewerUrl } from '../ui/mediaViewerUtils';
import type { SequenceMedia } from './encounterTransitions';

export function playableEncounterFragments(media?: SequenceMedia) {
  const seen = new Set<number>();
  if (!media || media.schema_version !== 1 || media.complete_video !== false) return [];
  return media.sections.filter(section => {
    if (section.kind !== 'movie_fragment' || section.status !== 'published' ||
        typeof section.file_id !== 'number' || !Number.isFinite(section.file_id) ||
        typeof section.url !== 'string' || !mediaViewerUrl(section.url) || seen.has(section.file_id)) return false;
    seen.add(section.file_id);
    return true;
  });
}

function FragmentList({ items }: { items: MediaItem[] }) {
  const viewer = useMediaViewer();
  return <details>
    <summary>Video fragments · {items.length}</summary>
    <p>Published fragments from this sequence, not a complete cinematic recording.</p>
    {items.map((item, index) => <Button key={item.id} size="sm" variant="secondary"
      onClick={() => viewer.open(items, index)}>{item.title}</Button>)}
  </details>;
}

export function QuestSequenceFragments({ media }: { media?: SequenceMedia }) {
  const sections = playableEncounterFragments(media);
  if (!sections.length) return null;
  // Use complete published files. Source ranges and clocks are not playback crop commands.
  const items: MediaItem[] = sections.map((section, index) => ({
    id: `sequence-fragment-${section.file_id}`, type: 'video', src: section.url!, title: `Watch video fragment ${index + 1}`,
  }));
  return <FragmentList items={items} />;
}
