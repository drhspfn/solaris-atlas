'use client';

import { ArrowUp, BookOpen, ChevronRight, Users } from 'lucide-react';
import { Fragment, useEffect, useState } from 'react';
import { api } from '@/api/client';
import { PlayerText } from '@/components/dialogue/PlayerText';
import { AnalysisActions } from '@/components/story/AnalysisActions';
import { cutsceneSlots } from '@/components/story/cutscenePlacement';
import { QuestRuntimeBlock, runtimeBlockAnchor, runtimeBlockSlots } from '@/components/story/QuestRuntimeBlock';
import { QuestIllustration } from '@/components/story/QuestIllustration';
import { QuestClassification } from '@/components/story/QuestClassification';
import { QuestContinuity } from '@/components/story/QuestContinuity';
import { Cutscene, QuestCutscenes, RelatedQuestCutscenes } from '@/components/story/QuestCutscenes';
import { QuestExplanation } from '@/components/story/QuestExplanation';
import {
  DialogueAudioReference,
  type QuestMediaManifest,
  QuestMediaReferences,
} from '@/components/story/QuestMediaReferences';
import { EmptyInline, ErrorPanel, PageLoader } from '@/components/ui/Feedback';
import { APP_SETTINGS } from '@/config/settings';
import { entityPath } from '@/data/entities';
import type { QuestContinuity as QuestContinuityData } from '@/data/story';
import { useLocale } from '@/hooks/useLocale';
import { usePlayerDisplay } from '@/hooks/usePlayerDisplay';
import { Link, useLocation, useSearchParams } from '@/lib/router';
import { useNarrativePreferences } from '@/preferences/NarrativePreferences';

type ChoiceBranch = {
  choice: any;
  sourceLineId: string;
  optionIndex: number;
  lineIds: string[];
  continuationLineId: string | null;
};

