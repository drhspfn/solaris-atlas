'use client';

import { ArrowRight, BookOpen, ChevronRight, GitBranch, Search, X } from 'lucide-react';
import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/api/client';
import { ErrorPanel, PageLoader } from '@/components/ui/Feedback';
import { Select } from '@/components/ui/Select';
import type { StoryMap, StoryTreeNode } from '@/data/story';
import { useLocale } from '@/hooks/useLocale';
import { Link, useNavigate, useSearchParams } from '@/lib/router';
import { questPath } from '@/lib/routeKey';
import { storyChapterGroups, storyMapPage, storyPageForHash, storyPageNumber } from './storyMapPaging';

const questTypes = [
  { id: 0, label: 'All story paths' },
  { id: 1, label: 'Main story' },
  { id: 3, label: 'Companion stories' },
  { id: 9, label: 'Exploration stories' },
  { id: 2, label: 'Side stories' },
];

function StoryStep({ node, version }: { node: StoryTreeNode; version: string }) {
  return (
    <li className="story-step">
      <span className="story-step-marker" aria-hidden="true" />
      <div className="story-step-content">
        <div className="story-step-heading">
          <span className="story-step-index">
            {node.source_kind === 'quest_tree' ? 'PATH' : 'QUEST'} {node.id}
          </span>
          {node.chapter_label && <span className="story-step-chapter">{node.chapter_label}</span>}
        </div>
        {node.source_kind === 'quest_tree' && <h3>{node.title}</h3>}
        {node.quests.length > 0 ? (
          <div className="story-step-quests">
            {node.quests.map((quest) => (
              <Link
                key={quest.game_quest_id}
                to={`${questPath(quest)}?game_version=${encodeURIComponent(version)}`}
                className="story-quest-link"
              >
                <BookOpen size={16} aria-hidden="true" />
                <span>
                  <strong>{quest.title}</strong>
                  <small>
                    Quest {quest.game_quest_id}
                    {quest.first_observed_game_version &&
                      ` · Seen from ${quest.first_observed_game_version}`}
                  </small>
                </span>
                <ArrowRight size={16} aria-hidden="true" />
              </Link>
            ))}
          </div>
        ) : (
          <p className="story-step-empty">This tree node has no quest records.</p>
        )}
        {node.source_kind === 'quest_prerequisite' && node.previous_node_ids.length > 0 && (
          <small className="story-step-branch">
            Requires {node.previous_node_ids.map((id) => `quest ${id}`).join(', ')} · prerequisite,
            not immediate sequence
          </small>
        )}
        {node.source_kind === 'quest_tree' &&
          (node.previous_node_ids.length > 1 || !node.next_node_id) && (
            <small className="story-step-branch">
              {node.previous_node_ids.length > 1
                ? `${node.previous_node_ids.length} authored predecessor links`
                : 'End of this authored path'}
            </small>
          )}
      </div>
    </li>
  );
}

