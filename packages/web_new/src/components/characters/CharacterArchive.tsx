"use client";

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
import { availableLocales } from "../../data/locales";
import { Select } from "@/components/ui/Select";
import { TrackPlayer } from "@/components/ui/TrackPlayer";
import { RenderedAnimation } from "@/components/ui/RenderedAnimation";
import {
  selectAnimationClip,
  type AnimationPreview,
} from "@/components/ui/animationPreview";
import "@/styles/character-voice-lines.css";
import "@/styles/character-talents.css";

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

function artworkLabel(art: CharacterArchiveData["artwork"][number]) {
  if (art.scope === "shared_resource") {
    if (art.kind === "RolePortrait") return "Shared portrait reference";
    return art.kind === "RoleStand"
      ? "Shared quest artwork"
      : "Shared resource icon";
  }
  return art.kind.replaceAll(/([a-z])([A-Z])/g, "$1 $2");
}

function ArtworkCard({
  art,
}: {
  art: CharacterArchiveData["artwork"][number];
}) {
  const [failed, setFailed] = useState(false);
  const label = artworkLabel(art);
  return (
    <figure className="character-artwork-card">
      {art.url && !failed ? (
        <a
          href={apiUrl(art.url.replace(/^\/api/, ""))}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={`Open ${label} image`}
        >
          <img
            src={apiUrl(art.url.replace(/^\/api/, ""))}
            alt={label}
            loading="lazy"
            width={256}
            height={256}
            onError={() => setFailed(true)}
          />
        </a>
      ) : (
        <div className="character-artwork-placeholder">
          <Image size={24} aria-hidden="true" />
          <span>{failed ? "Image unavailable" : "Image not exported"}</span>
          {failed && (
            <button type="button" onClick={() => setFailed(false)}>
              Retry image
            </button>
          )}
        </div>
      )}
      <figcaption>{label}</figcaption>
    </figure>
  );
}

function gameText(
  value: string | null | undefined,
  params: string[] = [],
): string {
  if (!value) return "Text unavailable in this language.";
  return value
    .replace(/<[^>]*>/g, "")
    .replace(
      /\{(\d+)\}/g,
      (match, index: string) => params[Number(index)] ?? match,
    )
    .trim();
}

function talentText(
  value: string | null | undefined,
  params: string[] = [],
): string {
  return gameText(value, params)
    .replace(/\{Cus:Ipt,Touch=(?:Tap|tap) PC=(Press|press) Gamepad=(?:Press|press)\}/g, "$1")
    .replace(/\{\d+[^}]*\}/g, "—");
}

const voiceLocales = availableLocales.filter(({ code }) =>
  ["en", "ja", "zh-Hans", "ko"].includes(code),
);
const initialVoiceLocale = (locale: string) =>
  voiceLocales.some((option) => option.code === locale) ? locale : "en";

