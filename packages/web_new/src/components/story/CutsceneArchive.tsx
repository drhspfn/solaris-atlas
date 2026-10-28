"use client";

import { ArrowLeft, ArrowRight, Film, Play, Search } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "@/api/client";
import { useLocale } from "@/hooks/useLocale";
import { Link, useSearchParams } from "@/lib/router";
import { questPath } from "@/lib/routeKey";
import { ErrorPanel, PageLoader } from "@/components/ui/Feedback";
import { Cutscene } from "./QuestCutscenes";
import { cutsceneAnchor } from "./cutsceneAnchor";
import type { QuestMediaEvent } from "./QuestMediaReferences";

type Quest = { game_quest_id: string; slug?: string; name: string };
type Scene = {
  node_id: number;
  name: string;
  title: string;
  title_kind: string;
  quests: Quest[];
};
export type CutscenePage = {
  game_version: string;
  total: number;
  offset: number;
  limit: number;
  items: Scene[];
};
export type CutsceneDetail = Scene & {
  game_version: string;
  event: QuestMediaEvent;
  passages: (Quest & { focus_line?: string | null } &
    Pick<QuestMediaEvent, "flow_state" | "action_index" | "reference">)[];
};

export function CutsceneArchive({
  initial,
  versions,
}: {
  initial: CutscenePage | null;
  versions: string[];
}) {
  const locale = useLocale();
  const [params] = useSearchParams();
  const query = params?.toString() || "";
  const [data, setData] = useState(initial);
  const [loading, setLoading] = useState(!initial);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    const search = new URLSearchParams(query);
    search.set("locale", locale);
    setLoading(true);
    setError("");
    api<CutscenePage>(`/cutscenes?${search}`, { signal: controller.signal })
      .then(setData)
      .catch((cause) => {
        if (!controller.signal.aborted) setError(String(cause));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [query, locale]);
  function pageLink(offset: number) {
    const next = new URLSearchParams(query);
    next.set("offset", String(offset));
    return `/cutscenes?${next}`;
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
        action="/cutscenes"
        method="get"
        key={query}
      >
        <label>
          Scene code
          <div className="cutscene-search">
            <Search size={17} aria-hidden="true" />
            <input
              name="q"
              placeholder="M0311…"
              defaultValue={params?.get("q") || ""}
              maxLength={128}
            />
          </div>
        </label>
        <label>
          Game data snapshot
          <select
            name="game_version"
            defaultValue={params?.get("game_version") || ""}
          >
            <option value="">Latest imported snapshot</option>
            {versions.map((version) => (
              <option key={version}>{version}</option>
            ))}
          </select>
        </label>
        <label>
          Availability
          <select
            name="playable"
            defaultValue={params?.get("playable") || "true"}
          >
            <option value="true">Ready to watch</option>
            <option value="false">All scene references</option>
          </select>
        </label>
        <button type="submit" className="button">
          Apply filters
        </button>
      </form>
      {error ? (
        <ErrorPanel message={error} />
      ) : loading ? (
        <div role="status" className="empty-inline">
          Loading scenes…
        </div>
      ) : (
        data && (
          <>
            <div className="cutscene-count">
              {data.total} scenes{" "}
              <span>Snapshot {data.game_version} · Spoilers ahead</span>
            </div>
            <p className="cutscene-snapshot-note">
              Includes scenes present in this snapshot, including earlier
              chapters. Video assets may come from a newer client.
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
            <div className="cutscene-list">
              {data.items.map((scene, index) => (
                <Link
                  key={scene.node_id}
                  to={`/cutscenes/${encodeURIComponent(scene.name)}?game_version=${encodeURIComponent(data.game_version)}&locale=${locale}`}
                  className="cutscene-list-row"
                >
                  <span className="cutscene-list-index">
                    {String(data.offset + index + 1).padStart(2, "0")}
                  </span>
                  <span className="cutscene-play-mark">
                    <Play size={19} aria-hidden="true" />
                  </span>
                  <span className="cutscene-list-copy">
                    <small>
                      {scene.name}
                      {scene.title_kind === "analysis" ? " · AI title" : ""}
                    </small>
                    <h2>{scene.title}</h2>
                    <span>
                      {scene.quests.length
                        ? `${scene.quests.length} linked quest${scene.quests.length > 1 ? "s" : ""}`
                        : "Quest context not yet linked"}
                    </span>
                  </span>
                  <ArrowRight size={20} aria-hidden="true" />
                </Link>
              ))}
            </div>
            <nav className="cutscene-pagination" aria-label="Cutscene pages">
              {data.offset > 0 && (
                <Link to={pageLink(Math.max(0, data.offset - data.limit))}>
                  <ArrowLeft size={16} />
                  Previous
                </Link>
              )}
              <span>
                {data.total
                  ? `${data.offset + 1}–${Math.min(data.offset + data.limit, data.total)} of ${data.total}`
                  : "0 scenes"}
              </span>
              {data.offset + data.limit < data.total && (
                <Link to={pageLink(data.offset + data.limit)}>
                  Next
                  <ArrowRight size={16} />
                </Link>
              )}
            </nav>
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
                <small>Open at this scene in the quest</small>
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
