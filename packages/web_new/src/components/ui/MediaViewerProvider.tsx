"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { MediaViewer, type MediaItem } from "./MediaViewer";
import { mediaViewerIndex } from "./mediaViewerUtils";

type ViewerState = { items: MediaItem[]; activeIndex: number | null };
export type MediaViewerContextValue = ViewerState & {
  open: (items: MediaItem[], index?: number) => void;
  close: () => void;
  next: () => void;
  previous: () => void;
  goTo: (index: number) => void;
};

const MediaViewerContext = createContext<MediaViewerContextValue | null>(null);
const empty: ViewerState = { items: [], activeIndex: null };

function validIndex(index: number, count: number): number {
  return mediaViewerIndex(
    0,
    Number.isFinite(index) ? Math.trunc(index) : 0,
    count,
  );
}

export function MediaViewerProvider({
  children,
  resetKey,
}: {
  children: ReactNode;
  resetKey?: string;
}) {
  const [state, setState] = useState<ViewerState>(empty);
  const previousResetKey = useRef(resetKey);
  useEffect(() => {
    if (previousResetKey.current !== resetKey) {
      previousResetKey.current = resetKey;
      setState(empty);
    }
  }, [resetKey]);
  const open = useCallback((items: MediaItem[], index = 0) => {
    const snapshot = items.slice();
    setState(
      snapshot.length
        ? { items: snapshot, activeIndex: validIndex(index, snapshot.length) }
        : empty,
    );
  }, []);
  const close = useCallback(() => setState(empty), []);
  const goTo = useCallback(
    (index: number) =>
      setState((current) =>
        current.activeIndex === null
          ? current
          : {
              ...current,
              activeIndex: validIndex(index, current.items.length),
            },
      ),
    [],
  );
  const move = useCallback(
    (delta: number) =>
      setState((current) =>
        current.activeIndex === null
          ? current
          : {
              ...current,
              activeIndex: mediaViewerIndex(
                current.activeIndex,
                delta,
                current.items.length,
              ),
            },
      ),
    [],
  );
  const next = useCallback(() => move(1), [move]);
  const previous = useCallback(() => move(-1), [move]);

  return (
    <MediaViewerContext.Provider
      value={{ ...state, open, close, next, previous, goTo }}
    >
      {children}
      <MediaViewer
        items={state.items}
        activeIndex={state.activeIndex}
        onIndexChange={goTo}
        onClose={close}
      />
    </MediaViewerContext.Provider>
  );
}

export function useMediaViewer(): MediaViewerContextValue {
  const context = useContext(MediaViewerContext);
  if (!context)
    throw new Error("useMediaViewer must be used within MediaViewerProvider");
  return context;
}
