import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ArrowUp, BookOpen, ChevronRight, Users } from "lucide-react";
import { api } from "../api/client";
import { entityPath } from "../data/entities";
import { useLocale } from "../hooks/useLocale";
import { ErrorPanel, EmptyInline, PageLoader } from "../components/ui/Feedback";
import { PlayerText } from "../components/dialogue/PlayerText";
import { usePlayerDisplay } from "../hooks/usePlayerDisplay";
import { QuestContinuity } from "../components/story/QuestContinuity";
import { DialogueAudioReference, QuestMediaReferences, type QuestMediaManifest } from "../components/story/QuestMediaReferences";
import type { QuestContinuity as QuestContinuityData } from "../data/story";

export function QuestPage() {
  const { key = "" } = useParams();
  const [searchParams] = useSearchParams();
  const gameVersion = searchParams.get("game_version") || "";
  const locale = useLocale();
  const playerDisplay = usePlayerDisplay();
  const [profile, setProfile] = useState<any>(null);
  const [transcript, setTranscript] = useState<any>(null);
  const [continuity, setContinuity] = useState<QuestContinuityData | null>(null);
  const [media, setMedia] = useState<QuestMediaManifest | null>(null);
  const [continuityError, setContinuityError] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [onlyChoices, setOnlyChoices] = useState(false);
  const [onlyLines, setOnlyLines] = useState(false);
  const [showBackToTop, setShowBackToTop] = useState(false);
  useEffect(() => {
    const updateVisibility = () => setShowBackToTop(window.scrollY > window.innerHeight);
    updateVisibility();
    window.addEventListener("scroll", updateVisibility, { passive: true });
    return () => window.removeEventListener("scroll", updateVisibility);
  }, []);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    setContinuity(null);
    setMedia(null);
    setContinuityError("");
    const selection = new URLSearchParams({ locale });
    if (gameVersion) selection.set("game_version", gameVersion);
    Promise.all([
      api<any>(`/quests/${key}/profile?${selection}`),
      api<any>(`/quests/${key}/transcript?${selection}&limit=2000`),
      api<QuestContinuityData>(`/quests/${key}/continuity?${selection}`)
        .then((result) => ({ result, failure: "" }))
        .catch((reason: Error) => ({ result: null, failure: reason.message })),
      api<QuestMediaManifest>(`/quests/${key}/media?${selection}`)
        .catch(() => null),
    ])
      .then(([p, t, c, m]) => {
        if (!active) return;
        setProfile(p);
        setTranscript(t);
        setContinuity(c.result);
        setContinuityError(c.failure);
        setMedia(m);
      })
      .catch((e) => { if (active) setError(e.message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [key, locale, gameVersion]);
  if (loading) return <PageLoader />;
  if (error)
    return (
      <div className="page-container">
        <ErrorPanel message={error} />
      </div>
    );
  const quest = profile.quest;
  const lines = transcript.lines || [];
  const scenes = transcript.scenes || profile.scenes || [];
  const explicitLinks = profile.explicit_links || [];
  const questConnections = explicitLinks.filter(
    (link: any) =>
      link.node?.type === "quest" &&
      ["requires_quest", "references_quest"].includes(link.relation),
  );
  const sourceGraphLinks = explicitLinks.filter(
    (link: any) => !questConnections.includes(link),
  );
  const shown = lines
    .filter((line: any) => !onlyChoices || line.player_choices?.length)
    .filter((line: any) => !onlyLines || line.speaker);
  const stateCounts = new Map<string, number>();
  for (const line of shown) {
    const state = line.flow_state || "Unassigned";
    stateCounts.set(state, (stateCounts.get(state) || 0) + 1);
  }
  const stateIndex = Array.from(stateCounts, ([key, count], index) => ({
    key, count, anchor: `flow-state-${index}`,
  }));
  const stateAnchors = new Map(stateIndex.map((state) => [state.key, state.anchor]));
  const firstLineInState = new Set<string>();
  return (
    <div className="page-container quest-container">
      <div className="breadcrumbs">
        <Link to="/">Archive</Link>
        <ChevronRight size={13} />
        <Link to="/story-map">Story map</Link>
        <ChevronRight size={13} />
        <span>{quest.name?.content || `Quest ${key}`}</span>
      </div>
      <section className="quest-hero">
        <div className="quest-emblem">
          <BookOpen size={28} />
          <span>STORY RECORD</span>
        </div>
        <div className="quest-intro">
          <span className="eyebrow left">
            QUEST DOSSIER / {quest.quest_type || "NARRATIVE"}
          </span>
          <h1>
            {quest.name?.content || `Quest ${key}`}
            <span className="heading-period">.</span>
          </h1>
          <div className="quest-meta">
            <span>
              QUEST ID <b>{quest.game_quest_id}</b>
            </span>
            <span>
              FLOW STATES <b>{stateIndex.length}</b>
            </span>
            <span>
              LINES <b>{lines.length}</b>
            </span>
          </div>
        </div>
      </section>
      {continuity && <QuestContinuity continuity={continuity} title={quest.name?.content || `Quest ${key}`} />}
      {continuityError && <div className="quest-continuity-warning" role="status">Story path unavailable: {continuityError}</div>}
      <div className="transcript-toolbar">
        <div>
          <span className="eyebrow left">AUTHORED TRANSCRIPT</span>
          <h2>Story, as recorded.</h2>
          <p>
            Dialogue appears in authored order. Runtime branches are preserved
            where present; this is not a single guaranteed playthrough.
          </p>
        </div>
        <div className="transcript-filters">
          <button
            onClick={() => {
              setOnlyLines(!onlyLines);
              setOnlyChoices(false);
            }}
            className={onlyLines ? "toggle active" : "toggle"}
          >
            <Users size={14} /> Spoken lines
          </button>
          <button
            onClick={() => {
              setOnlyChoices(!onlyChoices);
              setOnlyLines(false);
            }}
            className={onlyChoices ? "toggle active" : "toggle"}
          >
            Choice moments
          </button>
        </div>
      </div>
      <div className="transcript-layout">
        <aside className="scene-nav">
          <h3>
            IN THIS QUEST{" "}
            <span>{stateIndex.length.toString().padStart(2, "0")}</span>
          </h3>
          {stateIndex.length ? (
            <>
              <p className="flow-nav-note">Authored flow states. Branches may change the path you see in game.</p>
              <nav className="flow-state-list" aria-label="Quest flow states">
                {stateIndex.map((state, i) => (
                  <a href={`#${state.anchor}`} className="flow-state-link" key={state.key} title={state.key}>
                    <span>{String(i + 1).padStart(2, "0")}</span>
                    <span>Flow state {i + 1}<small>{state.key}</small></span>
                    <b>{state.count}</b>
                  </a>
                ))}
              </nav>
            </>
          ) : (
            <p>No transcript lines in this view.</p>
          )}
          {scenes.length > 0 && <p className="flow-nav-note">{scenes.length} source scene records are also linked to this quest.</p>}
          <QuestMediaReferences manifest={media} stateAnchors={stateAnchors} />
        </aside>
        <div className="transcript">
          {shown.length ? (
            shown.map((line: any, i: number) => {
              const state = line.flow_state || "Unassigned";
              const isFirst = !firstLineInState.has(state);
              firstLineInState.add(state);
              return (
              <article
                className="transcript-line"
                id={isFirst ? stateAnchors.get(state) : undefined}
                key={line.id || i}
              >
                <div className="line-rail">
                  <span>{String(i + 1).padStart(3, "0")}</span>
                  <i />
                </div>
                <div className="line-body">
                  <div className="speaker-row">
                    <span className="speaker-dot" />
                    <strong>
                      {line.speaker?.label || "Narration / Unknown speaker"}
                    </strong>
                    <span className="line-state">
                      {line.flow_state || "Story"}
                    </span>
                    {line.action?.name && (
                      <span className="action-tag">{line.action.name}</span>
                    )}
                  </div>
                  <p>
                    {line.text?.content || line.text?.inline_text ? (
                      <PlayerText display={playerDisplay} value={line.text} fallback={line.text?.inline_text || ""} />
                    ) : (
                      <i className="missing">Text unavailable in this locale</i>
                    )}
                  </p>
                  <DialogueAudioReference media={line.media} />
                  {line.player_choices?.length > 0 && (
                    <div className="choice-block">
                      <div className="choice-heading">
                        <span /> PLAYER CHOICE
                        {line.player_choices.length > 1 ? "S" : ""}
                      </div>
                      {line.player_choices.map((choice: any, j: number) => (
                        <div className="choice-option" key={j}>
                          <span className="choice-diamond">◇</span>
                          <span>
                            <PlayerText display={playerDisplay} value={choice.text} fallback="Choice text unavailable" />
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                  <div className="line-foot">
                    <span>
                      {line.source_type || "dialogue"}
                      {line.game_ids?.talk_item_id
                        ? ` · Talk ${line.game_ids.talk_item_id}`
                        : ""}
                    </span>
                    {line.provenance?.source_file && (
                      <span title={line.provenance.source_file}>
                        {line.provenance.source_file.split("/").at(-1)}
                        {line.provenance.source_row != null
                          ? ` · row ${line.provenance.source_row}`
                          : ""}
                      </span>
                    )}
                  </div>
                </div>
              </article>
            );})
          ) : (
            <EmptyInline text="No dialogue lines matched this view." />
          )}
        </div>
      </div>
      {(questConnections.length > 0 || sourceGraphLinks.length > 0) && (
        <section className="content-panel quest-links-panel">
          <div className="panel-title">
            <span>02</span>
            <h2>Quest connections</h2>
            <small>{questConnections.length}</small>
          </div>
          {questConnections.length > 0 ? (
            <div className="quest-link-grid">
            {questConnections.map((link: any, index: number) => {
              const target = link.node;
              const relationLabel =
                link.relation === "requires_quest"
                  ? link.direction === "outgoing"
                    ? "Prerequisite"
                    : "Required by this quest"
                  : link.direction === "outgoing"
                    ? "References"
                    : "Referenced by";
              return (
                <Link
                  className="quest-connection-card"
                  key={`${target.canonical_key}-${link.relation}-${index}`}
                  to={entityPath({
                    id: target.id,
                    canonical_key: target.canonical_key,
                    node_type: target.type,
                  })}
                >
                  <strong>{target.label || target.canonical_key}</strong>
                  <span className="quest-connection-kind">{relationLabel}</span>
                  <small>Quest {target.canonical_key?.split(":").at(-1)}</small>
                </Link>
              );
            })}
            </div>
          ) : (
            <EmptyInline text="No prerequisite or cross-quest references are recorded." />
          )}
          {sourceGraphLinks.length > 0 && (
            <details className="source-graph-details">
              <summary>Technical source links · {sourceGraphLinks.length}</summary>
              <p>These are raw graph connections such as quest structure nodes, not separate quests.</p>
              <div className="source-graph-list">
                {sourceGraphLinks.map((link: any, index: number) => {
                  const target = link.node;
                  const questNode = target?.type === "quest_node";
                  const nodeId = target?.canonical_key?.split(":").at(-1);
                  return (
                    <Link
                      key={`${target?.canonical_key}-${link.relation}-${index}`}
                      to={entityPath({
                        id: target.id,
                        canonical_key: target.canonical_key,
                        node_type: target.type,
                      })}
                    >
                      <span>{link.direction === "outgoing" ? "Outgoing" : "Incoming"}</span>
                      <strong>{questNode ? `Quest structure node ${nodeId?.split(":").at(-1)}` : target?.label || target?.canonical_key}</strong>
                      <small>{link.relation.replaceAll("_", " ")} · {link.basis.replaceAll("_", " ")}</small>
                    </Link>
                  );
                })}
              </div>
            </details>
          )}
        </section>
      )}
      {showBackToTop && (
        <button
          className="quest-back-to-top"
          type="button"
          onClick={() => {
            const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
            window.scrollTo({ top: 0, behavior: reducedMotion ? "auto" : "smooth" });
          }}
        >
          <ArrowUp size={16} aria-hidden="true" />
          <span>Back to top</span>
        </button>
      )}
    </div>
  );
}
