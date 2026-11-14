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
import {
  hasEngineCinematicTracks,
  illustrationSections,
} from "./sequenceIllustrations";
import { mediaViewerUrl } from "../ui/mediaViewerUtils";
import styles from "./SequenceComponents.module.css";

type SceneType = "illustrated" | "video" | "engine" | "audio" | "unknown";
const sceneContent: Record<
  SceneType,
  { label: string; status: string; intro: string }
> = {
  illustrated: {
    label: "Illustrated scene",
    status: "Scene illustrations available.",
    intro: "Available scene media and source details are shown below.",
  },
  video: {
    label: "Cinematic scene",
    status: "Video components available; full scene playback is unavailable.",
    intro: "Available scene media and source details are shown below.",
  },
  engine: {
    label: "In-game cinematic",
    status:
      "In-game cinematic · Rendered by the game engine. No standalone recording is available.",
    intro: "Rendered by the game engine. No standalone recording is available.",
  },
  audio: {
    label: "Scripted scene",
    status: "Scene audio available; full scene playback is unavailable.",
    intro: "Available scene media and source details are shown below.",
  },
  unknown: {
    label: "Scripted scene",
    status:
      "No standalone media available. The scene's visual format is unknown.",
    intro: "No standalone media available. Source details are shown below.",
  },
};

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
  const isEngineCinematic =
    !hasIllustrations &&
    !hasVideo &&
    (hasEngineCinematicTracks(media) ||
      Boolean(
        event.engine_path &&
        /sequence|level[abc]_seq|da_/i.test(event.engine_path),
      ) ||
      Boolean(
        event.reference && /sequence|level[abc]_seq|da_/i.test(event.reference),
      ) ||
      Boolean(
        event.source?.raw_path && /sequence/i.test(event.source.raw_path),
      ));
  const hasMedia =
    hasIllustrations || hasVideo || playableSequenceAudio(media).length > 0;

  let sceneType: SceneType = "unknown";
  if (hasIllustrations) sceneType = "illustrated";
  else if (hasVideo) sceneType = "video";
  else if (isEngineCinematic) sceneType = "engine";
  else if (hasMedia) sceneType = "audio";

  const { label: sceneLabel, status, intro } = sceneContent[sceneType];
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
      <p className={styles.status}>{status}</p>
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
          <p className={styles.dialogIntro}>{intro}</p>
          {isEngineCinematic && (
            <p className={styles.engineExplanation}>
              This sequence is rendered in real-time by the Unreal Engine client
              using 3D character models and camera tracks.
            </p>
          )}
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
