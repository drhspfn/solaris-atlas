"use client";

import { ArrowRight, Search, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "@/api/client";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Pagination } from "@/components/ui/Pagination";
import { Pill } from "@/components/ui/Pill";
import { EmptyInline, ErrorPanel } from "@/components/ui/Feedback";
import { Skeleton, SkeletonGroup, TextSkeleton } from "@/components/ui/Skeleton";
import {
  PlayerText,
  type PlayerDisplay,
} from "@/components/dialogue/PlayerText";
import { entityPath } from "@/data/entities";
import { localizedText } from "@/data/localized";
import { Link } from "@/lib/router";
import { questPath } from "@/lib/routeKey";
import "@/styles/catalog-controls.css";
import "@/styles/character-connections.css";

export type ConnectionsSummary = {
  total: number;
  next_cursor: string | null;
  has_more: boolean;
};
type ConnectionsPage = ConnectionsSummary & { results: any[] };
const pageSize = 12;

function rowText(row: any): string {
  const target = row.quest || row.character || row.speaker || row.target || row;
  return [
    localizedText(target.name),
    localizedText(target.title),
    target.label,
    ...(row.shared_scenes || []).flatMap((scene: any) => [
      ...(scene.quest_refs || []).map((quest: any) =>
        localizedText(quest.name),
      ),
      ...(scene.dialogue || []).map((line: any) => localizedText(line.text)),
    ]),
  ]
    .filter(Boolean)
    .join(" ")
    .toLocaleLowerCase();
}

export function CharacterConnectionsSkeleton({ kind }: { kind: "quests" | "characters" }) {
  return <SkeletonGroup label={kind === "quests" ? "Loading story appearances" : "Loading character connections"} className="character-connections-list">
    {Array.from({ length: 3 }, (_, i) => kind === "quests" ?
      <div className="connection-row" key={i}><div><Skeleton style={{ width: 180, height: 13 }} /><Skeleton style={{ width: 100, height: 10 }} /></div></div> :
      <article className="character-connection-card" key={i}><header><Skeleton style={{ width: 140, height: 13 }} /><Skeleton style={{ width: 80, height: 12 }} /></header><div className="character-connection-preview"><TextSkeleton lines={2} /></div></article>)}
  </SkeletonGroup>;
}

