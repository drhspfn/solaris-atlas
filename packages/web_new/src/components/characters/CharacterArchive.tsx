"use client";

import { Skeleton, SkeletonGroup, TextSkeleton } from "../ui/Skeleton";
import {
  BookOpen,
  ChevronDown,
  Image,
  Music2,
  Sparkles,
  Swords,
} from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { Link } from "@/lib/router";

import { api, apiUrl } from "../../api/client";
import { entityPath } from "../../data/entities";
import { gameTextPlainText } from "@/data/gameText";
import { availableLocales } from "../../data/locales";
import { PlayerText, type PlayerDisplay } from "@/components/dialogue/PlayerText";
import { Select } from "@/components/ui/Select";
import { TrackPlayer } from "@/components/ui/TrackPlayer";
import { CharacterMediaGallery, useCharacterMedia } from "@/components/characters/CharacterMedia";
import type { AnimationPreview } from "@/components/ui/animationPreview";
import "@/styles/character-voice-lines.css";
import "@/styles/character-talents.css";
import "@/styles/character-archive-layout.css";

export interface CharacterArchiveData {
  trial_spine?: {
    url: string;
    file_id: number;
    animation_preview?: AnimationPreview | null;
  } | null;
  biography: string | null;
  profile: {
    country: string | null;
    birthday: string | null;
    affiliation: string | null;
    talent: string | null;
    talent_description: string | null;
    voice_actors: Record<string, string | null>;
    max_level: number | null;
    element_id: number | null;
    weapon_type_id: number | null;
    quality_id: number | null;
    element: string | null;
    quality: string | null;
  };
  artwork: {
    kind: string;
    engine_path: string;
    url: string | null;
    scope?: "character" | "shared_resource";
  }[];
  skills: {
    id: number;
    type: number;
    order: number;
    name: string | null;
    description: string | null;
    description_params?: string[];
    max_level: number;
    attributes?: {
      id: number;
      order: number;
      name: string | null;
      description: string | null;
      parameters: string[][];
      values: string[];
      source: unknown;
    }[];
    icon: { engine_path: string | null; url: string | null };
  }[];
  skill_tree: {
    id: number;
    skill_id: number;
    order: number;
    type: number;
    parent_nodes: number[];
    costs: {
      item_id: number;
      count: number;
      item: { canonical_key: string; name: string } | null;
    }[];
    title: string | null;
    description: string | null;
  }[];
  resonance_chain: {
    id: number;
    order: number;
    name: string | null;
    description: string | null;
    description_params: string[];
    icon: { engine_path: string | null; url: string | null };
  }[];
  voice_lines: {
    id: number;
    order: number;
    title: string | null;
    text: string | null;
    audio_event_path: string | null;
    audio_url: string | null;
    audio_tracks?: Record<string, string>;
    audio_variants?: {
      id: string;
      source_name: string;
      language: string;
      url: string | null;
      status: 'available' | 'missing';
    }[];
  }[];
  other_voice_events: { category: string; event: string }[];
}

type MaterialUse = {
  item_id: number;
  item?: {
    id: number;
    canonical_key: string;
    label: string;
    slug?: string | null;
    href?: string;
  };
  kind: string;
  level_cap?: number;
  required_count?: number;
  group_id?: number;
};

function hasVoiceLine(line: CharacterArchiveData["voice_lines"][number]) {
  return !!(line.title?.trim() || line.text?.trim() || line.audio_event_path || line.audio_url ||
    Object.values(line.audio_tracks ?? {}).some(Boolean) || line.audio_variants?.length);
}

function artworkLabel(art: CharacterArchiveData["artwork"][number]) {
  if (art.scope === "shared_resource") {
    if (art.kind === "RolePortrait") return "Shared portrait reference";
    return art.kind === "RoleStand"
      ? "Shared quest artwork"
      : "Shared resource icon";
  }
  return art.kind.replaceAll(/([a-z])([A-Z])/g, "$1 $2");
}

