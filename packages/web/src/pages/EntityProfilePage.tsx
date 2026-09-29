import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowRight, ChevronRight, BookOpen, Sparkles } from "lucide-react";
import { api } from "../api/client";
import { categoryTitle, entityPath, type Entity } from "../data/entities";
import { localizedText } from "../data/localized";
import { useLocale } from "../hooks/useLocale";
import { PanelTitle } from "../components/ui/PanelTitle";
import { ErrorPanel, EmptyInline, PageLoader } from "../components/ui/Feedback";
import { InlineDialogueSearch } from "../components/search/InlineDialogueSearch";
import { PlayerText } from "../components/dialogue/PlayerText";
import { usePlayerDisplay } from "../hooks/usePlayerDisplay";

export function Profile({ kind }: { kind: "character" | "item" | "location" }) {
  const { key = "" } = useParams();
  const locale = useLocale();
  const playerDisplay = usePlayerDisplay();
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showAllRelated, setShowAllRelated] = useState(false);
  useEffect(() => {
    setLoading(true);
    setError("");
    api<any>(
      `/${kind === "character" ? "characters" : kind === "item" ? "items" : "locations"}/${encodeURIComponent(key)}/profile?locale=${locale}`,
    )
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [kind, key, locale]);
  if (loading) return <PageLoader />;
  if (error)
    return (
      <div className="page-container">
        <ErrorPanel message={error} />
      </div>
    );
  const entity = data?.[kind];
  const entityId = entity?.canonical_key?.split(":").at(-1) || key;
  const fallbackName =
    kind === "character"
      ? `Unnamed character · ${entityId}`
      : kind === "item"
        ? `Unnamed item · ${entityId}`
        : `Area · ${entityId}`;
  const name = localizedText(entity?.name) || localizedText(entity?.canonical_name, fallbackName);
  const description = localizedText(entity?.description);
  const relatedByIdentity = new Map<string, any>();
  const unresolvedSpeakerEvidence: any[] = [];
  for (const link of data?.co_present_characters || []) {
    const target = link.character;
    if (target?.canonical_key) {
      relatedByIdentity.set(`co-present:${target.canonical_key}`, {
        ...link,
        target,
        relation: "Shared authored flow state",
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
  const visibleRelated = showAllRelated ? related : related.slice(0, 18);
  const quests = data?.quests_with_dialogue || data?.quest_references || [];
  const itemQuestUses = kind === "item" ? data?.quest_uses || [] : [];
  const characterMaterials = kind === "character" ? data?.progression_materials || [] : [];
  const materialsByItem = new Map<string, { item: any; uses: any[] }>();
  for (const use of characterMaterials) {
    const key = use.item?.canonical_key || `unresolved:${use.item_id}`;
    const group = materialsByItem.get(key) || { item: use.item, uses: [] };
    group.uses.push(use);
    materialsByItem.set(key, group);
  }
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
  const progressionGroups = Array.from(progressionGroupsByCharacter.values()).sort(
    (a, b) => (a.character.label || "").localeCompare(b.character.label || ""),
  );
  const unresolvedProgression = (data?.progression_uses || []).filter(
    (use: any) => !use.character?.canonical_key,
  );
  return (
    <div className="page-container">
      <div className="breadcrumbs">
        <Link to="/">Archive</Link>
        <ChevronRight size={13} />
        <Link to={`/catalog/${kind}`}>{categoryTitle[kind]}</Link>
        <ChevronRight size={13} />
        <span>{name}</span>
      </div>
      <div className="profile-hero">
        <div className={`profile-portrait art-${kind}`}>
          <div className="portrait-rings" />
          <span>
            {kind === "character" ? "✳" : kind === "item" ? "✧" : "⌖"}
          </span>
          <small>{kind.toUpperCase()}</small>
        </div>
        <div className="profile-main">
          <span className="eyebrow left">
            {categoryTitle[kind].toUpperCase()} PROFILE
          </span>
          <h1>
            {name}
            <span className="heading-period">.</span>
          </h1>
          <p>
            {description ||
              (kind === "character"
                ? "A character recorded in the Solaris Atlas story archive."
                : "Explore source-backed details and story links for this entry.")}
          </p>
          <div className="profile-tags">
            <span>{entity?.canonical_key}</span>
            {entity?.playable != null && (
              <span>{entity.playable ? "Playable" : "Non-playable"}</span>
            )}
            {entity?.game_item_id && <span>Item #{entity.game_item_id}</span>}
          </div>
        </div>
        <div className="profile-source">
          <span>ARCHIVE SOURCE</span>
          <strong>{data?.source?.version || "Game data"}</strong>
          <small>{data?.source?.source_file || "Source metadata linked"}</small>
        </div>
      </div>
      <div className="profile-columns">
        <section className="content-panel">
          <PanelTitle
            number="01"
            title={
              kind === "character"
                ? "Story appearances"
                : kind === "item"
                  ? "Where to find"
                  : "Quest references"
            }
            count={kind === "item" ? itemQuestUses.length : quests.length}
          />
          {kind === "item" && data?.harvest_world_maps?.length > 0 && (
            <div className="subsection item-region">
              <h3>Gathering region</h3>
              {data.harvest_world_maps.map((world: any, index: number) => {
                const region = world.named_region;
                const regionName = localizedText(region?.name, `Map ${world.map_id}`);
                const regionLink = region?.canonical_key
                  ? entityPath({ id: 0, canonical_key: region.canonical_key, node_type: "area" })
                  : undefined;
                return (
                  <div className="region-card" key={`${world.map_id}-${index}`}>
                    <div className="region-heading">
                      <span className="connection-index">⌖</span>
                      <div>
                        {regionLink ? <Link to={regionLink}><strong>{regionName}</strong></Link> : <strong>{regionName}</strong>}
                        <small>World map · Map #{world.map_id} · {world.entity_ids?.length || 0} spawn entity references</small>
                      </div>
                      {regionLink && <Link to={regionLink} aria-label={`Open ${regionName}`}><ArrowRight size={15} /></Link>}
                    </div>
                    <p>Raw data locates collection references in this region; it does not provide a verified named sub-area or exact map coordinates.</p>
                  </div>
                );
              })}
            </div>
          )}
          {kind === "item" && itemQuestUses.length > 0 && (
            <div className="subsection">
              <h3>Quest requirements</h3>
              {itemQuestUses.map((use: any, index: number) => {
                const quest = use.quest || {};
                const questId = quest.game_quest_id || quest.canonical_key?.split(":").at(-1);
                return (
                  <Link to={`/quests/${questId}`} className="connection-row" key={`${use.quest_node_key}-${index}`}>
                    <span className="connection-index">{String(index + 1).padStart(2, "0")}</span>
                    <div>
                      <strong>{localizedText(quest.name, `Quest ${questId}`)}</strong>
                      <small>Hand-in · {use.required_count ?? "—"} required · node {use.quest_node_key}</small>
                    </div>
                    <ArrowRight size={16} />
                  </Link>
                );
              })}
              <small className="source-caption">Source: {itemQuestUses[0].source?.file}, row {itemQuestUses[0].source?.row}</small>
            </div>
          )}
          {quests.length ? (
            <div className="list-stack">
              {quests.map((q: any, i: number) => {
                const quest = q.quest || q;
                const id =
                  quest.game_quest_id || quest.canonical_key?.split(":").at(-1);
                return (
                  <Link to={`/quests/${id}`} className="connection-row" key={i}>
                    <span className="connection-index">
                      {String(i + 1).padStart(2, "0")}
                    </span>
                    <div>
                      <strong>
                        {localizedText(quest.name, localizedText(quest.title, `Quest ${id}`))}
                      </strong>
                      <small>
                        {quest.quest_type || "Story quest"}
                        {quest.game_quest_id ? ` · ${quest.game_quest_id}` : ""}
                      </small>
                    </div>
                    <ArrowRight size={16} />
                  </Link>
                );
              })}
            </div>
          ) : kind !== "item" ? (
            <EmptyInline text="No confirmed quest links in this snapshot." />
          ) : null}
          {kind === "character" && (
            <div className="reading-cta">
              <div>
                <BookOpen size={17} />
                <span>Want the whole conversation?</span>
              </div>
              <p>
                Open a quest transcript to read every line in authored order.
              </p>
              {quests[0] && (
                <Link
                  to={`/quests/${quests[0].quest?.game_quest_id || quests[0].game_quest_id}`}
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
              kind === "character"
                ? "Shared scenes & links"
                : kind === "item"
                  ? "Acquisition & locations"
                  : "Places & connections"
            }
            count={kind === "item" ? progressionGroups.length + unresolvedProgression.length : related.length}
          />
          {kind === "character" && related.length ? (
            <div className="note-strip">
              <Sparkles size={15} />
              <span>
                Shared authored flow state — co-presence does not imply a
                personal relationship.
              </span>
            </div>
          ) : null}
          {kind === "item" ? null : related.length ? (
            <div className="list-stack">
              {visibleRelated.map((link: any, i: number) => {
                const target =
                  link.character || link.node || link.target || link.area;
                if (!target) return null;
                const item: Entity = {
                  id: target.id || i,
                  canonical_key: target.canonical_key || target.label,
                  node_type: target.type || "character",
                  category: target.type === "area" ? "location" : undefined,
                  title: target.label || target.canonical_key,
                };
                if (link.shared_scenes) {
                  const profileHref = entityPath(item);
                  return (
                    <article className="shared-person-card" key={target.canonical_key}>
                      <header className="shared-person-heading">
                        <span className="connection-index">↗</span>
                        <div>
                          <strong>{target.label || target.canonical_key}</strong>
                          <small>{link.shared_scenes_total} shared authored flow states · co-presence only</small>
                        </div>
                        <Link to={profileHref} className="shared-person-profile" aria-label={`Open ${target.label || "character"} profile`}>
                          Open profile <ArrowRight size={14} />
                        </Link>
                      </header>
                      <div className="shared-scene-list">
                        {link.shared_scenes.map((scene: any, sceneIndex: number) => (
                          <details className="shared-scene" key={`${scene.flow_state}-${sceneIndex}`}>
                            <summary>
                              <span>{scene.flow_state}</span>
                              <small>{scene.dialogue?.length || 0} dialogue lines</small>
                            </summary>
                            {scene.quest_refs?.map((quest: any) => {
                              const questId = quest.game_quest_id || quest.canonical_key?.split(":").at(-1);
                              return (
                                <Link className="shared-scene-quest" to={`/quests/${questId}`} key={quest.canonical_key || questId}>
                                  {localizedText(quest.name, `Quest ${questId}`)} <ArrowRight size={12} />
                                </Link>
                              );
                            })}
                            <div className="shared-transcript">
                              {(scene.dialogue || []).map((line: any, lineIndex: number) => (
                                <div className="shared-transcript-line" key={`${line.id || lineIndex}`}>
                                  <strong>{line.speaker?.label || line.speaker?.canonical_key || "Unknown speaker"}</strong>
                                  <p><PlayerText display={playerDisplay} value={line.text} fallback={line.text?.inline_text || "Text unavailable in this locale"} /></p>
                                </div>
                              ))}
                              {scene.dialogue_truncated && <small>Transcript shortened; open the quest for the complete authored sequence.</small>}
                            </div>
                            <small className="shared-scene-basis">Evidence: {scene.basis} · {scene.source?.file}</small>
                          </details>
                        ))}
                      </div>
                    </article>
                  );
                }
                return (
                  <Link to={entityPath(item)} className="connection-row" key={i}>
                    <span className="connection-index">↗</span>
                    <div>
                      <strong>{target.label || target.canonical_key}</strong>
                      <small>{(link.relation || link.basis || "Related entry").replaceAll("_", " ")}</small>
                    </div>
                    <ArrowRight size={16} />
                  </Link>
                );
              })}
            </div>
          ) : (
            <EmptyInline text="No confirmed story or entity links in this snapshot." />
          )}
          {kind === "character" && unresolvedSpeakerEvidence.length > 0 && (
            <details className="unresolved-voices">
              <summary>Unresolved speaker IDs in these scenes <span>{unresolvedSpeakerEvidence.length}</span></summary>
              <p>These source speaker IDs have no deterministic character mapping in this snapshot.</p>
              {unresolvedSpeakerEvidence.map((voice: any) => (
                <div className="unresolved-voice-row" key={voice.target.canonical_key}>
                  <strong>{voice.target.label || voice.target.canonical_key}</strong>
                  <small>{(voice.flow_states || []).join(" · ")}</small>
                </div>
              ))}
            </details>
          )}
          {kind === "character" && characterMaterials.length > 0 && (
            <div className="subsection character-materials">
              <h3>Progression materials</h3>
              <div className="list-stack">
                {Array.from(materialsByItem.entries()).map(([key, group]) => (
                  <div className="connection-row character-material-row" key={key}>
                    <span className="connection-index">✧</span>
                    <div>
                      {group.item?.canonical_key ? (
                        <Link to={entityPath({
                          id: group.item.id,
                          canonical_key: group.item.canonical_key,
                          node_type: "item",
                        })}>
                          <strong>{group.item.label}</strong>
                        </Link>
                      ) : (
                        <strong>Unresolved item · {group.uses[0].item_id}</strong>
                      )}
                      <div className="character-material-uses">
                        {group.uses.map((use: any, index: number) => (
                          <span key={`${use.kind}-${use.level_cap}-${use.group_id}-${index}`}>
                            {use.kind === "character_ascension"
                              ? `Lv. ${use.level_cap ?? "—"}`
                              : use.kind.replaceAll("_", " ")}
                            {use.required_count != null ? ` · ${use.required_count}×` : ""}
                          </span>
                        ))}
                      </div>
                    </div>
                    <details className="progression-evidence">
                      <summary>Source</summary>
                      {group.uses.map((use: any, index: number) => (
                        <small key={`${use.source?.file}-${use.source?.row}-${index}`}>
                          {use.source?.file} · row {use.source?.row} · {use.source?.raw_path}
                        </small>
                      ))}
                    </details>
                  </div>
                ))}
              </div>
            </div>
          )}
          {kind === "character" && related.length > 18 && (
            <button
              type="button"
              className="text-button"
              onClick={() => setShowAllRelated((visible) => !visible)}
            >
              {showAllRelated ? "Show fewer links" : `Show all ${related.length} links`}
            </button>
          )}
          {kind === "item" && data?.progression_uses?.length > 0 && (
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
                      canonical_key: character.canonical_key,
                      node_type: "character",
                    });
                    const ascensionUses = uses
                      .filter((use) => use.kind === "character_ascension")
                      .sort((a, b) => (a.level_cap || 0) - (b.level_cap || 0));
                    const otherUses = uses.filter((use) => use.kind !== "character_ascension");
                    const typeLabels: Record<string, string> = {
                      skill_tree_material: "Skill tree",
                      resonator_level_material: "Resonator level-up",
                      resonator_skill_material: "Resonator skills",
                      weapon_breach_material: "Weapon breakthrough",
                      unresolved_progression_material: "Unresolved progression group",
                    };
                    return (
                      <article className="progression-card" key={character.canonical_key}>
                        <header className="progression-card-head">
                          <span className="progression-avatar">{(character.label || "?").slice(0, 1)}</span>
                          <div>
                            <Link to={characterHref}>{character.label || character.canonical_key}</Link>
                            <small>Resonator · {uses.length} source {uses.length === 1 ? "entry" : "entries"}</small>
                          </div>
                          <ArrowRight size={14} />
                        </header>
                        {ascensionUses.length > 0 && (
                          <div className="progression-track">
                            <div className="progression-track-title">
                              <span>Resonator ascension</span>
                              <b>{ascensionUses.reduce((sum, use) => sum + (use.required_count || 0), 0)}× total</b>
                            </div>
                            <div className="progression-levels">
                              {ascensionUses.map((use, index) => (
                                <span className="progression-level-chip" key={`${use.level_cap}-${index}`}>
                                  <small>Lv. {use.level_cap}</small><b>{use.required_count}×</b>
                                </span>
                              ))}
                            </div>
                          </div>
                        )}
                        {otherUses.map((use, index) => (
                          <div className="progression-track" key={`${use.kind}-${use.item_group_id}-${index}`}>
                            <div className="progression-track-title">
                              <span>{typeLabels[use.kind] || "Progression material"}</span>
                              <b>{use.required_count}×</b>
                            </div>
                            <small className="progression-group-id">Group {use.item_group_id}</small>
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
                      <span><strong>Group {use.item_group_id}</strong><small>{use.kind === "unresolved_progression_material" ? "No exact character mapping in this snapshot" : "Character record unavailable"}</small></span>
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
          {kind === "item" && data?.shop_offers?.length > 0 && (
            <div className="subsection">
              <h3>Shop offers</h3>
              {data.shop_offers.map((offer: any, index: number) => (
                <div className="plain-row" key={`${offer.offer_id}-${index}`}>
                  <span>
                    <strong>{localizedText(offer.shop_name, `Shop #${offer.shop_id}`)}</strong>
                    <small>{offer.prices?.map((price: any) => `${price.amount} ${price.item?.label || `currency #${price.item_id}`}`).join(" + ")} · limit {offer.purchase_limit ?? "—"}</small>
                  </span>
                  <small>{offer.item_count}× per purchase</small>
                </div>
              ))}
              <small className="source-caption">Exact item offer in {data.shop_offers[0].source?.file}, row {data.shop_offers[0].source?.row}.</small>
            </div>
          )}
          {kind === "item" && data?.acquisition_paths?.length > 0 && (
            <div className="subsection">
              <h3>Ways to obtain</h3>
              {data.acquisition_paths.map((a: any, i: number) => (
                <div className="plain-row" key={i}>
                  <span>{localizedText(a.description, `Access path ${a.id}`)}</span>
                  <small>Source-linked</small>
                </div>
              ))}
            </div>
          )}
          {kind === "location" && data?.hierarchy?.parent && (
            <div className="subsection">
              <h3>Parent location</h3>
              <Link
                className="plain-row"
                to={entityPath({
                  id: 0,
                  canonical_key: data.hierarchy.parent.canonical_key,
                  node_type: data.hierarchy.parent.type,
                })}
              >
                {data.hierarchy.parent.label}
                <ArrowRight size={14} />
              </Link>
            </div>
          )}
          {kind === "location" && data?.hierarchy?.children?.length > 0 && (
            <div className="subsection">
              <h3>Sub-areas</h3>
              {data.hierarchy.children.map((child: any, index: number) => (
                <Link
                  className="plain-row"
                  key={child.canonical_key || index}
                  to={entityPath({
                    id: child.id,
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
      {kind === "character" && (
        <section className="content-panel full-panel">
          <PanelTitle number="03" title="Dialogue search" />
          <InlineDialogueSearch character={key} quests={quests} playerDisplay={playerDisplay} />
        </section>
      )}
    </div>
  );
}
