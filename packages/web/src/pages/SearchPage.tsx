import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ChevronRight } from "lucide-react";
import { api } from "../api/client";
import { categories, categoryTitle, type Entity } from "../data/entities";
import { useLocale } from "../hooks/useLocale";
import { EntityCard } from "../components/cards/EntityCard";
import { SearchBox } from "../components/search/SearchBox";
import { ErrorPanel, EmptyState } from "../components/ui/Feedback";

export function SearchPage() {
  const [params] = useSearchParams();
  const q = params.get("q") || "";
  const selected = params.getAll("category");
  const locale = useLocale();
  const [data, setData] = useState<Entity[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    setLoading(true);
    setError("");
    const p = new URLSearchParams({ q, locale, limit: "60" });
    selected.forEach((c) => p.append("category", c));
    api<{ results: Entity[] }>(`/search?${p}`)
      .then((d) => setData(d.results))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [q, params.toString(), locale]);
  return (
    <div className="page-container">
      <div className="breadcrumbs">
        <Link to="/">Archive</Link>
        <ChevronRight size={13} />
        <span>Search</span>
      </div>
      <div className="search-page-head">
        <div>
          <span className="eyebrow left">ARCHIVE SEARCH</span>
          <h1>
            Results for <em>“{q}”</em>
          </h1>
        </div>
        <SearchBox initial={q} />
      </div>
      <div className="search-filters">
        <span>FILTER BY</span>
        {["all", ...categories.map((c) => c.key)].map((c) => (
          <Link
            key={c}
            className={
              selected.includes(c) || (!selected.length && c === "all")
                ? "filter-pill selected"
                : "filter-pill"
            }
            to={`/search?q=${encodeURIComponent(q)}${c === "all" ? "" : `&category=${c}`}`}
          >
            {c === "all" ? "Everything" : categoryTitle[c]}
          </Link>
        ))}
      </div>
      {error ? (
        <ErrorPanel message={error} />
      ) : loading ? (
        <div className="loading-grid">
          {Array.from({ length: 8 }, (_, i) => (
            <div className="skeleton" key={i} />
          ))}
        </div>
      ) : data.length ? (
        <>
          <p className="results-label">
            {data.length} results · sorted by relevance
          </p>
          <div className="entity-grid">
            {data.map((item, i) => (
              <EntityCard item={item} index={i} key={`${item.id}-${i}`} />
            ))}
          </div>
        </>
      ) : (
        <EmptyState query={q} />
      )}
    </div>
  );
}
