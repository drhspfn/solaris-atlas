"use client";

import { useEffect, useRef, useState } from "react";
import { Volume2, X } from "lucide-react";
import { api } from "@/api/client";
import { CharacterModelImport } from "@/components/characters/CharacterModelImport";
import { CharacterProfileVisual } from "@/components/characters/CharacterProfileVisual";
import { NpcFlowParticipants } from "@/components/profile/NpcFlowParticipants";
import { NpcInstances } from "@/components/profile/NpcInstances";
import { NpcShops } from "@/components/profile/NpcShops";
import type { CharacterModelData } from "@/components/models/types";
import { ErrorPanel } from "@/components/ui/Feedback";
import {
  Skeleton,
  SkeletonGroup,
  TextSkeleton,
} from "@/components/ui/Skeleton";
import { Pagination } from "@/components/ui/Pagination";
import { PanelTitle } from "@/components/ui/PanelTitle";
import { Breadcrumbs } from "@/components/ui/Breadcrumbs";
import { gameTextPlainText } from "@/data/gameText";
import {
  PlayerText,
  type PlayerDisplay,
} from "@/components/dialogue/PlayerText";
import { conversationSpeakerName } from "@/data/npcContext";
import { useLocale } from "@/hooks/useLocale";
import { Link } from "@/lib/router";
import { questPath } from "@/lib/routeKey";
import "@/styles/npc-profile.css";

const PAGE_SIZE = 20;
const DEFAULT_DISPLAY: PlayerDisplay = {
  name: "Rover",
  colorMode: "accent",
  customColor: "",
};

function speakerName(label?: string) {
  const normalized = conversationSpeakerName(label);
  return normalized === "Rover" && /\{PlayerName\}/i.test(label || "")
    ? "{PlayerName}"
    : normalized;
}

type Npc = {
  blueprint_id: number;
  name_scope: "instance" | "blueprint";
  name_variants: string[];
  speakers: (
    | string
    | { id: number; canonical_key: string; slug?: string; label?: any }
  )[];
  flow_reference_count: number;
  shop_count: number;
};
type Line = {
  canonical_key: string;
  flow_state: string;
  text: unknown;
  speaker: { label?: string; canonical_key: string } | null;
  speaker_relation:
    | "confirmed_npc_speaker"
    | "npc_actor_context"
    | "interaction_participant";
  quests: { canonical_key: string; game_quest_id: number; name: unknown }[];
};

function questHref(
  quest: Line["quests"][number],
  locale: string,
  gameVersion?: string,
) {
  const [pathAndQuery, hash] = questPath(quest).split("#", 2);
  const [path, query] = pathAndQuery.split("?", 2);
  const params = new URLSearchParams(query);
  params.set("locale", locale);
  if (gameVersion) params.set("game_version", gameVersion);
  return `${path}?${params}${hash ? `#${hash}` : ""}`;
}

export function NpcQuestCardSkeleton() {
  return (
    <div className="npc-quest-card">
      <Skeleton style={{ width: "70%", height: 14 }} />
    </div>
  );
}

export function NpcDialogueSkeleton() {
  return (
    <article className="npc-dialogue-card">
      <div className="npc-dialogue-text">
        <TextSkeleton lines={2} />
      </div>
      <div className="npc-dialogue-meta">
        <Skeleton style={{ width: 100, height: 12 }} />
        <Skeleton style={{ width: 130, height: 12 }} />
      </div>
      <div className="npc-dialogue-source">
        <Skeleton style={{ width: 90, height: 12 }} />
      </div>
    </article>
  );
}

