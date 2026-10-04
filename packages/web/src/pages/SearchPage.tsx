import { ChevronRight } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { api } from '../api/client';
import { EntityCard } from '../components/cards/EntityCard';
import { SearchBox } from '../components/search/SearchBox';
import { StoryQuestionResults } from '../components/search/StoryQuestionResults';
import { EmptyState, ErrorPanel } from '../components/ui/Feedback';
import { APP_SETTINGS } from '../config/settings';
import { categories, categoryTitle, type Entity } from '../data/entities';
import { useLocale } from '../hooks/useLocale';

export function SearchPage() {
  const [params] = useSearchParams();
  const q = params.get('q') || '';
  const storyMode = params.get('mode') === 'story';
  const version = params.get('game_version') || '';
  const selected = params.getAll('category');
  const selectedLocale = useLocale();
  const locale = params.get('locale') || selectedLocale;
  const queryString = params.toString();
  const [data, setData] = useState<Entity[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    if (storyMode) return () => controller.abort();
    setLoading(true);
    setError('');
    const p = new URLSearchParams({ q, locale, limit: String(APP_SETTINGS.limits.search) });
    new URLSearchParams(queryString).getAll('category').forEach((c) => p.append('category', c));
    api<{ results: Entity[] }>(`/search?${p}`, { signal: controller.signal })
      .then((d) => setData(d.results))
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [q, queryString, locale, storyMode]);
  return (
    <div className="page-container">
      <div className="breadcrumbs">
        <Link to="/">Archive</Link>
        <ChevronRight size={13} />
        <span>Search</span>
      </div>
      <div className="search-page-head">
        <div>
          <span className="eyebrow left">
            {storyMode ? 'STORY EXPLANATIONS' : 'ARCHIVE SEARCH'}
          </span>
          <h1>
            {q ? (
              <>
                Results for <em>“{q}”</em>
              </>
            ) : storyMode ? (
              'Explore the story'
            ) : (
              'Search the archive'
            )}
          </h1>
        </div>
        <SearchBox initial={q} />
      </div>
      <nav className="search-filters" aria-label="Search mode">
        <Link
          className={!storyMode ? 'filter-pill selected' : 'filter-pill'}
          to={`/search?${new URLSearchParams({ q, locale })}`}
        >
          Source records
        </Link>
        <Link
          className={storyMode ? 'filter-pill selected' : 'filter-pill'}
          to={`/search?mode=story&q=${encodeURIComponent(q)}&locale=${encodeURIComponent(locale)}${version ? `&game_version=${encodeURIComponent(version)}` : ''}`}
        >
          Story explanations
        </Link>
      </nav>
      {!storyMode && (
        <>
          <div className="search-filters">
            <span>FILTER BY</span>
            {['all', ...categories.map((c) => c.key)].map((c) => (
              <Link
                key={c}
                className={
                  selected.includes(c) || (!selected.length && c === 'all')
                    ? 'filter-pill selected'
                    : 'filter-pill'
                }
                to={`/search?q=${encodeURIComponent(q)}${c === 'all' ? '' : `&category=${c}`}`}
              >
                {c === 'all' ? 'Everything' : categoryTitle[c]}
              </Link>
            ))}
          </div>
          {error ? (
            <ErrorPanel message={error} />
          ) : loading ? (
            <div className="loading-grid">
              {Array.from({ length: APP_SETTINGS.presentation.skeletonCount }, (_, i) => (
                <div className="skeleton" key={i} />
              ))}
            </div>
          ) : data.length ? (
            <>
              <p className="results-label">{data.length} results · sorted by relevance</p>
              <div className="entity-grid">
                {data.map((item, i) => (
                  <EntityCard item={item} index={i} key={`${item.id}-${i}`} />
                ))}
              </div>
            </>
          ) : (
            <EmptyState query={q} />
          )}
        </>
      )}
      {storyMode && <StoryQuestionResults query={q} version={version} locale={locale} />}
    </div>
  );
}
