import { ArrowRight, ChevronDown, Network } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import { api } from '../../api/client';
import type { ExplanationResponse } from '../../data/explanations';
import { availableLocales } from '../../data/locales';
import { ConnectionLink } from './ConnectionLink';
import { ExplanationBlocks } from './ExplanationBlocks';
import { ExplanationContext, ExplanationFollowUps } from './ExplanationContext';
import { ExplanationSources } from './ExplanationSources';

export function QuestExplanation({
  questId,
  version,
  locale,
}: {
  questId: number;
  version: string;
  locale: string;
}) {
  const [data, setData] = useState<ExplanationResponse | null>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    setError('');
    const query = new URLSearchParams({ locale });
    if (version) query.set('game_version', version);
    api<ExplanationResponse>(`/quests/${questId}/explanation?${query}`, {
      signal: controller.signal,
    })
      .then(setData)
      .catch((reason: Error) => {
        if (!controller.signal.aborted) setError(reason.message);
      });
    return () => controller.abort();
  }, [questId, version, locale, retry]);
  const explanation = data?.explanation;
  return (
    <details className="quest-explanation" open>
      <summary className="explanation-heading">
        <Network size={20} aria-hidden="true" />
        <div>
          <span className="eyebrow left">STORY NOTES</span>
          <h2>What this quest means</h2>
        </div>
        <span className="explanation-badge">AI interpretation</span>
        <ChevronDown className="explanation-chevron" size={18} aria-hidden="true" />
      </summary>
      <div className="explanation-content">
        {error ? (
          <div className="explanation-status" role="alert">
            <p>Story notes could not be loaded. {error}</p>
            <button type="button" onClick={() => setRetry((value) => value + 1)}>
              Try again
            </button>
          </div>
        ) : !data ? (
          <p className="explanation-status" role="status">
            Loading story notes…
          </p>
        ) : !explanation ? (
          <p className="explanation-status">
            Story notes are not available for this quest in the selected version yet.
          </p>
        ) : (
          <>
            {explanation.locale && explanation.locale !== locale && (
              <p className="explanation-status">
                These story notes are available in{' '}
                {availableLocales.find((language) => language.code === explanation.locale)?.label ||
                  explanation.locale}
                . Source passages retain their original language.
              </p>
            )}
            <h3 className="story-reading-title">{explanation.title}</h3>
            <ExplanationBlocks explanation={explanation} />
            <details className="explanation-context story-research-details">
              <summary>Analysis details and connections</summary>
              <ExplanationContext explanation={explanation} />
              <ExplanationFollowUps explanation={explanation} />
              {explanation.links.length > 0 && (
                <section className="explanation-context" aria-label="Graph edges">
                  <h3>Connections</h3>
                  <p>Agent interpretations backed by source passages.</p>
                  {explanation.links.map((link, index) => {
                    const from = explanation.nodes.find((node) => node.id === link.from_node_id);
                    const to = explanation.nodes.find((node) => node.id === link.to_node_id);
                    return (
                      <article className="explanation-edge" key={index}>
                        <div className="explanation-edge-path">
                          {from ? (
                            <Link to={from.href}>{from.label}</Link>
                          ) : (
                            <span>Source record</span>
                          )}
                          <span className="explanation-edge-relation">
                            →{' '}
                            {link.relation_label ||
                              link.relation.replaceAll('_', ' ').toLowerCase()}{' '}
                            →
                          </span>
                          {to ? <Link to={to.href}>{to.label}</Link> : <span>Related record</span>}
                        </div>
                        <p>
                          <ConnectionLink link={link}>{link.explanation}</ConnectionLink>
                        </p>
                        {link.certainty && (
                          <span className="explanation-badge">{link.certainty}</span>
                        )}
                        {link.confidence != null && (
                          <span className="explanation-edge-confidence">
                            Agent confidence · {Math.round(link.confidence * 100)}%
                          </span>
                        )}
                        <ExplanationSources
                          citations={link.citations || []}
                          nodes={explanation.nodes}
                        />
                      </article>
                    );
                  })}
                </section>
              )}
              {explanation.events.length > 0 && (
                <div className="explanation-related">
                  <span>Events in this quest</span>
                  {explanation.events.map((event) => (
                    <Link
                      key={event.node_id}
                      to={`/story-analysis/events/${event.node_id}?game_version=${encodeURIComponent(explanation.game_version)}&locale=${locale}`}
                    >
                      {event.title}
                      <ArrowRight size={13} aria-hidden="true" />
                    </Link>
                  ))}
                </div>
              )}
              {explanation.unresolved_questions.length > 0 && (
                <details className="explanation-context">
                  <summary>Open questions · {explanation.unresolved_questions.length}</summary>
                  <ul>
                    {explanation.unresolved_questions.map((question, index) => (
                      <li key={index}>{question}</li>
                    ))}
                  </ul>
                </details>
              )}
            </details>
          </>
        )}
        <Link
          className="explanation-search-link"
          to={`/search?mode=story&locale=${locale}${version ? `&game_version=${encodeURIComponent(version)}` : ''}`}
        >
          Ask a story question <ArrowRight size={14} aria-hidden="true" />
        </Link>
      </div>
    </details>
  );
}
