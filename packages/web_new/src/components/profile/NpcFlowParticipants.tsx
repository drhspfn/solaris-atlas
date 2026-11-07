"use client";

import { ChevronDown, MessageCircle, ArrowUpRight } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "@/api/client";
import { ErrorPanel } from "@/components/ui/Feedback";
import { Skeleton, SkeletonGroup, TextSkeleton } from "@/components/ui/Skeleton";
import { Pagination } from "@/components/ui/Pagination";
import { PanelTitle } from "@/components/ui/PanelTitle";
import {
  PlayerText,
  type PlayerDisplay,
} from "@/components/dialogue/PlayerText";
import { conversationSpeakerName } from "@/data/npcContext";
import { gameTextPlainText } from "@/data/gameText";
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
const UNAVAILABLE_TEXT = "Dialogue text is unavailable in this language.";

function speakerName(label?: string) {
  const normalized = conversationSpeakerName(label);
  return normalized === "Rover" && /\{PlayerName\}/i.test(label || "")
    ? "{PlayerName}"
    : normalized;
}

export function NpcConversationCardSkeleton() {
  return <article className="npc-scene-card">
    <div className="npc-scene-intro"><Skeleton style={{ width: 19, height: 19, flexShrink: 0 }} /><Skeleton style={{ width: "55%", height: 16 }} /></div>
    <p className="npc-scene-speakers"><Skeleton style={{ width: "40%", height: 12 }} /></p>
    <div className="npc-scene-preview"><TextSkeleton lines={2} /></div>
    <div className="npc-scene-reader"><Skeleton style={{ width: 125, height: 13, marginTop: 14 }} /></div>
  </article>;
}

export function NpcConversationLineSkeleton() {
  return <div className="npc-scene-line"><strong><Skeleton style={{ width: 90, height: 12 }} /></strong><TextSkeleton lines={2} /></div>;
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
  const hasText = (line: Line) => !!gameTextPlainText(line.text, display).trim();
  const preview = scene.preview.slice(0, 2);
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
        {!preview.some(hasText) ? <p>{UNAVAILABLE_TEXT}</p> : preview.map((line, index) => (
          <p key={line.canonical_key || index}>
            <PlayerText value={hasText(line) ? line.text : null} fallback={UNAVAILABLE_TEXT} display={display} />
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
            <SkeletonGroup label="Loading conversation" className="npc-scene-lines">{Array.from({ length: 3 }, (_, i) => <NpcConversationLineSkeleton key={i} />)}</SkeletonGroup>
          ) : (
            <>
              <div className="npc-scene-lines">
                {!data.results.some(hasText) ? <p>{UNAVAILABLE_TEXT}</p> : data.results.map((line, index) => (
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
                      <PlayerText value={hasText(line) ? line.text : null} fallback={UNAVAILABLE_TEXT} display={display} />
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
        <SkeletonGroup label="Loading conversations" className="npc-scene-grid">{Array.from({ length: 4 }, (_, i) => <NpcConversationCardSkeleton key={i} />)}</SkeletonGroup>
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
