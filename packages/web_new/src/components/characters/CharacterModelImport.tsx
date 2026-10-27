"use client";

import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import { api, ApiError } from "@/api/client";
import { useAuth } from "@/auth/AuthProvider";
import { Select } from "@/components/ui/Select";
import "@/styles/character-model.css";

type Job = {
  id: number;
  status: string;
  result?: unknown;
  error?: string | null;
};
const terminal = (status: string) =>
  ["completed", "failed", "enqueue_failed", "cancelled"].includes(status);

export function CharacterModelImport({
  characterKey,
  initialGameVersion,
  versions = [],
  active = true,
  onComplete,
}: {
  characterKey?: string;
  initialGameVersion?: string;
  versions?: string[];
  active?: boolean;
  onComplete?: () => void;
}) {
  const { user } = useAuth();
  if (user?.role !== "admin") return null;
  return (
    <ImportControls
      key={`${characterKey ?? ""}:${initialGameVersion ?? ""}`}
      characterKey={characterKey}
      initialGameVersion={initialGameVersion}
      versions={versions}
      active={active}
      onComplete={onComplete}
    />
  );
}

function ImportControls({
  characterKey,
  initialGameVersion,
  versions,
  active,
  onComplete,
}: {
  characterKey?: string;
  initialGameVersion?: string;
  versions: string[];
  active: boolean;
  onComplete?: () => void;
}) {
  const { refresh: refreshAuth } = useAuth();
  const id = useId();
  const [open, setOpen] = useState(false);
  const [key, setKey] = useState(characterKey ?? "");
  const [version, setVersion] = useState(initialGameVersion ?? "");
  const [force, setForce] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [invalidKey, setInvalidKey] = useState(false);
  const [retry, setRetry] = useState(0);
  const submitting = useRef(false);
  const request = useRef<AbortController | null>(null);
  const completed = useRef<number | null>(null);
  const complete = useRef(onComplete);
  complete.current = onComplete;
  const keyInput = useRef<HTMLInputElement>(null);
  const pending = !!job && !terminal(job.status);

  useEffect(() => () => request.current?.abort(), []);

  useEffect(() => {
    if (!open || !active || !pending || !job) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let attempts = 0;
    async function poll() {
      if (document.visibilityState === "hidden") {
        timer = setTimeout(poll, 3000);
        return;
      }
      try {
        const result = await api<Job>(`/admin/character-models/${job!.id}`, {
          signal: controller.signal,
          cacheTtl: 0,
        });
        if (controller.signal.aborted) return;
        setJob(result);
        setError("");
        if (result.status === "completed" && completed.current !== result.id) {
          completed.current = result.id;
          complete.current?.();
        }
        if (terminal(result.status)) return;
        if (++attempts >= 200) {
          setError(
            "Import is still pending. Check status to continue monitoring this run.",
          );
          return;
        }
        timer = setTimeout(poll, 3000);
      } catch (cause) {
        if (controller.signal.aborted) return;
        setError(
          cause instanceof Error
            ? cause.message
            : "Could not check import status.",
        );
        if (cause instanceof ApiError && [401, 403].includes(cause.status))
          void refreshAuth();
      }
    }
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [open, active, pending, job?.id, retry, refreshAuth]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (submitting.current || pending) return;
    setError("");
    setInvalidKey(false);
    if (!/^character:[1-9][0-9]*$/.test(key.trim())) {
      setInvalidKey(true);
      setError("Enter a canonical character key, for example character:1505.");
      keyInput.current?.focus();
      return;
    }
    submitting.current = true;
    setJob(null);
    setBusy(true);
    const controller = new AbortController();
    request.current = controller;
    try {
      const result = await api<Job>("/admin/character-models", {
        method: "POST",
        signal: controller.signal,
        body: {
          character_key: key.trim(),
          ...(version.trim() ? { game_version: version.trim() } : {}),
          force,
        },
      });
      if (controller.signal.aborted) return;
      setJob(result);
      if (result.status === "completed") complete.current?.();
    } catch (cause) {
      if (controller.signal.aborted) return;
      setError(
        cause instanceof Error
          ? cause.message
          : "Could not queue character model import.",
      );
      if (cause instanceof ApiError && [401, 403].includes(cause.status))
        void refreshAuth();
    } finally {
      submitting.current = false;
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  return (
    <details
      className="character-model-import"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>Import character model · admin</summary>
      <p>Import a rigged model with textures. Animations are not included.</p>
      <form
        noValidate
        onSubmit={(event) => void submit(event)}
        className="character-model-form"
      >
        <label htmlFor={`${id}-key`}>
          Character key
          <input
            ref={keyInput}
            id={`${id}-key`}
            value={key}
            placeholder="character:1505"
            aria-invalid={invalidKey}
            aria-describedby={invalidKey ? `${id}-error` : undefined}
            readOnly={!!characterKey}
            disabled={busy || pending}
            required
            onChange={(event) => setKey(event.target.value)}
          />
        </label>
        {versions.length ? (
          <Select
            label="Game version"
            value={version}
            disabled={busy || pending}
            onChange={setVersion}
            options={[
              { value: "", label: "Latest available" },
              ...versions.map((value) => ({ value, label: value })),
            ]}
          />
        ) : (
          <label htmlFor={`${id}-version`}>
            Game version (optional)
            <input
              id={`${id}-version`}
              value={version}
              placeholder="Latest available"
              disabled={busy || pending}
              onChange={(event) => setVersion(event.target.value)}
            />
          </label>
        )}
        <label className="character-model-force">
          <input
            type="checkbox"
            checked={force}
            disabled={busy || pending}
            onChange={(event) => setForce(event.target.checked)}
          />{" "}
          Rebuild existing model
        </label>
        <button type="submit" disabled={busy || pending} aria-busy={busy}>
          {busy
            ? "Queuing import…"
            : pending
              ? "Import pending…"
              : job && job.status !== "completed"
                ? "Retry import"
                : "Import model"}
        </button>
      </form>
      {job && (
        <p role="status">
          Import #{job.id}: {job.status}
          {job.status === "completed" ? " · Model ready." : ""}
        </p>
      )}
      {(error || job?.error) && (
        <p role="alert" id={`${id}-error`}>
          {error || job?.error}
        </p>
      )}
      {error && pending && (
        <button
          type="button"
          onClick={() => {
            setError("");
            setRetry((n) => n + 1);
          }}
        >
          Check status
        </button>
      )}
    </details>
  );
}
