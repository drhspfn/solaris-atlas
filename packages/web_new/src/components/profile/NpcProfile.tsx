"use client";

import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { api } from "@/api/client";
import { CharacterModelImport } from "@/components/characters/CharacterModelImport";
import { CharacterProfileVisual } from "@/components/characters/CharacterProfileVisual";
import { NpcFlowParticipants } from "@/components/profile/NpcFlowParticipants";
import { NpcInstances } from "@/components/profile/NpcInstances";
import { NpcShops } from "@/components/profile/NpcShops";
import type { CharacterModelData } from "@/components/models/types";
import { ErrorPanel } from "@/components/ui/Feedback";
import { Pagination } from "@/components/ui/Pagination";
import { PanelTitle } from "@/components/ui/PanelTitle";
import { localizedText } from "@/data/localized";
import { conversationSpeakerName } from "@/data/npcContext";
import { useLocale } from "@/hooks/useLocale";
import { Link } from "@/lib/router";
import { questPath } from "@/lib/routeKey";
import "@/styles/npc-profile.css";

const PAGE_SIZE = 20;

type Npc = {
  blueprint_id: number;
  name_scope: 'instance' | 'blueprint';
  name_variants: string[];
  speakers: string[];
  flow_reference_count: number;
  shop_count: number;
};
type Line = {
  canonical_key: string;
  flow_state: string;
  text: unknown;
  speaker: { label?: string; canonical_key: string } | null;
  speaker_relation: 'confirmed_npc_speaker' | 'npc_actor_context' | 'interaction_participant';
  quests: { canonical_key: string; game_quest_id: number; name: unknown }[];
};

function NpcQuests({
  characterKey,
  gameVersion,
}: {
  characterKey: string;
  gameVersion?: string;
}) {
  const locale = useLocale();
  const [page, setPage] = useState(1);
  const [result, setResult] = useState<{
    results: Line["quests"];
    total: number;
  } | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({
      locale,
      limit: String(PAGE_SIZE),
      offset: String((page - 1) * PAGE_SIZE),
    });
    if (gameVersion) params.set("game_version", gameVersion);
    setResult(null);
    setError("");
    api<{ results: Line["quests"]; total: number }>(
      `/npcs/${encodeURIComponent(characterKey)}/quests?${params}`,
      { signal: controller.signal },
    )
      .then((value) => {
        if (!controller.signal.aborted) setResult(value);
      })
      .catch((cause: Error) => {
        if (!controller.signal.aborted) setError(cause.message);
      });
    return () => controller.abort();
  }, [characterKey, gameVersion, locale, page, retry]);
  return (
    <section className="content-panel full-panel npc-section">
      <PanelTitle number="02" title="Quest appearances" />
      {error ? (
        <ErrorPanel
          message={error}
          onRetry={() => setRetry((value) => value + 1)}
        />
      ) : !result ? (
        <p role="status">Loading quests…</p>
      ) : (
        <>
          {!result.results.length && (
            <p>No linked quest appearances found.</p>
          )}
          <div className="npc-quest-grid">
          {result.results.map((quest) => (
            <Link
              className="npc-quest-card"
              key={quest.canonical_key}
              to={questPath(quest)}
            >
              {localizedText(quest.name, `Quest ${quest.game_quest_id}`)}
            </Link>
          ))}
          </div>
          {result.total > PAGE_SIZE && (
            <Pagination
              mode="pages"
              page={page}
              pageSize={PAGE_SIZE}
              total={result.total}
              onPageChange={setPage}
              ariaLabel="Quest pages"
            />
          )}
        </>
      )}
    </section>
  );
}

