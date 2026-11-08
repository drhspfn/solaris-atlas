"use client";

import { useEffect, useRef, useState } from "react";
import { CircleHelp, Film, X } from "lucide-react";
import { Button } from "../ui/Button";
import { PlayerText, type PlayerDisplay } from "../dialogue/PlayerText";
import type { QuestMediaEvent } from "./QuestMediaReferences";
import {
  QuestSequenceFragments,
  playableEncounterFragments,
} from "./QuestSequenceFragments";
import { playableSequenceAudio } from "./QuestSequenceAudio";
import { illustrationSections } from "./sequenceIllustrations";
import { mediaViewerUrl } from "../ui/mediaViewerUtils";
import styles from "./SequenceComponents.module.css";

export function QuestSceneDetails({
  event,
  display,
}: {
  event: QuestMediaEvent;
  display?: PlayerDisplay;
}) {
  const [open, setOpen] = useState(false);
  const media = event.sequence_media;
  const hasIllustrations = illustrationSections(media).some((section) =>
    mediaViewerUrl(section.url!),
  );
  const hasVideo = playableEncounterFragments(media).length > 0;
  const sections = media?.schema_version === 1 ? media.sections : [];
  const isEngineCinematic =
    !hasIllustrations &&
    !hasVideo &&
    sections.some((section) => section.kind === "camera") &&
    sections.some((section) =>
      ["skeletal_animation", "skeletal_animation_asset"].includes(section.kind),
    );
  const hasMedia =
    hasIllustrations ||
    hasVideo ||
    playableSequenceAudio(media).length > 0;
  const sceneLabel = hasIllustrations
    ? "Illustrated scene"
    : hasVideo
      ? "Cinematic scene"
      : isEngineCinematic
        ? "In-game cinematic"
        : "Scripted scene";
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const element = dialog.current;
    const overflow = document.body.style.overflow;
    element?.showModal();
    document.body.style.overflow = "hidden";
    return () => {
      element?.close();
      document.body.style.overflow = overflow;
      trigger.current?.focus({ preventScroll: true });
    };
  }, [open]);
  return (
    <>
      <header className={styles.sceneHeader}>
        <h3>
          <Film size={18} aria-hidden="true" />
          <PlayerText
            value={event.title}
            fallback={sceneLabel}
            display={display}
          />
        </h3>
        <button
          ref={trigger}
          type="button"
          className={styles.infoButton}
          aria-label="Scene information"
          onClick={() => setOpen(true)}
        >
          <CircleHelp size={20} />
        </button>
      </header>
      <p className={styles.status}>
        {hasIllustrations
          ? "Scene illustrations available."
          : hasVideo
            ? "Video components available; full scene playback is unavailable."
            : isEngineCinematic
              ? "In-game cinematic · Rendered by the game engine. No standalone recording is available."
              : hasMedia
                ? "Scene audio available; full scene playback is unavailable."
                : "No standalone media available. The scene's visual format is unknown."}
      </p>
      {open && (
        <dialog
          ref={dialog}
          className={styles.sceneDialog}
          aria-label="Scene information"
          onCancel={(event) => {
            event.preventDefault();
            setOpen(false);
          }}
          onClick={(event) => {
            if (event.target !== event.currentTarget) return;
            const rect = event.currentTarget.getBoundingClientRect();
            if (
              event.clientX < rect.left ||
              event.clientX > rect.right ||
              event.clientY < rect.top ||
              event.clientY > rect.bottom
            )
              setOpen(false);
          }}
        >
          <header className={styles.dialogHeader}>
            <div>
              <span className="eyebrow left">SCENE LIBRARY</span>
              <h2>Scene information</h2>
            </div>
            <Button
              variant="ghost"
              size="icon"
              aria-label="Close scene information"
              onClick={() => setOpen(false)}
            >
              <X size={20} />
            </Button>
          </header>
          <p className={styles.dialogIntro}>
            {isEngineCinematic
              ? "Rendered by the game engine. No standalone recording is available."
              : hasMedia
                ? "Available scene media and source details are shown below."
                : "No standalone media available. Source details are shown below."}
          </p>
          <QuestSequenceFragments media={event.sequence_media} />
          <details className={styles.source} open={!hasMedia}>
            <summary>Technical details</summary>
            <dl className={styles.sourceFields}>
              <dt>Scene resource</dt>
              <dd>{event.engine_path || event.reference}</dd>
              <dt>Data file</dt>
              <dd>{event.source.file}</dd>
              <dt>Data location</dt>
              <dd>{event.source.raw_path}</dd>
              <dt>Story placement</dt>
              <dd>
                {event.story_position
                  ? "Linked to the story"
                  : event.review_position
                    ? "Archive order; exact story position is unconfirmed"
                    : "Not yet linked"}
              </dd>
            </dl>
          </details>
        </dialog>
      )}
    </>
  );
}
