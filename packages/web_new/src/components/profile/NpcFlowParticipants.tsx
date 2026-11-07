"use client";

import { ChevronDown, MessageCircle, ArrowUpRight } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "@/api/client";
import { ErrorPanel } from "@/components/ui/Feedback";
import { Pagination } from "@/components/ui/Pagination";
import { PanelTitle } from "@/components/ui/PanelTitle";
import {
  PlayerText,
  type PlayerDisplay,
} from "@/components/dialogue/PlayerText";
import { conversationSpeakerName } from "@/data/npcContext";
import { useLocale } from "@/hooks/useLocale";
import { Link } from "@/lib/router";
import { questPath } from "@/lib/routeKey";
import "@/styles/npc-context.css";

type Speaker = { canonical_key: string; label?: string };
type Line = { canonical_key?: string; text: unknown; speaker: Speaker | null };
type Quest = {
  canonical_key: string;
  game_quest_id?: number;
  name: unknown;
  href?: string;
  slug?: string;
};
type Scene = {
  state_id: number;
  title: string;
  preview: Line[];
  speakers: Speaker[];
  quests: Quest[];
};
type Page<T> = { results: T[]; total: number };
const PAGE_SIZE = 8;
const LINES_PER_PAGE = 20;

function speakerName(label?: string) {
  const normalized = conversationSpeakerName(label);
  return normalized === "Rover" && /\{PlayerName\}/i.test(label || "")
    ? "{PlayerName}"
    : normalized;
}

function Conversation({
  scene,
  characterKey,
  gameVersion,
  display,
}: {
  scene: Scene;
  characterKey: string;
  gameVersion?: string;
  display: PlayerDisplay;
}) {
  const locale = useLocale();
  const [open, setOpen] = useState(false);
  const [page, setPage] = useState(1);
  const [data, setData] = useState<Page<Line> | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    const params = new URLSearchParams({
      locale,
      state_id: String(scene.state_id),
      limit: String(LINES_PER_PAGE),
      offset: String((page - 1) * LINES_PER_PAGE),
    });
    if (gameVersion) params.set("game_version", gameVersion);
    setData(null);
    setError("");
    api<Page<Line>>(
      `/npcs/${encodeURIComponent(characterKey)}/dialogue?${params}`,
      { signal: controller.signal },
    )
      .then((value) => {
        if (!controller.signal.aborted) setData(value);
      })
      .catch((cause: Error) => {
        if (!controller.signal.aborted) setError(cause.message);
      });
    return () => controller.abort();
  }, [characterKey, gameVersion, scene.state_id, locale, open, page, retry]);
  const speakers = [
    ...new Set(scene.speakers.map((speaker) => speakerName(speaker.label))),
  ];
  const questHref = (quest: Quest) => {
    const params = new URLSearchParams({ locale });
    if (gameVersion) params.set("game_version", gameVersion);
    const line = scene.preview[0]?.canonical_key;
    if (line) params.set("line", line);
    return `${questPath(quest)}?${params}`;
  };
  return (
    <article className="npc-scene-card">
      <div className="npc-scene-intro">
        <MessageCircle size={19} aria-hidden="true" />
        <h3>
          <PlayerText
            value={scene.title}
            fallback="Conversation"
            display={display}
          />
        </h3>
      </div>
      {!!speakers.length && (
        <p className="npc-scene-speakers">
          {speakers.map((speaker, index) => (
            <span key={speaker}>
              {index > 0 && " · "}
              <PlayerText value={speaker} display={display} />
            </span>
          ))}
        </p>
      )}
      <div className="npc-scene-preview">
        {scene.preview.slice(0, 2).map((line, index) => (
          <p key={line.canonical_key || index}>
            <PlayerText value={line.text} display={display} />
          </p>
        ))}
      </div>
      {!!scene.quests.length && (
        <div className="npc-scene-quests">
          {scene.quests.map((quest) => (
            <Link key={quest.canonical_key} to={questHref(quest)}>
              <PlayerText
                value={quest.name}
                fallback="Open quest"
                display={display}
              />
              <ArrowUpRight size={14} aria-hidden="true" />
            </Link>
          ))}
        </div>
      )}
      <details
        className="npc-scene-reader"
        onToggle={(event) => setOpen(event.currentTarget.open)}
      >
        <summary>
          Read conversation
          <ChevronDown
            size={16}
            className="npc-scene-chevron"
            aria-hidden="true"
          />
        </summary>
        {open &&
          (error ? (
            <ErrorPanel
              message={error}
              onRetry={() => setRetry((value) => value + 1)}
            />
          ) : !data ? (
            <p className="npc-context-loading" role="status">
              Loading conversation…
            </p>
          ) : (
            <>
              <div className="npc-scene-lines">
                {data.results.map((line, index) => (
                  <div
                    className="npc-scene-line"
                    key={line.canonical_key || index}
                  >
                    <strong>
                      <PlayerText
                        value={speakerName(line.speaker?.label)}
                        display={display}
                      />
                    </strong>
                    <p>
                      <PlayerText value={line.text} display={display} />
                    </p>
                  </div>
                ))}
              </div>
              {data.total > LINES_PER_PAGE && (
                <Pagination
                  mode="pages"
                  page={page}
                  pageSize={LINES_PER_PAGE}
                  total={data.total}
                  onPageChange={setPage}
                  ariaLabel="Conversation pages"
                />
              )}
            </>
          ))}
      </details>
    </article>
  );
}

export function NpcFlowParticipants({
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
  const [data, setData] = useState<Page<Scene> | null>(null);
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
    setData(null);
    setError("");
    api<Page<Scene>>(
      `/npcs/${encodeURIComponent(characterKey)}/scenes?${params}`,
      { signal: controller.signal },
    )
      .then((value) => {
        if (!controller.signal.aborted) setData(value);
      })
      .catch((cause: Error) => {
        if (!controller.signal.aborted) setError(cause.message);
      });
    return () => controller.abort();
  }, [characterKey, gameVersion, locale, page, retry]);
  if (data && !data.total) return null;
  return (
    <section className="content-panel full-panel npc-context-section npc-conversations">
      <PanelTitle number="04" title="Conversations" />
      {data && (
        <p className="npc-context-count">
          {data.total} {data.total === 1 ? "conversation" : "conversations"}
        </p>
      )}
      {error ? (
        <ErrorPanel
          message={error}
          onRetry={() => setRetry((value) => value + 1)}
        />
      ) : !data ? (
        <p className="npc-context-loading" role="status">
          Loading conversations…
        </p>
      ) : (
        <>
          <div className="npc-scene-grid">
            {data.results.map((scene) => (
              <Conversation
                key={`${characterKey}:${locale}:${scene.state_id}`}
                scene={scene}
                characterKey={characterKey}
                gameVersion={gameVersion}
                display={display}
              />
            ))}
          </div>
          {data.total > PAGE_SIZE && (
            <Pagination
              mode="pages"
              page={page}
              pageSize={PAGE_SIZE}
              total={data.total}
              onPageChange={setPage}
              ariaLabel="Conversation list pages"
            />
          )}
        </>
      )}
    </section>
  );
}
