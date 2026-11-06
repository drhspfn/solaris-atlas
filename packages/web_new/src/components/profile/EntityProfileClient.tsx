'use client';

import { ArrowRight, BookOpen, ChevronRight } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api, apiUrl } from '@/api/client';
import type { CharacterModelData } from '@/components/models/types';
import { CharacterMediaProvider } from '@/components/characters/CharacterMedia';
import '@/styles/character-profile.css';
import { CharacterProfileVisual } from '@/components/characters/CharacterProfileVisual';
import { CharacterModelImport } from '@/components/characters/CharacterModelImport';
import { NpcProfile } from '@/components/profile/NpcProfile';
import {
  CharacterArchive,
  type CharacterArchiveData,
} from '@/components/characters/CharacterArchive';
import { CharacterConnections } from '@/components/characters/CharacterConnections';
import { ItemAcquisition } from '@/components/items/ItemAcquisition';
import { InlineDialogueSearch } from '@/components/search/InlineDialogueSearch';
import { EmptyInline, ErrorPanel, PageLoader } from '@/components/ui/Feedback';
import { PanelTitle } from '@/components/ui/PanelTitle';
import { Select } from '@/components/ui/Select';
import { APP_SETTINGS } from '@/config/settings';
import { categoryTitle, type Entity, entityPath } from '@/data/entities';
import { characterPortrait } from '@/data/characterArtwork';
import { selectCharacterVariant, type CharacterVariant } from '@/data/characterIdentity';
import { localizedText } from '@/data/localized';
import { useLocale } from '@/hooks/useLocale';
import { usePlayerDisplay } from '@/hooks/usePlayerDisplay';
import { Link } from '@/lib/router';
import { questPath } from '@/lib/routeKey';

