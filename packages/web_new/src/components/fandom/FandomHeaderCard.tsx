"use client";

import { useEffect, useState } from "react";
import { api } from "@/api/client";
import { BookOpen, ExternalLink } from "lucide-react";

export type FandomReference = {
  page_title: string;
  page_url: string;
  gallery_url?: string | null;
  summary?: string | null;
  license?: string | null;
};

export function FandomHeaderCard({
  canonicalKey,
  initialReference,
}: {
  canonicalKey?: string;
  initialReference?: FandomReference | null;
}) {
  const [ref, setRef] = useState<FandomReference | null>(
    initialReference ?? null,
  );

  useEffect(() => {
    setRef(initialReference ?? null);
  }, [initialReference]);

  useEffect(() => {
    if (initialReference !== undefined || !canonicalKey) return;
    let cancelled = false;
    void api<{ fandom_reference: FandomReference | null }>(
      `/fandom/link?canonical_key=${encodeURIComponent(canonicalKey)}`,
    )
      .then((res) => {
        if (!cancelled) setRef(res.fandom_reference);
      })
      .catch(() => {
        /* ignore optional fandom enrichment */
      });
    return () => {
      cancelled = true;
    };
  }, [canonicalKey, initialReference]);

  if (!ref?.page_url) return null;

  return (
    <aside
      aria-label="Fandom community summary"
      style={{
        marginTop: "0.75rem",
        padding: "0.75rem 0.9rem",
        borderRadius: "8px",
        border: "1px solid var(--border, rgba(255,255,255,0.12))",
        background: "rgba(255, 255, 255, 0.025)",
        display: "grid",
        gap: "0.45rem",
        maxWidth: "70ch",
      }}
    >
      {ref.summary && (
        <p
          style={{
            margin: 0,
            fontSize: "0.92rem",
            lineHeight: 1.5,
            opacity: 0.92,
          }}
        >
          {ref.summary}
        </p>
      )}
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: "0.75rem",
          alignItems: "center",
          fontSize: "0.84rem",
          opacity: 0.85,
        }}
      >
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: "0.35rem",
          }}
        >
          <BookOpen size={14} aria-hidden="true" />
          Want to learn more?
        </span>
        <a
          href={ref.page_url}
          target="_blank"
          rel="noreferrer"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: "0.25rem",
          }}
        >
          Read on Fandom ({ref.page_title}){" "}
          <ExternalLink size={12} aria-hidden="true" />
        </a>
        {ref.gallery_url && (
          <a
            href={ref.gallery_url}
            target="_blank"
            rel="noreferrer"
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "0.25rem",
            }}
          >
            Gallery <ExternalLink size={12} aria-hidden="true" />
          </a>
        )}
        {ref.license && (
          <span style={{ fontSize: "0.75rem", opacity: 0.65 }}>
            {ref.license}
          </span>
        )}
      </div>
    </aside>
  );
}
