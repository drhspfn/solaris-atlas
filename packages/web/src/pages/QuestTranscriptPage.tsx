import { ArrowUp, BookOpen, ChevronRight, Users } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';

import { api } from '../api/client';
import { PlayerText } from '../components/dialogue/PlayerText';
import { QuestContinuity } from '../components/story/QuestContinuity';
import {
  DialogueAudioReference,
  type QuestMediaManifest,
  QuestMediaReferences,
} from '../components/story/QuestMediaReferences';
import { EmptyInline, ErrorPanel, PageLoader } from '../components/ui/Feedback';
import { APP_SETTINGS } from '../config/settings';
import { entityPath } from '../data/entities';
import type { QuestContinuity as QuestContinuityData } from '../data/story';
import { useLocale } from '../hooks/useLocale';
import { usePlayerDisplay } from '../hooks/usePlayerDisplay';
import { useNarrativePreferences } from '../preferences/NarrativePreferences';

type ChoiceBranch = {
  choice: any;
  sourceLineId: string;
  optionIndex: number;
  lineIds: string[];
  continuationLineId: string | null;
};

export function QuestPage() {
  const { key = '' } = useParams();
  const [searchParams] = useSearchParams();
  const gameVersion = searchParams.get('game_version') || '';
  const locale = useLocale();
  const playerDisplay = usePlayerDisplay();
  const { voiceLanguage, setVoiceLanguage } = useNarrativePreferences();
  const [profile, setProfile] = useState<any>(null);
  const [transcript, setTranscript] = useState<any>(null);
  const [continuity, setContinuity] = useState<QuestContinuityData | null>(null);
  const [media, setMedia] = useState<QuestMediaManifest | null>(null);
  const [continuityError, setContinuityError] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [onlyChoices, setOnlyChoices] = useState(false);
  const [onlyLines, setOnlyLines] = useState(false);
  const [activeChoiceId, setActiveChoiceId] = useState<string | null>(null);
  const [showBackToTop, setShowBackToTop] = useState(false);
  useEffect(() => {
    if (!activeChoiceId) return;
    let enteredBranch = false;
    const updateHighlight = () => {
      const activeLines = document.querySelectorAll('.branch-line-active');
      const inView = Array.from(activeLines).some((element) => {
        const rect = element.getBoundingClientRect();
        return rect.bottom > 0 && rect.top < window.innerHeight;
      });
      if (inView) enteredBranch = true;
      else if (enteredBranch) setActiveChoiceId(null);
    };
    window.addEventListener('scroll', updateHighlight, { passive: true });
    const frame = requestAnimationFrame(updateHighlight);
    return () => {
      window.removeEventListener('scroll', updateHighlight);
      cancelAnimationFrame(frame);
    };
  }, [activeChoiceId]);
  useEffect(() => {
    const updateVisibility = () => setShowBackToTop(window.scrollY > window.innerHeight);
    updateVisibility();
    window.addEventListener('scroll', updateVisibility, { passive: true });
    return () => window.removeEventListener('scroll', updateVisibility);
  }, []);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError('');
    setContinuity(null);
    setMedia(null);
    setContinuityError('');
    const selection = new URLSearchParams({ locale });
    if (gameVersion) selection.set('game_version', gameVersion);
    Promise.all([
      api<any>(`/quests/${key}/profile?${selection}`),
      api<any>(`/quests/${key}/transcript?${selection}&limit=${APP_SETTINGS.limits.transcript}`),
      api<QuestContinuityData>(`/quests/${key}/continuity?${selection}`)
        .then((result) => ({ result, failure: '' }))
        .catch((reason: Error) => ({ result: null, failure: reason.message })),
      api<QuestMediaManifest>(`/quests/${key}/media?${selection}`).catch(() => null),
    ])
      .then(([p, t, c, m]) => {
        if (!active) return;
        setProfile(p);
        setTranscript(t);
        setContinuity(c.result);
        setContinuityError(c.failure);
        setMedia(m);
      })
      .catch((e) => {
        if (active) setError(e.message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [key, locale, gameVersion]);
  if (loading) return <PageLoader />;
  if (error)
    return (
      <div className="page-container">
        <ErrorPanel message={error} />
      </div>
    );
  const quest = profile.quest;
  const lines = transcript.lines || [];
  const scenes = transcript.scenes || profile.scenes || [];
  const storyGalleryRecord =
    profile.source?.raw_record?.Data?.Key?.includes('剧情图鉴专用') === true;
  const hasMediaReferences = Boolean(media?.events.length || media?.video_packages.length);
  const explicitLinks = profile.explicit_links || [];
  const questConnections = explicitLinks.filter(
    (link: any) =>
      link.node?.type === 'quest' && ['requires_quest', 'references_quest'].includes(link.relation),
  );
  const sourceGraphLinks = explicitLinks.filter((link: any) => !questConnections.includes(link));
  const availableLineIds = new Set<string>(lines.map((line: any) => line.id));
  const branches: ChoiceBranch[] = lines.flatMap((line: any) =>
    (line.player_choices || []).flatMap((choice: any, optionIndex: number) => {
      const lineIds = choice.branch?.line_ids;
      return Array.isArray(lineIds) &&
        lineIds.length > 0 &&
        lineIds.every((id: string) => availableLineIds.has(id))
        ? [
            {
              choice,
              sourceLineId: line.id,
              optionIndex,
              lineIds,
              continuationLineId: choice.branch.continuation_line_id,
            },
          ]
        : [];
    }),
  );
  const branchesByChoice = new Map(branches.map((branch) => [branch.choice.id, branch]));
  const branchOwners = new Map<string, ChoiceBranch[]>();
  for (const branch of branches) {
    for (const lineId of branch.lineIds) {
      branchOwners.set(lineId, [...(branchOwners.get(lineId) || []), branch]);
    }
  }
  const branchByLine = new Map<string, ChoiceBranch>();
  for (const branch of branches) {
    if (branch.lineIds.every((id) => branchOwners.get(id)?.length === 1)) {
      for (const lineId of branch.lineIds) branchByLine.set(lineId, branch);
    }
  }
  const joins = new Map<string, number>();
  for (const branch of branches) {
    if (branch.continuationLineId && branch.continuationLineId !== branch.sourceLineId) {
      joins.set(branch.continuationLineId, (joins.get(branch.continuationLineId) || 0) + 1);
    }
  }
  const choiceTargetIds = new Set<string>(
    lines.flatMap((line: any) =>
      (line.player_choices || []).map((choice: any) => choice.target_line_id).filter(Boolean),
    ),
  );
  for (const branch of branches) {
    for (const lineId of branch.lineIds) choiceTargetIds.add(lineId);
  }
  const shown = lines
    .filter(
      (line: any) => !onlyChoices || line.player_choices?.length || choiceTargetIds.has(line.id),
    )
    .filter((line: any) => !onlyLines || line.speaker);
  const stateCounts = new Map<string, number>();
  for (const line of shown) {
    const state = line.flow_state || 'Unassigned';
    stateCounts.set(state, (stateCounts.get(state) || 0) + 1);
  }
  const stateIndex = Array.from(stateCounts, ([key, count], index) => ({
    key,
    count,
    anchor: `flow-state-${index}`,
  }));
  const stateAnchors = new Map(stateIndex.map((state) => [state.key, state.anchor]));
  const lineAnchors = new Map<string, string>();
  const firstLineInState = new Set<string>();
  shown.forEach((line: any, index: number) => {
    const state = line.flow_state || 'Unassigned';
    lineAnchors.set(
      line.id,
      firstLineInState.has(state) ? `line-${index}` : stateAnchors.get(state)!,
    );
    firstLineInState.add(state);
  });
  return (
    <div className="page-container quest-container">
      <div className="breadcrumbs">
        <Link to="/">Archive</Link>
        <ChevronRight size={13} />
        <Link to="/story-map">Story map</Link>
        <ChevronRight size={13} />
        <span>{quest.name?.content || `Quest ${key}`}</span>
      </div>
      <section className="quest-hero">
        <div className="quest-emblem">
          <BookOpen size={28} />
          <span>STORY RECORD</span>
        </div>
        <div className="quest-intro">
          <span className="eyebrow left">QUEST DOSSIER / {quest.quest_type || 'NARRATIVE'}</span>
          <h1>
            {quest.name?.content || `Quest ${key}`}
            <span className="heading-period">.</span>
          </h1>
          <div className="quest-meta">
            <span>
              QUEST ID <b>{quest.game_quest_id}</b>
            </span>
            <span>
              FLOW STATES <b>{stateIndex.length}</b>
            </span>
            <span>
              LINES <b>{lines.length}</b>
            </span>
            {scenes.length > 0 && (
              <span>
                SCENES <b>{scenes.length}</b>
              </span>
            )}
          </div>
        </div>
      </section>
      {continuity && (
        <QuestContinuity continuity={continuity} title={quest.name?.content || `Quest ${key}`} />
      )}
      {continuityError && (
        <div className="quest-continuity-warning" role="status">
          Story path unavailable: {continuityError}
        </div>
      )}
      {lines.length === 0 ? (
        <section className="quest-no-transcript" aria-labelledby="quest-no-transcript-title">
          <span className="eyebrow left">SOURCE RECORD · NO DIALOGUE TRANSCRIPT</span>
          <h2 id="quest-no-transcript-title">
            {storyGalleryRecord ? 'Story gallery record' : 'No dialogue recorded for this quest'}
          </h2>
          <p>
            {storyGalleryRecord
              ? `The game data marks this as a story gallery record. This import has ${scenes.length} scene records, but no dialogue lines or playable video for it.`
              : scenes.length > 0
                ? `This import has ${scenes.length} scene records for this quest, but no dialogue lines. The source does not confirm what players see at this point.`
                : 'The game data lists this quest, but this import has no dialogue or scene records for it.'}
          </p>
          <p>Its place in the story and source links are still shown on this page.</p>
          {hasMediaReferences && <QuestMediaReferences manifest={media} stateAnchors={new Map()} />}
        </section>
      ) : (
        <>
          <div className="transcript-toolbar">
            <div>
              <span className="eyebrow left">AUTHORED TRANSCRIPT</span>
              <h2>Story, as recorded.</h2>
              <p>
                Choices with a recorded path show their own lines. Select an answer to highlight its
                branch; this is not a single guaranteed playthrough.
              </p>
            </div>
            <div className="transcript-filters">
              <label className="voice-language">
                Voice
                <select
                  aria-label="Voice language"
                  value={voiceLanguage}
                  onChange={(event) => setVoiceLanguage(event.target.value as typeof voiceLanguage)}
                >
                  <option value="en">English</option>
                  <option value="ja">Japanese</option>
                  <option value="ko">Korean</option>
                  <option value="zh">Chinese</option>
                </select>
              </label>
              <button
                onClick={() => {
                  setOnlyLines(!onlyLines);
                  setOnlyChoices(false);
                }}
                className={onlyLines ? 'toggle active' : 'toggle'}
              >
                <Users size={14} /> Spoken lines
              </button>
              <button
                onClick={() => {
                  setOnlyChoices(!onlyChoices);
                  setOnlyLines(false);
                }}
                className={onlyChoices ? 'toggle active' : 'toggle'}
              >
                Choice moments
              </button>
            </div>
          </div>
          <div className="transcript-layout">
            <aside className="scene-nav">
              <h3>
                IN THIS QUEST <span>{stateIndex.length.toString().padStart(2, '0')}</span>
              </h3>
              {stateIndex.length ? (
                <>
                  <p className="flow-nav-note">
                    Authored flow states. Branches may change the path you see in game.
                  </p>
                  <nav className="flow-state-list" aria-label="Quest flow states">
                    {stateIndex.map((state, i) => (
                      <a
                        href={`#${state.anchor}`}
                        className="flow-state-link"
                        key={state.key}
                        title={state.key}
                      >
                        <span>{String(i + 1).padStart(2, '0')}</span>
                        <span>
                          Flow state {i + 1}
                          <small>{state.key}</small>
                        </span>
                        <b>{state.count}</b>
                      </a>
                    ))}
                  </nav>
                </>
              ) : (
                <p>No transcript lines in this view.</p>
              )}
              {scenes.length > 0 && (
                <p className="flow-nav-note">
                  {scenes.length} source scene records are also linked to this quest.
                </p>
              )}
              <QuestMediaReferences manifest={media} stateAnchors={stateAnchors} />
            </aside>
            <div className="transcript">
              {shown.length ? (
                shown.map((line: any, i: number) => {
                  const branch = branchByLine.get(line.id);
                  const active = branch?.choice.id === activeChoiceId;
                  return (
                    <article
                      className={`transcript-line${branch ? ' branch-line' : ''}${active ? ' branch-line-active' : ''}`}
                      id={lineAnchors.get(line.id)}
                      key={line.id || i}
                    >
                      <div className="line-rail">
                        <span>{String(i + 1).padStart(3, '0')}</span>
                        <i />
                      </div>
                      <div className="line-body">
                        {(joins.get(line.id) || 0) > 1 && (
                          <div className="branch-join">Branches meet here</div>
                        )}
                        {branch && branch.lineIds[0] === line.id && (
                          <div className="branch-marker">
                            <span>
                              CHOICE {String(branch.optionIndex + 1).padStart(2, '0')} ·{' '}
                              {branch.lineIds.length}{' '}
                              {branch.lineIds.length === 1 ? 'LINE' : 'LINES'}
                            </span>
                            <strong>
                              <PlayerText
                                display={playerDisplay}
                                value={branch.choice.text}
                                fallback="Choice text unavailable"
                              />
                            </strong>
                            {active && (
                              <button type="button" onClick={() => setActiveChoiceId(null)}>
                                Clear highlight
                              </button>
                            )}
                          </div>
                        )}
                        <div className="speaker-row">
                          <span className="speaker-dot" />
                          <strong>{line.speaker?.label || 'Narration / Unknown speaker'}</strong>
                          <span className="line-state">{line.flow_state || 'Story'}</span>
                          {line.action?.name && (
                            <span className="action-tag">{line.action.name}</span>
                          )}
                        </div>
                        <DialogueAudioReference media={line.media}>
                          <p>
                            {line.text?.content || line.text?.inline_text ? (
                              <PlayerText
                                display={playerDisplay}
                                value={line.text}
                                fallback={line.text?.inline_text || ''}
                              />
                            ) : (
                              <i className="missing">Text unavailable in this locale</i>
                            )}
                          </p>
                        </DialogueAudioReference>
                        {line.player_choices?.length > 0 && (
                          <div className="choice-block">
                            <div className="choice-heading">
                              <span /> PLAYER CHOICE
                              {line.player_choices.length > 1 ? 'S' : ''}
                            </div>
                            {line.player_choices.map((choice: any, j: number) => (
                              <div className="choice-option" key={j}>
                                <span className="choice-diamond">
                                  {String(j + 1).padStart(2, '0')}
                                </span>
                                {lineAnchors.has(choice.target_line_id) ? (
                                  <button
                                    type="button"
                                    className="choice-branch-button"
                                    aria-pressed={activeChoiceId === choice.id}
                                    onClick={() => {
                                      const next =
                                        activeChoiceId === choice.id ||
                                        !branchesByChoice.has(choice.id)
                                          ? null
                                          : choice.id;
                                      setActiveChoiceId(next);
                                      if (activeChoiceId !== choice.id)
                                        requestAnimationFrame(() =>
                                          document
                                            .getElementById(lineAnchors.get(choice.target_line_id)!)
                                            ?.scrollIntoView({
                                              behavior: 'smooth',
                                              block: 'start',
                                            }),
                                        );
                                    }}
                                  >
                                    <span>
                                      <PlayerText
                                        display={playerDisplay}
                                        value={choice.text}
                                        fallback="Choice text unavailable"
                                      />
                                    </span>
                                    <small>
                                      {branchesByChoice.has(choice.id)
                                        ? `${branchesByChoice.get(choice.id)!.lineIds.length} ${branchesByChoice.get(choice.id)!.lineIds.length === 1 ? 'line' : 'lines'}${branchesByChoice.get(choice.id)!.continuationLineId === line.id ? ' · returns here' : ''}`
                                        : 'View reply'}
                                    </small>
                                  </button>
                                ) : (
                                  <span>
                                    <PlayerText
                                      display={playerDisplay}
                                      value={choice.text}
                                      fallback="Choice text unavailable"
                                    />
                                  </span>
                                )}
                              </div>
                            ))}
                          </div>
                        )}
                        <div className="line-foot">
                          <span>
                            {line.source_type || 'dialogue'}
                            {line.game_ids?.talk_item_id
                              ? ` · Talk ${line.game_ids.talk_item_id}`
                              : ''}
                          </span>
                          {line.provenance?.source_file && (
                            <span title={line.provenance.source_file}>
                              {line.provenance.source_file.split('/').at(-1)}
                              {line.provenance.source_row != null
                                ? ` · row ${line.provenance.source_row}`
                                : ''}
                            </span>
                          )}
                        </div>
                        {branch &&
                          branch.lineIds.at(-1) === line.id &&
                          branch.continuationLineId &&
                          lineAnchors.has(branch.continuationLineId) && (
                            <div className="branch-end">
                              <span>
                                {branch.continuationLineId === branch.sourceLineId
                                  ? 'Returns to the choice'
                                  : 'Continues after this branch'}
                              </span>
                              <a href={`#${lineAnchors.get(branch.continuationLineId)}`}>
                                Go there ↑
                              </a>
                            </div>
                          )}
                      </div>
                    </article>
                  );
                })
              ) : (
                <EmptyInline text="No dialogue lines matched this view." />
              )}
            </div>
          </div>
        </>
      )}
      {(questConnections.length > 0 || sourceGraphLinks.length > 0) && (
        <section className="content-panel quest-links-panel">
          <div className="panel-title">
            <span>{lines.length === 0 ? '01' : '02'}</span>
            <h2>Quest connections</h2>
            <small>{questConnections.length}</small>
          </div>
          {questConnections.length > 0 ? (
            <div className="quest-link-grid">
              {questConnections.map((link: any, index: number) => {
                const target = link.node;
                const relationLabel =
                  link.relation === 'requires_quest'
                    ? link.direction === 'outgoing'
                      ? 'Prerequisite'
                      : 'Required by this quest'
                    : link.direction === 'outgoing'
                      ? 'References'
                      : 'Referenced by';
                return (
                  <Link
                    className="quest-connection-card"
                    key={`${target.canonical_key}-${link.relation}-${index}`}
                    to={entityPath({
                      id: target.id,
                      canonical_key: target.canonical_key,
                      node_type: target.type,
                    })}
                  >
                    <strong>{target.label || target.canonical_key}</strong>
                    <span className="quest-connection-kind">{relationLabel}</span>
                    <small>Quest {target.canonical_key?.split(':').at(-1)}</small>
                  </Link>
                );
              })}
            </div>
          ) : (
            <EmptyInline text="No prerequisite or cross-quest references are recorded." />
          )}
          {sourceGraphLinks.length > 0 && (
            <details className="source-graph-details">
              <summary>Technical source links · {sourceGraphLinks.length}</summary>
              <p>
                These are raw graph connections such as quest structure nodes, not separate quests.
              </p>
              <div className="source-graph-list">
                {sourceGraphLinks.map((link: any, index: number) => {
                  const target = link.node;
                  const questNode = target?.type === 'quest_node';
                  const nodeId = target?.canonical_key?.split(':').at(-1);
                  return (
                    <Link
                      key={`${target?.canonical_key}-${link.relation}-${index}`}
                      to={entityPath({
                        id: target.id,
                        canonical_key: target.canonical_key,
                        node_type: target.type,
                      })}
                    >
                      <span>{link.direction === 'outgoing' ? 'Outgoing' : 'Incoming'}</span>
                      <strong>
                        {questNode
                          ? `Quest structure node ${nodeId?.split(':').at(-1)}`
                          : target?.label || target?.canonical_key}
                      </strong>
                      <small>
                        {link.relation.replaceAll('_', ' ')} · {link.basis.replaceAll('_', ' ')}
                      </small>
                    </Link>
                  );
                })}
              </div>
            </details>
          )}
        </section>
      )}
      {showBackToTop && (
        <button
          className="quest-back-to-top"
          type="button"
          onClick={() => {
            const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
            window.scrollTo({ top: 0, behavior: reducedMotion ? 'auto' : 'smooth' });
          }}
        >
          <ArrowUp size={16} aria-hidden="true" />
          <span>Back to top</span>
        </button>
      )}
    </div>
  );
}
