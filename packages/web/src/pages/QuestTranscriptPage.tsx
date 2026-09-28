import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { BookOpen, ChevronRight, Compass, Users } from "lucide-react";
import { api } from "../api/client";
import { entityPath } from "../data/entities";
import { useLocale } from "../hooks/useLocale";
import { ErrorPanel, EmptyInline, PageLoader } from "../components/ui/Feedback";

export function QuestPage() {
  const { key = "" } = useParams();
  const locale = useLocale();
  const [profile, setProfile] = useState<any>(null);
  const [transcript, setTranscript] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [onlyChoices, setOnlyChoices] = useState(false);
  const [onlyLines, setOnlyLines] = useState(false);
  useEffect(() => {
    setLoading(true);
    setError("");
    Promise.all([
      api<any>(`/quests/${key}/profile?locale=${locale}`),
      api<any>(`/quests/${key}/transcript?locale=${locale}&limit=2000`),
    ])
      .then(([p, t]) => {
        setProfile(p);
        setTranscript(t);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [key, locale]);
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
  return (
    <div className="page-container quest-container">
      <div className="breadcrumbs">
        <Link to="/">Archive</Link>
        <ChevronRight size={13} />
        <Link to="/catalog/quest">Quests</Link>
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
              SCENES <b>{scenes.length}</b>
            </span>
            <span>
              LINES <b>{lines.length}</b>
            </span>
          </div>
        </div>
      </section>
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
            <span>{scenes.length.toString().padStart(2, "0")}</span>
          </h3>
          {scenes.length ? (
            scenes.map((scene: any, i: number) => (
                <div className="scene-item" key={i}>
                  <span>{String(i + 1).padStart(2, "0")}</span>
                  {scene.title || `Scene ${i + 1}`}
                </div>
            ))
          ) : (
            <p>Scenes are not separately titled in this data.</p>
          )}
          <div className="tree-note">
            <Compass size={15} />
            <span>
              Flow states: {profile.flow_states?.length || 0}
              <br />
              Tree links: {profile.tree_edges?.length || 0}
            </span>
          </div>
        </aside>
        <div className="transcript">
          {shown.length ? (
            shown.map((line: any, i: number) => (
              <article
                className="transcript-line"
                id={i === 0 ? "scene-0" : undefined}
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
                    {line.text?.content || line.text?.inline_text || (
                      <i className="missing">Text unavailable in this locale</i>
                    )}
                  </p>
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
                            {choice.text?.content || "Choice text unavailable"}
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
            ))
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
    </div>
  );
}
