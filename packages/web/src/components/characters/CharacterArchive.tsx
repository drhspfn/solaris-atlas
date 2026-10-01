import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { BookOpen, ChevronDown, Image, Music2, Sparkles, Swords } from "lucide-react";
import { api, apiUrl } from "../../api/client";
import { availableLocales } from "../../data/locales";
import { entityPath } from "../../data/entities";

export interface CharacterArchiveData {
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
  artwork: { kind: string; engine_path: string; url: string | null }[];
  skills: { id: number; type: number; order: number; name: string | null; description: string | null; max_level: number; icon: { engine_path: string | null; url: string | null } }[];
  skill_tree: { id: number; skill_id: number; order: number; type: number; parent_nodes: number[]; costs: { item_id: number; count: number; item: { canonical_key: string; name: string } | null }[]; title: string | null; description: string | null }[];
  resonance_chain: { id: number; order: number; name: string | null; description: string | null; description_params: string[]; icon: { engine_path: string | null; url: string | null } }[];
  voice_lines: { id: number; order: number; title: string | null; text: string | null; audio_event_path: string | null; audio_url: string | null }[];
  other_voice_events: { category: string; event: string }[];
}

type MaterialUse = {
  item_id: number;
  item?: { id: number; canonical_key: string; label: string };
  kind: string;
  level_cap?: number;
  required_count?: number;
  group_id?: number;
};

function gameText(value: string | null | undefined, params: string[] = []): string {
  if (!value) return "Text unavailable in this language.";
  return value
    .replace(/<[^>]*>/g, "")
    .replace(/\{(\d+)\}/g, (match, index: string) => params[Number(index)] ?? match)
    .trim();
}

