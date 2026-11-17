"use client";

import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import { api, ApiError } from "@/api/client";
import { useAuth } from "@/auth/AuthProvider";
import { Button } from "@/components/ui/Button";
import { Film, X } from "lucide-react";
import type { QuestMediaEvent } from "./QuestMediaReferences";
import { useSequenceVideoContext } from "./SequenceVideoContext";
import styles from "./SequenceVideoAdmin.module.css";

type Recording = NonNullable<QuestMediaEvent["youtube_fallback"]>;

export function SequenceVideoAdmin({ event }: { event: QuestMediaEvent }) {
  const { user } = useAuth();
  const context = useSequenceVideoContext();
  const [open, setOpen] = useState(false);
  const titleId = useId();
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const element = dialog.current;
    element?.showModal();
    return () => { element?.close(); trigger.current?.focus({ preventScroll: true }); };
  }, [open]);
  if (user?.role !== "admin" || !context?.version || !event.occurrence_id) return null;
  return (
    <>
      <button ref={trigger} type="button" className={styles.trigger} onClick={() => setOpen(true)}
        aria-label={event.youtube_fallback ? "Edit YouTube recording" : "Add YouTube recording"}>
        <Film size={18} aria-hidden="true" /> <span>{event.youtube_fallback ? "Edit recording" : "Add recording"}</span>
      </button>
      {open && <dialog ref={dialog} className={styles.dialog} aria-labelledby={titleId}
        onCancel={(e) => { e.preventDefault(); setOpen(false); }}>
        <header className={styles.header}><h2 id={titleId}>YouTube recording</h2>
          <Button variant="ghost" size="icon" icon={<X size={20} />} aria-label="Close recording editor" onClick={() => setOpen(false)} />
        </header>
        <p className={styles.hint}>Add a recording for this scene and choose the time range.</p>
          <SequenceVideoForm
            key={`${context.version}:${event.occurrence_id}`}
            questKey={context.questKey}
            version={context.version}
            event={event}
            onChange={context.onChange}
          />
      </dialog>}
    </>
  );
}

export function SequenceVideoForm({ questKey, version, event, onChange }: {
  questKey: string;
  version: string;
  event: QuestMediaEvent;
  onChange: (occurrence: string, version: string, recording: Recording | null) => void;
}) {
  const { refresh } = useAuth();
  const id = useId();
  const current = event.youtube_fallback;
  const [url, setUrl] = useState(current ? `https://www.youtube.com/watch?v=${current.video_id}` : "");
  const [start, setStart] = useState(String(current?.start_seconds ?? 0));
  const [end, setEnd] = useState(current?.end_seconds == null ? "" : String(current.end_seconds));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const urlInput = useRef<HTMLInputElement>(null);
  const pending = useRef(false);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);

  async function saveOrDelete(remove: boolean) {
    if (pending.current || !event.occurrence_id) return;
    const startValue = Number(start);
    const endValue = end.trim() ? Number(end) : null;
    if (!remove && (!url.trim() || !/^\d+$/.test(start) || !Number.isSafeInteger(startValue)
      || startValue > 2147483647 || (endValue !== null && (!/^\d+$/.test(end)
        || !Number.isSafeInteger(endValue) || endValue > 2147483647 || endValue <= startValue)))) {
      setError("Enter a YouTube URL and whole seconds; the end must be later than the start.");
      setStatus("");
      urlInput.current?.focus();
      return;
    }
    pending.current = true;
    setBusy(true);
    setError("");
    setStatus("");
    const controller = new AbortController();
    request.current = controller;
    try {
      const result = await api<Recording>(
        `/admin/quests/${encodeURIComponent(questKey)}/media/${event.occurrence_id}/youtube?game_version=${encodeURIComponent(version)}`,
        { method: remove ? "DELETE" : "PUT", signal: controller.signal,
          ...(remove ? {} : { body: { url: url.trim(), start_seconds: startValue, end_seconds: endValue } }) },
      );
      if (controller.signal.aborted) return;
      if (remove) {
        setUrl("");
        setStart("0");
        setEnd("");
      } else {
        setUrl(`https://www.youtube.com/watch?v=${result.video_id}`);
        setStart(String(result.start_seconds));
        setEnd(result.end_seconds == null ? "" : String(result.end_seconds));
      }
      onChange(event.occurrence_id, version, remove ? null : {
        video_id: result.video_id, start_seconds: result.start_seconds, end_seconds: result.end_seconds,
      });
      setStatus(remove ? "Recording removed." : "Recording saved.");
    } catch (reason) {
      if (controller.signal.aborted) return;
      setError(reason instanceof ApiError && reason.status === 422
        ? "Enter a valid YouTube URL and time range."
        : reason instanceof Error ? reason.message : "Could not update the recording.");
      if (reason instanceof ApiError && [401, 403].includes(reason.status)) void refresh();
    } finally {
      pending.current = false;
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void saveOrDelete(false);
  };
  return (
    <form className={styles.form} onSubmit={submit} noValidate>
      <label htmlFor={`${id}-url`}>YouTube URL</label>
      <input ref={urlInput} id={`${id}-url`} type="url" value={url} disabled={busy}
        aria-invalid={!!error} aria-describedby={error ? `${id}-error` : undefined}
        onChange={(e) => { setUrl(e.target.value); setStatus(""); setError(""); }}
        placeholder="https://www.youtube.com/watch?v=…" />
      <div className={styles.times}>
        <label htmlFor={`${id}-start`}>Start (seconds)
          <input id={`${id}-start`} type="number" min="0" step="1" value={start} disabled={busy}
            onChange={(e) => { setStart(e.target.value); setStatus(""); setError(""); }} />
        </label>
        <label htmlFor={`${id}-end`}>End (seconds, optional)
          <input id={`${id}-end`} type="number" min="0" step="1" value={end} disabled={busy}
            onChange={(e) => { setEnd(e.target.value); setStatus(""); setError(""); }} />
        </label>
      </div>
      <div className={styles.actions}>
        <Button type="submit" variant="primary" size="sm" loading={busy}>Save recording</Button>
        {current && <Button variant="secondary" size="sm" disabled={busy}
          onClick={() => void saveOrDelete(true)}>Remove recording</Button>}
      </div>
      {error && <p id={`${id}-error`} role="alert">{error}</p>}
      {status && <p role="status">{status}</p>}
    </form>
  );
}
