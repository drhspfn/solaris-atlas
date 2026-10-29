"use client";

import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import { api, ApiError } from "@/api/client";
import { useAuth } from "@/auth/AuthProvider";
import { Select } from "@/components/ui/Select";
import { ActionMenu } from "@/components/ui/ActionMenu";
import { Upload, X } from "lucide-react";
import "@/styles/character-model.css";

type Job = {
  id: number;
  status: string;
  result?: { stage?: string; batch?: number; batches?: number; animations?: number } | null;
  error?: string | null;
};
const terminal = (status: string) =>
  ["completed", "failed", "enqueue_failed", "cancelled"].includes(status);
const stages: Record<string, string> = {
  extract: "Extracting model resources",
  rig_and_animation_plan: "Building rig and animation plan",
  animation_batch: "Exporting animations",
  animation_batch_completed: "Animation batch complete",
  merge_animation_batches: "Assembling animations",
  publish: "Publishing model",
  validate_archive: "Validating the uploaded model archive",
};

export function CharacterModelImport({
  characterKey,
  initialGameVersion,
  versions = [],
  active = true,
  variant = "inline",
  onComplete,
}: {
  characterKey?: string;
  initialGameVersion?: string;
  versions?: string[];
  active?: boolean;
  variant?: "inline" | "actions";
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
      variant={variant}
      onComplete={onComplete}
    />
  );
}

