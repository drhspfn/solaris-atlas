import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ChevronRight, Search, X } from "lucide-react";
import { api } from "../api/client";
import { categoryTitle, categories, type Entity } from "../data/entities";
import { useLocale } from "../hooks/useLocale";
import { EntityCard } from "../components/cards/EntityCard";
import { ErrorPanel, EmptyState } from "../components/ui/Feedback";

export function Catalog() {
  const { category = "character" } = useParams();
  const locale = useLocale();
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<Entity[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [offset, setOffset] = useState(0);
  const [searchResults, setSearchResults] = useState<Entity[] | null>(null);
  useEffect(() => {
    setQuery("");
    setOffset(0);
    setSearchResults(null);
  }, [category]);
  useEffect(() => {
    setLoading(true);
    setError("");
    api<{ results: Entity[]; total: number }>(
      `/catalog?category=${category}&locale=${locale}&limit=24&offset=${offset}`,
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
    setError("");
    try {
      const d = await api<{ results: Entity[] }>(
        `/search?q=${encodeURIComponent(query)}&category=${category}&locale=${locale}&limit=50`,
      );
      setSearchResults(d.results);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  };
  const list = searchResults ?? items;
  const title = categoryTitle[category] ?? "Archive";
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
          <span>
            {(searchResults ? searchResults.length : total).toLocaleString()}
          </span>
          <small>{searchResults ? "matches" : "entries"}</small>
        </div>
      </div>
      <div className="catalog-tools">
        <form className="catalog-search" onSubmit={search}>
          <Search size={17} />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={`Search ${title.toLowerCase()}...`}
          />
          {query && (
            <button
              type="button"
              onClick={() => {
                setQuery("");
                setSearchResults(null);
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
              className={
                category === c.key ? "filter-pill selected" : "filter-pill"
              }
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
          {Array.from({ length: 8 }, (_, i) => (
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
          {!searchResults && total > 24 && (
            <div className="pagination">
              <button
                disabled={!offset}
                onClick={() => setOffset(Math.max(0, offset - 24))}
              >
                Previous
              </button>
              <span>
                {offset + 1}–{Math.min(offset + list.length, total)} of{" "}
                {total.toLocaleString()}
              </span>
              <button
                disabled={offset + 24 >= total}
                onClick={() => setOffset(offset + 24)}
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