export function CharacterConnections({
  characterKey,
  section,
  locale,
  gameVersion,
  initialRows,
  summary,
  playerDisplay,
}: {
  characterKey: string;
  section: "quests" | "characters";
  locale: string;
  gameVersion?: string | null;
  initialRows: any[];
  summary?: ConnectionsSummary;
  playerDisplay: PlayerDisplay;
}) {
  const label =
    section === "quests" ? "story appearances" : "shared scenes and links";
  // Guest player titles are randomized in the browser; hydrate source previews consistently.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const previewDisplay: PlayerDisplay = mounted ? playerDisplay : { name: "Rover", colorMode: "accent", customColor: "" };
  const [draft, setDraft] = useState("");
  const [composing, setComposing] = useState(false);
  const [selection, setSelection] = useState<{
    q: string;
    cursor: string | null;
    history: (string | null)[];
  }>({ q: "", cursor: null, history: [] });
  const [result, setResult] = useState<{
    key: string;
    data: ConnectionsPage;
  } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [retry, setRetry] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const request = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const key = JSON.stringify([characterKey, section, locale, gameVersion, selection.q, selection.cursor]);
  const server = summary !== undefined;

  function cancel() {
    request.current?.abort();
    generation.current++;
    setLoading(false);
    setError("");
    setNotice("");
  }
  function search(q: string) {
    cancel();
    setSelection({ q: q.trim(), cursor: null, history: [] });
  }
  useEffect(() => {
    if (composing) return;
    const timer = setTimeout(() => {
      setSelection((previous) =>
        previous.q === draft.trim() && !request.current?.signal.aborted
          ? previous
          : { q: draft.trim(), cursor: null, history: [] },
      );
    }, 300);
    return () => clearTimeout(timer);
  }, [draft, composing]);

  useEffect(() => {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    const current = ++generation.current;
    setError("");
    setLoading(false);
    if (!server || (!selection.q && selection.cursor === null && retry === 0))
      return () => controller.abort();
    setLoading(true);
    const params = new URLSearchParams({
      section,
      q: selection.q,
      limit: String(pageSize),
      locale,
    });
    if (selection.cursor !== null) params.set("cursor", selection.cursor);
    if (gameVersion) params.set("game_version", gameVersion);
    api<ConnectionsPage>(
      `/characters/${encodeURIComponent(characterKey)}/connections?${params}`,
      { signal: controller.signal },
    )
      .then((data) => {
        if (!controller.signal.aborted && generation.current === current) {
          setResult({
            key,
            data: { ...data, results: data.results.slice(0, pageSize) },
          });
        }
      })
      .catch((cause: unknown) => {
        if (!controller.signal.aborted && generation.current === current) {
          if (cause && typeof cause === "object" && "status" in cause && cause.status === 409 && selection.cursor !== null) {
            setNotice("The archive was updated. Showing the first page of fresh results.");
            setSelection({ q: selection.q, cursor: null, history: [] });
            setRetry((value) => value + 1);
          } else {
            setError(cause instanceof Error ? cause.message : "Connections could not be loaded.");
          }
        }
      })
      .finally(() => {
        if (!controller.signal.aborted && generation.current === current)
          setLoading(false);
      });
    return () => controller.abort();
  }, [characterKey, section, locale, gameVersion, server, selection, retry]);

  const filtered = server
    ? []
    : initialRows.filter(
        (row) =>
          !selection.q ||
          rowText(row).includes(selection.q.toLocaleLowerCase()),
      );
  const offset = server ? 0 : Number(selection.cursor || 0);
  const localPage: ConnectionsPage = {
    results: filtered.slice(offset, offset + pageSize),
    total: filtered.length,
    has_more: offset + pageSize < filtered.length,
    next_cursor:
      offset + pageSize < filtered.length ? String(offset + pageSize) : null,
  };
  const page = server
    ? result?.key === key
      ? result.data
      : !selection.q && selection.cursor === null
        ? { ...summary, results: initialRows.slice(0, pageSize) }
        : { results: [], total: 0, has_more: false, next_cursor: null }
    : localPage;
  const pending = loading || composing || draft.trim() !== selection.q ||
    (server && !error && (!!selection.q || selection.cursor !== null) && result?.key !== key);
  const pageNumber = selection.history.length + 1;

  return (
    <div className="character-connections">
      <form
        className="search-box ui-search-form character-connections-search"
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          if (!composing) search(draft);
        }}
      >
        <Search size={17} aria-hidden="true" />
        <Input
          ref={input}
          type="search"
          value={draft}
          maxLength={200}
          aria-label={`Search ${label}`}
          placeholder={`Search ${label}`}
          onCompositionStart={() => {
            cancel();
            setComposing(true);
          }}
          onCompositionEnd={(event) => {
            setDraft(event.currentTarget.value);
            setComposing(false);
          }}
          onChange={(event) => {
            cancel();
            setDraft(event.target.value);
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter" && event.nativeEvent.isComposing)
              event.preventDefault();
          }}
        />
        {draft && (
          <Button
            variant="ghost"
            size="icon"
            className="search-clear search-action"
            icon={<X size={16} />}
            aria-label={`Clear ${label} search`}
            onClick={() => {
              setDraft("");
              setComposing(false);
              search("");
              input.current?.focus();
            }}
          />
        )}
        <Button
          type="submit"
          variant="ghost"
          size="icon"
          className="search-action"
          icon={<ArrowRight size={17} />}
          aria-label={`Search ${label}`}
        />
      </form>
      <div className="character-connections-caption">
        <Pill>
          {page.total} {section === "quests" ? "appearances" : "connections"}
        </Pill>
        {!server && <small>Search within this snapshot.</small>}
      </div>
      {section === "characters" && (
        <p className="character-connections-note">
          Shared scenes show co-presence, not a personal relationship.
        </p>
      )}
      {error && (
        <ErrorPanel
          message={error}
          onRetry={() => setRetry((value) => value + 1)}
        />
      )}
      {notice && <p role="status">{notice}</p>}
      {pending ? <CharacterConnectionsSkeleton kind={section} /> : <div className="character-connections-list">
        {!pending && !error && !page.results.length && (
          <EmptyInline
            text={
              selection.q
                ? "No matching connections. Try another search."
                : "No confirmed connections in this snapshot."
            }
          />
        )}
        {!error &&
          page.results.map((row: any, index: number) => {
            if (section === "quests") {
              const quest = row.quest || row;
              return (
                <Link
                  className="connection-row"
                  to={questPath(quest)}
                  key={quest.canonical_key || quest.game_quest_id || index}
                >
                  <div>
                    <strong>
                      {localizedText(
                        quest.name,
                        localizedText(quest.title, "Quest transcript"),
                      )}
                    </strong>
                    <small>
                      {quest.classification?.kind &&
                      quest.classification.kind !== "unclassified"
                        ? quest.classification.label
                        : quest.quest_type || "Story appearance"}
                    </small>
                  </div>
                  <ArrowRight size={15} aria-hidden="true" />
                </Link>
              );
            }
            const target =
              row.character ||
              row.speaker ||
              row.node ||
              row.target ||
              row.area;
            if (!target) return null;
            const scenes = (row.shared_scenes || row.shared_states || []).slice(
              0,
              2,
            );
            const coPresent = !!(
              row.shared_scenes?.length ||
              row.shared_states?.length ||
              row.flow_states?.length ||
              row.basis === "shared_flow_state_dialogue"
            );
            const name = localizedText(
              target.label,
              localizedText(target.name, "Character"),
            );
            const href = entityPath({
              id: target.id || 0,
              canonical_key: target.canonical_key,
              slug: target.slug,
              href: target.href,
              node_type: target.node_type || target.type || (row.speaker ? "speaker" : "character"),
            });
            return (
              <article
                className="character-connection-card"
                key={`${target.canonical_key}-${row.relation || index}`}
              >
                <header>
                  <Link to={href}>{name}</Link>
                  <Pill>
                    {coPresent
                      ? "Co-presence"
                      : (row.relation || "Explicit link").replaceAll("_", " ")}
                  </Pill>
                </header>
                {scenes.map((scene: any, i: number) => (
                  <div className="character-connection-preview" key={i}>
                    {(scene.quest_refs || [])
                      .slice(0, 3)
                      .map((quest: any, j: number) => (
                        <Link
                          to={questPath(quest)}
                          key={quest.canonical_key || j}
                        >
                          {localizedText(quest.name, "Open quest transcript")}
                        </Link>
                      ))}
                    {(scene.dialogue || [])
                      .slice(0, 1)
                      .map((line: any, j: number) => (
                        <p key={j}>
                          <PlayerText
                            display={previewDisplay}
                            value={line.text}
                            fallback={
                              line.text?.inline_text ||
                              "Text unavailable in this language."
                            }
                          />
                        </p>
                      ))}
                  </div>
                ))}
              </article>
            );
          })}
      </div>}
      <Pagination
        mode="cursor"
        ariaLabel={`${label} pagination`}
        disabled={pending}
        hasPrevious={selection.history.length > 0}
        hasNext={page.has_more && page.next_cursor !== null}
        label={
          page.total
            ? `Page ${pageNumber} · ${page.total} results`
            : "0 results"
        }
        onPrevious={() => {
          cancel();
          setSelection((previous) => ({
            ...previous,
            cursor: previous.history.at(-1) ?? null,
            history: previous.history.slice(0, -1),
          }));
        }}
        onNext={() => {
          if (!page.next_cursor) return;
          cancel();
          setSelection((previous) => ({
            ...previous,
            cursor: page.next_cursor,
            history: [...previous.history, previous.cursor],
          }));
        }}
      />
    </div>
  );
}
