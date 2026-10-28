"use client";

import { Download, Maximize2, X } from "lucide-react";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { api, apiUrl } from "@/api/client";
import "@/styles/character-model.css";

type Asset = { url: string; size_bytes: number };
type Model = {
  status: "ready" | "missing";
  game_version: string | null;
  preview: Asset | null;
  download: Asset | null;
  physics: Asset | null;
  animations?: Asset | null;
  viewer_url?: string | null;
};
const assetUrl = (url: string) => apiUrl(url.replace(/^\/api(?=\/)/, ""));
const size = (bytes: number) => `${(bytes / 1024 ** 2).toFixed(1)} MiB`;

export function CharacterModel({
  characterKey,
  name,
  gameVersion,
  refreshKey = 0,
}: {
  characterKey: string;
  name: string;
  gameVersion?: string;
  refreshKey?: number;
}) {
  const id = useId();
  const preview = useRef<HTMLDivElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const [nearby, setNearby] = useState(false);
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
    setNearby(false);
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
  }, [characterKey, gameVersion, revision, refreshKey]);

  useEffect(() => {
    if (model?.status !== "ready" || !model.preview || !preview.current) return;
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
  }, [model]);

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

  let runtimeURL = "/model-viewer/index.html";
  if (model?.viewer_url) {
    try {
      const publishedViewer = new URL(model.viewer_url);
      if (["https:", "http:"].includes(publishedViewer.protocol))
        const current = new URL("../current/index.html", publishedViewer);
        runtimeURL = `${current.origin}${current.pathname}`;
    } catch {
      // Invalid metadata retains the local runtime fallback.
    }
  }
  const params = new URLSearchParams({ name });
  if (model?.preview) params.set("model", assetUrl(model.preview.url));
  const previewSrc = `${runtimeURL}?${params}&mode=preview`;
  if (model?.physics) params.set("physics", assetUrl(model.physics.url));
  if (model?.animations) {
    params.set("animations", assetUrl(model.animations.url));
    params.set("animation_bytes", String(model.animations.size_bytes));
  }
  // The current pointer upgrades published models without rebuilding their assets.
  const viewerSrc = `${runtimeURL}?${params}`;

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
          {model.preview && (
            <div className="character-model-preview" ref={preview}>
              {nearby && (
                <ModelFrame
                  key={previewSrc}
                  src={previewSrc}
                  title={`3D preview of ${name}`}
                  compact
                />
              )}
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
          {model.game_version && (
            <p className="character-model-version">{model.game_version}</p>
          )}
          <div className="character-model-actions">
            {model.download && (
              <a href={assetUrl(model.download.url)} download>
                <Download size={16} aria-hidden="true" /> Full-resolution GLB ·{" "}
                {size(model.download.size_bytes)}
              </a>
            )}
            {model.animations && (
              <a href={assetUrl(model.animations.url)} download>
                <Download size={16} aria-hidden="true" /> Animations ·{" "}
                {size(model.animations.size_bytes)}
              </a>
            )}
          </div>
          {viewing && model.preview && (
            <dialog
              ref={dialog}
              className="character-model-dialog character-model-viewer"
              aria-labelledby={`${id}-viewer-title`}
              onCancel={(event) => {
                event.preventDefault();
                setViewing(false);
              }}
              onClose={() => setViewing(false)}
            >
              <header className="character-model-dialog-heading">
                <h2 id={`${id}-viewer-title`}>{name} · 3D model</h2>
                <button
                  type="button"
                  autoFocus
                  aria-label="Close 3D viewer"
                  onClick={() => setViewing(false)}
                >
                  <X size={20} aria-hidden="true" />
                </button>
              </header>
              <ModelFrame
                key={viewerSrc}
                src={viewerSrc}
                title={`3D viewer of ${name}`}
              />
            </dialog>
          )}
        </>
      )}
    </section>
  );
}

function ModelFrame({
  src,
  title,
  compact = false,
}: {
  src: string;
  title: string;
  compact?: boolean;
}) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [message, setMessage] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const trustedOrigin = new URL(src, window.location.origin).origin;
    function receive(event: MessageEvent) {
      if (
        event.origin !== trustedOrigin ||
        !frame.current?.contentWindow ||
        event.source !== frame.current.contentWindow ||
        event.data?.type !== "solaris-model-state"
      ) return;
      if (event.data.state === "ready") setState("ready");
      else if (event.data.state === "error") {
        setMessage(typeof event.data.message === "string" ? event.data.message : "");
        setState("error");
      }
    }
    window.addEventListener("message", receive);
    return () => window.removeEventListener("message", receive);
  }, [src, attempt]);
  useEffect(() => {
    if (state !== "loading") return;
    const timer = setTimeout(() => setState("error"), 30000);
    return () => clearTimeout(timer);
  }, [state, attempt]);
  return (
    <div
      className={`character-model-embed${compact ? " character-model-embed-compact" : ""}`}
    >
      {state !== "error" && (
        <iframe
          ref={frame}
          key={attempt}
          title={title}
          src={src}
          className="character-model-frame"
          allowFullScreen={!compact}
          tabIndex={compact ? -1 : 0}
          aria-hidden={compact || undefined}
          onError={() => setState("error")}
        />
      )}
      {state === "loading" && (
        <p className="character-model-feedback" role="status">
          Loading model…
        </p>
      )}
      {state === "error" && (
        <div className="character-model-feedback" role="alert">
          <p>{message || "Model could not be loaded."}</p>
          <button
            type="button"
            onClick={() => {
              setAttempt((value) => value + 1);
              setMessage("");
              setState("loading");
            }}
          >
            Retry viewer
          </button>
        </div>
      )}
    </div>
  );
}
