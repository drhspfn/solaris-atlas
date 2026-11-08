"use client";

import { useEffect, useRef, useState } from "react";
import { CircleHelp, Film, X } from "lucide-react";
import { Button } from "../ui/Button";
import { PlayerText, type PlayerDisplay } from "../dialogue/PlayerText";
import type { QuestMediaEvent } from "./QuestMediaReferences";
import { QuestSequenceFragments } from "./QuestSequenceFragments";
import styles from "./SequenceComponents.module.css";

export function QuestSceneDetails({
  event,
  display,
}: {
  event: QuestMediaEvent;
  display?: PlayerDisplay;
}) {
  const [open, setOpen] = useState(false);
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
            fallback="Story scene"
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
            Explore the artwork and audio used in this scene. Select an image to
            open the viewer.
          </p>
          <QuestSequenceFragments media={event.sequence_media} />
          <details className={styles.source}>
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