export function NpcInteractions({ characterKey, npc, gameVersion }: {
  characterKey: string; npc: Npc; gameVersion?: string;
}) {
  const locale = useLocale();
  const searchInput = useRef<HTMLInputElement>(null);
  const [input, setInput] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [result, setResult] = useState<{
    results: Line[];
    total: number;
  } | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setResult(null);
    setError("");
    const params = new URLSearchParams({
      locale,
      q: query,
      limit: String(PAGE_SIZE),
      offset: String((page - 1) * PAGE_SIZE),
    });
    if (gameVersion) params.set("game_version", gameVersion);
    api<{ results: Line[]; total: number }>(
      `/npcs/${encodeURIComponent(characterKey)}/dialogue?${params}`,
      { signal: controller.signal },
    )
      .then((value) => {
        if (!controller.signal.aborted) setResult(value);
      })
      .catch((cause: Error) => {
        if (!controller.signal.aborted) setError(cause.message);
      });
    return () => controller.abort();
  }, [characterKey, locale, query, page, retry, gameVersion]);
  return <>
      <section className="content-panel full-panel npc-section">
        <PanelTitle number="01" title="Dialogue" />
        <form
          className="dialogue-search" noValidate
          onSubmit={(event) => {
            event.preventDefault();
            setPage(1);
            setQuery(input.trim());
          }}
        >
          <input
            ref={searchInput}
            aria-label="Search NPC dialogue"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="Search dialogue"
          />
          {(input || query) && <button type="button" className="search-clear" aria-label="Clear dialogue search" onClick={() => {
            setInput('');
            setQuery('');
            setPage(1);
            searchInput.current?.focus();
          }}><X size={16} aria-hidden="true" /></button>}
          <button type="submit">Find lines</button>
        </form>
        {error ? (
          <ErrorPanel
            message={error}
            onRetry={() => setRetry((value) => value + 1)}
          />
        ) : !result ? (
          <p role="status">Loading dialogue…</p>
        ) : (
          <>
            {!result.results.length && <p>No linked dialogue found.</p>}
            <div className="npc-dialogue-list">
            {result.results.map((line) => (
              <article
                className="npc-dialogue-card"
                key={`${line.canonical_key}:${line.flow_state}:${line.speaker?.canonical_key || 'none'}`}
              >
                <p className="npc-dialogue-text">{localizedText(line.text, "Text unavailable")}</p>
                <div className="npc-dialogue-meta">
                  <span>{conversationSpeakerName(line.speaker?.label)}</span>
                  <small>{line.speaker_relation === 'confirmed_npc_speaker' ? 'Confirmed speaker' : line.speaker_relation === 'npc_actor_context' ? 'Associated line' : 'Conversation participant'}</small>
                </div>
                {line.quests.map((quest) => (
                  <p className="npc-dialogue-quest" key={quest.canonical_key}>
                    <Link to={questPath(quest)}>
                      {localizedText(
                        quest.name,
                        `Quest ${quest.game_quest_id}`,
                      )}
                    </Link>
                  </p>
                ))}
                <details className="npc-dialogue-source">
                  <summary>Source details</summary>
                  <span>Story state: {line.flow_state}</span>
                  {line.speaker?.canonical_key && <span>Speaker: {line.speaker.canonical_key}</span>}
                </details>
              </article>
            ))}
            </div>
            {result.total > PAGE_SIZE && (
              <Pagination
                mode="pages"
                page={page}
                pageSize={PAGE_SIZE}
                total={result.total}
                onPageChange={setPage}
                ariaLabel="Dialogue pages"
              />
            )}
          </>
        )}
      </section>
      <NpcInstances key={characterKey} characterKey={characterKey} gameVersion={gameVersion} />
      <NpcQuests
        characterKey={characterKey}
        gameVersion={gameVersion}
      />
      {npc.shop_count > 0 && <NpcShops characterKey={characterKey} gameVersion={gameVersion} />}
      <section className="content-panel full-panel npc-section">
          <PanelTitle number="04" title="Conversations" />
          <NpcFlowParticipants characterKey={characterKey} gameVersion={gameVersion} />
      </section>
  </>;
}

export function NpcProfile({
  data,
  initialModel,
  gameVersion,
}: {
  data: { character: any; npc: Npc };
  initialModel?: CharacterModelData | null;
  gameVersion?: string;
}) {
  const { character, npc } = data;
  const name = localizedText(
    character.name,
    character.canonical_name || `NPC ${npc.blueprint_id}`,
  );
  const [revision, setRevision] = useState(0);
  return (
    <div className="page-container character-profile-page">
      <div className="breadcrumbs">
        <Link to="/">Archive</Link>
        <span> / </span>
        <Link to="/catalog/character">Characters</Link>
        <span> / </span>
        <span>{name}</span>
      </div>
      <div className="profile-hero character-profile-hero npc-profile-hero">
        <CharacterProfileVisual
          characterKey={character.canonical_key}
          name={name}
          portraitUrl={null}
          initialModel={initialModel}
          refreshKey={revision}
        />
        <div className="profile-main">
          <span className="eyebrow left">NPC PROFILE</span>
          <div className="character-profile-title-row">
            <h1>
              {name}
              <span className="heading-period">.</span>
            </h1>
            <CharacterModelImport
              characterKey={character.canonical_key}
              variant="actions"
              onComplete={() => setRevision((value) => value + 1)}
            />
          </div>
          <p>
            {localizedText(
              character.description,
              "An NPC recorded in the Solaris Atlas archive.",
            )}
          </p>
          {npc.name_scope === 'instance' && <p>This name is recorded for one appearance.{npc.name_variants.length > 1 ? ` Other recorded names: ${npc.name_variants.filter((variant) => variant !== name).join(', ')}.` : ''}</p>}
          <div className="profile-tags">
            <span>NPC</span>
          </div>
        </div>
      </div>
      <NpcInteractions key={character.canonical_key} characterKey={character.canonical_key} npc={npc} gameVersion={gameVersion} />
    </div>
  );
}
