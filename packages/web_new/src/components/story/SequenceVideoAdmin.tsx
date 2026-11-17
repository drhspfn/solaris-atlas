"use client";

import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import { api, ApiError } from "@/api/client";
import { useAuth } from "@/auth/AuthProvider";
import { Button } from "@/components/ui/Button";
import type { QuestMediaEvent, QuestMediaManifest } from "./QuestMediaReferences";
import styles from "./SequenceVideoAdmin.module.css";

type Recording = NonNullable<QuestMediaEvent["youtube_fallback"]>;

export function SequenceVideoAdmin({ questKey, manifest, onChange }: {
  questKey: string;
  manifest: QuestMediaManifest | null;
  onChange: (occurrence: string, version: string, recording: Recording | null) => void;
}) {
  const { user } = useAuth();
  if (user?.role !== "admin" || !manifest?.game_version) return null;
  const scenes = manifest.events.filter((event) => event.kind === "sequence");
  if (!scenes.length) return null;
  return (
    <section className={styles.panel} aria-label="Manage scene recordings">
      <details>
        <summary>Manage scene recordings</summary>
        <p>Choose a scene occurrence in snapshot {manifest.game_version}.</p>
        {scenes.map((event, index) => event.occurrence_id ? (
          <SequenceVideoForm
            key={`${manifest.game_version}:${event.occurrence_id}`}
            questKey={questKey}
            version={manifest.game_version!}
            event={event}
            onChange={onChange}
          />
        ) : (
          <p key={`unmatched-${index}`}>A scene could not be uniquely identified and cannot be edited.</p>
        ))}
      </details>
    </section>
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
      <p><strong>{event.title || "In-game scene"}</strong>
        {" · "}{event.flow_state || "Unmapped flow"} · action {event.action_index}</p>
      <small className={styles.source}>{event.engine_path || event.reference} · {event.source?.raw_path}</small>
      <label htmlFor={`${id}-url`}>YouTube URL</label>
      <input id={`${id}-url`} type="url" value={url} disabled={busy}
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
      {error && <p role="alert">{error}</p>}
      {status && <p role="status">{status}</p>}
    </form>
  );
}