function NpcQuests({
  characterKey,
  gameVersion,
  display,
}: {
  characterKey: string;
  gameVersion?: string;
  display: PlayerDisplay;
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
  if (result && !result.total) return null;
  return (
    <section className="content-panel full-panel npc-section">
      <PanelTitle number="02" title="Quest appearances" />
      {error ? (
        <ErrorPanel
          message={error}
          onRetry={() => setRetry((value) => value + 1)}
        />
      ) : !result ? (
        <SkeletonGroup label="Loading quests" className="npc-quest-grid">
          {Array.from({ length: 4 }, (_, i) => (
            <NpcQuestCardSkeleton key={i} />
          ))}
        </SkeletonGroup>
      ) : (
        <>
          {!result.results.length && <p>No linked quest appearances found.</p>}
          <div className="npc-quest-grid">
            {result.results.map((quest) => (
              <Link
                className="npc-quest-card"
                key={quest.canonical_key}
                to={questHref(quest, locale, gameVersion)}
              >
                <PlayerText
                  value={quest.name}
                  fallback={`Quest ${quest.game_quest_id}`}
                  display={display}
                />
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

export function NpcInteractions({
  characterKey,
  npc,
  gameVersion,
  display = DEFAULT_DISPLAY,
}: {
  characterKey: string;
  npc: Npc;
  gameVersion?: string;
  display?: PlayerDisplay;
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
  const hasShops = (npc.shop_count || 0) > 0;
  return (
    <>
      {hasShops && (
        <NpcShops
          characterKey={characterKey}
          gameVersion={gameVersion}
          display={display}
          sectionNumber="01"
        />
      )}
      <section className="content-panel full-panel npc-section">
        <PanelTitle number={hasShops ? "02" : "01"} title="Dialogue" />
        <form
          className="dialogue-search"
          noValidate
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
          {(input || query) && (
            <button
              type="button"
              className="search-clear"
              aria-label="Clear dialogue search"
              onClick={() => {
                setInput("");
                setQuery("");
                setPage(1);
                searchInput.current?.focus();
              }}
            >
              <X size={16} aria-hidden="true" />
            </button>
          )}
          <button type="submit">Find lines</button>
        </form>
        {error ? (
          <ErrorPanel
            message={error}
            onRetry={() => setRetry((value) => value + 1)}
          />
        ) : !result ? (
          <SkeletonGroup label="Loading dialogue" className="npc-dialogue-list">
            {Array.from({ length: 3 }, (_, i) => (
              <NpcDialogueSkeleton key={i} />
            ))}
          </SkeletonGroup>
        ) : (
          <>
            {!result.results.length && <p>No linked dialogue found.</p>}
            <div className="npc-dialogue-list">
              {result.results.map((line) => (
                <article
                  className="npc-dialogue-card"
                  key={`${line.canonical_key}:${line.flow_state}:${line.speaker?.canonical_key || "none"}`}
                >
                  <p className="npc-dialogue-text">
                    <PlayerText
                      value={line.text}
                      fallback="Text unavailable"
                      display={display}
                    />
                  </p>
                  <div className="npc-dialogue-meta">
                    <span>
                      <PlayerText
                        value={speakerName(line.speaker?.label)}
                        display={display}
                      />
                    </span>
                    <small>
                      {line.speaker_relation === "confirmed_npc_speaker"
                        ? "Confirmed speaker"
                        : line.speaker_relation === "npc_actor_context"
                          ? "Associated line"
                          : "Conversation participant"}
                    </small>
                  </div>
                  {line.quests.map((quest) => (
                    <p className="npc-dialogue-quest" key={quest.canonical_key}>
                      <Link to={questHref(quest, locale, gameVersion)}>
                        <PlayerText
                          value={quest.name}
                          fallback={`Quest ${quest.game_quest_id}`}
                          display={display}
                        />
                      </Link>
                    </p>
                  ))}
                  <details className="npc-dialogue-source">
                    <summary>Source details</summary>
                    <span>Story state: {line.flow_state}</span>
                    {line.speaker?.canonical_key && (
                      <span>Speaker: {line.speaker.canonical_key}</span>
                    )}
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
      <NpcInstances
        key={characterKey}
        characterKey={characterKey}
        gameVersion={gameVersion}
      />
      <NpcQuests
        characterKey={characterKey}
        gameVersion={gameVersion}
        display={display}
      />
      <NpcFlowParticipants
        characterKey={characterKey}
        gameVersion={gameVersion}
        display={display}
      />
    </>
  );
}

export function NpcProfile({
  data,
  initialModel,
  gameVersion,
  display = DEFAULT_DISPLAY,
}: {
  data: { character: any; npc: Npc };
  initialModel?: CharacterModelData | null;
  gameVersion?: string;
  display?: PlayerDisplay;
}) {
  const { character, npc } = data;
  const name = gameTextPlainText(
    character.name,
    display,
    gameTextPlainText(
      character.canonical_name,
      display,
      `NPC ${npc.blueprint_id}`,
    ),
  );
  const isSharedTemplate = name.startsWith("Shared NPC template");
  const templateVariants = isSharedTemplate
    ? name
        .replace(/^Shared NPC template(\s*·\s*)?/, "")
        .split("/")
        .map((s) => s.trim().replace(/\.$/, ""))
        .filter(Boolean)
    : [];
  const slugKey = (
    character.slug ||
    character.canonical_key ||
    ""
  ).toLowerCase();
  const primaryTitle = isSharedTemplate
    ? templateVariants.find((v) => slugKey.includes(v.toLowerCase())) ||
      templateVariants[0] ||
      name
    : name;
  const [revision, setRevision] = useState(0);
  return (
    <div className="page-container character-profile-page">
      <Breadcrumbs
        items={[
          { label: "Archive", to: "/" },
          { label: "Characters", to: "/catalog/character" },
          {
            label: isSharedTemplate ? (
              primaryTitle
            ) : (
              <PlayerText
                value={character.name}
                fallback={name}
                display={display}
              />
            ),
          },
        ]}
      />
      <div className="profile-hero character-profile-hero npc-profile-hero">
        <CharacterProfileVisual
          characterKey={character.canonical_key}
          name={isSharedTemplate ? primaryTitle : name}
          portraitUrl={null}
          initialModel={initialModel}
          refreshKey={revision}
        />
        <div className="profile-main">
          <div className="character-profile-title-row">
            <h1>
              {isSharedTemplate ? (
                <>
                  {primaryTitle}
                  <span className="npc-shared-template-pill">
                    Shared Template
                  </span>
                </>
              ) : (
                <PlayerText
                  value={character.name}
                  fallback={name}
                  display={display}
                />
              )}
              <span className="heading-period">.</span>
            </h1>
            <CharacterModelImport
              characterKey={character.canonical_key}
              variant="actions"
              onComplete={() => setRevision((value) => value + 1)}
            />
          </div>
          <p>
            <PlayerText
              value={character.description}
              fallback="An NPC recorded in the Solaris Atlas archive."
              display={display}
            />
          </p>
          {isSharedTemplate && templateVariants.length > 1 && (
            <p className="npc-shared-variants-note">
              Shared character configuration · Identical model also appears as:{" "}
              <b>
                {templateVariants.filter((v) => v !== primaryTitle).join(", ")}
              </b>
            </p>
          )}
          {npc.speakers && npc.speakers.length > 0 && (
            <div className="npc-speaker-connections">
              <span className="npc-speaker-label">Spoken Voice:</span>
              {npc.speakers.map((spk, idx) => {
                const spkSlug =
                  typeof spk === "string" ? spk : spk.slug || spk.canonical_key;
                const spkLabel =
                  typeof spk === "string"
                    ? spk
                    : spk.label?.content ||
                      spk.slug?.replace(/^\d+-/, "") ||
                      spk.canonical_key;
                return (
                  <Link
                    key={idx}
                    to={`/nodes/${encodeURIComponent(spkSlug)}`}
                    className="npc-speaker-chip"
                  >
                    <Volume2 size={13} aria-hidden="true" />
                    <span>{spkLabel}</span>
                  </Link>
                );
              })}
            </div>
          )}
          {npc.name_scope === "instance" && (
            <p>
              This name is recorded for one appearance.
              {npc.name_variants.length > 1 && (
                <>
                  {" "}
                  Other recorded names:{" "}
                  {npc.name_variants
                    .filter((variant) => variant !== name)
                    .map((variant, index) => (
                      <span key={`${variant}-${index}`}>
                        {index > 0 && ", "}
                        <PlayerText value={variant} display={display} />
                      </span>
                    ))}
                  .
                </>
              )}
            </p>
          )}
          <div className="profile-tags">
            <span>NPC</span>
          </div>
        </div>
      </div>
      <NpcInteractions
        key={character.canonical_key}
        characterKey={character.canonical_key}
        npc={npc}
        gameVersion={gameVersion}
        display={display}
      />
    </div>
  );
}