const voiceLocales = availableLocales.filter(({ code }) => ["en", "ja", "zh-Hans", "ko"].includes(code));
const initialVoiceLocale = (locale: string) => voiceLocales.some((option) => option.code === locale) ? locale : "en";

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
  const [voiceLocale, setVoiceLocale] = useState(initialVoiceLocale(locale));
  const [voiceArchive, setVoiceArchive] = useState<{ locale: string; data: CharacterArchiveData } | null>(null);
  const [voiceError, setVoiceError] = useState("");

  useEffect(() => {
    setVoiceLocale(initialVoiceLocale(locale));
    setVoiceArchive(null);
    setVoiceError("");
  }, [locale, characterKey]);

  useEffect(() => {
    if (voiceLocale === locale) return;
    const controller = new AbortController();
    setVoiceArchive(null);
    setVoiceError("");
    api<CharacterArchiveData>(`/characters/${encodeURIComponent(characterKey)}/archive?locale=${encodeURIComponent(voiceLocale)}`)
      .then((result) => { if (!controller.signal.aborted) setVoiceArchive({ locale: voiceLocale, data: result }); })
      .catch((error: Error) => { if (!controller.signal.aborted) setVoiceError(error.message); });
    return () => controller.abort();
  }, [characterKey, locale, voiceLocale]);

  const groups = new Map<string, { item: MaterialUse["item"]; uses: MaterialUse[] }>();
  for (const use of materials) {
    const key = use.item?.canonical_key || `unresolved:${use.item_id}`;
    const group = groups.get(key) || { item: use.item, uses: [] };
    group.uses.push(use);
    groups.set(key, group);
  }
  const spoken = voiceLocale === locale ? archive : voiceArchive?.locale === voiceLocale ? voiceArchive.data : null;
  const portrait = archive?.artwork.find((art) => art.kind === "RoleHeadIconLarge");

  return (
    <div className="character-archive">
      <details className="character-fold" open>
        <summary><BookOpen size={19} /><span>Profile & progression</span><small>{archive?.profile.max_level ? `Max level ${archive.profile.max_level}` : "Game records"}</small><ChevronDown size={17} /></summary>
        <div className="character-fold-body">
          {archive?.biography && <p className="character-biography">{gameText(archive.biography)}</p>}
          <div className="character-facts">
            {archive?.profile.element && <div><small>Attribute</small><strong>{gameText(archive.profile.element)}</strong></div>}
            {archive?.profile.quality && <div><small>Rarity</small><strong>{gameText(archive.profile.quality)}</strong></div>}
            {archive?.profile.affiliation && <div><small>Affiliation</small><strong>{gameText(archive.profile.affiliation)}</strong></div>}
            {archive?.profile.country && <div><small>Origin</small><strong>{gameText(archive.profile.country)}</strong></div>}
            {archive?.profile.birthday && <div><small>Birthday</small><strong>{gameText(archive.profile.birthday)}</strong></div>}
            {archive?.profile.talent && <div><small>Talent</small><strong>{gameText(archive.profile.talent)}</strong></div>}
          </div>
          {portrait && !portrait.url && <div className="character-asset-note"><Image size={16} /> Portrait source found; image export is pending. <code title={portrait.engine_path}>{portrait.engine_path.split("/").at(-1)}</code></div>}
          {!!archive?.artwork.length && <details className="character-subfold"><summary>Artwork and icon source paths <span>{archive.artwork.length} references</span></summary><div className="character-asset-list">{archive.artwork.map((art) => <div key={art.kind}><strong>{art.kind.replaceAll(/([a-z])([A-Z])/g, "$1 $2")}</strong><code title={art.engine_path}>{art.engine_path}</code><small>{art.url ? "Ready" : "Awaiting export"}</small></div>)}</div></details>}
          <h3>Upgrade materials <span>{groups.size}</span></h3>
          {groups.size ? <div className="character-material-grid">
            {Array.from(groups.entries()).map(([key, group]) => (
              <div className="character-material-card" key={key}>
                {group.item?.canonical_key ? <Link to={entityPath({ id: group.item.id, canonical_key: group.item.canonical_key, node_type: "item" })}>{group.item.label || key}</Link> : <strong>Item #{group.uses[0].item_id}</strong>}
                <div>{group.uses.map((use, index) => <span key={`${key}-${index}`}>{use.kind === "character_ascension" ? `Ascension · Lv. ${use.level_cap ?? "?"}` : use.kind.replaceAll("_", " ")}{use.required_count != null ? ` · ${use.required_count}×` : ""}</span>)}</div>
              </div>
            ))}
          </div> : <p className="character-muted">No upgrade material records in this snapshot.</p>}
        </div>
      </details>

      <details className="character-fold">
        <summary><Swords size={19} /><span>Skills & skill tree</span><small>{archive?.skills.length ?? 0} skills · {archive?.skill_tree.length ?? 0} nodes</small><ChevronDown size={17} /></summary>
        <div className="character-fold-body">
          {archive?.skills.length ? <div className="character-skill-grid">
            {archive.skills.map((skill) => <article className="character-skill-card" key={skill.id}>
              <div className="character-skill-heading">
                {skill.icon.url ? <img src={apiUrl(skill.icon.url.replace(/^\/api/, ""))} alt="" /> : <span className="character-icon-fallback"><Swords size={19} /></span>}
                <div><small>Skill {String(skill.order).padStart(2, "0")}</small><h3>{gameText(skill.name)}</h3></div>
              </div>
              <p>{gameText(skill.description)}</p>
              <small className="character-source-hint">Source: Skill #{skill.id} · up to level {skill.max_level}</small>
            </article>)}
          </div> : <p className="character-muted">No skill records in this snapshot.</p>}
          {archive?.skill_tree.length ? <details className="character-subfold"><summary>Skill tree requirements <span>{archive.skill_tree.length} nodes</span></summary>
            <div className="character-tree-list">{archive.skill_tree.map((node) => <div key={node.id}><strong>{node.title ? gameText(node.title) : `Node ${node.order}`}</strong><small>{node.costs.length ? node.costs.map((cost) => `${cost.item?.name || `Item ${cost.item_id}`} ×${cost.count}`).join(" · ") : "No material cost recorded"}</small>{node.description && <p>{gameText(node.description)}</p>}</div>)}</div>
          </details> : null}
        </div>
      </details>

      <details className="character-fold">
        <summary><Sparkles size={19} /><span>Resonance chain</span><small>{archive?.resonance_chain.length ?? 0} sequences</small><ChevronDown size={17} /></summary>
        <div className="character-fold-body character-chain-grid">
          {archive?.resonance_chain.map((chain) => <article className="character-chain-card" key={chain.id}>
            <span className="character-chain-number">{String(chain.order).padStart(2, "0")}</span>
            <div><h3>{gameText(chain.name)}</h3><p>{gameText(chain.description, chain.description_params)}</p></div>
          </article>)}
          {!archive?.resonance_chain.length && <p className="character-muted">No chain records in this snapshot.</p>}
        </div>
      </details>

      <details className="character-fold">
        <summary><Music2 size={19} /><span>Voice lines</span><small>{archive?.voice_lines.length ?? 0} entries</small><ChevronDown size={17} /></summary>
        <div className="character-fold-body">
          <div className="character-voice-toolbar">
            <span>Voice language</span>
            <div className="character-voice-languages" role="group" aria-label="Voice language">{voiceLocales.map((option) => <button type="button" key={option.code} aria-pressed={voiceLocale === option.code} onClick={() => { setVoiceError(""); setVoiceLocale(option.code); }}>{option.label}</button>)}</div>
            {spoken?.profile.voice_actors[voiceLocale] && <span>{gameText(spoken.profile.voice_actors[voiceLocale])}</span>}
          </div>
          <p className="character-muted">Voice event paths are recorded in the game data. Playback appears when an audio file for this language has been exported and linked.</p>
          {voiceError && <p className="character-error">{voiceError}</p>}
          {voiceLocale !== locale && !spoken && !voiceError && <p className="character-muted">Loading voice lines…</p>}
          <div className="character-voice-list">{spoken?.voice_lines.map((line) => <article className="character-voice-line" key={line.id}>
            <div><small>{String(line.order).padStart(2, "0")}</small><h3>{gameText(line.title)}</h3><p>{gameText(line.text)}</p>{line.audio_event_path && <small className="character-source-hint" title={line.audio_event_path}>Voice event · {line.audio_event_path.split("/").at(-1)}</small>}</div>
            {line.audio_url ? <audio controls preload="none" src={apiUrl(line.audio_url.replace(/^\/api/, ""))} aria-label={`Play ${gameText(line.title)}`} /> : <span className="character-audio-pending">Awaiting audio</span>}
          </article>)}</div>
          {!!spoken?.other_voice_events.length && <details className="character-subfold"><summary>Other character voice events <span>{spoken.other_voice_events.length} event names</span></summary><div className="character-asset-list">{spoken.other_voice_events.map((event) => <div key={`${event.category}-${event.event}`}><strong>{event.category.replaceAll(/([a-z])([A-Z])/g, "$1 $2")}</strong><code>{event.event}</code><small>Event name only</small></div>)}</div></details>}
        </div>
      </details>
    </div>
  );
}