export function StoryMapClient({ initialMap, initialPage = 1, initialQuery = '' }: {
  initialMap?: StoryMap | null; initialPage?: number; initialQuery?: string;
}) {
  const searchInput = useRef<HTMLInputElement>(null);
  const locale = useLocale();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const selectionKey = params?.toString() || '';
  const query = params ? params.get('q') || '' : initialQuery;
  const version = params?.get('game_version') || '';
  const questType = Number(params?.get('quest_type') ?? '0');
  const selectedType = questTypes.some((entry) => entry.id === questType) ? questType : 0;
  const requestedPage = storyPageNumber(params ? params.get('page') : initialPage);
  const [map, setMap] = useState<StoryMap | null>(initialMap ?? null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(!initialMap);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    if (initialMap && locale === 'en' && retry === 0 &&
        initialMap.selected_game_version === (version || null) &&
        initialMap.quest_type_id === selectedType) {
      setMap(initialMap);
      setLoading(false);
      setError('');
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError('');
    const search = new URLSearchParams({ locale, quest_type_id: String(selectedType) });
    if (version) search.set('game_version', version);
    api<StoryMap>(`/story-map?${search.toString()}`, { signal: controller.signal })
      .then((result) => {
        if (!controller.signal.aborted) setMap(result);
      })
      .catch((reason: Error) => {
        if (!controller.signal.aborted) setError(reason.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => {
      controller.abort();
    };
  }, [locale, selectedType, version, retry, initialMap]);

  const groupedChapters = useMemo(() => storyChapterGroups(map?.chapters || []), [map]);
  const total = groupedChapters.reduce((sum, group) => sum + group.count, 0);
  const paging = useMemo(() => storyMapPage(groupedChapters, query, requestedPage),
    [groupedChapters, query, requestedPage]);
  const resolvedHash = useRef('');
  const pendingHash = useRef('');

  function pageHref(page: number, searchText = query) {
    const next = new URLSearchParams(selectionKey);
    if (page > 1) next.set('page', String(page));
    else next.delete('page');
    if (searchText.trim()) next.set('q', searchText);
    else next.delete('q');
    return `/story-map${next.size ? `?${next}` : ''}`;
  }

  function setQuery(value: string) {
    // Next synchronizes native history with useSearchParams without refetching RSC.
    // Keep both query and page in the URL; no local reset can override navigation.
    window.history.replaceState(null, '', pageHref(1, value));
  }

  useEffect(() => {
    if (loading) return;
    const reveal = () => {
      const hash = window.location.hash;
      if (!hash || hash === resolvedHash.current) return;
      const matchingPage = storyPageForHash(paging.matches, hash);
      const targetPage = matchingPage ?? storyPageForHash(groupedChapters, hash);
      if (targetPage === null) return;
      resolvedHash.current = hash;
      pendingHash.current = decodeURIComponent(hash).slice(1);
      const heading = document.getElementById(pendingHash.current);
      if (targetPage === paging.page && matchingPage !== null && heading) {
        heading.scrollIntoView({ block: 'start' });
        heading.focus({ preventScroll: true });
        pendingHash.current = '';
      }
      if (targetPage !== paging.page || matchingPage === null)
        navigate(`${pageHref(targetPage, matchingPage === null ? '' : query)}${hash}`, { replace: true });
    };
    const changed = () => { resolvedHash.current = ''; reveal(); };
    reveal();
    window.addEventListener('hashchange', changed);
    return () => window.removeEventListener('hashchange', changed);
  }, [loading, groupedChapters, paging.matches, paging.page, query, selectionKey, navigate]);

  useEffect(() => {
    if (!pendingHash.current) return;
    const frame = requestAnimationFrame(() => {
      const heading = document.getElementById(pendingHash.current);
      if (!heading) return;
      heading.scrollIntoView({ block: 'start' });
      heading.focus({ preventScroll: true });
      pendingHash.current = '';
    });
    return () => cancelAnimationFrame(frame);
  }, [paging.visibleGroups]);

  function updateParam(key: string, value: string) {
    const next = new URLSearchParams(params?.toString() || '');
    if (value) next.set(key, value);
    else next.delete(key);
    next.delete('page');
    if (query.trim()) next.set('q', query);
    else next.delete('q');
    if (setParams) {
      setParams(next);
    }
  }

  return (
    <div className="page-container story-map-page">
      <div className="breadcrumbs">
        <Link to="/">Archive</Link>
        <ChevronRight size={13} aria-hidden="true" />
        <span>Story map</span>
      </div>
      <header className="story-map-hero">
        <div>
          <span className="eyebrow left">TRACE THE STORY SOURCES</span>
          <h1>
            Explore the story<span className="heading-period">.</span>
          </h1>
          <p>
            Read the quest chains recorded in the game data. Open a quest for its scenes, choices,
            and full transcript.
          </p>
        </div>
        <div className="story-map-seal">
          <GitBranch size={31} aria-hidden="true" />
          <span>SOURCE TRACE / QUESTS</span>
        </div>
      </header>
      <div className="story-map-tools">
        <div className="story-map-field">
          <span>Story path</span>
          <Select
            label="Story path"
            value={selectedType}
            onChange={(value) => updateParam('quest_type', value)}
            options={questTypes.map((entry) => ({ value: entry.id, label: entry.label }))}
          />
        </div>
        <div className="story-map-field">
          <span>Patch</span>
          <Select
            label="Patch"
            value={version}
            onChange={(value) => updateParam('game_version', value)}
            options={[
              { value: '', label: 'All patches' },
              ...(map?.imported_game_versions || []).map((item) => ({ value: item, label: item })),
            ]}
          />
        </div>
        <div className="story-map-search">
          <label htmlFor="story-path-search">Find a quest in this path</label>
          <span>
            <Search size={16} aria-hidden="true" />
            <input
              id="story-path-search"
              ref={searchInput}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Quest or chapter name"
            />
            {query && (
              <button
                type="button"
                className="search-clear"
                aria-label="Clear quest search"
                onClick={() => {
                  setQuery('');
                  searchInput.current?.focus();
                }}
              >
                <X size={16} aria-hidden="true" />
              </button>
            )}
          </span>
        </div>
      </div>
      {loading ? (
        <PageLoader />
      ) : error ? (
        <ErrorPanel message={error} onRetry={() => setRetry((value) => value + 1)} />
      ) : (
        map && (
          <>
            <div className="story-map-context" id="story-map-results" tabIndex={-1}>
              <span>
                {paging.total} of {total} source entries ·{' '}
                {version ? `additions in ${version}` : 'all patches in order'}
              </span>
              <p>
                {version
                  ? 'Earlier quests are not repeated in this patch. '
                  : 'Quests are grouped from the earliest imported patch to the latest, without repeats. '}
                Patch assignments follow the first appearance in the available imported data.
                {' '}
                Chapters and links follow the available game data; prerequisite links do not prove
                an immediate next quest.
              </p>
            </div>
            {!map.chapters.length ? (
              <div className="story-map-empty">
                <h2>
                  {version ? `No additions in ${version} for this story path` : 'No source-backed story path'}
                </h2>
                <p>
                  Try another story path or select All story paths to see this patch's other quests.
                  The quest transcript archive is still available.
                </p>
                <Link className="text-link" to="/catalog/quest">
                  Browse quests <ArrowRight size={15} />
                </Link>
              </div>
            ) : paging.total ? (
              paging.visibleGroups.map((group, pageIndex) => (
                <Fragment key={`${group.version}:${group.title}:${group.index}`}>
                  {!version &&
                    (pageIndex === 0 || paging.visibleGroups[pageIndex - 1].version !== group.version) && (
                      <div className="story-patch-marker">
                        First seen in snapshot {group.version}
                      </div>
                    )}
                  <section
                    className="story-chapter"
                    aria-labelledby={`story-chapter-group-${group.index}`}
                  >
                    <div className="story-chapter-heading">
                      <span>{String(group.index + 1).padStart(2, '0')}</span>
                      <div>
                        <small>
                          {group.treeAvailable
                            ? `QUEST TREE CHAPTER ${group.acts[0].id}`
                            : group.unchaptered
                              ? 'QUESTS WITHOUT A CHAPTER'
                              : group.subtitle || 'QUEST CHAPTER'}
                        </small>
                        <h2 id={`story-chapter-group-${group.index}`} tabIndex={-1}>{group.title}</h2>
                      </div>
                      <b>
                        {group.count}{' '}
                        {group.treeAvailable ? 'paths' : 'source quests'}
                      </b>
                    </div>
                    {group.acts.map((act) => (
                      <div key={act.id} className="story-act">
                        {!group.treeAvailable &&
                          !group.unchaptered &&
                          act.act_title !== group.title && (
                            <h3>
                              {[act.act_number, act.act_title].filter(Boolean).join(' · ') ||
                                `Act ${act.id}`}
                            </h3>
                          )}
                        <ol className="story-steps" start={act.start}>
                          {act.nodes.map((node) => (
                            <StoryStep key={node.id} node={node} version={group.version} />
                          ))}
                        </ol>
                      </div>
                    ))}
                  </section>
                </Fragment>
              ))
            ) : (
              <div className="story-map-empty">
                <h2>No matching quests</h2>
                <p>Try another name or story path.</p>
              </div>
            )}
            {paging.total > 0 && (
              <nav className="pagination" aria-label="Story pages">
                {paging.page > 1 && <Link to={`${pageHref(paging.page - 1)}#story-map-results`}>Previous</Link>}
                <span aria-live="polite">
                  {paging.start}–{paging.end} of {paging.total.toLocaleString()} · Page {paging.page} of {paging.pages}
                </span>
                {paging.page < paging.pages && <Link to={`${pageHref(paging.page + 1)}#story-map-results`}>Next</Link>}
              </nav>
            )}
          </>
        )
      )}
    </div>
  );
}
