"use client";

import { ArrowLeft, ArrowRight, Film, Play, Search } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/api/client";
import { useLocale } from "@/hooks/useLocale";
import { Link, useSearchParams } from "@/lib/router";
import { questPath } from "@/lib/routeKey";
import { Select } from "@/components/ui/Select";
import { ErrorPanel, PageLoader } from "@/components/ui/Feedback";
import { InfiniteScroll } from "@/components/ui/InfiniteScroll";
import { Cutscene } from "./QuestCutscenes";
import { cutsceneAnchor } from "./cutsceneAnchor";
import type { QuestMediaEvent } from "./QuestMediaReferences";

type Quest = {
  game_quest_id: string;
  slug?: string;
  name: string;
  context_inferred?: boolean;
};
type Scene = {
  node_id: number;
  name: string;
  title: string;
  title_kind: string;
  game_version: string;
  first_observed_game_version: string;
  ordering_basis: string;
  preview_url?: string | null;
  quests: Quest[];
};
export type CutscenePage = {
  game_version: string | null;
  imported_game_versions: string[];
  total: number;
  offset: number;
  limit: number;
  next_cursor: string | null;
  items: Scene[];
};
export type CutsceneDetail = Scene & {
  game_version: string;
  event: QuestMediaEvent;
  passages: (Quest & { focus_line?: string | null } & Pick<
      QuestMediaEvent,
      "flow_state" | "action_index" | "reference"
    >)[];
};

