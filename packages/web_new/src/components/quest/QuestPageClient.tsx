'use client';

import { ArrowUp, BookOpen, ChevronRight, Users } from 'lucide-react';
import { Fragment, useEffect, useState } from 'react';
import { api } from '@/api/client';
import { PlayerText } from '@/components/dialogue/PlayerText';
import { gameTextPlainText } from '@/data/gameText';
import { QuestSidebar, type SidebarGroup } from './QuestSidebar';
import { activityDestination, dialogueSearchIndex } from './questSidebarModel';
import { localizedText } from '@/data/localized';
import { cutsceneAnchor, packageCutsceneAnchor } from '@/components/story/cutsceneAnchor';
import { isSourceOnlyActivity, sidebarActivityTitle } from '@/components/story/runtimeActivity';
import { AnalysisActions } from '@/components/story/AnalysisActions';
import { cutsceneSlots, visibleCutsceneEntries } from '@/components/story/cutscenePlacement';
import {
  QuestRuntimeBlock,
  EncounterCinematicBoundary,
  encounterCinematicParts,
  encounterFlowOwners,
  encounterOwnsMedia,
  type EncounterFlow,
  runtimeBlockAnchor,
  runtimeBlockSlots,
} from '@/components/story/QuestRuntimeBlock';
import { encounterFlows } from '@/components/story/encounterTransitions';
import { QuestIllustration } from '@/components/story/QuestIllustration';
import { QuestIllustratedScene } from '@/components/story/QuestIllustratedScene';
import { QuestFork } from '@/components/story/QuestFork';
import { illustratedRuns, questForks } from '@/components/story/questReading';
import '@/styles/quest-reader.css';
import { QuestClassification } from '@/components/story/QuestClassification';
import { QuestContinuity } from '@/components/story/QuestContinuity';
import { Cutscene, QuestCutscenes, RelatedQuestCutscenes } from '@/components/story/QuestCutscenes';
import { QuestSequenceFragments, playableEncounterFragments, hasSequenceComponents } from '@/components/story/QuestSequenceFragments';
import { QuestExplanation } from '@/components/story/QuestExplanation';
import {
  DialogueAudioReference,
  type QuestMediaManifest,
  QuestMediaReferences,
} from '@/components/story/QuestMediaReferences';
import { EmptyInline, ErrorPanel, PageLoader } from '@/components/ui/Feedback';
import { Select } from '@/components/ui/Select';
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
  const [continuity, setContinuity] = useState<QuestContinuityData | null>(
    initialContinuity ?? null,
  );
  const [media, setMedia] = useState<QuestMediaManifest | null>(initialMedia ?? null);
  const [continuityError, setContinuityError] = useState('');
  const [loading, setLoading] = useState(!initialProfile || !initialTranscript);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const [onlyChoices, setOnlyChoices] = useState(false);
  const [onlyLines, setOnlyLines] = useState(false);
  const [activeChoiceId, setActiveChoiceId] = useState<string | null>(null);
  const [showBackToTop, setShowBackToTop] = useState(false);

  useEffect(() => {
    const reveal = () => {
      const hash = window.location?.hash;
      if (!hash) return;
      let id: string;
      try {
        id = decodeURIComponent(hash.slice(1));
      } catch {
        return;
      }
      const target = document.getElementById(id);
      for (let parent = target?.parentElement; parent; parent = parent.parentElement)
        if (parent instanceof HTMLDetailsElement) parent.open = true;
      target?.scrollIntoView({ block: 'start' });
      if (target) {
        if (!target.hasAttribute('tabindex')) target.tabIndex = -1;
        target.focus({ preventScroll: true });
      }
    };
    const frame = requestAnimationFrame(reveal);
    window.addEventListener('hashchange', reveal);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('hashchange', reveal);
    };
  }, [loading, transcript]);

  useEffect(() => {
    if (!citedLine) return;
    setOnlyChoices(false);
    setOnlyLines(false);
    if (loading) return;
    const frame = requestAnimationFrame(() => {
      const target = Array.from(document.querySelectorAll<HTMLElement>('[data-dialogue-key]')).find(
        (element) => element.dataset.dialogueKey === citedLine,
      );
      for (let parent = target?.parentElement; parent; parent = parent.parentElement)
        if (parent instanceof HTMLDetailsElement) parent.open = true;
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
      retry === 0 &&
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
    const controller = new AbortController();
    setLoading(true);
    setError('');
    const selection = new URLSearchParams({ locale });
    if (gameVersion) selection.set('game_version', gameVersion);
    const transcriptSelection = new URLSearchParams(selection);
    if (citedLine) transcriptSelection.set('focus_line', citedLine);

    Promise.all([
      api<any>(`/quests/${encodeURIComponent(questKey)}/profile?${selection}`, { signal: controller.signal }),
      api<any>(
        `/quests/${encodeURIComponent(questKey)}/transcript?${transcriptSelection}&limit=${APP_SETTINGS.limits.transcript}&include_raw=false`,
        { signal: controller.signal },
      ),
      api<QuestContinuityData>(`/quests/${encodeURIComponent(questKey)}/continuity?${selection}`, { signal: controller.signal })
        .then((result) => ({ result, failure: '' }))
        .catch((reason: Error) => ({ result: null, failure: reason.message })),
      api<QuestMediaManifest>(`/quests/${encodeURIComponent(questKey)}/media?${selection}`, { signal: controller.signal }).catch(
        () => null,
      ),
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
      controller.abort();
    };
  }, [
    questKey,
    locale,
    gameVersion,
    citedLine,
    initialSelection,
    initialProfile,
    initialTranscript,
    initialContinuity,
    initialMedia,
    retry,
  ]);

  if (loading && (!profile || !transcript)) return <PageLoader />;
  if (error && (!profile || !transcript))
    return (
      <div className="page-container">
        <ErrorPanel message={error} onRetry={() => setRetry((value) => value + 1)} />
      </div>
    );

  const quest = profile?.quest || {};
  const lines = transcript?.lines || [];
  const scenes = transcript?.scenes || profile?.scenes || [];
  const classification = quest.classification;
  const storyGalleryRecord = classification?.kind === 'story_gallery';
  const hasMediaReferences = Boolean(
    media?.events?.length || media?.video_packages?.length || media?.related_cutscenes?.length,
  );
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
  const activitySlots = runtimeBlockSlots(
    shown,
    onlyLines || onlyChoices ? [] : transcript?.story_blocks || [],
    Boolean(transcript?.offset || lines.length >= (transcript?.limit || 0)),
  );
  const encounterBlocks = [...activitySlots.values()].flat();
  const encounterOwners = encounterFlowOwners(encounterBlocks, shown);
  const interactionIds = new Set<string>(encounterBlocks.flatMap((block) =>
    (block.interactions || []).map((item) => `${block.id}:${item.id}`)));
  const pageMediaEvents = visibleCutsceneEntries(shown, media?.events || [],
    Boolean(transcript?.offset || lines.length >= (transcript?.limit || 0)), interactionIds);
  const encounterMediaOwners = new Map<string, string>();
  for (const event of pageMediaEvents) {
    if (event.interaction_id) continue;
    const flow = encounterBlocks.flatMap((block) => block.encounter ? encounterFlows(block.encounter) : [])
      .find((flow) => encounterOwnsMedia(event, new Map([[flow.state, flow.id]]), encounterBlocks) &&
        encounterOwners.get(flow.state) === flow.id);
    if (flow) encounterMediaOwners.set(event.reference, flow.id);
  }

  const contextualCutscenes = pageMediaEvents.filter(
    (event) => event.kind === 'cutscene' && !event.interaction_id && event.context_inferred && !event.story_order_mapped && !encounterMediaOwners.has(event.reference),
  );
  const videoSlots = cutsceneSlots(
    shown,
    onlyLines || onlyChoices
      ? []
      : pageMediaEvents.filter(
          (event) => !event.interaction_id && (!event.context_inferred || event.story_order_mapped) && !encounterMediaOwners.has(event.reference),
        ),
    Boolean(transcript?.offset || lines.length >= (transcript?.limit || 0)),
    ['cutscene', 'sequence'],
  );
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

  const interactionOwners = new Map<string, string>();
  const activityBlocks = [...activitySlots.values()].flat();
  const mountedActivityAnchors = new Map(activityBlocks.map((block) => [block.id, runtimeBlockAnchor(block.id)]));
  const searchText = (value: unknown, fallback: string) =>
    gameTextPlainText(value, playerDisplay, fallback);
  const dialogueSearch = dialogueSearchIndex(lines.map((line: any) => ({
    flow_state: line.flow_state, text: searchText(line.text, ''),
  })));
  const visibleSidebarScenes = new Set([
    ...Array.from(videoSlots.values()).flat(), ...contextualCutscenes,
    ...pageMediaEvents.filter((event) => encounterMediaOwners.has(event.reference)),
    ...pageMediaEvents.filter((event) => event.interaction_id &&
      activityBlocks.some((block) => block.interactions?.some((item) => `${block.id}:${item.id}` === event.interaction_id))),
  ].map(cutsceneAnchor));
  const eventSceneNames = new Set(pageMediaEvents.filter((event) => event.kind === 'cutscene').map((event) => event.reference));
  const sidebarGroups: SidebarGroup[] = [
    { id: 'dialogue', title: 'Dialogue', entries: stateIndex.map((state) => {
      const first = shown.find((line: any) => line.flow_state === state.key);
      return { id: state.key, title: first?.text, fallback: 'Dialogue passage',
        searchText: dialogueSearch.get(state.key) || 'Dialogue passage', href: `#${state.anchor}`,
        note: `${state.count} ${state.count === 1 ? 'line' : 'lines'} in this view`, inView: true };
    }) },
    { id: 'scenes', title: 'Scenes', entries: [
      ...pageMediaEvents.filter((event) => event.kind !== 'audio_event').map((event) => {
        const anchor = visibleSidebarScenes.has(cutsceneAnchor(event))
          ? cutsceneAnchor(event) : stateAnchors.get(event.flow_state);
        const fallback = event.kind === 'cutscene' ? `Cutscene · ${event.reference.replace('cutscene:', '')}` : 'Story scene';
        return { id: cutsceneAnchor(event), title: event.title, fallback,
          searchText: searchText(event.title, fallback), inView: Boolean(anchor),
          href: anchor ? `#${anchor}` : activityDestination({ id: '', flow_states:
            [event.flow_state, ...(event.transcript_states || [])].map((state) => ({ state })) }, new Map(), lines, searchParams?.toString() || ''),
        };
      }),
      ...(media?.related_cutscenes || []).filter((scene) => !eventSceneNames.has(`cutscene:${scene.name}`)).map((scene) => ({
        id: packageCutsceneAnchor(scene.name), title: scene.name, fallback: 'Related scene',
        searchText: scene.name, href: `#${packageCutsceneAnchor(scene.name)}`, inView: true,
        note: 'Related · position unknown',
      })),
    ] },
    { id: 'activities', title: 'Activities', entries: (transcript?.story_blocks || []).filter((block: any) => !isSourceOnlyActivity(block)).map((block: any) => ({
      id: block.id, title: sidebarActivityTitle(block), fallback: 'Story activity',
      searchText: searchText(sidebarActivityTitle(block), 'Story activity'), inView: mountedActivityAnchors.has(block.id),
      href: activityDestination(block, mountedActivityAnchors, lines, searchParams?.toString() || ''),
      note: !block.position || block.ordering === 'related' ? 'Related · position unknown' : undefined,
    })).sort((a: { inView: boolean }, b: { inView: boolean }) => Number(b.inView) - Number(a.inView)) },
  ];
  for (const block of activityBlocks)
    for (const item of block.interactions || [])
      for (const flow of item.flow_states)
        if (!interactionOwners.has(flow.state))
          interactionOwners.set(flow.state, `${block.id}:${item.id}`);
  const inlineStates = new Set(
    shown
      .filter((line: any) => !line.story_position && (interactionOwners.has(line.flow_state) || encounterOwners.has(line.flow_state)))
      .map((line: any) => line.flow_state),
  );
  const indexedLines = new Map<string, { line: any; index: number }>(
    shown.map((line: any, index: number) => [line.id, { line, index }]),
  );
  const forks = questForks(shown.filter((line: any) => !inlineStates.has(line.flow_state)));
  const forksBySource = new Map(forks.map((fork) => [fork.source, fork]));
  const forkOwners = new Map(
    forks.flatMap((fork) =>
      fork.paths.flatMap((path) => path.lineIds.map((id) => [id, path.id] as const)),
    ),
  );
  const forkMediaOwners = new Map<string, string>();
  for (const state of stateCounts.keys()) {
    const owners = new Set<string | undefined>(
      shown
        .filter((line: any) => line.flow_state === state)
        .map((line: any) => forkOwners.get(line.id)),
    );
    if (owners.size === 1 && !owners.has(undefined)) forkMediaOwners.set(state, [...owners][0]!);
  }
  const breaks = new Set<string>(
    shown
      .filter((line: any, index: number) => videoSlots.has(index) || activitySlots.has(index))
      .map((line: any) => line.id),
  );
  const sceneRuns = illustratedRuns(
    shown.filter((line: any) => !inlineStates.has(line.flow_state) && !forkOwners.has(line.id)),
    breaks,
  );
  const sceneOwners = new Map(
    sceneRuns.flatMap((run) => run.map((line) => [line.id, run] as const)),
  );
  const renderExcerpt = (selected: Array<{ line: any; index: number }>, events: any[]) => {
    const scenes = cutsceneSlots(
      selected.map(({ line }) => line),
      events,
      false,
      ['cutscene', 'sequence'],
    );
    const runs = illustratedRuns(
      selected.map(({ line }) => line),
      new Set(selected.filter((_, index) => scenes.has(index)).map(({ line }) => line.id)),
    );
    const owners = new Map(runs.flatMap((run) => run.map((line) => [line.id, run] as const)));
    return [
      ...selected.map(({ line, index }, slot) => {
        const run = owners.get(line.id);
        return (
          <Fragment key={line.id}>
            {scenes.get(slot)?.map((event) => (
              <Cutscene key={`${event.action}-${event.reference}`} event={event} display={playerDisplay} />
            ))}
            {run
              ? run[0].id === line.id && (
                  <QuestIllustratedScene
                    key={run[0].id}
                    lines={run as any}
                    anchors={lineAnchors}
                    focusLine={citedLine}
                    renderLine={(entry) =>
                      renderTranscriptLine(entry, indexedLines.get(entry.id)!.index, true, true)
                    }
                  />
                )
              : renderTranscriptLine(line, index, true)}
          </Fragment>
        );
      }),
      ...(scenes.get(selected.length) || []).map((event) => (
        <Cutscene key={`${event.action}-${event.reference}`} event={event} display={playerDisplay} />
      )),
    ];
  };
  const renderEncounterFlow = (flow: EncounterFlow) => {
    const related = shown.filter((line: any) => line.flow_state === flow.state);
    if (encounterOwners.get(flow.state) !== flow.id) {
      return related.length ? [<a key={flow.id} href={`#${lineAnchors.get(related[0].id)}`}>Read the dialogue shared by this branch ↗</a>] : [];
    }
    const selected = related.filter((line: any) => inlineStates.has(line.flow_state));
    const parts = encounterCinematicParts(selected, flow.cinematics);
    const componentPart = parts.findIndex(part => part.cinematics.length > 0);
    const componentCount = playableEncounterFragments(flow.sequence_media).length;
    const rendered = parts.map((part, index) => {
      const events = pageMediaEvents.filter(event => event.kind === 'cutscene' &&
        encounterMediaOwners.get(event.reference) === flow.id &&
        parts.findIndex((part) => part.cinematics.some(scene => scene.reference === event.reference)) === index);
      const content = renderExcerpt(part.lines.map((line: any) => indexedLines.get(line.id)!), events);
      return part.cinematics.length
        ? <EncounterCinematicBoundary key={`${flow.id}-${index}`} media={part.cinematics} unplaced={!part.lines.length} playbackResolved={events.some(event => !!event.playback)} componentCount={index === componentPart ? componentCount : 0}>
            {index === componentPart && <QuestSequenceFragments media={flow.sequence_media} />}
            {content}
          </EncounterCinematicBoundary>
        : <Fragment key={`${flow.id}-${index}`}>{content}</Fragment>;
    });
    return componentPart < 0 && hasSequenceComponents(flow.sequence_media)
      ? [<QuestSequenceFragments key={`${flow.id}-components`} media={flow.sequence_media} />, ...rendered]
      : rendered;
  };
  const renderInteraction = (states: string[], owner: string) => {
    const selected = shown
      .map((line: any, index: number) => ({ line, index }))
      .filter(
        ({ line }: any) =>
          inlineStates.has(line.flow_state) &&
          states.includes(line.flow_state) &&
          interactionOwners.get(line.flow_state) === owner,
      );
    return renderExcerpt(
      selected,
      pageMediaEvents.filter(
        (event) =>
          !encounterMediaOwners.has(event.reference) &&
          (event.interaction_id ? event.interaction_id === owner :
            selected.some(({ line }: any) => line.flow_state === event.flow_state)),
      ),
    );
  };

  const renderTranscriptLine = (line: any, i: number, embedded = false, inScene = false) => {
    const activities =
      !embedded &&
      activitySlots
        .get(i)
        ?.map((block) => (
          <QuestRuntimeBlock
            key={block.id}
            block={block}
            display={playerDisplay}
            stateAnchors={stateAnchors}
            renderInteraction={renderInteraction}
            renderEncounterFlow={renderEncounterFlow}
          />
        ));
    const scenes =
      !embedded &&
      videoSlots
        .get(i)
        ?.filter(
          (event) => !inlineStates.has(event.flow_state) && !forkMediaOwners.has(event.flow_state),
        )
        .map((event) => <Cutscene key={`${event.action}-${event.reference}`} event={event} display={playerDisplay} />);
    if (!embedded && (inlineStates.has(line.flow_state) || forkOwners.has(line.id)))
      return (
        <Fragment key={line.id}>
          {activities}
          {scenes}
        </Fragment>
      );
    const run = !embedded && sceneOwners.get(line.id);
    if (run)
      return (
        <Fragment key={line.id}>
          {activities}
          {scenes}
          {run[0].id === line.id && (
            <QuestIllustratedScene
              key={run[0].id}
              lines={run as any}
              anchors={lineAnchors}
              focusLine={citedLine}
              renderLine={(entry) =>
                renderTranscriptLine(entry, indexedLines.get(entry.id)!.index, true, true)
              }
            />
          )}
        </Fragment>
      );
    const branch = branchByLine.get(line.id);
    const active = branch?.choice.id === activeChoiceId;
    const lineSource = (
      <div className="line-foot">
        <span className="line-state">{line.flow_state || 'Story'}</span>
        {line.action?.name && <span className="action-tag">{line.action.name}</span>}
        <span>
          {line.source_type || 'dialogue'}
          {line.game_ids?.talk_item_id != null ? ` · Talk ${line.game_ids.talk_item_id}` : ''}
        </span>
        {line.provenance?.source_file && (
          <span title={line.provenance.source_file}>
            {line.provenance.source_file.split('/').at(-1)}
            {line.provenance.source_row != null ? ` · row ${line.provenance.source_row}` : ''}
          </span>
        )}
      </div>
    );
    return (
      <Fragment key={line.id || i}>
        {activities}
        {!embedded &&
          line.story_order_mapped === false &&
          (i === 0 || shown[i - 1].story_order_mapped !== false) && (
            <p>Additional dialogue · position in the story is not mapped</p>
          )}
        {scenes}
        {!inScene &&
          line.illustration &&
          line.illustration.kind !== 'clear' &&
          (i === 0 || shown[i - 1].illustration?.id !== line.illustration.id) && (
            <QuestIllustration key={line.illustration.id} illustration={line.illustration} />
          )}
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
            {(joins.get(line.id) || 0) > 1 && <div className="branch-join">Branches meet here</div>}
            {branch && branch.lineIds[0] === line.id && (
              <div className="branch-marker">
                <span>
                  CHOICE {String(branch.optionIndex + 1).padStart(2, '0')} · {branch.lineIds.length}{' '}
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
              <div className={`choice-block${forksBySource.has(line.id) ? ' is-fork' : ''}`}>
                <div className="choice-heading">
                  <span /> PLAYER CHOICE
                  {line.player_choices.length > 1 ? 'S' : ''}
                </div>
                {line.player_choices.map((choice: any, j: number) => (
                  <div className="choice-option" key={j}>
                    <span className="choice-diamond">{String(j + 1).padStart(2, '0')}</span>
                    {lineAnchors.has(choice.target_line_id) ? (
                      <button
                        type="button"
                        className="choice-branch-button"
                        aria-pressed={activeChoiceId === choice.id}
                        onClick={() => {
                          const next =
                            activeChoiceId === choice.id || !branchesByChoice.has(choice.id)
                              ? null
                              : choice.id;
                          setActiveChoiceId(next);
                          const anchor = lineAnchors.get(choice.target_line_id);
                          if (anchor && activeChoiceId !== choice.id) window.location.hash = anchor;
                          if (activeChoiceId !== choice.id)
                            requestAnimationFrame(() =>
                              document
                                .getElementById(lineAnchors.get(choice.target_line_id)!)
                                ?.scrollIntoView({
                                  behavior: window.matchMedia('(prefers-reduced-motion: reduce)')
                                    .matches
                                    ? 'auto'
                                    : 'smooth',
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
            <details className="quest-line-source">
              <summary>Source reference</summary>
              {lineSource}
            </details>
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
                  <a href={`#${lineAnchors.get(branch.continuationLineId)}`}>Go there ↑</a>
                </div>
              )}
          </div>
        </article>
        {forksBySource.has(line.id) && (
          <QuestFork
            fork={forksBySource.get(line.id)!}
            display={playerDisplay}
            anchors={lineAnchors}
            selected={activeChoiceId}
            renderPath={(ids) => {
              const selected = ids.map((id) => indexedLines.get(id)!).filter(Boolean);
              const owner = ids.length ? forkOwners.get(ids[0]) : null;
              return renderExcerpt(
                selected,
                pageMediaEvents.filter(
                  (event) => owner && !event.interaction_id && !encounterMediaOwners.has(event.reference) && forkMediaOwners.get(event.flow_state) === owner,
                ),
              );
            }}
          />
        )}
      </Fragment>
    );
  };

  return (
    <div className="page-container quest-container">
      {error && <ErrorPanel message={error} onRetry={() => setRetry((value) => value + 1)} />}
      {loading && <p role="status">Updating quest transcript…</p>}
      <div className="breadcrumbs">
        <Link to="/">Archive</Link>
        <ChevronRight size={13} />
        <Link to="/story-map">Story map</Link>
        <ChevronRight size={13} />
        <span><PlayerText value={quest.name} fallback={`Quest ${questKey}`} display={playerDisplay} /></span>
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
              <PlayerText value={quest.name} fallback={`Quest ${questKey}`} display={playerDisplay} />
              <span className="heading-period">.</span>
            </h1>
            <AnalysisActions
              target={{
                kind: 'quest',
                questId: Number(quest.game_quest_id || questKey),
              }}
            />
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
                  ) : (
                    `Item ${requirement.item_id}`
                  )}
                  {requirement.count != null ? ` · ${requirement.count} required` : ''}
                </small>
              </span>
              <small>Requirement · not a reward</small>
            </div>
          ))}
        </section>
      )}
      {continuity && (
        <QuestContinuity
          continuity={continuity} display={playerDisplay}
          title={quest.name?.content || `Quest ${questKey}`}
        />
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
            {classification?.kind === 'test'
              ? 'Test quest — no dialogue recorded'
              : storyGalleryRecord
                ? 'Story gallery record'
                : 'No dialogue recorded for this quest'}
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
          <p>
            {classification?.kind === 'test'
              ? 'Recorded requirements and source links are still shown on this page.'
              : 'Its place in the story and source links are still shown on this page.'}
          </p>
          {(transcript?.story_blocks || []).map((block: any) => (
            <QuestRuntimeBlock
              key={block.id}
              block={block}
              display={playerDisplay}
              stateAnchors={new Map()}
            />
          ))}
          {hasMediaReferences && <QuestMediaReferences manifest={media} stateAnchors={new Map()} />}
          <QuestCutscenes display={playerDisplay} manifest={media} stateAnchors={new Map()} />
          <RelatedQuestCutscenes display={playerDisplay} manifest={media} />
        </section>
      ) : (
        <>
          <div className="transcript-toolbar">
            <div>
              <span className="eyebrow left">AUTHORED TRANSCRIPT</span>
              <h2>Inside the story.</h2>
              <p>
                Follow the dialogue, explore each answer, and open the interactions along the way.
              </p>
              <details className="quest-reading-notes">
                <summary>About this reading order</summary>
                <p>
                  Alternatives are separate paths, not consecutive events. Incomplete paths remain
                  in the transcript with their source references.
                  {transcript?.tree_order_asset_version && (
                    <> Scene order uses game client {transcript.tree_order_asset_version}.</>
                  )}
                </p>
              </details>
            </div>
            <div className="transcript-filters">
              <Select
                label="Voice language"
                value={voiceLanguage}
                onChange={(value) => setVoiceLanguage(value as typeof voiceLanguage)}
                options={[
                  { value: 'en', label: 'English' },
                  { value: 'ja', label: 'Japanese' },
                  { value: 'ko', label: 'Korean' },
                  { value: 'zh', label: 'Chinese' },
                ]}
              />
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
            <QuestSidebar key={`${questKey}:${locale}:${gameVersion}`} groups={sidebarGroups} display={playerDisplay}
              activeAnchor={stateAnchors.get(shown.find((line: any) => line.id === citedLine)?.flow_state) || ''} />
            <div className="transcript">
              {shown.length ? (
                shown.map((line: any, i: number) => renderTranscriptLine(line, i))
              ) : (
                <EmptyInline text="No dialogue lines matched this view." />
              )}
              {videoSlots
                .get(shown.length)
                ?.filter(
                  (event) =>
                    !inlineStates.has(event.flow_state) && !forkMediaOwners.has(event.flow_state),
                )
                .map((event) => (
                  <Cutscene key={`${event.action}-${event.reference}`} event={event} display={playerDisplay} />
                ))}
              {activitySlots.get(shown.length)?.map((block) => (
                <QuestRuntimeBlock
                  key={block.id}
                  block={block}
                  display={playerDisplay}
                  stateAnchors={stateAnchors}
                  renderInteraction={renderInteraction}
                  renderEncounterFlow={renderEncounterFlow}
                />
              ))}
              {contextualCutscenes.map((event) => (
                <Cutscene key={`${event.action}-${event.reference}`} event={event} display={playerDisplay} />
              ))}
              {!!activitySlots.get(-1)?.length && (
                <section aria-label="Related activities">
                  <h3>Related activities</h3>
                  <p>Their position in this passage is not known.</p>
                  {activitySlots.get(-1)!.map((block) => (
                    <QuestRuntimeBlock key={block.id} block={block} display={playerDisplay}
                      stateAnchors={stateAnchors} renderInteraction={renderInteraction}
                      renderEncounterFlow={renderEncounterFlow} />
                  ))}
                </section>
              )}
              <RelatedQuestCutscenes display={playerDisplay} manifest={media} />
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
            window.scrollTo({
              top: 0,
              behavior: reducedMotion ? 'auto' : 'smooth',
            });
          }}
        >
          <ArrowUp size={16} aria-hidden="true" />
          <span>Back to top</span>
        </button>
      )}
    </div>
  );
}