function gameText(
  value: string | null | undefined,
  params: string[] = [],
  preserveMarkup = false,
  display?: PlayerDisplay,
): string {
  if (!value) return "Text unavailable in this language.";
  const formatted = value.replace(
      /\{(\d+)\}/g,
      (match, index: string) => params[Number(index)] ?? match,
    )
    .trim();
  return preserveMarkup ? formatted : gameTextPlainText(formatted, display);
}

function talentText(
  value: string | null | undefined,
  params: string[] = [],
): string {
  return gameText(value, params, true)
    .replace(
      /\{Cus:Ipt,Touch=(?:Tap|tap) PC=(Press|press) Gamepad=(?:Press|press)\}/g,
      "$1",
    )
    .replace(/\{\d+[^}]*\}/g, "—");
}

const voiceLocales = availableLocales.filter(({ code }) =>
  ["en", "ja", "zh-Hans", "ko"].includes(code),
);
const initialVoiceLocale = (locale: string) =>
  voiceLocales.some((option) => option.code === locale) ? locale : "en";

export function CharacterVoicePlayback({ line, textLocale, display }: {
  line: CharacterArchiveData['voice_lines'][number];
  textLocale: string;
  display?: PlayerDisplay;
}) {
  const [choice, setChoice] = useState('');
  const label = gameText(line.title, [], false, display);
  const languageLabel = (language: string) =>
    availableLocales.find((option) => option.code === language)?.label ?? language;
  const mediaUrl = (url: string) => apiUrl(url.replace(/^\/api/, ''));
  if (!line.audio_variants?.length) return <TrackPlayer
    src={line.audio_url ? mediaUrl(line.audio_url) : null}
    tracks={line.audio_tracks ? Object.fromEntries(Object.entries(line.audio_tracks)
      .map(([language, url]) => [languageLabel(language), mediaUrl(url)])) : undefined}
    defaultTrack={languageLabel(initialVoiceLocale(textLocale))}
    label={label}
  />;

  // Authored alternatives can be random/state branches, not gender or a default.
  const counts = new Map<string, number>();
  const options = [...line.audio_variants].sort((a, b) =>
    a.language.localeCompare(b.language) || a.source_name.localeCompare(b.source_name),
  ).map((variant) => {
    const number = (counts.get(variant.language) ?? 0) + 1;
    counts.set(variant.language, number);
    const available = variant.status === 'available' && !!variant.url;
    return {
      value: variant.source_name,
      label: `${languageLabel(variant.language)} · Variant ${number}${available ? '' : ' · unavailable'}`,
      disabled: !available,
      url: available ? mediaUrl(variant.url!) : null,
    };
  });
  // A different language may still have one proven legacy track.
  for (const [language, url] of Object.entries(line.audio_tracks ?? {})) {
    if (!counts.has(language)) options.push({
      value: `track:${language}`, label: languageLabel(language), disabled: false, url: mediaUrl(url),
    });
  }
  const selected = options.find((option) => option.value === choice && !option.disabled);
  return <div className="character-voice-variants">
    <Select label={`Audio variant: ${label}`} placeholder="Choose language & variant"
      value={selected?.value ?? ''} onChange={setChoice} options={options} />
    {selected && <TrackPlayer key={selected.value} src={selected.url} label={`${label} · ${selected.label}`} />}
  </div>;
}

