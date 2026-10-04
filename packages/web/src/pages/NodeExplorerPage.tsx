import { ArrowRight, ChevronRight, ExternalLink, Network } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';

import { api } from '../api/client';
import { PlayerText } from '../components/dialogue/PlayerText';
import { EmptyInline, ErrorPanel, PageLoader } from '../components/ui/Feedback';
import { APP_SETTINGS } from '../config/settings';
import { type Entity, entityPath } from '../data/entities';
import { localizedText } from '../data/localized';
import { interpolatePlayerName } from '../data/playerName';
import { questPassagePath } from '../data/story';
import { useLocale } from '../hooks/useLocale';
import { usePlayerDisplay } from '../hooks/usePlayerDisplay';

type NodeDetail = {
  canonical_key: string;
  slug: string | null;
  status: string;
  metadata_json: Record<string, unknown>;
  type_id: number;
};
type Related = {
  relation: string;
  direction: string;
  basis: string;
  node: { id: number; canonical_key: string; type: string; label: string };
};

export function NodeExplorerPage() {
  const { key = '' } = useParams();
  const selectedLocale = useLocale();
  const [params] = useSearchParams();
  const locale = params.get('locale') || selectedLocale;
  const gameVersion = params.get('game_version') || '';
  const playerDisplay = usePlayerDisplay();
  const [node, setNode] = useState<NodeDetail | null>(null);
  const [related, setRelated] = useState<Related[]>([]);
  const [narrative, setNarrative] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError('');
    const canonicalKey = key;
    const selection = new URLSearchParams({ locale });
    if (gameVersion) selection.set('game_version', gameVersion);
    Promise.all([
      api<NodeDetail>(`/nodes/${encodeURIComponent(canonicalKey)}`),
      api<{ results: Related[] }>(
        `/nodes/${encodeURIComponent(canonicalKey)}/related?locale=${locale}&limit=${APP_SETTINGS.limits.relatedNodes}`,
      ),
      api<any>(`/nodes/${encodeURIComponent(canonicalKey)}/narrative-context?${selection}`).catch(
        () => null,
      ),
    ])
      .then(([detail, links, context]) => {
        if (!active) return;
        setNode(detail);
        setRelated(links.results);
        setNarrative(context?.available ? context : null);
      })
      .catch((reason: Error) => {
        if (active) setError(reason.message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [key, locale, gameVersion]);

  if (loading) return <PageLoader />;
  if (error || !node)
    return (
      <div className="page-container">
        <ErrorPanel message={error || 'Node not found'} />
      </div>
    );
  const dialogueText = interpolatePlayerName(
    localizedText(narrative?.dialogue?.text, narrative?.dialogue?.text?.inline_text || ''),
    playerDisplay.name,
  );
  const title = dialogueText || node.slug || node.canonical_key;
  return (
    <div className="page-container">
      <div className="breadcrumbs">
        <Link to="/">Archive</Link>
        <ChevronRight size={13} />
        <span>Source node</span>
        <ChevronRight size={13} />
        <span>{title}</span>
      </div>
      <section className="generic-node-hero">
        <div className="generic-node-icon">
          <Network size={26} />
        </div>
        <div>
          <span className="eyebrow left">
            {narrative ? 'DIALOGUE ENTRY' : `SOURCE-LINKED NODE / ${node.status.toUpperCase()}`}
          </span>
          <h1>
            {narrative ? (
              <PlayerText
                display={playerDisplay}
                value={narrative.dialogue?.text}
                fallback={narrative.dialogue?.text?.inline_text || title}
              />
            ) : (
              title
            )}
            <span className="heading-period">.</span>
          </h1>
          {narrative?.dialogue?.speaker && (
            <Link
              className="node-speaker-link"
              to={entityPath({
                id: narrative.dialogue.speaker.id,
                canonical_key: narrative.dialogue.speaker.canonical_key,
                node_type: narrative.dialogue.speaker.type,
              } as Entity)}
            >
              Spoken by {narrative.dialogue.speaker.label} <ArrowRight size={13} />
            </Link>
          )}
          <p>{node.canonical_key}</p>
        </div>
        <a
          href={`/api/nodes/${encodeURIComponent(node.canonical_key)}`}
          target="_blank"
          rel="noreferrer"
        >
          Raw API <ExternalLink size={13} />
        </a>
      </section>
      {narrative && (
        <section className="content-panel node-narrative-context">
          <div className="panel-title">
            <span>01</span>
            <h2>Where this line appears</h2>
          </div>
          <blockquote>
            {dialogueText ? (
              <PlayerText
                display={playerDisplay}
                value={narrative.dialogue?.text}
                fallback={narrative.dialogue?.text?.inline_text || ''}
              />
            ) : (
              'Text is unavailable in this locale.'
            )}
          </blockquote>
          <div className="node-context-meta">
            <span>
              Flow state <b>{narrative.flow_state?.state_key}</b>
            </span>
            <span>
              Authored order{' '}
              <b>
                {narrative.dialogue?.action?.name || 'ShowTalk'} · line{' '}
                {narrative.dialogue?.source_index ?? '—'}
              </b>
            </span>
            <span>
              Source{' '}
              <b>
                {narrative.dialogue?.provenance?.source_file || 'Unknown'} · row{' '}
                {narrative.dialogue?.provenance?.source_row ?? '—'}
              </b>
            </span>
          </div>
          <div className="node-context-quests">
            <h3>Quest references</h3>
            {narrative.quests?.length ? (
              narrative.quests.map((quest: any) => {
                const id = quest.game_quest_id || quest.canonical_key?.split(':').at(-1);
                return (
                  <Link
                    className="node-quest-card"
                    to={questPassagePath(
                      id,
                      narrative.dialogue.id,
                      locale,
                      narrative.game_version || gameVersion,
                    )}
                    key={quest.canonical_key || id}
                  >
                    <span>
                      <strong>{localizedText(quest.name, `Quest ${id}`)}</strong>
                      <small>Open quest at this line →</small>
                    </span>
                    <ArrowRight size={15} />
                  </Link>
                );
              })
            ) : (
              <p>
                No confirmed quest link is available for this line in the imported data. Nearby
                dialogue is shown below.
              </p>
            )}
          </div>
          <div className="node-context-transcript">
            <h3>Nearby authored lines</h3>
            {narrative.context?.map((line: any) => (
              <article
                className={
                  line.id === narrative.dialogue?.id
                    ? 'node-context-line current'
                    : 'node-context-line'
                }
                key={line.id}
              >
                <span>{line.speaker?.label || 'Unknown speaker'}</span>
                <p>
                  <PlayerText
                    display={playerDisplay}
                    value={line.text}
                    fallback={line.text?.inline_text || 'Text unavailable in this locale.'}
                  />
                </p>
                {line.id !== narrative.dialogue?.id && (
                  <Link to={`/nodes/${encodeURIComponent(line.id)}`}>
                    Open line <ArrowRight size={12} />
                  </Link>
                )}
              </article>
            ))}
          </div>
          <small className="node-context-note">
            Authored order is preserved; this excerpt does not claim a specific runtime branch
            traversal.
          </small>
        </section>
      )}
      <details className="content-panel generic-relations raw-node-connections">
        <summary>
          <span>Source graph connections</span>
          <small>{related.length} technical references</small>
        </summary>
        {related.length ? (
          <div className="list-stack">
            {related.map((edge, index) => (
              <Link
                className="connection-row"
                to={entityPath({
                  id: edge.node.id,
                  canonical_key: edge.node.canonical_key,
                  node_type: edge.node.type,
                } as Entity)}
                key={`${edge.node.canonical_key}-${edge.relation}-${index}`}
              >
                <span className="connection-index">
                  {edge.direction === 'outgoing' ? '↗' : '↙'}
                </span>
                <div>
                  <strong>{edge.node.label}</strong>
                  <small>
                    {edge.relation.replaceAll('_', ' ')} · {edge.basis.replaceAll('_', ' ')}
                  </small>
                </div>
                <ChevronRight size={15} />
              </Link>
            ))}
          </div>
        ) : (
          <EmptyInline text="No linked entity neighbors are available for this node." />
        )}
      </details>
    </div>
  );
}
