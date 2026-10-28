"use client";

import { useEffect, useRef } from "react";
import type { ReactNode } from "react";

type Props = {
  children: ReactNode;
  hasMore: boolean;
  loading: boolean;
  onLoadMore: () => void;
  loader: ReactNode;
  skeleton: ReactNode;
  error?: string;
};

/** Presentation and viewport trigger only; pagination and requests belong to the caller. */
export function InfiniteScroll({
  children,
  hasMore,
  loading,
  onLoadMore,
  loader,
  skeleton,
  error,
}: Props) {
  const sentinel = useRef<HTMLDivElement>(null);
  const load = useRef(onLoadMore);
  load.current = onLoadMore;

  useEffect(() => {
    if (
      !hasMore ||
      loading ||
      error ||
      !sentinel.current ||
      typeof IntersectionObserver === "undefined"
    )
      return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) load.current();
      },
      { rootMargin: "400px 0px" },
    );
    observer.observe(sentinel.current);
    return () => observer.disconnect();
  }, [hasMore, loading, error]);

  return (
    <>
      {children}
      {loading && (
        <div role="status" aria-live="polite">
          {skeleton}
          {loader}
        </div>
      )}
      {error && <p role="alert">{error}</p>}
      {hasMore && !loading && (
        <div ref={sentinel} className="infinite-scroll-control">
          <button type="button" onClick={onLoadMore}>
            {error ? "Retry" : "Load more"}
          </button>
        </div>
      )}
    </>
  );
}