export function CutsceneArchive({
  initial,
  versions,
}: {
  initial: CutscenePage | null;
  versions: string[];
}) {
  const locale = useLocale();
  const [params, setParams] = useSearchParams();
  const query = params?.toString() || "";
  const [data, setData] = useState(initial);
  const [loading, setLoading] = useState(!initial);
  const [error, setError] = useState("");
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const pageRequest = useRef<AbortController | null>(null);
  const [searchText, setSearchText] = useState(params?.get("q") || "");
  useEffect(() => setSearchText(params?.get("q") || ""), [query]);
  function filter(key: string, value: string) {
    setParams((current) => {
      const next = new URLSearchParams(current);
      if (value) next.set(key, value);
      else next.delete(key);
      next.delete("offset");
      next.delete("cursor");
      return next;
    });
  }
  useEffect(() => {
    const controller = new AbortController();
    const search = new URLSearchParams(query);
    search.delete("cursor");
    search.set("locale", locale);
    if (refresh) search.set("_refresh", String(refresh));
    setLoading(true);
    setError("");
    setMoreError("");
    setLoadingMore(false);
    setData(null);
    api<CutscenePage>(`/cutscenes?${search}`, { signal: controller.signal, cacheTtl: 0 })
      .then(setData)
      .catch((cause) => {
        if (!controller.signal.aborted) setError(String(cause));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => {
      controller.abort();
      pageRequest.current?.abort();
      pageRequest.current = null;
    };
  }, [query, locale, refresh]);
  async function loadMore() {
    if (!data?.next_cursor || pageRequest.current) return;
    const controller = new AbortController();
    pageRequest.current = controller;
    setLoadingMore(true);
    setMoreError("");
    const search = new URLSearchParams(query);
    search.delete("offset");
    search.set("cursor", data.next_cursor);
    search.set("locale", locale);
    try {
      const next = await api<CutscenePage>(`/cutscenes?${search}`, {
        signal: controller.signal,
        cacheTtl: 0,
      });
      if (controller.signal.aborted) return;
      setData((current) => {
        if (!current) return current;
        const seen = new Set(current.items.map((item) => item.node_id));
        return {
          ...next,
          offset: current.offset,
          items: [
            ...current.items,
            ...next.items.filter((item) => !seen.has(item.node_id)),
          ],
        };
      });
    } catch (cause) {
      if (!controller.signal.aborted) {
        if (cause instanceof ApiError && cause.code === "cursor_expired") setRefresh((value) => value + 1);
        else setMoreError(String(cause));
      }
    } finally {
      if (pageRequest.current === controller) {
        pageRequest.current = null;
        setLoadingMore(false);
      }
    }
  }
  return (
    <main className="page-container cutscene-archive">
      <header className="cutscene-archive-heading">
        <span className="eyebrow">SOLARIS / CINEMATIC ARCHIVE</span>
        <h1>Cutscenes</h1>
        <p>Watch the story. Return to the quest where it unfolds.</p>
      </header>
      <form
        className="cutscene-filters"
        onSubmit={(event) => {
          event.preventDefault();
          filter("q", searchText.trim());
        }}
      >
        <label className="cutscene-filter">
          Scene code
          <div className="cutscene-search">
            <Search size={17} aria-hidden="true" />
            <input
              name="q"
              placeholder="M0311…"
              value={searchText}
              onChange={(event) => setSearchText(event.target.value)}
              maxLength={128}
            />
          </div>
        </label>
        <div className="cutscene-filter">
          <span>Game version</span>
          <Select
            label="Game version"
            value={params?.get("game_version") || ""}
            onChange={(value) => filter("game_version", value)}
            options={[
              { value: "", label: "All versions" },
              ...(data?.imported_game_versions || versions).map((version) => ({
                value: version,
                label: version,
              })),
            ]}
          />
        </div>
        <div className="cutscene-filter">
          <span>Availability</span>
          <Select
            label="Availability"
            value={params?.get("playable") || "true"}
            onChange={(value) => filter("playable", value)}
            options={[
              { value: "true", label: "Ready to watch" },
              { value: "false", label: "All scene references" },
            ]}
          />
        </div>
        <button type="submit" className="cutscene-apply">
          Search
        </button>
      </form>
      {error ? (
        <ErrorPanel message={error} />
      ) : loading ? (
        <div role="status" aria-live="polite">
          <div className="cutscene-list" aria-hidden="true">
            {Array.from({ length: 6 }, (_, index) => (
              <div className="cutscene-card-skeleton skeleton" key={index} />
            ))}
          </div>
          <p className="cutscene-loading">Loading scenes…</p>
        </div>
      ) : (
        data && (
          <>
            <div className="cutscene-count">
              {data.total} scenes{" "}
              <span>
                {data.game_version
                  ? `Snapshot ${data.game_version}`
                  : "All versions"}{" "}
                · Newest first · Spoilers ahead
              </span>
            </div>
            <p className="cutscene-snapshot-note">
              Newest imported versions first, then story-map order. Scenes
              without a confirmed position use a stable fallback. Video assets
              may come from a newer client.
            </p>
            {!data.items.length && (
              <div className="empty-state">
                <Film size={32} />
                <h2>No matching scenes</h2>
                <p>
                  Try another code, snapshot, or include all scene references.
                </p>
              </div>
            )}
            <InfiniteScroll
              hasMore={Boolean(data.next_cursor)}
              loading={loadingMore}
              onLoadMore={loadMore}
              error={moreError}
              loader={<p className="cutscene-loading">Loading more scenes…</p>}
              skeleton={
                <div className="cutscene-list" aria-hidden="true">
                  {Array.from({ length: 3 }, (_, index) => (
                    <div
                      className="cutscene-card-skeleton skeleton"
                      key={index}
                    />
                  ))}
                </div>
              }
            >
              <div className="cutscene-list">
                {data.items.map((scene) => (
                  <Link
                    key={scene.node_id}
                    to={`/cutscenes/${encodeURIComponent(scene.name)}?game_version=${encodeURIComponent(scene.game_version)}&locale=${locale}`}
                    className="cutscene-list-row"
                  >
                    {scene.preview_url && (
                      <span className="cutscene-card-image" aria-hidden="true">
                        {/* Decorative artwork; the title remains readable without the image. */}
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img src={scene.preview_url} alt="" loading="lazy" />
                      </span>
                    )}
                    <span className="cutscene-play-mark">
                      <Play size={19} aria-hidden="true" />
                    </span>
                    <span className="cutscene-list-copy">
                      <small>
                        {scene.name} · v{scene.first_observed_game_version}
                        {scene.title_kind === "analysis" ? " · AI title" : ""}
                      </small>
                      <h2>{scene.title}</h2>
                      <span>
                        {scene.quests[0]?.name === scene.title
                          ? "Quest scene"
                          : scene.quests[0]?.name || "Story position not yet linked"}
                        {scene.ordering_basis === "fallback"
                          ? " · Order unconfirmed"
                          : ""}
                      </span>
                    </span>
                    <ArrowRight size={20} aria-hidden="true" />
                  </Link>
                ))}
              </div>
            </InfiniteScroll>
          </>
        )
      )}
    </main>
  );
}

export function CutsceneRecord({
  name,
  initial,
}: {
  name: string;
  initial: CutsceneDetail | null;
}) {
  const locale = useLocale();
  const [params] = useSearchParams();
  const version = params?.get("game_version") || "";
  const [data, setData] = useState(initial);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setError("");
    const search = new URLSearchParams({ locale });
    if (version) search.set("game_version", version);
    api<CutsceneDetail>(`/cutscenes/${encodeURIComponent(name)}?${search}`, {
      signal: controller.signal,
    })
      .then(setData)
      .catch((cause) => {
        if (!controller.signal.aborted) setError(String(cause));
      });
    return () => controller.abort();
  }, [name, version, locale]);
  if (error)
    return (
      <main className="page-container">
        <ErrorPanel message={error} />
      </main>
    );
  if (!data) return <PageLoader />;
  return (
    <main className="page-container cutscene-archive">
      <Link
        className="cutscene-back"
        to={`/cutscenes?game_version=${data.game_version}`}
      >
        <ArrowLeft size={16} />
        Cutscene archive
      </Link>
      <header className="cutscene-archive-heading">
        <span className="eyebrow">
          {data.name} / SNAPSHOT {data.game_version}
        </span>
        <h1>{data.title}</h1>
        {data.title_kind === "analysis" && (
          <p>AI title · Grounded in the scene analysis</p>
        )}
      </header>
      {data.event.playback ? (
        <Cutscene event={{ ...data.event, title: data.title }} />
      ) : (
        <div className="empty-inline">
          This scene is recorded in the game data, but a playable export is not
          available yet.
        </div>
      )}
      <section className="cutscene-context">
        <h2>Where this scene appears</h2>
        <p>
          Source-linked quest context. Conditional dungeon branches do not
          establish a complete playthrough order.
        </p>
        {data.passages.length ? (
          data.passages.map((passage) => (
            <Link
              className="cutscene-context-link"
              key={`${passage.game_quest_id}:${passage.flow_state}:${passage.action_index}`}
              to={`${questPath(passage)}?game_version=${data.game_version}&locale=${locale}${passage.focus_line ? `&line=${encodeURIComponent(passage.focus_line)}` : ""}#${cutsceneAnchor(passage)}`}
            >
              <span>
                {passage.name}
                <small>
                  {passage.context_inferred
                    ? "Scenario context inferred from game progress; exact playthrough order is unconfirmed"
                    : "Open at this scene in the quest"}
                </small>
              </span>
              <ArrowRight size={19} />
            </Link>
          ))
        ) : (
          <p>No confirmed quest link is available in this snapshot.</p>
        )}
      </section>
    </main>
  );
}
