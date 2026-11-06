"use client";

import { Button } from "../ui/Button";
import { useMediaViewer } from "../ui/MediaViewerProvider";
import type { MediaItem } from "../ui/MediaViewer";
import { mediaViewerUrl } from "../ui/mediaViewerUtils";
import type { SequenceMedia } from "./encounterTransitions";
import {
  componentOccurrences,
  orderComponents,
  sequenceTime,
} from "./sequenceComponents";
import styles from "./SequenceComponents.module.css";
import { QuestSequenceAudio, playableSequenceAudio } from "./QuestSequenceAudio";

export function hasSequenceComponents(media?: SequenceMedia) {
  return playableEncounterFragments(media).length > 0 || playableSequenceAudio(media).length > 0;
}

export function playableEncounterFragments(media?: SequenceMedia) {
  const seen = new Set<number>();
  if (!media || media.schema_version !== 1 || media.complete_video !== false)
    return [];
  return orderComponents(media.sections).filter((section) => {
    if (
      section.kind !== "movie_fragment" ||
      section.status !== "published" ||
      typeof section.file_id !== "number" ||
      !Number.isFinite(section.file_id) ||
      typeof section.url !== "string" ||
      !mediaViewerUrl(section.url) ||
      seen.has(section.file_id)
    )
      return false;
    seen.add(section.file_id);
    return true;
  });
}

function FragmentList({
  items,
  media,
}: {
  items: MediaItem[];
  media: SequenceMedia;
}) {
  const viewer = useMediaViewer();
  return (
    <><details className={styles.components}>
      <summary>Video components · {items.length}</summary>
      <p>
        These files are used inside the game-rendered scene. They may need other
        layers or materials to look complete.
      </p>
      <ul aria-label="Video components in this scene">
        {items.map((item, index) => (
          <li key={item.id}>
            <Button
              size="sm"
              variant="secondary"
              onClick={() => viewer.open(items, index)}
            >
              {item.title}
            </Button>
            <ul aria-label="Places this component is used">
              {componentOccurrences(
                media,
                Number(item.id.replace("sequence-fragment-", "")),
              ).map((use, occurrence) => (
                <li key={occurrence}>
                  {use.interval
                    ? `${sequenceTime(use.interval.start)}–${sequenceTime(use.interval.end)} in its scene`
                    : "Position not decoded"}
                  {use.rover === 'male' ? ' · Male Rover' : use.rover === 'female' ? ' · Female Rover' : ''}
                  {use.role === 'post_process_layer' && <p>Scene layer · needs the game's compositing material.</p>}
                  <details>
                    <summary>Source</summary>
                    <small>{String(use.source || "Unknown section")}</small>
                    <pre>
                      {JSON.stringify(
                        {
                          parameters: use.parameters,
                          role: use.role,
                          mediaTexture: use.mediaTexture,
                          materialBindings: use.materialBindings,
                        },
                        null,
                        2,
                      )}
                    </pre>
                  </details>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
    </details><QuestSequenceAudio media={media} /></>
  );
}

export function QuestSequenceFragments({ media }: { media?: SequenceMedia }) {
  const sections = playableEncounterFragments(media);
  if (!sections.length) return playableSequenceAudio(media).length ? <QuestSequenceAudio media={media} /> : null;
  // Use complete published files. Source ranges and clocks are not playback crop commands.
  const items: MediaItem[] = sections.map((section, index) => ({
    id: `sequence-fragment-${section.file_id}`,
    type: "video",
    src: section.url!,
    title: `View video component ${index + 1}`,
  }));
  return <FragmentList items={items} media={media!} />;
}
