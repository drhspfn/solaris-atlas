"use client";

import { ArrowRight, ChevronRight, Search, X } from "lucide-react";
import React, { useEffect, useRef, useState } from "react";
import { api } from "@/api/client";
import { EntityCard, EntityCardSkeleton } from "@/components/cards/EntityCard";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { SearchInput } from "@/components/ui/SearchInput";
import { Breadcrumbs } from "@/components/ui/Breadcrumbs";
import { PageHeader } from "@/components/ui/PageHeader";
import { SkeletonGroup } from "@/components/ui/Skeleton";
import { Pill } from "@/components/ui/Pill";
import { Pagination } from "@/components/ui/Pagination";
import "@/styles/catalog-controls.css";
import { EmptyState, ErrorPanel } from "@/components/ui/Feedback";
import { APP_SETTINGS } from "@/config/settings";
import { categories, categoryTitle, type Entity } from "@/data/entities";
import { useLocale } from "@/hooks/useLocale";
import { usePlayerDisplay } from "@/hooks/usePlayerDisplay";
import { Link } from "@/lib/router";

const emptyItems: Entity[] = [];

export function CatalogClient({
  category,
  initialItems = emptyItems,
  initialTotal = 0,
  initialLoaded = false,
}: {
  category: string;
  initialItems?: Entity[];
  initialTotal?: number;
  initialLoaded?: boolean;
}) {
  const searchInput = useRef<HTMLInputElement>(null);
  const locale = useLocale();
  const display = usePlayerDisplay();
  const [characterKind, setCharacterKind] = useState("playable");
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<Entity[]>(initialItems);
  const [total, setTotal] = useState(initialTotal);
  const [loading, setLoading] = useState(!initialLoaded);
  const [error, setError] = useState("");
  const [offset, setOffset] = useState(0);
  const [searchResults, setSearchResults] = useState<Entity[] | null>(null);
  const [submittedQuery, setSubmittedQuery] = useState("");
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    setOffset(0);
  }, [locale]);

  useEffect(() => {
    if (
      initialLoaded &&
      offset === 0 &&
      !submittedQuery &&
      locale === "en" &&
      retry === 0 &&
      characterKind === "playable"
    ) {
      setItems(initialItems);
      setTotal(initialTotal);
      setSearchResults(null);
      setLoading(false);
      setError("");
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError("");
    const path = submittedQuery
      ? `/search?q=${encodeURIComponent(submittedQuery)}&category=${encodeURIComponent(category)}&locale=${encodeURIComponent(locale)}&limit=${APP_SETTINGS.limits.catalogSearch}`
      : `/catalog?category=${encodeURIComponent(category)}&locale=${encodeURIComponent(locale)}&limit=${APP_SETTINGS.limits.catalogPage}&offset=${offset}`;
    api<{ results: Entity[]; total: number }>(
      `${path}${category === "character" ? `&character_kind=${characterKind}` : ""}`,
      { signal: controller.signal },
    )
      .then((d) => {
        if (controller.signal.aborted) return;
        if (submittedQuery) setSearchResults(d.results);
        else {
          setSearchResults(null);
          setItems(d.results);
          setTotal(d.total);
        }
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [
    category,
    characterKind,
    locale,
    offset,
    submittedQuery,
    retry,
    initialLoaded,
    initialItems,
    initialTotal,
  ]);

  const search = (e: React.FormEvent) => {
    e.preventDefault();
    setSubmittedQuery(query.trim());
    setOffset(0);
    setRetry((value) => value + 1);
  };

  const list = searchResults ?? items;
  const title = categoryTitle[category] ?? "Archive";

  return (
    <div className="page-container">
      <Breadcrumbs items={[{ label: "Archive", to: "/" }, { label: title }]} />
      <PageHeader
        title={title}
        description="Browse source-linked entries from the current game archive."
        actions={
          <div className="total-chip">
            <span>
              {(searchResults ? searchResults.length : total).toLocaleString()}
            </span>
            <small>{searchResults ? "matches" : "entries"}</small>
          </div>
        }
      />
      <div className="catalog-tools">
        <SearchInput
          ref={searchInput}
          asForm
          className="catalog-search"
          value={query}
          onChange={setQuery}
          onSubmit={() => {
            setSubmittedQuery(query.trim());
            setOffset(0);
            setRetry((value) => value + 1);
          }}
          onClear={() => {
            setQuery("");
            setSearchResults(null);
            setSubmittedQuery("");
            setError("");
          }}
          placeholder={`Search ${title.toLowerCase()}...`}
          ariaLabel={`Search ${title.toLowerCase()}`}
          loading={loading}
        />
        <nav className="filter-pills" aria-label="Catalog categories">
          {categories.map((c) => (
            <Pill
              kind="link"
              selected={category === c.key}
              href={`/catalog/${c.key}`}
              key={c.key}
            >
              {c.label}
            </Pill>
          ))}
        </nav>
      </div>
      {category === "character" && (
        <div
          className="filter-pills character-kind-filters"
          role="group"
          aria-label="Character categories"
        >
          {Object.entries({
            playable: "Playable",
            story: "Story & event roles",
            npc: "NPCs",
            technical: "Technical roles",
            all: "All roles",
          }).map(([value, label]) => (
            <Pill
              key={value}
              kind="button"
              selected={characterKind === value}
              onClick={() => {
                setCharacterKind(value);
                setOffset(0);
              }}
            >
              {label}
            </Pill>
          ))}
        </div>
      )}
      {error ? (
        <ErrorPanel
          message={error}
          onRetry={() => setRetry((value) => value + 1)}
        />
      ) : loading ? (
        <SkeletonGroup className="entity-grid" label="Loading catalog">
          {Array.from(
            { length: APP_SETTINGS.presentation.skeletonCount },
            (_, i) => (
              <EntityCardSkeleton key={i} />
            ),
          )}
        </SkeletonGroup>
      ) : list.length ? (
        <>
          <div className="entity-grid">
            {list.map((item, i) => (
              <EntityCard
                item={item}
                index={i}
                display={display}
                key={`${item.id}-${i}`}
              />
            ))}
          </div>
          {!searchResults && total > APP_SETTINGS.limits.catalogPage && (
            <Pagination
              mode="pages"
              className="catalog-pagination"
              page={Math.floor(offset / APP_SETTINGS.limits.catalogPage) + 1}
              pageSize={APP_SETTINGS.limits.catalogPage}
              total={total}
              onPageChange={(page) =>
                setOffset((page - 1) * APP_SETTINGS.limits.catalogPage)
              }
              label={`${offset + 1}–${Math.min(offset + list.length, total)} of ${total.toLocaleString()}`}
            />
          )}
        </>
      ) : (
        <EmptyState query={query} />
      )}
    </div>
  );
}
