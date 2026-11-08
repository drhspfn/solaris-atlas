"use client";

import { useState } from "react";
import { useMediaViewer } from "../ui/MediaViewerProvider";
import { mediaViewerUrl } from "../ui/mediaViewerUtils";
import type { SequenceMedia } from "./encounterTransitions";
import {
  illustrationSections,
  illustrationStatus,
} from "./sequenceIllustrations";
import styles from "./SequenceComponents.module.css";

function Illustration({
  src,
  label,
  open,
}: {
  src: string;
  label: string;
  open: () => void;
}) {
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  return failed ? (
    <div className={styles.imageError} role="status">
      <p>Illustration could not be loaded.</p>
      <button
        type="button"
        onClick={() => {
          setFailed(false);
          setAttempt((value) => value + 1);
        }}
      >
        Retry
      </button>
    </div>
  ) : (
    <button
      type="button"
      className={styles.imageButton}
      onClick={open}
      aria-label={`Open ${label}`}
    >
      <img
        key={attempt}
        src={src}
        alt={label}
        loading="lazy"
        onError={() => setFailed(true)}
      />
    </button>
  );
}

export function QuestSequenceIllustrations({
  media,
}: {
  media?: SequenceMedia;
}) {
  const viewer = useMediaViewer();
  const sections = illustrationSections(media).filter(
    (section) => !!mediaViewerUrl(section.url!),
  );
  const items = sections.map((section, index) => ({
    id: `sequence-illustration-${section.file_id}-${index}`,
    src: section.url!,
    type: "image" as const,
    title: `Scene illustration ${index + 1}`,
  }));
  const status = illustrationStatus(media);
  return (
    <div className={styles.illustrations}>
      {items.length > 0 && (
        <div className={styles.imageList}>
          {items.map((item, index) => (
            <figure key={item.id}>
              <Illustration
                src={item.src}
                label={item.title}
                open={() => viewer.open(items, index)}
              />
              <figcaption>
                {item.title}
                {typeof sections[index].time_seconds === "number" &&
                Number.isFinite(sections[index].time_seconds)
                  ? ` · ${sections[index].time_seconds}s`
                  : ""}
                <a
                  href={item.src}
                  target="_blank"
                  rel="noreferrer"
                  className={styles.imageLink}
                >
                  Open image file ↗
                </a>
              </figcaption>
            </figure>
          ))}
        </div>
      )}
      {(!items.length ||
        (media?.composition && media.composition.state !== "ready")) && (
        <p className={styles.status}>{status}</p>
      )}
    </div>
  );
}
