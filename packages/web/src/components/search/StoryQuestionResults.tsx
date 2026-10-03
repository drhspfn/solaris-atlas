import { ArrowUpRight } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import { api } from '../../api/client';
import { APP_SETTINGS } from '../../config/settings';
import type { ExplanationResults } from '../../data/explanations';
import { ExplanationBlocks } from '../story/ExplanationBlocks';

export function StoryQuestionResults({
  query,
  version,
  locale,
}: {
  query: string;
  version: string;
  locale: string;
}) {
  const [data, setData] = useState<ExplanationResults | null>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    setError('');
    if (!query.trim()) return () => controller.abort();
    const params = new URLSearchParams({
      q: query,
      locale,
      limit: String(APP_SETTINGS.limits.explanationSearch),
    });
    if (version) params.set('game_version', version);
    api<ExplanationResults>(`/story-analysis/search?${params}`, { signal: controller.signal })
      .then(setData)
      .catch((reason: Error) => {
        if (!controller.signal.aborted) setError(reason.message);
      });
    return () => controller.abort();
  }, [query, version, locale, retry]);
  if (!query.trim())
    return (
      <div className="explanation-status">
        <h2>Follow the story, with sources.</h2>
        <p>
          Search for an event, a faction, or a question. Results come from published quest
          explanations.
        </p>
      </div>
    );
  if (error)
    return (
      <div className="explanation-status" role="alert">
        <p>Story explanations could not be searched. {error}</p>
        <button type="button" onClick={() => setRetry((value) => value + 1)}>
          Try again
        </button>
      </div>
    );
  if (!data)
    return (
      <p className="explanation-status" role="status">
        Searching story notes…
      </p>
    );
  if (!data.results.length)
    return (
      <div className="explanation-status">
        <h2>No matching explanations yet</h2>
        <p>
          Try the name of a character or event. Only analyzed quests in the selected version and
          language appear here.
        </p>
        <Link to={`/search?${new URLSearchParams({ q: query, locale })}`}>
          Search source records instead
        </Link>
      </div>
    );
  return (
    <div className="explanation-results">
      <p className="results-label">
        {data.results.length} matches · {data.game_version} · sorted by relevance
      </p>
      {data.results.map((result, index) => (
        <section className="quest-explanation" key={`${result.id}-${index}`}>
          <header className="explanation-result-heading">
            <div>
              <span className="eyebrow left">QUEST {result.quest_id} · AI INTERPRETATION</span>
              <h2>{result.title}</h2>
            </div>
            <Link
              to={`/quests/${result.quest_id}?game_version=${encodeURIComponent(result.game_version)}&locale=${locale}`}
            >
              Read quest <ArrowUpRight size={15} aria-hidden="true" />
            </Link>
          </header>
          <ExplanationBlocks explanation={result} />
        </section>
      ))}
    </div>
  );
}