export function QuestPageClient({
  questKey,
  initialProfile,
  initialTranscript,
  initialContinuity,
  initialMedia,
  initialSelection,
}: {
  questKey: string;
  initialProfile?: any;
  initialTranscript?: any;
  initialContinuity?: QuestContinuityData | null;
  initialMedia?: QuestMediaManifest | null;
  initialSelection: { locale: string; gameVersion: string; citedLine: string };
}) {
  const searchParams = useSearchParams();
  const gameVersion = searchParams?.get('game_version') || '';
  const selectedLocale = useLocale();
  const locale = searchParams?.get('locale') || selectedLocale;
  const citedLine = searchParams?.get('line');
  const navigation = useLocation();
  const playerDisplay = usePlayerDisplay();
  const { voiceLanguage, setVoiceLanguage } = useNarrativePreferences();

  const [profile, setProfile] = useState<any>(initialProfile || null);
  const [transcript, setTranscript] = useState<any>(initialTranscript || null);
  const [continuity, setContinuity] = useState<QuestContinuityData | null>(initialContinuity ?? null);
  const [media, setMedia] = useState<QuestMediaManifest | null>(initialMedia ?? null);
  const [continuityError, setContinuityError] = useState('');
  const [loading, setLoading] = useState(!initialProfile || !initialTranscript);
  const [error, setError] = useState('');
  const [onlyChoices, setOnlyChoices] = useState(false);
  const [onlyLines, setOnlyLines] = useState(false);
  const [activeChoiceId, setActiveChoiceId] = useState<string | null>(null);
  const [showBackToTop, setShowBackToTop] = useState(false);

  useEffect(() => {
    if (!citedLine) return;
    setOnlyChoices(false);
    setOnlyLines(false);
    if (loading) return;
    const frame = requestAnimationFrame(() => {
      const target = Array.from(document.querySelectorAll<HTMLElement>('[data-dialogue-key]')).find(
        (element) => element.dataset.dialogueKey === citedLine,
      );
      target?.scrollIntoView({ block: 'center' });
      target?.focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(frame);
  }, [citedLine, loading, transcript, navigation.key]);

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
    if (
      initialProfile &&
      initialTranscript &&
      locale === initialSelection.locale &&
      gameVersion === initialSelection.gameVersion &&
      (citedLine || '') === initialSelection.citedLine
    ) {
      setProfile(initialProfile);
      setTranscript(initialTranscript);
      setContinuity(initialContinuity ?? null);
      setMedia(initialMedia ?? null);
      setContinuityError('');
      setError('');
      setLoading(false);
      return;
    }
    let active = true;
    setLoading(true);
    setError('');
    const selection = new URLSearchParams({ locale });
    if (gameVersion) selection.set('game_version', gameVersion);
    const transcriptSelection = new URLSearchParams(selection);
    if (citedLine) transcriptSelection.set('focus_line', citedLine);

    Promise.all([
      api<any>(`/quests/${encodeURIComponent(questKey)}/profile?${selection}`),
      api<any>(
        `/quests/${encodeURIComponent(questKey)}/transcript?${transcriptSelection}&limit=${APP_SETTINGS.limits.transcript}&include_raw=false`,
      ),
      api<QuestContinuityData>(`/quests/${encodeURIComponent(questKey)}/continuity?${selection}`)
        .then((result) => ({ result, failure: '' }))
        .catch((reason: Error) => ({ result: null, failure: reason.message })),
      api<QuestMediaManifest>(`/quests/${encodeURIComponent(questKey)}/media?${selection}`).catch(() => null),
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
  }, [
    questKey, locale, gameVersion, citedLine, initialSelection, initialProfile,
    initialTranscript, initialContinuity, initialMedia,
  ]);

  if (loading && (!profile || !transcript)) return <PageLoader />;
  if (error && (!profile || !transcript))
    return (
      <div className="page-container">
        <ErrorPanel message={error} />
      </div>
    );

  const quest = profile?.quest || {};
  const lines = transcript?.lines || [];
  const scenes = transcript?.scenes || profile?.scenes || [];
  const classification = quest.classification;
  const storyGalleryRecord = classification?.kind === 'story_gallery';
  const hasMediaReferences = Boolean(media?.events?.length || media?.video_packages?.length || media?.related_cutscenes?.length);
  const explicitLinks = profile?.explicit_links || [];
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
  const contextualCutscenes = (media?.events || []).filter((event) => event.kind === 'cutscene' && event.context_inferred && !event.story_order_mapped);
  const videoSlots = cutsceneSlots(
    shown,
    onlyLines || onlyChoices ? [] : (media?.events || []).filter((event) => !event.context_inferred || event.story_order_mapped),
    Boolean(transcript?.offset || lines.length >= (transcript?.limit || 0)),
  );
  const activitySlots = runtimeBlockSlots(shown, onlyLines || onlyChoices ? [] : (transcript?.story_blocks || []),
    Boolean(transcript?.offset || lines.length >= (transcript?.limit || 0)));
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
        <span>{quest.name?.content || `Quest ${questKey}`}</span>
      </div>
      <section className="quest-hero">
        <div className="quest-emblem">
          <BookOpen size={28} />
          <span>{classification?.kind === 'test' ? 'TEST QUEST' : 'STORY RECORD'}</span>
        </div>
        <div className="quest-intro">
          <span className="eyebrow left">QUEST DOSSIER / {quest.quest_type || 'NARRATIVE'}</span>
          <div className="quest-title-row">
            <h1>
              {quest.name?.content || `Quest ${questKey}`}
              <span className="heading-period">.</span>
            </h1>
            <AnalysisActions target={{ kind: 'quest', questId: Number(quest.game_quest_id || questKey) }} />
          </div>
          <div className="quest-meta">
            <span>
              QUEST ID <b>{quest.game_quest_id || questKey}</b>
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
      <QuestClassification value={classification} />
      {profile?.item_requirements?.length > 0 && (
        <section className="subsection" aria-label="Item requirements">
          <h2>Item requirements</h2>
          {profile.item_requirements.map((requirement: any, index: number) => (
            <div className="plain-row" key={index}>
              <span>
                <strong>{requirement.alternatives ? 'Hand-in option' : requirement.label}</strong>
                <small>
                  {requirement.item ? (
                    <Link to={requirement.item.href}>{requirement.item.label}</Link>
                  ) : `Item ${requirement.item_id}`}
                  {requirement.count != null ? ` · ${requirement.count} required` : ''}
                </small>
              </span>
              <small>Requirement · not a reward</small>
            </div>
          ))}
        </section>
      )}
      {continuity && (
        <QuestContinuity continuity={continuity} title={quest.name?.content || `Quest ${questKey}`} />
      )}
      {continuityError && (
        <div className="quest-continuity-warning" role="status">
          Story path unavailable: {continuityError}
        </div>
      )}
      <QuestExplanation
        questId={Number(quest.game_quest_id || questKey)}
        version={gameVersion || continuity?.selected_game_version || ''}
        locale={locale}
      />
      {lines.length === 0 ? (
        <section className="quest-no-transcript" aria-labelledby="quest-no-transcript-title">
          <span className="eyebrow left">SOURCE RECORD · NO DIALOGUE TRANSCRIPT</span>
          <h2 id="quest-no-transcript-title">
            {classification?.kind === 'test' ? 'Test quest — no dialogue recorded' :
              storyGalleryRecord ? 'Story gallery record' : 'No dialogue recorded for this quest'}
          </h2>
          <p>
            {classification?.kind === 'test'
              ? 'The source classifies this as a test quest. No dialogue is recorded for it in this snapshot.'
              : storyGalleryRecord
              ? `The game data marks this as a story gallery record. This import has ${scenes.length} scene records, but no dialogue lines or playable video for it.`
              : scenes.length > 0
                ? `This import has ${scenes.length} scene records for this quest, but no dialogue lines. The source does not confirm what players see at this point.`
                : 'The game data lists this quest, but this import has no dialogue or scene records for it.'}
          </p>
          <p>{classification?.kind === 'test'
            ? 'Recorded requirements and source links are still shown on this page.'
            : 'Its place in the story and source links are still shown on this page.'}</p>
          {(transcript?.story_blocks || []).map((block: any) => <QuestRuntimeBlock key={block.id}
            block={block} display={playerDisplay} stateAnchors={new Map()} />)}
          {hasMediaReferences && <QuestMediaReferences manifest={media} stateAnchors={new Map()} />}
          <QuestCutscenes manifest={media} stateAnchors={new Map()} />
          <RelatedQuestCutscenes manifest={media} />
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
              {transcript?.tree_order_asset_version && <p>Scene order uses game client {transcript.tree_order_asset_version}.</p>}
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
                  <nav className="flow-state-list" aria-label="Quest chapters">
                    {stateIndex.map((state, i) => (
                      <a href={`#${state.anchor}`} className="flow-state-link" key={state.key}>
                        <span>{String(i + 1).padStart(2, '0')}</span>
                        <span>
                          Part {i + 1}
                          <small>
                            {state.count} {state.count === 1 ? 'line' : 'lines'}
                          </small>
                        </span>
                        <b>{state.count}</b>
                      </a>
                    ))}
                  </nav>
                </>
              ) : (
                <p>No transcript lines in this view.</p>
              )}
              <QuestMediaReferences
                manifest={media}
                stateAnchors={stateAnchors}
                visibleCutscenes={[...Array.from(videoSlots.values()).flat(), ...contextualCutscenes]}
              />
              {transcript?.story_blocks?.length > 0 && <details className="quest-activity-nav">
                <summary>Exploration &amp; interactions · {transcript.story_blocks.length}</summary>
                <nav aria-label="Quest activities">
                  {transcript.story_blocks.map((block: any) => <a key={block.id} href={`#${runtimeBlockAnchor(block.id)}`}>
                    <PlayerText display={playerDisplay} value={block.title} fallback="Story activity" />
                  </a>)}
                </nav>
              </details>}
            </aside>
            <div className="transcript">
              {shown.length ? (
                shown.map((line: any, i: number) => {
                  const branch = branchByLine.get(line.id);
                  const active = branch?.choice.id === activeChoiceId;
                  return (
                    <Fragment key={line.id || i}>
                      {activitySlots.get(i)?.map((block) => <QuestRuntimeBlock key={block.id}
                        block={block} display={playerDisplay} stateAnchors={stateAnchors} />)}
                      {line.story_order_mapped === false && (i === 0 || shown[i - 1].story_order_mapped !== false) && (
                        <p>Additional dialogue · position in the story is not mapped</p>
                      )}
                      {videoSlots.get(i)?.map((event) => (
                        <Cutscene key={`${event.action}-${event.reference}`} event={event} />
                      ))}
                      {line.illustration && line.illustration.kind !== 'clear' &&
                        (i === 0 || shown[i - 1].illustration?.id !== line.illustration.id) &&
                        <QuestIllustration key={line.illustration.id} illustration={line.illustration} />}
                      <article
                        className={`transcript-line${branch ? ' branch-line' : ''}${active ? ' branch-line-active' : ''}${line.id === citedLine ? ' cited-passage' : ''}`}
                        data-dialogue-key={line.id}
                        tabIndex={-1}
                        id={lineAnchors.get(line.id)}
                        key={line.id || i}
                      >
                        <div className="line-rail">
                          <span>{String((transcript?.offset || 0) + i + 1).padStart(3, '0')}</span>
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
                            <strong>
                              <PlayerText
                                display={playerDisplay}
                                value={line.speaker?.label}
                                fallback="Narration / Unknown speaker"
                              />
                            </strong>
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
                                              .getElementById(
                                                lineAnchors.get(choice.target_line_id)!,
                                              )
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
                    </Fragment>
                  );
                })
              ) : (
                <EmptyInline text="No dialogue lines matched this view." />
              )}
              {videoSlots.get(shown.length)?.map((event) => (
                <Cutscene key={`${event.action}-${event.reference}`} event={event} />
              ))}
              {activitySlots.get(shown.length)?.map((block) => <QuestRuntimeBlock key={block.id}
                block={block} display={playerDisplay} stateAnchors={stateAnchors} />)}
              {contextualCutscenes.map((event) => (
                <Cutscene key={`${event.action}-${event.reference}`} event={event} />
              ))}
              <RelatedQuestCutscenes manifest={media} />
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
                      slug: target.slug,
                      href: target.href,
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
                        slug: target.slug,
                        href: target.href,
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
