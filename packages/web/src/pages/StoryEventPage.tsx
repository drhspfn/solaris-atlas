import { ChevronRight } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';

import { api } from '../api/client';
import { ErrorPanel, PageLoader } from '../components/ui/Feedback';
import type { SourceCitation } from '../data/explanations';
import { useLocale } from '../hooks/useLocale';

type StoryEvent = {
  title: string;
  description: string;
  quest_id: number;
  game_version: string;
  citations: SourceCitation[];
};

export function StoryEventPage() {
  const { key } = useParams();
  const [params] = useSearchParams();
  const selectedLocale = useLocale();
  const locale = params.get('locale') || selectedLocale;
  const version = params.get('game_version') || '';
  const [event, setEvent] = useState<StoryEvent | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    setEvent(null);
    setError('');
    const query = new URLSearchParams({ locale });
    if (version) query.set('game_version', version);
    api<StoryEvent>(`/story-analysis/events/${key}?${query}`, { signal: controller.signal })
      .then(setEvent)
      .catch((reason: Error) => {
        if (!controller.signal.aborted) setError(reason.message);
      });
    return () => controller.abort();
  }, [key, locale, version]);
  if (error)
    return (
      <div className="page-container">
        <ErrorPanel message={error} />
      </div>
    );
  if (!event) return <PageLoader />;
  return (
    <div className="page-container">
      <div className="breadcrumbs">
        <Link to="/">Archive</Link>
        <ChevronRight size={13} />
        <span>Story event</span>
      </div>
      <section className="quest-explanation explanation-event">
        <span className="eyebrow left">AI INTERPRETATION · {event.game_version}</span>
        <h1>{event.title}</h1>
        <p>{event.description}</p>
        {event.citations.map((citation, index) => (
          <blockquote key={index}>{citation.quote}</blockquote>
        ))}
        <Link
          className="explanation-search-link"
          to={`/quests/${event.quest_id}?${new URLSearchParams({ game_version: event.game_version, locale })}`}
        >
          Read the quest and its sources
        </Link>
      </section>
    </div>
  );
}
