import { ChevronRight, Search, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import { api } from '../api/client';
import { EntityCard } from '../components/cards/EntityCard';
import { EmptyState, ErrorPanel } from '../components/ui/Feedback';
import { APP_SETTINGS } from '../config/settings';
import { categories, categoryTitle, type Entity } from '../data/entities';
import { useLocale } from '../hooks/useLocale';

export function Catalog() {
  const searchInput = useRef<HTMLInputElement>(null);
  const { category = 'character' } = useParams();
  const locale = useLocale();
  const [query, setQuery] = useState('');
  const [items, setItems] = useState<Entity[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [offset, setOffset] = useState(0);
  const [searchResults, setSearchResults] = useState<Entity[] | null>(null);
  useEffect(() => {
    setQuery('');
    setOffset(0);
    setSearchResults(null);
  }, [category]);
  useEffect(() => {
    setLoading(true);
    setError('');
    api<{ results: Entity[]; total: number }>(
      `/catalog?category=${category}&locale=${locale}&limit=${APP_SETTINGS.limits.catalogPage}&offset=${offset}`,
    )
      .then((d) => {
        setItems(d.results);
        setTotal(d.total);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [category, locale, offset]);
  const search = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim()) {
      setSearchResults(null);
      return;
    }
    setLoading(true);
    setError('');
    try {
      const d = await api<{ results: Entity[] }>(
        `/search?q=${encodeURIComponent(query)}&category=${category}&locale=${locale}&limit=${APP_SETTINGS.limits.catalogSearch}`,
      );
      setSearchResults(d.results);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
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
        <form className="catalog-search" onSubmit={search}>
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
        <ErrorPanel message={error} />
      ) : loading ? (
        <div className="loading-grid">
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
