"use client";

import { Download, Maximize2, X } from "lucide-react";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { api, apiUrl } from "@/api/client";
import { Select } from "@/components/ui/Select";
import "@/styles/character-model.css";

import { ModelViewer } from '@/components/models/ModelViewer';
import type { CharacterModelData as Model } from '@/components/models/types';

const assetUrl = (url: string) => apiUrl(url.replace(/^\/api(?=\/)/, ""));
const size = (bytes: number) => `${(bytes / 1024 ** 2).toFixed(1)} MiB`;

export function CharacterModel({
  characterKey,
  name,
  gameVersion,
  refreshKey = 0,
  initialModel,
}: {
  characterKey: string;
  name: string;
  gameVersion?: string;
  refreshKey?: number;
  initialModel?: Model | null;
}) {
  const id = useId();
  const preview = useRef<HTMLDivElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const [nearby, setNearby] = useState(false);
  const [model, setModel] = useState<Model | null>(initialModel ?? null);
  const [selectedForm, setSelectedForm] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [viewing, setViewing] = useState(false);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    if (!initialModel || revision || refreshKey) setModel(null);
    setSelectedForm(null);
    setError("");
    setViewing(false);
    setNearby(false);
    const query = gameVersion
      ? `?game_version=${encodeURIComponent(gameVersion)}`
      : "";
    if (initialModel && !revision && !refreshKey) return () => controller.abort();
    api<Model>(
      `/characters/${encodeURIComponent(characterKey)}/model${query}`,
      {
        signal: controller.signal,
        cacheTtl: 0,
      },
    )
      .then((result) => {
        if (!controller.signal.aborted) setModel(result);
      })
      .catch((cause) => {
        if (!controller.signal.aborted)
          setError(
            cause instanceof Error
              ? cause.message
              : "Could not load model availability.",
          );
      });
    return () => controller.abort();
  }, [characterKey, gameVersion, revision, refreshKey, initialModel]);

  // A form owns all its assets; absent optional assets never borrow the default rig.
  const form = model?.forms?.find((item) => item.id === selectedForm)
    ?? model?.forms?.[0];
  const assets = form ?? model;

  useEffect(() => {
    if (model?.status !== "ready" || !assets?.preview || !preview.current) return;
    if (typeof IntersectionObserver === "undefined") {
      setNearby(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setNearby(true);
          observer.disconnect();
        }
      },
      { rootMargin: "240px" },
    );
    observer.observe(preview.current);
    return () => observer.disconnect();
  }, [model, assets]);

  useEffect(() => {
    if (!viewing) return;
    const element = dialog.current;
    const returnFocus = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    element?.showModal();
    document.body.style.overflow = "hidden";
    return () => {
      element?.close();
      document.body.style.overflow = overflow;
      if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true });
    };
  }, [viewing]);

  const formOptions = model?.forms?.map(({ id, label }) => ({ value: id, label })) ?? [];
  const formSelector = formOptions.length > 1 && (
    <Select
      className="character-model-form-select"
      label="Model form"
      value={form?.id ?? ""}
      options={formOptions}
      onChange={setSelectedForm}
    />
  );

  return (
    <section
      className="content-panel character-model"
      aria-label="Character model"
    >
      <div className="character-model-heading">
        <h2>Character model</h2>
        {model?.status === "ready" && formSelector}
      </div>
      {error ? (
        <div role="alert">
          <p>Model availability could not be loaded: {error}</p>
          <button type="button" onClick={refresh}>
            Retry model availability
          </button>
        </div>
      ) : !model ? (
        <p role="status">Checking model availability…</p>
      ) : model.status === "missing" ? (
        <p>
          No model has been published for this character
          {gameVersion ? ` in ${gameVersion}` : ""}.
        </p>
      ) : (
        <>
          {assets?.preview && (
            <div className="character-model-preview" ref={preview}>
              {!viewing && <ModelViewer
                key={`${form?.id ?? "legacy"}:${assets.preview.url}`}
                assets={assets} name={name} preview active={nearby}
              />}
              <button
                type="button"
                className="character-model-expand"
                aria-label={`Open 3D viewer of ${name}`}
                aria-haspopup="dialog"
                aria-expanded={viewing}
                onClick={() => setViewing(true)}
              >
                <Maximize2 size={18} aria-hidden="true" />
                <span>Explore model</span>
              </button>
            </div>
          )}
          <div className="character-model-actions">
            {assets?.download && (
              <a href={assetUrl(assets.download.url)} download>
                <Download size={16} aria-hidden="true" /> Full-resolution GLB ·{" "}
                {size(assets.download.size_bytes)}
              </a>
            )}
            {assets?.animations && (
              <a href={assetUrl(assets.animations.url)} download>
                <Download size={16} aria-hidden="true" /> Animations ·{" "}
                {size(assets.animations.size_bytes)}
              </a>
            )}
          </div>
          <dialog
              ref={dialog}
              className="character-model-dialog character-model-viewer"
              aria-labelledby={`${id}-viewer-title`}
              onCancel={(event) => {
                if (event.target !== event.currentTarget) return;
                event.preventDefault();
                setViewing(false);
              }}
              onClose={(event) => { if (event.target === event.currentTarget) setViewing(false); }}
            >
              <header className="character-model-dialog-heading">
                <h2 id={`${id}-viewer-title`}>{name} · 3D model</h2>
                {formSelector}
                <button
                  type="button"
                  autoFocus
                  aria-label="Close 3D viewer"
                  onClick={() => setViewing(false)}
                >
                  <X size={20} aria-hidden="true" />
                </button>
              </header>
              {assets?.preview ? (
                <ModelViewer key={`${viewing}:${form?.id ?? "legacy"}:${assets.preview.url}`}
                  assets={assets} name={name} active={viewing} />
              ) : (
                <p role="status">No preview has been published for this form.</p>
              )}
            </dialog>
        </>
      )}
    </section>
  );
}
