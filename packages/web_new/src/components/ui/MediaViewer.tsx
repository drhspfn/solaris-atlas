"use client";

import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
} from "react";
import {
  ChevronLeft,
  ChevronRight,
  Download,
  RotateCcw,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { Button } from "./Button";
import {
  mediaViewerIndex,
  mediaViewerUrl,
  mediaViewerZoom,
} from "./mediaViewerUtils";
import "@/styles/media-viewer.css";

export type MediaItem = {
  id: string;
  src: string;
  type: "image" | "video";
  title: string;
  poster?: string;
  downloadName?: string;
};
export type MediaViewerProps = {
  items: MediaItem[];
  activeIndex: number | null;
  onIndexChange: (index: number) => void;
  onClose: () => void;
};

type Point = { x: number; y: number };
const origin = { x: 0, y: 0 };

export function MediaViewer({
  items,
  activeIndex,
  onIndexChange,
  onClose,
}: MediaViewerProps) {
  const dialog = useRef<HTMLDialogElement>(null);
  const image = useRef<HTMLImageElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const drag = useRef<{
    id: number;
    x: number;
    y: number;
    offset: Point;
  } | null>(null);
  const [zoom, setZoom] = useState(1);
  const [offset, setOffset] = useState<Point>(origin);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const item = activeIndex === null ? undefined : items[activeIndex];
  const isOpen = !!item;
  const url = item && mediaViewerUrl(item.src);

  useEffect(() => {
    if (!isOpen) return;
    const element = dialog.current;
    if (!element) return;
    const previous =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    element.showModal();
    return () => {
      if (element.open) element.close();
      document.body.style.overflow = overflow;
      previous?.focus();
    };
  }, [isOpen]);

  useEffect(() => {
    setZoom(1);
    setOffset(origin);
    setFailed(false);
    setAttempt(0);
    drag.current = null;
  }, [item?.id, item?.src]);

  function changeZoom(next: number) {
    const value = mediaViewerZoom(next);
    setZoom(value);
    // Reset translation when zoom changes so no portion can become unreachable.
    setOffset(origin);
  }
  function navigate(delta: number) {
    if (activeIndex === null) return;
    const next = mediaViewerIndex(activeIndex, delta, items.length);
    if (next !== activeIndex) onIndexChange(next);
  }
  function keyDown(event: KeyboardEvent<HTMLDialogElement>) {
    const target = event.target as HTMLElement;
    if (
      target.isContentEditable ||
      /^(INPUT|TEXTAREA|SELECT|VIDEO)$/.test(target.tagName) ||
      target.getAttribute("role") === "slider"
    )
      return;
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      navigate(event.key === "ArrowLeft" ? -1 : 1);
    }
  }
  function pointerDown(event: PointerEvent<HTMLImageElement>) {
    if (zoom <= 1 || event.button !== 0) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = {
      id: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      offset,
    };
  }
  function pointerMove(event: PointerEvent<HTMLImageElement>) {
    if (
      !drag.current ||
      drag.current.id !== event.pointerId ||
      !image.current ||
      !stage.current
    )
      return;
    const container = stage.current.getBoundingClientRect();
    const fit = Math.min(
      container.width / image.current.naturalWidth,
      container.height / image.current.naturalHeight,
    );
    const maxX = Math.max(
      0,
      (image.current.naturalWidth * fit * zoom - container.width) / 2,
    );
    const maxY = Math.max(
      0,
      (image.current.naturalHeight * fit * zoom - container.height) / 2,
    );
    setOffset({
      x: Math.max(
        -maxX,
        Math.min(maxX, drag.current.offset.x + event.clientX - drag.current.x),
      ),
      y: Math.max(
        -maxY,
        Math.min(maxY, drag.current.offset.y + event.clientY - drag.current.y),
      ),
    });
  }

  return (
    <dialog
      ref={dialog}
      className="media-viewer"
      aria-label="Media viewer"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onKeyDown={keyDown}
    >
      {item && (
        <div className="media-viewer-layout">
          <header className="media-viewer-header">
            <div className="media-viewer-heading">
              <strong>{item.title}</strong>
              <span aria-live="polite">
                {(activeIndex ?? 0) + 1} / {items.length}
              </span>
            </div>
            <div className="media-viewer-actions">
              {item.type === "image" && (
                <>
                  <Button
                    size="icon"
                    variant="secondary"
                    aria-label="Zoom out"
                    title="Zoom out"
                    icon={<ZoomOut size={18} />}
                    disabled={zoom <= 1}
                    onClick={() => changeZoom(zoom - 0.5)}
                  />
                  <span className="media-viewer-zoom" aria-live="polite">
                    {Math.round(zoom * 100)}%
                  </span>
                  <Button
                    size="icon"
                    variant="secondary"
                    aria-label="Zoom in"
                    title="Zoom in"
                    icon={<ZoomIn size={18} />}
                    disabled={zoom >= 4}
                    onClick={() => changeZoom(zoom + 0.5)}
                  />
                  <Button
                    size="icon"
                    variant="secondary"
                    aria-label="Reset zoom"
                    title="Reset zoom"
                    icon={<RotateCcw size={18} />}
                    disabled={zoom === 1}
                    onClick={() => changeZoom(1)}
                  />
                </>
              )}
              {url && (
                <a
                  className="media-viewer-download"
                  href={url}
                  download={item.downloadName || ""}
                  target="_blank"
                  rel="noopener noreferrer"
                  aria-label={`Download ${item.title}`}
                  title="Download"
                >
                  <Download size={18} aria-hidden="true" />
                </a>
              )}
              <Button
                size="icon"
                variant="secondary"
                aria-label="Close media viewer"
                title="Close"
                icon={<X size={18} />}
                onClick={onClose}
                autoFocus
              />
            </div>
          </header>
          <div className="media-viewer-content">
            <Button
              className="media-viewer-nav"
              size="icon"
              variant="secondary"
              aria-label="Previous media"
              icon={<ChevronLeft size={22} />}
              disabled={activeIndex === 0}
              onClick={() => navigate(-1)}
            />
            <div ref={stage} className="media-viewer-stage">
              {failed || !url ? (
                <div className="media-viewer-error" role="alert">
                  <p>Media could not be loaded.</p>
                  {url && (
                    <Button
                      variant="secondary"
                      onClick={() => {
                        setFailed(false);
                        setAttempt((value) => value + 1);
                      }}
                    >
                      Retry
                    </Button>
                  )}
                </div>
              ) : item.type === "image" ? (
                <img
                  key={`${item.id}-${attempt}`}
                  ref={image}
                  src={url}
                  alt={item.title}
                  draggable={false}
                  onError={() => setFailed(true)}
                  onPointerDown={pointerDown}
                  onPointerMove={pointerMove}
                  onPointerUp={() => {
                    drag.current = null;
                  }}
                  onPointerCancel={() => {
                    drag.current = null;
                  }}
                  style={{
                    transform: `translate(${offset.x}px, ${offset.y}px) scale(${zoom})`,
                    cursor: zoom > 1 ? "grab" : "default",
                    touchAction: zoom > 1 ? "none" : "auto",
                  }}
                />
              ) : (
                <video
                  key={`${item.id}-${attempt}`}
                  src={url}
                  poster={item.poster && mediaViewerUrl(item.poster)}
                  controls
                  playsInline
                  preload="metadata"
                  onError={() => setFailed(true)}
                />
              )}
            </div>
            <Button
              className="media-viewer-nav"
              size="icon"
              variant="secondary"
              aria-label="Next media"
              icon={<ChevronRight size={22} />}
              disabled={activeIndex === items.length - 1}
              onClick={() => navigate(1)}
            />
          </div>
        </div>
      )}
    </dialog>
  );
}
