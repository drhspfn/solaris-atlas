import { ChevronRight } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';

import { api } from '../api/client';
import { ExplanationSources } from '../components/story/ExplanationSources';
import { PageLoader } from '../components/ui/Feedback';
import type { StoryConnection } from '../data/explanations';
import { useLocale } from '../hooks/useLocale';

export function StoryConnectionPage() {
  const { documentId, index } = useParams();
  const [params] = useSearchParams();
  const selectedLocale = useLocale();
  const locale = params.get('locale') || selectedLocale;
  const [data, setData] = useState<StoryConnection | null>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    setError('');
    api<StoryConnection>(
      `/story-analysis/connections/${documentId}/${index}?${new URLSearchParams({ locale })}`,
      { signal: controller.signal },
    )
      .then(setData)
      .catch((reason: Error) => {
        if (!controller.signal.aborted) setError(reason.message);
      });
    return () => controller.abort();
  }, [documentId, index, locale, retry]);
  if (error)
    return (
      <div className="page-container">
        <section className="quest-explanation explanation-content" role="alert">
          <h1>Connection unavailable</h1>
          <p>{error}</p>
          <button
            className="btn-outline"
            type="button"
            onClick={() => setRetry((value) => value + 1)}
          >
            Try again
          </button>
          <Link to="/story">Browse the story archive</Link>
        </section>
      </div>
    );
  if (!data) return <PageLoader />;
  return (
    <div className="page-container">
      <div className="breadcrumbs">
        <Link to="/">Archive</Link>
        <ChevronRight size={13} aria-hidden="true" />
        <span>Story connection</span>
      </div>
      <article className="quest-explanation explanation-event story-connection-page">
        <span className="eyebrow left">
          AI INTERPRETATION{data.certainty ? ` · ${data.certainty}` : ''}
        </span>
        <h1>{data.title}</h1>
        <div className="explanation-edge-path">
          {data.from_node && <Link to={data.from_node.href}>{data.from_node.label}</Link>}
          <span aria-hidden="true">→</span>
          {data.to_node && <Link to={data.to_node.href}>{data.to_node.label}</Link>}
        </div>
        <p>{data.explanation}</p>
        <h2>Source passages</h2>
        <ExplanationSources citations={data.citations || []} nodes={data.nodes} />
        <details className="explanation-context">
          <summary>About this interpretation</summary>
          <p>
            Analysis revision {data.revision} · Source snapshot {data.game_version}. This
            explanation describes the connection; it does not replace the imported game records.
          </p>
          {data.confidence != null && (
            <p>Agent confidence · {Math.round(data.confidence * 100)}%</p>
          )}
        </details>
        <Link
          className="explanation-search-link"
          to={`/quests/${data.quest_id}?${new URLSearchParams({ game_version: data.game_version, locale })}`}
        >
          Read the quest
        </Link>
      </article>
    </div>
  );
}