export function SkillScaling({
  skill, display,
}: {
  skill: CharacterArchiveData["skills"][number];
  display?: PlayerDisplay;
}) {
  const [selectedLevel, setSelectedLevel] = useState(1);
  if (!skill.attributes?.length) return null;

  // Source arrays may include unobtainable levels; only the skill cap defines choices.
  const maxLevel = Number.isFinite(skill.max_level)
    ? Math.max(1, Math.floor(skill.max_level))
    : 1;
  const level = Math.min(selectedLevel, maxLevel);
  const skillLabel = skill.name ? gameTextPlainText(talentText(skill.name), display) : "Talent";

  return (
    <div className="character-skill-scaling">
      {maxLevel > 1 && (
        <label className="character-skill-level character-talent-level">
          <span>Talent level</span>
          <output>
            {level} / {maxLevel}
          </output>
          <input
            type="range"
            aria-label={`${skillLabel} talent level`}
            min={1}
            max={maxLevel}
            step={1}
            value={level}
            onChange={(event) =>
              setSelectedLevel(event.currentTarget.valueAsNumber)
            }
          />
        </label>
      )}
      <div
        className="character-talent-stat-scroll"
        role="region"
        aria-label={`${skillLabel} stats`}
        tabIndex={0}
      >
        <table aria-label={`${skillLabel} values at level ${level}`}>
          <tbody>
            {[...skill.attributes]
              .sort((a, b) => a.order - b.order)
              .map((attribute) => (
                <tr key={attribute.id}>
                  <th scope="row">
                    <PlayerText value={talentText(attribute.name || attribute.description || "Stat")} display={display} />
                    {attribute.name &&
                      attribute.description &&
                      !/\{\d+[^}]*\}/.test(attribute.description) && (
                        <small><PlayerText value={talentText(attribute.description)} display={display} /></small>
                      )}
                  </th>
                  <td>
                    {attribute.values.length
                      ? (attribute.values.length === 1
                          ? attribute.values[0]
                          : attribute.values[level - 1]) || "—"
                      : attribute.parameters.length
                        ? attribute.parameters.map((values, index) => (
                            <div key={index}>
                              <small>Parameter {index + 1}: </small>
                              {(values.length === 1
                                ? values[0]
                                : values[level - 1]) || "—"}
                            </div>
                          ))
                        : "—"}
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function SkillCard({
  skill, display,
}: {
  skill: CharacterArchiveData["skills"][number];
  display?: PlayerDisplay;
}) {
  const id = useId();
  const [tab, setTab] = useState("description");
  const [canExpand, setCanExpand] = useState(false);
  const preview = useRef<HTMLParagraphElement>(null);
  const description = talentText(
    skill.description,
    skill.description_params || [],
  );
  const name = skill.name ? gameTextPlainText(talentText(skill.name), display) : "Talent";
  const hasStats = !!skill.attributes?.length;

  useEffect(() => {
    const element = preview.current;
    if (!element) return;
    const measure = () => {
      if (element.clientHeight > 0)
        setCanExpand(element.scrollHeight > element.clientHeight + 1);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [description]);

  return (
    <article className="character-skill-card character-talent-card">
      <div className="character-skill-heading">
        {skill.icon.url ? (
          <img src={apiUrl(skill.icon.url.replace(/^\/api/, ""))} alt="" />
        ) : (
          <span className="character-icon-fallback">
            <Swords size={19} aria-hidden="true" />
          </span>
        )}
        <h3><PlayerText value={skill.name ? talentText(skill.name) : "Talent"} display={display} /></h3>
      </div>
      {hasStats && (
        <div
          className="character-talent-tabs"
          role="tablist"
          aria-label={`${name} details`}
          onKeyDown={(event) => {
            if (event.nativeEvent.isComposing) return;
            const next =
              event.key === "Home"
                ? "description"
                : event.key === "End"
                  ? "stats"
                  : event.key === "ArrowLeft" || event.key === "ArrowRight"
                    ? tab === "description"
                      ? "stats"
                      : "description"
                    : null;
            if (!next) return;
            event.preventDefault();
            setTab(next);
            event.currentTarget
              .querySelectorAll<HTMLButtonElement>('[role="tab"]')
              [next === "description" ? 0 : 1]?.focus();
          }}
        >
          {["description", "stats"].map((name) => (
            <button
              key={name}
              type="button"
              role="tab"
              id={`${id}-${name}-tab`}
              aria-controls={`${id}-${name}-panel`}
              aria-selected={tab === name}
              tabIndex={tab === name ? 0 : -1}
              onClick={() => setTab(name)}
            >
              {name === "description" ? "Description" : "Stats"}
            </button>
          ))}
        </div>
      )}
      <div
        id={`${id}-description-panel`}
        role={hasStats ? "tabpanel" : undefined}
        aria-labelledby={hasStats ? `${id}-description-tab` : undefined}
        hidden={hasStats && tab !== "description"}
      >
        <div className="character-talent-description">
          <p ref={preview} className="character-talent-preview">
            <PlayerText value={description} display={display} />
          </p>
          {canExpand && (
            <details>
              <summary>
                <span className="talent-read-more">Read more</span>
                <span className="talent-show-less">Show less</span>
              </summary>
              <p><PlayerText value={description} display={display} /></p>
            </details>
          )}
        </div>
      </div>
      {hasStats && (
        <div
          className="character-talent-stats-panel"
          id={`${id}-stats-panel`}
          role="tabpanel"
          aria-labelledby={`${id}-stats-tab`}
          hidden={tab !== "stats"}
        >
          <SkillScaling skill={skill} display={display} />
        </div>
      )}
    </article>
  );
}

export function CharacterArchive({
  archive,
  materials,
  locale,
  characterKey,
  gameVersion,
  introduction,
  display,
}: {
  archive: CharacterArchiveData | null;
  materials: MaterialUse[];
  locale: string;
  characterKey: string;
  gameVersion?: string;
  introduction?: string;
  display?: PlayerDisplay;
}) {
  const { items: mediaItems } = useCharacterMedia();
  const [textLocale, setTextLocale] = useState(locale);
  const [textArchive, setTextArchive] = useState<{
    locale: string;
    data: CharacterArchiveData;
  } | null>(null);
  const [textError, setTextError] = useState("");

  useEffect(() => {
    setTextLocale(locale);
    setTextArchive(null);
    setTextError("");
  }, [locale, characterKey, gameVersion]);

  useEffect(() => {
    if (textLocale === locale) return;
    const controller = new AbortController();
    setTextError("");
    api<CharacterArchiveData>(
      `/characters/${encodeURIComponent(characterKey)}/archive?${gameVersion ? `game_version=${encodeURIComponent(gameVersion)}&` : ''}locale=${encodeURIComponent(textLocale)}`,
    )
      .then((result) => {
        if (!controller.signal.aborted)
          setTextArchive({ locale: textLocale, data: result });
      })
      .catch((error: Error) => {
        if (!controller.signal.aborted) setTextError(error.message);
      });
    return () => controller.abort();
  }, [characterKey, locale, textLocale, gameVersion]);

  const groups = new Map<
    string,
    { item: MaterialUse["item"]; uses: MaterialUse[] }
  >();
  for (const use of materials) {
    const key = use.item?.canonical_key || `unresolved:${use.item_id}`;
    const group = groups.get(key) || { item: use.item, uses: [] };
    group.uses.push(use);
    groups.set(key, group);
  }
  const spoken =
    textLocale === locale ? archive : (textArchive?.data ?? archive);
  const textLoading =
    textLocale !== locale && textArchive?.locale !== textLocale && !textError;
  const portrait = archive?.artwork.find(
    (art) =>
      art.kind === "RoleHeadIconLarge" && art.scope !== "shared_resource",
  );
  const biography = archive?.biography &&
    gameText(archive.biography, [], false, display).replace(/\s+/g, " ") !==
      (introduction ? gameText(introduction, [], false, display).replace(/\s+/g, " ") : "");
  const profile = archive?.profile;
  const hasProfileDetails = biography || profile?.element || profile?.quality ||
    profile?.affiliation || profile?.country || profile?.birthday || profile?.talent || groups.size;
  const hasProfile = hasProfileDetails || archive?.artwork.length || mediaItems.length;
  const skills = archive?.skills.filter((skill) =>
    skill.name?.trim() || skill.description?.trim() || skill.attributes?.length,
  ) ?? [];
  const chain = archive?.resonance_chain.filter((entry) =>
    entry.name?.trim() || entry.description?.trim(),
  ) ?? [];
  const voiceLines = spoken?.voice_lines.filter(hasVoiceLine) ?? [];
  const voiceEvents = spoken?.other_voice_events.filter((event) => event.event?.trim()) ?? [];
  const hasVoices = !!(archive?.voice_lines.some(hasVoiceLine) ||
    archive?.other_voice_events.some((event) => event.event?.trim()) ||
    voiceLines.length || voiceEvents.length);

  return (
    <div className="character-archive">
      {!!hasProfile && <details className="character-fold" open>
        <summary>
          <BookOpen size={19} />
          <span>Profile & progression</span>
          <small>
            {hasProfileDetails && archive?.profile.max_level
              ? `Max level ${archive.profile.max_level}`
              : "Game records"}
          </small>
          <ChevronDown size={17} />
        </summary>
        <div className="character-fold-body">
          {biography && (
              <p className="character-biography">
                <PlayerText value={archive?.biography} display={display} />
              </p>
            )}
          <dl className="character-facts">
            {archive?.profile.element && (
              <div>
                <dt>Attribute</dt>
                <dd><PlayerText value={archive.profile.element} display={display} /></dd>
              </div>
            )}
            {archive?.profile.quality && (
              <div>
                <dt>Rarity</dt>
                <dd><PlayerText value={archive.profile.quality} display={display} /></dd>
              </div>
            )}
            {archive?.profile.affiliation && (
              <div>
                <dt>Affiliation</dt>
                <dd><PlayerText value={archive.profile.affiliation} display={display} /></dd>
              </div>
            )}
            {archive?.profile.country && (
              <div>
                <dt>Origin</dt>
                <dd><PlayerText value={archive.profile.country} display={display} /></dd>
              </div>
            )}
            {archive?.profile.birthday && (
              <div>
                <dt>Birthday</dt>
                <dd><PlayerText value={archive.profile.birthday} display={display} /></dd>
              </div>
            )}
            {archive?.profile.talent && (
              <div>
                <dt>Talent</dt>
                <dd><PlayerText value={archive.profile.talent} display={display} /></dd>
              </div>
            )}
          </dl>
          {portrait && !portrait.url && (
            <div className="character-asset-note">
              <Image size={16} /> Portrait source found; image export is
              pending.{" "}
              <code title={portrait.engine_path}>
                {portrait.engine_path.split("/").at(-1)}
              </code>
            </div>
          )}
          <CharacterMediaGallery />
          {!!archive?.artwork.length && (
            <section
              aria-label="Character artwork"
              className="character-artwork"
            >
              <details className="character-subfold">
                <summary>
                  Source references{" "}
                  <span>{archive.artwork.length} references</span>
                </summary>
                <div className="character-asset-list">
                  {archive.artwork.map((art) => (
                    <div key={art.kind}>
                      <strong>{artworkLabel(art)}</strong>
                      <code title={art.engine_path}>{art.engine_path}</code>
                      <small>
                        {art.url ? (
                          <a
                            href={apiUrl(art.url.replace(/^\/api/, ""))}
                            target="_blank"
                            rel="noopener noreferrer"
                          >
                            View image
                          </a>
                        ) : (
                          "No exported image"
                        )}
                      </small>
                    </div>
                  ))}
                </div>
              </details>
            </section>
          )}
          {!!groups.size && (<>
            <h3>Upgrade materials <span>{groups.size}</span></h3>
            <div className="character-material-grid">
              {Array.from(groups.entries()).map(([key, group]) => (
                <div className="character-material-card" key={key}>
                  {group.item?.canonical_key ? (
                    <Link
                      to={entityPath({
                        id: group.item.id,
                        slug: group.item.slug,
                        href: group.item.href,
                        canonical_key: group.item.canonical_key,
                        node_type: "item",
                      })}
                    >
                      {group.item.label || key}
                    </Link>
                  ) : (
                    <strong>Item #{group.uses[0].item_id}</strong>
                  )}
                  <div>
                    {group.uses.map((use, index) => (
                      <span key={`${key}-${index}`}>
                        {use.kind === "character_ascension"
                          ? `Ascension · Lv. ${use.level_cap ?? "?"}`
                          : use.kind.replaceAll("_", " ")}
                        {use.required_count != null
                          ? ` · ${use.required_count}×`
                          : ""}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </>)}
        </div>
      </details>}

      {!!skills.length && <details className="character-fold">
        <summary>
          <Swords size={19} />
          <span>Talents</span>
          <ChevronDown size={17} />
        </summary>
        <div className="character-fold-body">
            <div className="character-skill-grid">
              {skills
                .map((skill) => (
                  <SkillCard
                    key={`${characterKey}-${skill.id}`}
                    skill={skill}
                    display={display}
                  />
                ))}
            </div>
        </div>
      </details>}

      {!!chain.length && <details className="character-fold">
        <summary>
          <Sparkles size={19} />
          <span>Resonance chain</span>
          <small>{chain.length} sequences</small>
          <ChevronDown size={17} />
        </summary>
        <div className="character-fold-body character-chain-grid">
          {chain.map((chain) => (
            <article className="character-chain-card" key={chain.id}>
              <span className="character-chain-number">
                {String(chain.order).padStart(2, "0")}
              </span>
              <div>
                <h3><PlayerText value={chain.name} fallback="Text unavailable in this language." display={display} /></h3>
                <p><PlayerText value={gameText(chain.description, chain.description_params, true)} display={display} /></p>
              </div>
            </article>
          ))}
        </div>
      </details>}

      {hasVoices && <details className="character-fold">
        <summary>
          <Music2 size={19} />
          <span>Voice lines</span>
          <small>{voiceLines.length} entries</small>
          <ChevronDown size={17} />
        </summary>
        <div className="character-fold-body">
          <div className="character-voice-toolbar">
            <span>Text language</span>
            <Select
              label="Voice line text language"
              value={textLocale}
              options={availableLocales.map((option) => ({
                value: option.code,
                label: option.label,
              }))}
              onChange={(value) => {
                setTextError("");
                setTextLocale(value);
              }}
            />
            {spoken?.profile.voice_actors[textLocale] && (
              <span><PlayerText value={spoken.profile.voice_actors[textLocale]} display={display} /></span>
            )}
          </div>
          <p className="character-muted">
            Choose the text language above. Each player lets you choose its
            audio language separately.
          </p>
          {textError && <p className="character-error">{textError}</p>}
          {textLoading && (
            <CharacterVoiceSkeleton />
          )}
          <div className="character-voice-list" aria-busy={textLoading}>
            {!textLoading && voiceLines.map((line) => (
              <article className="character-voice-line" key={line.id}>
                <div>
                  <small>{String(line.order).padStart(2, "0")}</small>
                  <h3><PlayerText value={line.title} fallback="Text unavailable in this language." display={display} /></h3>
                  <p><PlayerText value={line.text} fallback="Text unavailable in this language." display={display} /></p>
                  {line.audio_event_path && (
                    <small
                      className="character-source-hint"
                      title={line.audio_event_path}
                    >
                      Voice event · {line.audio_event_path.split("/").at(-1)}
                    </small>
                  )}
                </div>
                <CharacterVoicePlayback key={`${characterKey}:${line.id}`} line={line} textLocale={textLocale} display={display} />
              </article>
            ))}
          </div>
          {!!voiceEvents.length && (
            <details className="character-subfold">
              <summary>
                Other character voice events{" "}
                <span>{voiceEvents.length} event names</span>
              </summary>
              <div className="character-asset-list">
                {voiceEvents.map((event) => (
                  <div key={`${event.category}-${event.event}`}>
                    <strong>
                      {event.category.replaceAll(/([a-z])([A-Z])/g, "$1 $2")}
                    </strong>
                    <code>{event.event}</code>
                    <small>Event name only</small>
                  </div>
                ))}
              </div>
            </details>
          )}
        </div>
      </details>}
    </div>
  );
}


export function CharacterVoiceSkeleton() {
  return <SkeletonGroup label="Loading voice lines" className="character-voice-list">
    {[0,1,2].map(index => <article className="character-voice-line" key={index}>
      <div className="skeleton-card-body"><Skeleton style={{width:'40%',height:'1.2rem'}} /><TextSkeleton lines={3} /><Skeleton style={{height:'2.5rem'}} /></div>
    </article>)}
  </SkeletonGroup>;
}
export function CharacterArchiveSkeleton() {
  return <SkeletonGroup label="Loading character details" className="page-skeleton">
    <Skeleton style={{width:'35%',height:'1.5rem'}} />
    <div className="page-skeleton-panels">{[0,1,2].map(index => <article className="page-skeleton-panel" key={index}><Skeleton style={{height:'5rem'}} /><TextSkeleton lines={3} /></article>)}</div>
  </SkeletonGroup>;
}