export function EntityProfileClient({
  kind,
  entityKey,
  initialData,
  initialArchive,
  initialModel,
  gameVersion,
}: {
  kind: 'character' | 'item' | 'location';
  entityKey: string;
  initialData?: any;
  initialArchive?: CharacterArchiveData | null;
  initialModel?: CharacterModelData | null;
  gameVersion?: string;
}) {
  const locale = useLocale();
  const playerDisplay = usePlayerDisplay();
  const [selectedRole, setSelectedRole] = useState(entityKey);
  const [profileRole, setProfileRole] = useState(entityKey);
  const [profileLocale, setProfileLocale] = useState('en');
  const [data, setData] = useState<any>(initialData || null);
  const [loading, setLoading] = useState(!initialData);
  const [error, setError] = useState('');
  const [archive, setArchive] = useState<CharacterArchiveData | null>(initialArchive ?? null);
  const [archiveError, setArchiveError] = useState('');
  const [modelRevision, setModelRevision] = useState(0);
  const [profileRetry, setProfileRetry] = useState(0);
  const [archiveRetry, setArchiveRetry] = useState(0);

  useEffect(() => { setSelectedRole(entityKey); }, [entityKey]);

  useEffect(() => {
    if (initialData && selectedRole === entityKey && locale === 'en' && profileRetry === 0) {
      setData(initialData);
      setProfileRole(entityKey);
      setProfileLocale('en');
      setLoading(false);
      setError('');
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError('');
    api<any>(
      `/${kind === 'character' ? 'characters' : kind === 'item' ? 'items' : 'locations'}/${encodeURIComponent(selectedRole)}/profile?${kind === 'character' ? `connections_limit=12&${gameVersion ? `game_version=${encodeURIComponent(gameVersion)}&` : ''}` : ''}locale=${locale}`,
      { signal: controller.signal },
    )
      .then((result) => {
        if (!controller.signal.aborted) { setData(result); setProfileRole(selectedRole); setProfileLocale(locale); }
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [kind, entityKey, selectedRole, locale, initialData, profileRetry, gameVersion]);

  useEffect(() => {
    if (initialArchive && selectedRole === entityKey && locale === 'en' && archiveRetry === 0) {
      setArchive(initialArchive);
      setArchiveError('');
      return;
    }
    setArchive(null);
    setArchiveError('');
    if (kind !== 'character' || data?.npc || selectedRole.startsWith('character:npc:')) return;
    const controller = new AbortController();
    api<CharacterArchiveData>(
      `/characters/${encodeURIComponent(selectedRole)}/archive?${gameVersion ? `game_version=${encodeURIComponent(gameVersion)}&` : ''}locale=${encodeURIComponent(locale)}`,
      { signal: controller.signal },
    )
      .then((result) => {
        if (!controller.signal.aborted) setArchive(result);
      })
      .catch((cause: Error) => {
        if (!controller.signal.aborted) setArchiveError(cause.message);
      });
    return () => controller.abort();
  }, [kind, entityKey, selectedRole, locale, initialArchive, archiveRetry, gameVersion]);

  useEffect(() => {
    const family = data?.family;
    if (family?.selected_key && family.selected_key !== data?.character?.canonical_key) {
      setSelectedRole(family.selected_key);
    }
  }, [data]);

  if (loading && !data) return <PageLoader />;
  if (error && !data)
    return (
      <div className="page-container">
        <ErrorPanel message={error} onRetry={() => setProfileRetry((value) => value + 1)} />
      </div>
    );

  if (data?.npc) return <NpcProfile key={data.character.canonical_key} data={data} initialModel={initialModel} gameVersion={gameVersion} />;

  const entity = data?.[kind];
  const entityId = entity?.canonical_key?.split(':').at(-1) || entityKey;
  const fallbackName =
    kind === 'character'
      ? `Unnamed character · ${entityId}`
      : kind === 'item'
        ? `Unnamed item · ${entityId}`
        : `Area · ${entityId}`;
  const family = data?.family;
  const variants: CharacterVariant[] = family?.variants || [];
  const selectedVariant = variants.find((variant) => variant.canonical_key === family?.selected_key);
  const name = family?.name || localizedText(entity?.name) || localizedText(entity?.canonical_name, fallbackName);
  const description = localizedText(entity?.description);
  const relatedByIdentity = new Map<string, any>();
  const unresolvedSpeakerEvidence: any[] = [];
  for (const link of data?.co_present_characters || []) {
    const target = link.character;
    if (target?.canonical_key) {
      relatedByIdentity.set(`co-present:${target.canonical_key}`, {
        ...link,
        target,
        relation: 'Shared authored flow state',
        speaker_evidence: [],
      });
    }
  }
  for (const link of data?.co_present_speakers || []) {
    const target = link.speaker;
    if (target?.canonical_key) {
      const mappedCharacters = link.character_entities || [];
      const matched = mappedCharacters
        .map((character: any) => relatedByIdentity.get(`co-present:${character.canonical_key}`))
        .filter(Boolean);
      if (matched.length) {
        for (const characterLink of matched) {
          characterLink.speaker_evidence.push({
            speaker: target,
            flow_states: link.flow_states || [],
          });
        }
      } else {
        unresolvedSpeakerEvidence.push({
          ...link,
          target,
        });
      }
    }
  }
  for (const link of data?.explicit_links || []) {
    const target = link.target;
    if (target?.canonical_key) {
      relatedByIdentity.set(`explicit:${link.relation}:${target.canonical_key}`, link);
    }
  }
  const related = Array.from(relatedByIdentity.values());
  const visibleRelated = related.slice(0, APP_SETTINGS.limits.relatedPreview);
  const quests = data?.connections_preview?.quests?.results || data?.quests_with_dialogue || data?.quest_references || [];
  const itemQuestUses = kind === 'item' ? data?.quest_uses || [] : [];
  const characterMaterials = kind === 'character' ? data?.progression_materials || [] : [];
  const portraitUrl =
    archive ? characterPortrait(archive.artwork) : data?.media?.image_url;
  const progressionGroupsByCharacter = new Map<string, { character: any; uses: any[] }>();
  for (const use of data?.progression_uses || []) {
    const characterKey = use.character?.canonical_key;
    if (!characterKey) continue;
    const group = progressionGroupsByCharacter.get(characterKey) || {
      character: use.character,
      uses: [],
    };
    group.uses.push(use);
    progressionGroupsByCharacter.set(characterKey, group);
  }
  const progressionGroups = Array.from(progressionGroupsByCharacter.values()).sort((a, b) =>
    (a.character.label || '').localeCompare(b.character.label || ''),
  );
  const unresolvedProgression = (data?.progression_uses || []).filter(
    (use: any) => !use.character?.canonical_key,
  );

  const introduction = description || (kind === 'character' ? archive?.biography?.replace(/<[^>]*>/g, '').trim() : '') ||
    (kind === 'character' ? 'A character recorded in the Solaris Atlas story archive.' : 'Explore source-backed details and story links for this entry.');

  const content = (
    <div className={`page-container${kind === 'character' ? ' character-profile-page' : ''}`}>
      {error && <ErrorPanel message={error} onRetry={() => setProfileRetry((value) => value + 1)} />}
      {loading && <p role="status">Updating archive record…</p>}
      <div className="breadcrumbs">
        <Link to="/">Archive</Link>
        <ChevronRight size={13} />
        <Link to={`/catalog/${kind}`}>{categoryTitle[kind]}</Link>
        <ChevronRight size={13} />
        <span>{name}</span>
      </div>
      <div className={`profile-hero${kind === 'character' ? ' character-profile-hero' : ''}`}>
        {kind === 'character' ? (
          <CharacterProfileVisual key={entity?.canonical_key ?? selectedRole} characterKey={entity?.canonical_key ?? selectedRole} name={name} portraitUrl={portraitUrl}
            initialModel={selectedRole === entityKey ? initialModel : null} refreshKey={modelRevision} />
        ) : (
          <div className={`profile-portrait art-${kind}`}>
            {portraitUrl && <img className="character-portrait-image" src={apiUrl(portraitUrl.replace(/^\/api/, ''))} alt={`${name} portrait`} />}
            <div className="portrait-rings" />
            {!portraitUrl && <span>{kind === 'item' ? '✧' : '⌖'}</span>}
            <small>{kind.toUpperCase()}</small>
          </div>
        )}
        <div className="profile-main">
          <span className="eyebrow left">{categoryTitle[kind].toUpperCase()} PROFILE</span>
          <div className="character-profile-title-row">
            <h1>{name}<span className="heading-period">.</span></h1>
            {kind === 'character' && entity?.canonical_key && (
              <CharacterModelImport characterKey={entity.canonical_key} variant="actions"
                onComplete={() => setModelRevision((value) => value + 1)} />
            )}
          </div>
          <p>{introduction}</p>
          {selectedVariant && (
            <div className="rover-variant-controls" aria-label="Rover appearance and element">
              <div>
                <span>Gender</span>
                <Select label="Rover gender" value={selectedVariant.gender}
                  options={Array.from(new Set(variants.map((variant) => variant.gender))).map((gender) => ({value: gender, label: gender === 'male' ? 'Male' : 'Female', disabled: !selectCharacterVariant(variants, selectedVariant, { gender })}))}
                  disabled={loading}
                  onChange={(gender) => {
                    const next = selectCharacterVariant(variants, selectedVariant, { gender });
                    if (next) setSelectedRole(next.canonical_key);
                  }} />
              </div>
              <div>
                <span>Attribute</span>
                <Select label="Rover attribute" value={selectedVariant.element_id}
                  options={variants.filter((variant) => variant.gender === selectedVariant.gender).map((variant) => ({value: variant.element_id, label: localizedText(variant.element, `Attribute ${variant.element_id}`)}))}
                  disabled={loading}
                  onChange={(element) => {
                    const next = selectCharacterVariant(variants, selectedVariant, { element_id: Number(element) });
                    if (next) setSelectedRole(next.canonical_key);
                  }} />
              </div>
            </div>
          )}
          <div className="profile-tags">
            <span>{entity?.canonical_key}</span>
            {entity?.playable != null && (
              <span>{entity.playable ? 'Playable' : 'Non-playable'}</span>
            )}
            {entity?.game_item_id && <span>Item #{entity.game_item_id}</span>}
          </div>
        </div>
        {kind !== 'character' && <div className="profile-source">
          <span>ARCHIVE SOURCE</span>
          <strong>{data?.source?.version || 'Game data'}</strong>
          <small>{data?.source?.source_file || 'Source metadata linked'}</small>
        </div>}
      </div>
      {kind === 'character' &&
        (archive && profileRole === selectedRole ? (
          <CharacterArchive
            key={`archive:${selectedRole}`}
            archive={archive}
            introduction={introduction}
            materials={characterMaterials}
            locale={locale}
            characterKey={selectedRole}
            gameVersion={gameVersion}
          />
        ) : archiveError ? (
          <ErrorPanel message={`Character details unavailable: ${archiveError}`}
            onRetry={() => setArchiveRetry((value) => value + 1)} />
        ) : (
          <div className="character-archive-loading" role="status">Loading character details…</div>
        ))}
      <div className="profile-columns">
        <section className="content-panel">
          <PanelTitle
            number="01"
            title={
              kind === 'character'
                ? 'Story appearances'
                : kind === 'item'
                  ? 'Where to obtain'
                  : 'Quest references'
            }
            count={kind === 'item' ? undefined : kind === 'character' ? data?.connections_summary?.quests?.total ?? quests.length : quests.length}
          />
          {kind === 'item' && <ItemAcquisition data={data} locale={locale} />}
          {kind === 'character' ? (
            profileRole === selectedRole && profileLocale === locale ? (
              <CharacterConnections key={`quests-${selectedRole}-${locale}-${gameVersion || entity?.game_version || "current"}`}
                characterKey={selectedRole} section="quests" locale={locale}
                gameVersion={gameVersion || entity?.game_version} initialRows={quests}
                summary={data?.connections_preview?.quests || data?.connections_summary?.quests} playerDisplay={playerDisplay} />
            ) : <p role="status">Loading story appearances…</p>
          ) : kind !== 'item' && quests.length ? (
            <div className="list-stack">
              {quests.map((q: any, i: number) => {
                const quest = q.quest || q;
                const id = quest.game_quest_id || quest.canonical_key?.split(':').at(-1);
                return (
                  <Link to={questPath(quest)} className="connection-row" key={i}>
                    <span className="connection-index">{String(i + 1).padStart(2, '0')}</span>
                    <div>
                      <strong>
                        {localizedText(quest.name, localizedText(quest.title, `Quest ${id}`))}
                      </strong>
                      <small>
                        {quest.classification?.kind && quest.classification.kind !== 'unclassified'
                          ? quest.classification.label : quest.quest_type || 'Quest record'}
                        {quest.game_quest_id ? ` · ${quest.game_quest_id}` : ''}
                      </small>
                    </div>
                    <ArrowRight size={16} />
                  </Link>
                );
              })}
            </div>
          ) : kind !== 'item' ? (
            <EmptyInline text="No confirmed quest links in this snapshot." />
          ) : null}
          {kind === 'character' && (
            <div className="reading-cta">
              <div>
                <BookOpen size={17} />
                <span>Want the whole conversation?</span>
              </div>
              <p>Open a quest transcript to read every line in authored order.</p>
              {quests[0] && (
                <Link
                  to={questPath(quests[0].quest || quests[0])}
                  className="small-link"
                >
                  Read a transcript <ArrowRight size={14} />
                </Link>
              )}
            </div>
          )}
        </section>
        <section className="content-panel">
          <PanelTitle
            number="02"
            title={
              kind === 'character'
                ? 'Shared scenes & links'
                : kind === 'item'
                  ? 'Uses & connections'
                  : 'Places & connections'
            }
            count={
              kind === 'item'
                ? progressionGroups.length + unresolvedProgression.length
                : kind === 'character' ? data?.connections_summary?.characters?.total ?? related.length : related.length
            }
          />
          {kind === 'item' && itemQuestUses.length > 0 && (
            <div className="subsection">
              <h3>Quest requirements</h3>
              {itemQuestUses.map((use: any, index: number) => {
                const quest = use.quest || {};
                const questId = quest.game_quest_id || quest.canonical_key?.split(':').at(-1);
                return (
                  <Link
                    to={questPath(quest)}
                    className="connection-row"
                    key={`${use.quest_node_key}-${index}`}
                  >
                    <span className="connection-index">{String(index + 1).padStart(2, '0')}</span>
                    <div>
                      <strong>{localizedText(quest.name, `Quest ${questId}`)}</strong>
                      <small>
                        Hand-in · {use.required_count ?? '—'} required · node {use.quest_node_key}
                      </small>
                    </div>
                    <ArrowRight size={16} />
                  </Link>
                );
              })}
              <small className="source-caption">
                Source: {itemQuestUses[0].source?.file}, row {itemQuestUses[0].source?.row}
              </small>
            </div>
          )}
          {kind === 'item' && quests.length > 0 && <h3 className="subsection">Quest references</h3>}
          {kind === 'item' && (
            <>
              {quests.length ? (
                <div className="list-stack">
                  {quests.map((q: any, i: number) => {
                    const quest = q.quest || q;
                    const id = quest.game_quest_id || quest.canonical_key?.split(':').at(-1);
                    return (
                      <Link to={questPath(quest)} className="connection-row" key={i}>
                        <span className="connection-index">{String(i + 1).padStart(2, '0')}</span>
                        <div>
                          <strong>
                            {localizedText(quest.name, localizedText(quest.title, `Quest ${id}`))}
                          </strong>
                          <small>
                            {quest.classification?.kind && quest.classification.kind !== 'unclassified'
                              ? quest.classification.label : quest.quest_type || 'Quest record'}
                            {quest.game_quest_id ? ` · ${quest.game_quest_id}` : ''}
                          </small>
                          {q.references?.map((reference: any, index: number) => reference.interpretation && (
                            <small key={index}>{reference.interpretation.label}
                              {reference.interpretation.is_reward === false ? ' · not a reward' : ''}
                            </small>
                          ))}
                        </div>
                        <ArrowRight size={16} />
                      </Link>
                    );
                  })}
                </div>
              ) : null}
            </>
          )}
          {kind === 'character' ? (
            profileRole === selectedRole && profileLocale === locale ? (
              <CharacterConnections key={`characters-${selectedRole}-${locale}-${gameVersion || entity?.game_version || "current"}`}
                characterKey={selectedRole} section="characters" locale={locale}
                gameVersion={gameVersion || entity?.game_version}
                initialRows={data?.connections_preview?.characters?.results || [...related, ...unresolvedSpeakerEvidence]}
                summary={data?.connections_preview?.characters || data?.connections_summary?.characters} playerDisplay={playerDisplay} />
            ) : <p role="status">Loading shared scenes and links…</p>
          ) : kind === 'item' ? null : related.length ? (
            <div className="list-stack">
              {visibleRelated.map((link: any, i: number) => {
                const target = link.character || link.node || link.target || link.area;
                if (!target) return null;
                const item: Entity = {
                  id: target.id || i, canonical_key: target.canonical_key || target.label,
                  slug: target.slug, href: target.href, node_type: target.type || 'character',
                  category: target.type === 'area' ? 'location' : undefined,
                  title: target.label || target.canonical_key,
                };
                return <Link to={entityPath(item)} className="connection-row" key={i}>
                  <span className="connection-index">↗</span>
                  <div><strong>{target.label || target.canonical_key}</strong>
                    <small>{(link.relation || link.basis || 'Related entry').replaceAll('_', ' ')}</small></div>
                  <ArrowRight size={16} />
                </Link>;
              })}
            </div>
          ) : <EmptyInline text="No confirmed story or entity links in this snapshot." />}
          {kind === 'item' && data?.progression_uses?.length > 0 && (
            <div className="subsection">
              <div className="progression-heading">
                <h3>Used in progression</h3>
                <span>{progressionGroups.length} characters</span>
              </div>
              {progressionGroups.length > 0 && (
                <div className="progression-grid">
                  {progressionGroups.map(({ character, uses }) => {
                    const characterHref = entityPath({
                      id: character.id,
                      slug: character.slug,
                      href: character.href,
                      canonical_key: character.canonical_key,
                      node_type: 'character',
                    });
                    const ascensionUses = uses
                      .filter((use) => use.kind === 'character_ascension')
                      .sort((a, b) => (a.level_cap || 0) - (b.level_cap || 0));
                    const otherUses = uses.filter((use) => use.kind !== 'character_ascension');
                    const typeLabels: Record<string, string> = {
                      skill_tree_material: 'Skill tree',
                      resonator_level_material: 'Resonator level-up',
                      resonator_skill_material: 'Resonator skills',
                      weapon_breach_material: 'Weapon breakthrough',
                      unresolved_progression_material: 'Unresolved progression group',
                    };
                    return (
                      <article className="progression-card" key={character.canonical_key}>
                        <header className="progression-card-head">
                          <span className="progression-avatar">
                            {(character.label || '?').slice(0, 1)}
                          </span>
                          <div>
                            <Link to={characterHref}>
                              {character.label || character.canonical_key}
                            </Link>
                            <small>
                              Resonator
                              {ascensionUses.length > 0 && (
                                <> · Lv. {ascensionUses[0].level_cap}
                                  {ascensionUses.length > 1 && `–${ascensionUses.at(-1)?.level_cap}`}
                                </>
                              )}
                            </small>
                          </div>
                          <ArrowRight size={14} />
                        </header>
                        {ascensionUses.length > 0 && (
                          <div className="progression-track">
                            <div className="progression-track-title">
                              <span>Resonator ascension</span>
                              <b>
                                {ascensionUses.reduce(
                                  (sum, use) => sum + (use.required_count || 0),
                                  0,
                                )}
                                × total
                              </b>
                            </div>
                            <div className="progression-levels">
                              {ascensionUses.map((use, index) => (
                                <span
                                  className="progression-level-chip"
                                  key={`${use.level_cap}-${index}`}
                                >
                                  <small>Lv. {use.level_cap}</small>
                                  <b>{use.required_count}×</b>
                                </span>
                              ))}
                            </div>
                          </div>
                        )}
                        {otherUses.map((use, index) => (
                          <div
                            className="progression-track"
                            key={`${use.kind}-${use.item_group_id}-${index}`}
                          >
                            <div className="progression-track-title">
                              <span>{typeLabels[use.kind] || 'Progression material'}</span>
                              <b>{use.required_count}×</b>
                            </div>
                            <small className="progression-group-id">
                              Group {use.item_group_id}
                            </small>
                          </div>
                        ))}
                        <details className="progression-evidence">
                          <summary>Source records</summary>
                          {uses.map((use, index) => (
                            <small key={`${use.source?.file}-${use.source?.row}-${index}`}>
                              {use.source?.file} · row {use.source?.row} · {use.source?.raw_path}
                            </small>
                          ))}
                        </details>
                      </article>
                    );
                  })}
                </div>
              )}
              {unresolvedProgression.length > 0 && (
                <div className="unresolved-progression">
                  <div className="progression-heading">
                    <h3>Unresolved source groups</h3>
                    <span>{unresolvedProgression.length}</span>
                  </div>
                  {unresolvedProgression.map((use: any, index: number) => (
                    <div className="unresolved-group-row" key={`${use.item_group_id}-${index}`}>
                      <span>
                        <strong>Group {use.item_group_id}</strong>
                        <small>
                          {use.kind === 'unresolved_progression_material'
                            ? 'No exact character mapping in this snapshot'
                            : 'Character record unavailable'}
                        </small>
                      </span>
                      <b>{use.required_count}×</b>
                    </div>
                  ))}
                  <details className="progression-evidence">
                    <summary>Source records</summary>
                    {unresolvedProgression.map((use: any, index: number) => (
                      <small key={`${use.source?.file}-${use.source?.row}-${index}`}>
                        {use.source?.file} · row {use.source?.row} · {use.source?.raw_path}
                      </small>
                    ))}
                  </details>
                </div>
              )}
            </div>
          )}
          {kind === 'location' && data?.hierarchy?.parent && (
            <div className="subsection">
              <h3>Parent location</h3>
              <Link
                className="plain-row"
                to={entityPath({
                  id: 0,
                  slug: data.hierarchy.parent.slug,
                  href: data.hierarchy.parent.href,
                  canonical_key: data.hierarchy.parent.canonical_key,
                  node_type: data.hierarchy.parent.type,
                })}
              >
                {data.hierarchy.parent.label}
                <ArrowRight size={14} />
              </Link>
            </div>
          )}
          {kind === 'location' && data?.hierarchy?.children?.length > 0 && (
            <div className="subsection">
              <h3>Sub-areas</h3>
              {data.hierarchy.children.map((child: any, index: number) => (
                <Link
                  className="plain-row"
                  key={child.canonical_key || index}
                  to={entityPath({
                    id: child.id,
                    slug: child.slug,
                    href: child.href,
                    canonical_key: child.canonical_key,
                    node_type: child.type,
                  })}
                >
                  {child.label || child.canonical_key}
                  <ArrowRight size={14} />
                </Link>
              ))}
            </div>
          )}
        </section>
      </div>
      {kind === 'character' && (
        <section className="content-panel full-panel">
          <PanelTitle number="03" title="Dialogue search" />
          <InlineDialogueSearch character={entityKey} quests={quests} playerDisplay={playerDisplay} />
        </section>
      )}
    </div>
  );
  return kind === 'character' ? (
    <CharacterMediaProvider key={entity?.canonical_key ?? selectedRole} archive={profileRole === selectedRole ? archive : null} portraitUrl={portraitUrl} name={name}>
      {content}
    </CharacterMediaProvider>
  ) : content;
}