export function SkillScaling({
  skill,
}: {
  skill: CharacterArchiveData["skills"][number];
}) {
  const [selectedLevel, setSelectedLevel] = useState(1);
  if (!skill.attributes?.length) return null;

  // Source arrays may include unobtainable levels; only the skill cap defines choices.
  const maxLevel = Number.isFinite(skill.max_level)
    ? Math.max(1, Math.floor(skill.max_level))
    : 1;
  const level = Math.min(selectedLevel, maxLevel);
  const skillLabel = skill.name ? talentText(skill.name) : "Talent";

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
                    {talentText(
                      attribute.name || attribute.description || "Stat",
                    )}
                    {attribute.name &&
                      attribute.description &&
                      !/\{\d+[^}]*\}/.test(attribute.description) && (
                        <small>{talentText(attribute.description)}</small>
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
  skill,
}: {
  skill: CharacterArchiveData["skills"][number];
}) {
  const id = useId();
  const [tab, setTab] = useState("description");
  const [canExpand, setCanExpand] = useState(false);
  const preview = useRef<HTMLParagraphElement>(null);
  const description = talentText(
    skill.description,
    skill.description_params || [],
  );
  const name = skill.name ? talentText(skill.name) : "Talent";
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
        <h3>{name}</h3>
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
            {description}
          </p>
          {canExpand && (
            <details>
              <summary>
                <span className="talent-read-more">Read more</span>
                <span className="talent-show-less">Show less</span>
              </summary>
              <p>{description}</p>
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
          <SkillScaling skill={skill} />
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
}: {
  archive: CharacterArchiveData | null;
  materials: MaterialUse[];
  locale: string;
  characterKey: string;
}) {
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
  }, [locale, characterKey]);

  useEffect(() => {
    if (textLocale === locale) return;
    const controller = new AbortController();
    setTextError("");
    api<CharacterArchiveData>(
      `/characters/${encodeURIComponent(characterKey)}/archive?locale=${encodeURIComponent(textLocale)}`,
    )
      .then((result) => {
        if (!controller.signal.aborted)
          setTextArchive({ locale: textLocale, data: result });
      })
      .catch((error: Error) => {
        if (!controller.signal.aborted) setTextError(error.message);
      });
    return () => controller.abort();
  }, [characterKey, locale, textLocale]);

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
  const trialArtwork = archive?.artwork.find(
    (art) =>
      art.kind === "RoleStand" && art.scope !== "shared_resource" && art.url,
  );

  const trialPreview = selectAnimationClip(
    archive?.trial_spine?.animation_preview,
    "idle",
  );

  return (
    <div className="character-archive">
      <details className="character-fold" open>
        <summary>
          <BookOpen size={19} />
          <span>Profile & progression</span>
          <small>
            {archive?.profile.max_level
              ? `Max level ${archive.profile.max_level}`
              : "Game records"}
          </small>
          <ChevronDown size={17} />
        </summary>
        <div className="character-fold-body">
          {archive?.biography && (
            <p className="character-biography">{gameText(archive.biography)}</p>
          )}
          <div className="character-facts">
            {archive?.profile.element && (
              <div>
                <small>Attribute</small>
                <strong>{gameText(archive.profile.element)}</strong>
              </div>
            )}
            {archive?.profile.quality && (
              <div>
                <small>Rarity</small>
                <strong>{gameText(archive.profile.quality)}</strong>
              </div>
            )}
            {archive?.profile.affiliation && (
              <div>
                <small>Affiliation</small>
                <strong>{gameText(archive.profile.affiliation)}</strong>
              </div>
            )}
            {archive?.profile.country && (
              <div>
                <small>Origin</small>
                <strong>{gameText(archive.profile.country)}</strong>
              </div>
            )}
            {archive?.profile.birthday && (
              <div>
                <small>Birthday</small>
                <strong>{gameText(archive.profile.birthday)}</strong>
              </div>
            )}
            {archive?.profile.talent && (
              <div>
                <small>Talent</small>
                <strong>{gameText(archive.profile.talent)}</strong>
              </div>
            )}
          </div>
          {portrait && !portrait.url && (
            <div className="character-asset-note">
              <Image size={16} /> Portrait source found; image export is
              pending.{" "}
              <code title={portrait.engine_path}>
                {portrait.engine_path.split("/").at(-1)}
              </code>
            </div>
          )}
          {archive?.trial_spine && trialPreview && (
            <section
              aria-label="Character trial animation"
              className="character-artwork character-trial-animation"
            >
              <h3>Trial animation</h3>
              <RenderedAnimation
                preview={trialPreview}
                label="Character trial animation"
                fallback={
                  <div className="character-artwork-placeholder">
                    {trialArtwork?.url ? (
                      <img
                        src={apiUrl(trialArtwork.url.replace(/^\/api/, ""))}
                        alt="Character trial artwork"
                        loading="lazy"
                      />
                    ) : null}
                  </div>
                }
              />
              <a href={archive.trial_spine.url} download>
                Download trial scene
              </a>
            </section>
          )}
          {!!archive?.artwork.length && (
            <section
              aria-label="Character artwork"
              className="character-artwork"
            >
              <h3>Artwork</h3>
              <div className="character-artwork-gallery">
                {archive.artwork
                  .filter((art) => art.scope !== "shared_resource")
                  .map((art) => (
                    <ArtworkCard key={`${art.kind}-${art.url}`} art={art} />
                  ))}
              </div>
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
          <h3>
            Upgrade materials <span>{groups.size}</span>
          </h3>
          {groups.size ? (
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
          ) : (
            <p className="character-muted">
              No upgrade material records in this snapshot.
            </p>
          )}
        </div>
      </details>

      <details className="character-fold">
        <summary>
          <Swords size={19} />
          <span>Talents</span>
          <ChevronDown size={17} />
        </summary>
        <div className="character-fold-body">
          {archive?.skills.length ? (
            <div className="character-skill-grid">
              {archive.skills
                .filter(
                  (skill) =>
                    skill.name?.trim() ||
                    skill.description?.trim() ||
                    skill.attributes?.length,
                )
                .map((skill) => (
                  <SkillCard
                    key={`${characterKey}-${skill.id}`}
                    skill={skill}
                  />
                ))}
            </div>
          ) : (
            <p className="character-muted">
              No skill records in this snapshot.
            </p>
          )}
        </div>
      </details>

      <details className="character-fold">
        <summary>
          <Sparkles size={19} />
          <span>Resonance chain</span>
          <small>{archive?.resonance_chain.length ?? 0} sequences</small>
          <ChevronDown size={17} />
        </summary>
        <div className="character-fold-body character-chain-grid">
          {archive?.resonance_chain.map((chain) => (
            <article className="character-chain-card" key={chain.id}>
              <span className="character-chain-number">
                {String(chain.order).padStart(2, "0")}
              </span>
              <div>
                <h3>{gameText(chain.name)}</h3>
                <p>{gameText(chain.description, chain.description_params)}</p>
              </div>
            </article>
          ))}
          {!archive?.resonance_chain.length && (
            <p className="character-muted">
              No chain records in this snapshot.
            </p>
          )}
        </div>
      </details>

      <details className="character-fold">
        <summary>
          <Music2 size={19} />
          <span>Voice lines</span>
          <small>{archive?.voice_lines.length ?? 0} entries</small>
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
              <span>{gameText(spoken.profile.voice_actors[textLocale])}</span>
            )}
          </div>
          <p className="character-muted">
            Choose the text language above. Each player lets you choose its
            audio language separately.
          </p>
          {textError && <p className="character-error">{textError}</p>}
          {textLoading && (
            <p className="character-muted" role="status">
              Loading selected text language…
            </p>
          )}
          <div className="character-voice-list" aria-busy={textLoading}>
            {spoken?.voice_lines.map((line) => (
              <article className="character-voice-line" key={line.id}>
                <div>
                  <small>{String(line.order).padStart(2, "0")}</small>
                  <h3>{gameText(line.title)}</h3>
                  <p>{gameText(line.text)}</p>
                  {line.audio_event_path && (
                    <small
                      className="character-source-hint"
                      title={line.audio_event_path}
                    >
                      Voice event · {line.audio_event_path.split("/").at(-1)}
                    </small>
                  )}
                </div>
                <TrackPlayer
                  src={
                    line.audio_url
                      ? apiUrl(line.audio_url.replace(/^\/api/, ""))
                      : null
                  }
                  tracks={
                    line.audio_tracks
                      ? Object.fromEntries(
                          Object.entries(line.audio_tracks).map(
                            ([language, url]) => [
                              availableLocales.find(
                                (option) => option.code === language,
                              )?.label ?? language,
                              apiUrl(url.replace(/^\/api/, "")),
                            ],
                          ),
                        )
                      : undefined
                  }
                  defaultTrack={
                    availableLocales.find(
                      (option) =>
                        option.code === initialVoiceLocale(textLocale),
                    )?.label
                  }
                  label={gameText(line.title)}
                />
              </article>
            ))}
          </div>
          {!!spoken?.other_voice_events.length && (
            <details className="character-subfold">
              <summary>
                Other character voice events{" "}
                <span>{spoken.other_voice_events.length} event names</span>
              </summary>
              <div className="character-asset-list">
                {spoken.other_voice_events.map((event) => (
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
      </details>
    </div>
  );
}
