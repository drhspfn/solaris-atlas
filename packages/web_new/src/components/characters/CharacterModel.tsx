"use client";

import { useCallback, useEffect, useState } from "react";
import { api, apiUrl } from "@/api/client";
import { CharacterModelImport } from "./CharacterModelImport";
import "@/styles/character-model.css";

type Asset = { url: string; size_bytes: number };
type Model = {
  status: "ready" | "missing";
  game_version: string | null;
  preview: Asset | null;
  download: Asset | null;
  physics: Asset | null;
  viewer_url?: string | null;
};
const assetUrl = (url: string) => apiUrl(url.replace(/^\/api(?=\/)/, ""));
const size = (bytes: number) => `${(bytes / 1024 ** 2).toFixed(1)} MiB`;

export function CharacterModel({
  characterKey,
  name,
  gameVersion,
}: {
  characterKey: string;
  name: string;
  gameVersion?: string;
}) {
  const [model, setModel] = useState<Model | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [viewing, setViewing] = useState(false);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    setModel(null);
    setError("");
    setViewing(false);
    const query = gameVersion
      ? `?game_version=${encodeURIComponent(gameVersion)}`
      : "";
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
  }, [characterKey, gameVersion, revision]);

  const params = new URLSearchParams({ name });
  if (model?.preview) params.set("model", assetUrl(model.preview.url));
  if (model?.physics) params.set("physics", assetUrl(model.physics.url));

  let viewerSrc = `/model-viewer/index.html?${params}`;
  if (model?.viewer_url) {
    try {
      const url = new URL(model.viewer_url);
      if (["https:", "http:"].includes(url.protocol)) {
        params.forEach((value, key) => url.searchParams.set(key, value));
        viewerSrc = url.href;
      }
    } catch {
      // Invalid viewer metadata must not break the character profile.
    }
  }

  return (
    <section
      className="content-panel character-model"
      aria-label="Character model"
    >
      <h2>Character model</h2>
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
          <p>
            Rigged model with textures · no animations
            {model.game_version ? ` · ${model.game_version}` : ""}
          </p>
          <div className="character-model-actions">
            {model.preview && (
              <button
                type="button"
                aria-expanded={viewing}
                onClick={() => setViewing((value) => !value)}
              >
                {viewing
                  ? "Close 3D viewer"
                  : `View 3D · ${size(model.preview.size_bytes)}`}
              </button>
            )}
            {model.download && (
              <a href={assetUrl(model.download.url)} download>
                Download full-resolution GLB · {size(model.download.size_bytes)}
              </a>
            )}
          </div>
          {viewing && model.preview && (
            <iframe
              title={`3D preview of ${name}`}
              src={viewerSrc}
              className="character-model-frame"
            />
          )}
        </>
      )}
      <CharacterModelImport
        characterKey={characterKey}
        initialGameVersion={gameVersion}
        onComplete={refresh}
      />
    </section>
  );
}
