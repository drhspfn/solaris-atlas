'use client';

import { ChevronRight, Search, X } from 'lucide-react';
import React, { useEffect, useRef, useState } from 'react';
import { api } from '@/api/client';
import { EntityCard } from '@/components/cards/EntityCard';
import { EmptyState, ErrorPanel } from '@/components/ui/Feedback';
import { APP_SETTINGS } from '@/config/settings';
import { categories, categoryTitle, type Entity } from '@/data/entities';
import { useLocale } from '@/hooks/useLocale';
import { Link } from '@/lib/router';

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
  const [query, setQuery] = useState('');
  const [items, setItems] = useState<Entity[]>(initialItems);
  const [total, setTotal] = useState(initialTotal);
  const [loading, setLoading] = useState(!initialLoaded);
  const [error, setError] = useState('');
  const [offset, setOffset] = useState(0);
  const [searchResults, setSearchResults] = useState<Entity[] | null>(null);
  const [submittedQuery, setSubmittedQuery] = useState('');
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    setOffset(0);
  }, [locale]);

  useEffect(() => {
    if (initialLoaded && offset === 0 && !submittedQuery && locale === 'en' && retry === 0) {
      setItems(initialItems);
      setTotal(initialTotal);
      setSearchResults(null);
      setLoading(false);
      setError('');
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError('');
    const path = submittedQuery
      ? `/search?q=${encodeURIComponent(submittedQuery)}&category=${encodeURIComponent(category)}&locale=${encodeURIComponent(locale)}&limit=${APP_SETTINGS.limits.catalogSearch}`
      : `/catalog?category=${encodeURIComponent(category)}&locale=${encodeURIComponent(locale)}&limit=${APP_SETTINGS.limits.catalogPage}&offset=${offset}`;
    api<{ results: Entity[]; total: number }>(path, { signal: controller.signal })
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
  }, [category, locale, offset, submittedQuery, retry, initialLoaded, initialItems, initialTotal]);

  const search = (e: React.FormEvent) => {
    e.preventDefault();
    setSubmittedQuery(query.trim());
    setOffset(0);
    setRetry((value) => value + 1);
  };

  const list = searchResults ?? items;
  const title = categoryTitle[category] ?? 'Archive';

  return (
    <div className="page-container">
      <div className="breadcrumbs">
        <Link to="/">Archive</Link>
        <ChevronRight size={13} />
        <span>{title}</span>
      </div>
      <div className="page-heading">
        <div>
          <span className="eyebrow left">THE ATLAS / COLLECTION</span>
          <h1>
            {title}
            <span className="heading-period">.</span>
          </h1>
          <p>Browse source-linked entries from the current game archive.</p>
        </div>
        <div className="total-chip">
          <span>{(searchResults ? searchResults.length : total).toLocaleString()}</span>
          <small>{searchResults ? 'matches' : 'entries'}</small>
        </div>
      </div>
      <div className="catalog-tools">
        <form className="catalog-search" onSubmit={search} noValidate>
          <Search size={17} />
          <input
            ref={searchInput}
            aria-label="Search catalog"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={`Search ${title.toLowerCase()}...`}
          />
          {query && (
            <button
              type="button"
              className="search-clear"
              aria-label="Clear catalog search"
              onClick={() => {
                setQuery('');
                setSearchResults(null);
                setSubmittedQuery('');
                setError('');
                searchInput.current?.focus();
              }}
            >
              <X size={15} />
            </button>
          )}
          <button className="submit-search">Search</button>
        </form>
        <div className="filter-pills">
          {categories.map((c) => (
            <Link
              className={category === c.key ? 'filter-pill selected' : 'filter-pill'}
              to={`/catalog/${c.key}`}
              key={c.key}
            >
              {c.label}
            </Link>
          ))}
        </div>
      </div>
      {error ? (
        <ErrorPanel message={error} onRetry={() => setRetry((value) => value + 1)} />
      ) : loading ? (
        <div className="loading-grid" role="status" aria-label="Loading catalog">
          {Array.from({ length: APP_SETTINGS.presentation.skeletonCount }, (_, i) => (
            <div className="skeleton" key={i} />
          ))}
        </div>
      ) : list.length ? (
        <>
          <div className="entity-grid">
            {list.map((item, i) => (
              <EntityCard item={item} index={i} key={`${item.id}-${i}`} />
            ))}
          </div>
          {!searchResults && total > APP_SETTINGS.limits.catalogPage && (
            <div className="pagination">
              <button
                disabled={!offset}
                onClick={() => setOffset(Math.max(0, offset - APP_SETTINGS.limits.catalogPage))}
              >
                Previous
              </button>
              <span>
                {offset + 1}–{Math.min(offset + list.length, total)} of {total.toLocaleString()}
              </span>
              <button
                disabled={offset + APP_SETTINGS.limits.catalogPage >= total}
                onClick={() => setOffset(offset + APP_SETTINGS.limits.catalogPage)}
              >
                Next
              </button>
            </div>
          )}
        </>
      ) : (
        <EmptyState query={query} />
      )}
    </div>
  );
}
