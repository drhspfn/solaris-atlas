"use client";

import { Maximize2 } from "lucide-react";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { api } from "@/api/client";
import { Button } from "@/components/ui/Button";
import { Select } from "@/components/ui/Select";
import "@/styles/character-model.css";

import { ModelViewer } from "@/components/models/ModelViewer";
import type { CharacterModelData as Model } from "@/components/models/types";

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
    if (initialModel && !revision && !refreshKey)
      return () => controller.abort();
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
  const form =
    model?.forms?.find((item) => item.id === selectedForm) ?? model?.forms?.[0];
  const assets = form ?? model;

  useEffect(() => {
    if (model?.status !== "ready" || !assets?.preview || !preview.current)
      return;
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

  const appearanceGroups = (model?.forms ?? []).reduce<
    {
      key: string;
      label: string;
      items: { id: string; subLabel: string; label: string }[];
    }[]
  >((groups, item) => {
    const match = item.label.match(/^(.*?)\s*·\s*(Form\s+\d+)$/i);
    const groupLabel = match ? match[1] : item.label;
    const subLabel = match ? match[2] : "Form 1";
    let group = groups.find((g) => g.label === groupLabel);
    if (!group) {
      group = { key: item.id, label: groupLabel, items: [] };
      groups.push(group);
    }
    group.items.push({ id: item.id, subLabel, label: item.label });
    return groups;
  }, []);
  const activeGroup =
    appearanceGroups.find((g) => g.items.some((i) => i.id === form?.id)) ??
    appearanceGroups[0];
  const formSelector = (appearanceGroups.length > 1 ||
    (activeGroup?.items.length ?? 0) > 1) && (
    <>
      {appearanceGroups.length > 1 && (
        <Select
          className="character-model-form-select"
          label="Model form"
          value={activeGroup?.key ?? ""}
          options={appearanceGroups.map((g) => ({
            value: g.key,
            label: g.label,
          }))}
          onChange={(groupKey) => {
            const next = appearanceGroups.find((g) => g.key === groupKey);
            if (next?.items[0]) setSelectedForm(next.items[0].id);
          }}
        />
      )}
      {(activeGroup?.items.length ?? 0) > 1 && (
        <Select
          className="character-model-form-select"
          label="Form"
          value={form?.id ?? ""}
          options={activeGroup!.items.map((i) => ({
            value: i.id,
            label: i.subLabel,
          }))}
          onChange={setSelectedForm}
        />
      )}
    </>
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
          <Button onClick={refresh}>Retry model availability</Button>
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
              {!viewing && (
                <ModelViewer
                  key={`${form?.id ?? "legacy"}:${assets.preview.url}`}
                  assets={assets}
                  name={name}
                  formLabel={form?.label}
                  preview
                  active={nearby}
                />
              )}
              <Button
                size="icon"
                variant="default"
                className="character-model-expand character-model-preview-open"
                aria-label={`Open 3D viewer of ${name}`}
                title={`Explore ${name} in 3D`}
                aria-haspopup="dialog"
                aria-expanded={viewing}
                icon={<Maximize2 size={19} />}
                onClick={() => setViewing(true)}
              />
            </div>
          )}
          <dialog
            ref={dialog}
            className="character-model-dialog character-model-viewer"
            aria-labelledby={`${id}-viewer-title`}
            onCancel={(event) => {
              if (event.target !== event.currentTarget) return;
              event.preventDefault();
              setViewing(false);
            }}
            onClose={(event) => {
              if (event.target === event.currentTarget) setViewing(false);
            }}
          >
            {assets?.preview ? (
              <ModelViewer
                key={`${viewing}:${form?.id ?? "legacy"}:${assets.preview.url}`}
                assets={assets}
                name={name}
                formLabel={form?.label}
                active={viewing}
                titleId={`${id}-viewer-title`}
                formSelector={formSelector}
                onClose={() => setViewing(false)}
              />
            ) : (
              <p role="status">No preview has been published for this form.</p>
            )}
          </dialog>
        </>
      )}
    </section>
  );
}