function ImportControls({
  characterKey,
  initialGameVersion,
  versions,
  active,
  variant,
  onComplete,
}: {
  characterKey?: string;
  initialGameVersion?: string;
  versions: string[];
  active: boolean;
  variant: "inline" | "actions";
  onComplete?: () => void;
}) {
  const { refresh: refreshAuth } = useAuth();
  const id = useId();
  const [open, setOpen] = useState(false);
  const [key, setKey] = useState(characterKey ?? "");
  const [version, setVersion] = useState(initialGameVersion ?? "");
  const [force, setForce] = useState(false);
  const [includeAnimations, setIncludeAnimations] = useState(true);
  const [archive, setArchive] = useState<File | null>(null);
  const [uploadProgress, setUploadProgress] = useState("");
  const uploadAttempt = useRef<{ file: File; key: string; version: string; token: string } | null>(null);
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
  const archiveInput = useRef<HTMLInputElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const pending = !!job && !terminal(job.status);

  useEffect(() => () => request.current?.abort(), []);

  useEffect(() => {
    if (variant !== "actions" || !open || !active) return;
    const element = dialog.current;
    const overflow = document.body.style.overflow;
    element?.showModal();
    document.body.style.overflow = "hidden";
    return () => {
      element?.close();
      document.body.style.overflow = overflow;
      if (opener.current?.isConnected)
        opener.current.focus({ preventScroll: true });
    };
  }, [variant, open, active]);

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
      let result: Job;
      if (archive) {
        if (archive.size > 4 * 1024 ** 3 || archive.size === 0)
          throw new Error("Choose a nonempty model ZIP archive up to 4 GiB.");
        if (
          uploadAttempt.current?.file !== archive ||
          uploadAttempt.current.key !== key.trim() ||
          uploadAttempt.current.version !== version
        ) {
          uploadAttempt.current = {
            file: archive, key: key.trim(), version,
            token: crypto.randomUUID().replaceAll("-", ""),
          };
        }
        const upload = await api<Job & { chunk_bytes: number }>("/admin/character-models/uploads", {
          method: "POST",
          signal: controller.signal,
          body: {
            character_key: key.trim(),
            ...(version.trim() ? { game_version: version.trim() } : {}),
            force, archive_bytes: archive.size, upload_token: uploadAttempt.current.token,
          },
        });
        if (upload.status === "uploading") {
          for (let offset = 0, part = 0; offset < archive.size; offset += upload.chunk_bytes, part++) {
            setUploadProgress(`Uploading archive · ${Math.round(offset / archive.size * 100)}%`);
            await api(`/admin/character-models/uploads/${upload.id}/parts/${part}`, {
              method: "PUT",
              body: archive.slice(offset, offset + upload.chunk_bytes),
              signal: controller.signal,
            });
          }
        }
        setUploadProgress("Upload complete · queuing validation and publication");
        result = await api<Job>(`/admin/character-models/uploads/${upload.id}/complete`, {
          method: "POST",
          signal: controller.signal,
        });
      } else {
        result = await api<Job>("/admin/character-models", {
          method: "POST",
          signal: controller.signal,
          body: {
            character_key: key.trim(),
            ...(version.trim() ? { game_version: version.trim() } : {}),
            force,
            include_animations: includeAnimations,
          },
        });
      }
      if (controller.signal.aborted) return;
      setJob(result);
      if (result.status === "completed" && completed.current !== result.id) {
        completed.current = result.id;
        complete.current?.();
      }
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

  const controls = (
    <>
      <p>
        Build from the game client, or upload a local model ZIP.
        Animations load separately on demand.
      </p>
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
        <label htmlFor={`${id}-archive`}>
          Local model archive (optional)
          <input ref={archiveInput} id={`${id}-archive`} type="file" accept=".zip,application/zip"
            disabled={busy || pending} onChange={event => {
              setArchive(event.target.files?.[0] ?? null); setUploadProgress("");
            }} />
          <small>Leave empty to build from the game. With an archive, its included animations and forms are used.</small>
        </label>
        {archive && <button type="button" disabled={busy || pending} onClick={() => {
          setArchive(null); setUploadProgress("");
          if (archiveInput.current) archiveInput.current.value = "";
        }}>Use game client instead</button>}
        <button type="button" disabled={busy || pending || !/^character:[1-9][0-9]*$/.test(key.trim())}
          onClick={() => void (async () => {
            try {
              const query = new URLSearchParams({ character_key: key.trim(), ...(version ? { game_version: version } : {}) });
              const recipe = await api<{ game_version: string }>(`/admin/character-models/recipe?${query}`, { cacheTtl: 0 });
              const url = URL.createObjectURL(new Blob([JSON.stringify(recipe, null, 2)], { type: "application/json" }));
              const link = document.createElement("a"); link.href = url; link.download = `${key.replace(":", "-")}-${recipe.game_version}-recipe.json`; link.click();
              setTimeout(() => URL.revokeObjectURL(url), 1000);
            } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not download model recipe."); }
          })()}>Download local build recipe</button>
        <label className="character-model-force">
          <input
            type="checkbox"
            checked={includeAnimations}
            disabled={busy || pending || !!archive}
            onChange={(event) => setIncludeAnimations(event.target.checked)}
          />{" "}
          Include animations as a separate download
        </label>
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
      {busy && uploadProgress && <p role="status">{uploadProgress}</p>}
      {job && (
        <p role="status">
          Import #{job.id}: {job.status.replaceAll("_", " ")}
          {job.status === "completed" ? " · Model ready." : ""}
          {pending && job.result?.stage && ` · ${stages[job.result.stage] || "Processing model"}`}
          {pending && Number.isInteger(job.result?.batch) && Number.isInteger(job.result?.batches)
            && ` · Batch ${job.result!.batch} of ${job.result!.batches}`}
        </p>
      )}
      {job && (
        <p>
          <a href="/admin/data-operations">
            Open task monitor · run #{job.id}
          </a>
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
    </>
  );

  if (variant === "inline")
    return (
      <details
        className="character-model-import"
        onToggle={(event) => setOpen(event.currentTarget.open)}
      >
        <summary>Import character model · admin</summary>
        {controls}
      </details>
    );

  return (
    <>
      <ActionMenu label="Character actions">
        <button
          type="button"
          aria-haspopup="dialog"
          onClick={(event) => {
            const menu = event.currentTarget.closest("details");
            opener.current =
              menu?.querySelector("summary") ?? event.currentTarget;
            if (menu) menu.open = false;
            opener.current?.focus({ preventScroll: true });
            setOpen(true);
          }}
        >
          <Upload size={16} aria-hidden="true" /> Import character model
        </button>
      </ActionMenu>
      <dialog
        ref={dialog}
        className="character-model-dialog character-model-import-dialog"
        aria-labelledby={`${id}-title`}
        onCancel={(event) => {
          event.preventDefault();
          setOpen(false);
        }}
        onClose={() => setOpen(false)}
      >
        <header className="character-model-dialog-heading">
          <h2 id={`${id}-title`}>Import character model</h2>
          <button
            type="button"
            autoFocus
            aria-label="Close model import"
            onClick={() => setOpen(false)}
          >
            <X size={20} aria-hidden="true" />
          </button>
        </header>
        {controls}
        {pending && (
          <p>Closing stops monitoring. The queued import continues.</p>
        )}
      </dialog>
    </>
  );
}
