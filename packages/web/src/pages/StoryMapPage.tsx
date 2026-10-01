import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ArrowRight, BookOpen, ChevronRight, GitBranch, Search } from "lucide-react";
import { api } from "../api/client";
import type { StoryMap, StoryTreeNode } from "../data/story";
import { useLocale } from "../hooks/useLocale";
import { ErrorPanel, PageLoader } from "../components/ui/Feedback";

const questTypes = [
  { id: 1, label: "Main story" },
  { id: 3, label: "Companion stories" },
  { id: 2, label: "Side stories" },
  { id: 0, label: "All story paths" },
];

function StoryStep({ node, version }: { node: StoryTreeNode; version: string }) {
  return (
    <li className="story-step">
      <span className="story-step-marker" aria-hidden="true" />
      <div className="story-step-content">
        <div className="story-step-heading">
          <span className="story-step-index">{node.source_kind === "quest_tree" ? "PATH" : "QUEST"} {node.id}</span>
          {node.chapter_label && <span className="story-step-chapter">{node.chapter_label}</span>}
        </div>
        {node.source_kind === "quest_tree" && <h3>{node.title}</h3>}
        {node.quests.length > 0 ? (
          <div className="story-step-quests">
            {node.quests.map((quest) => (
              <Link
                key={quest.game_quest_id}
                to={`/quests/${quest.game_quest_id}?game_version=${encodeURIComponent(version)}`}
                className="story-quest-link"
              >
                <BookOpen size={16} aria-hidden="true" />
                <span>
                  <strong>{quest.title}</strong>
                  <small>
                    Quest {quest.game_quest_id}
                    {quest.first_observed_game_version && ` · Seen from ${quest.first_observed_game_version}`}
                  </small>
                </span>
                <ArrowRight size={16} aria-hidden="true" />
              </Link>
            ))}
          </div>
        ) : <p className="story-step-empty">This tree node has no quest records.</p>}
        {node.source_kind === "quest_prerequisite" && node.previous_node_ids.length > 0 && (
          <small className="story-step-branch">Requires {node.previous_node_ids.map((id) => `quest ${id}`).join(", ")} · prerequisite, not immediate sequence</small>
        )}
        {node.source_kind === "quest_tree" && (node.previous_node_ids.length > 1 || !node.next_node_id) && (
          <small className="story-step-branch">
            {node.previous_node_ids.length > 1 ? `${node.previous_node_ids.length} authored predecessor links` : "End of this authored path"}
          </small>
        )}
      </div>
    </li>
  );
}

export function StoryMapPage() {
  const locale = useLocale();
  const [params, setParams] = useSearchParams();
  const version = params.get("game_version") || "";
  const questType = Number(params.get("quest_type") ?? "1");
  const selectedType = questTypes.some((entry) => entry.id === questType) ? questType : 1;
  const [query, setQuery] = useState("");
  const [map, setMap] = useState<StoryMap | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    const search = new URLSearchParams({ locale, quest_type_id: String(selectedType) });
    if (version) search.set("game_version", version);
    api<StoryMap>(`/story-map?${search}`)
      .then((result) => { if (active) setMap(result); })
      .catch((reason: Error) => { if (active) setError(reason.message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [locale, selectedType, version]);

  const chapters = useMemo(() => map?.chapters.map((chapter) => ({
    ...chapter,
    nodes: chapter.nodes.filter((node) =>
      !query.trim() || [chapter.title, chapter.act_title, node.title, node.chapter_label, ...node.quests.map((quest) => quest.title)]
        .some((value) => value?.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())),
    ),
  })).filter((chapter) => chapter.nodes.length) ?? [], [map, query]);
  const total = map?.chapters.reduce((sum, chapter) => sum + chapter.nodes.length, 0) ?? 0;
  const visible = chapters.reduce((sum, chapter) => sum + chapter.nodes.length, 0);
  const groupedChapters = useMemo(() => {
    const groups: { title: string; subtitle: string | null; acts: typeof chapters }[] = [];
    for (const chapter of chapters) {
      const title = chapter.title || `Chapter ${chapter.id}`;
      const subtitle = chapter.act_title === title ? null : chapter.chapter_number || null;
      const last = groups.at(-1);
      if (last && last.title === title && last.subtitle === subtitle) last.acts.push(chapter);
      else groups.push({ title, subtitle, acts: [chapter] });
    }
    return groups;
  }, [chapters]);

  function updateParam(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next);
  }

  return (
    <div className="page-container story-map-page">
      <div className="breadcrumbs">
        <Link to="/">Archive</Link><ChevronRight size={13} aria-hidden="true" />
        <span>Story map</span>
      </div>
      <header className="story-map-hero">
        <div>
          <span className="eyebrow left">TRACE THE STORY SOURCES</span>
          <h1>Explore the story<span className="heading-period">.</span></h1>
          <p>Read the quest chains recorded in the game data. Open a quest for its scenes, choices, and full transcript.</p>
        </div>
        <div className="story-map-seal"><GitBranch size={31} aria-hidden="true" /><span>SOURCE TRACE / QUESTS</span></div>
      </header>
      <div className="story-map-tools">
        <label>
          Story path
          <select value={selectedType} onChange={(event) => updateParam("quest_type", event.target.value)}>
            {questTypes.map((entry) => <option key={entry.id} value={entry.id}>{entry.label}</option>)}
          </select>
        </label>
        <label>
          Imported snapshot
          <select value={version || map?.selected_game_version || ""} onChange={(event) => updateParam("game_version", event.target.value)}>
            {(map?.imported_game_versions || []).map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </label>
        <label className="story-map-search">
          Find a quest in this path
          <span><Search size={16} aria-hidden="true" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Quest or chapter name" /></span>
        </label>
      </div>
      {loading ? <PageLoader /> : error ? <ErrorPanel message={error} /> : map && (
        <>
          <div className="story-map-context">
            <span>{visible} of {total} {map.tree_available ? "path nodes" : "source quests"} · snapshot {map.selected_game_version}</span>
            <p>{map.tree_available ? "Connections follow QuestTree predecessor and next references. Branches and runtime conditions may change the route a player sees." : "This snapshot has no QuestTree. Chapters come from QuestData; any links shown are prerequisites, not an authored next quest."} “Seen from” is the earliest imported snapshot, not a confirmed release date.</p>
          </div>
          {!map.chapters.length ? (
            <div className="story-map-empty">
              <h2>No source-backed path in this snapshot</h2>
              <p>No QuestTree or chapter records matched this story path. The quest transcript archive is still available.</p>
              <Link className="text-link" to="/catalog/quest">Browse quests <ArrowRight size={15} /></Link>
            </div>
          ) : chapters.length && !map.tree_available ? groupedChapters.map((group, index) => (
            <section key={`${group.title}:${group.subtitle}`} className="story-chapter" aria-labelledby={`story-chapter-group-${index}`}>
              <div className="story-chapter-heading">
                <span>{String(index + 1).padStart(2, "0")}</span>
                <div>
                  <small>{group.subtitle || "QUEST CHAPTER"}</small>
                  <h2 id={`story-chapter-group-${index}`}>{group.title}</h2>
                </div>
                <b>{group.acts.reduce((sum, act) => sum + act.nodes.length, 0)} source quests</b>
              </div>
              {group.acts.map((act) => (
                <div key={act.id} className="story-act">
                  {act.act_title !== group.title && <h3>{[act.act_number, act.act_title].filter(Boolean).join(" · ") || `Act ${act.id}`}</h3>}
                  <ol className="story-steps">{act.nodes.map((node) => <StoryStep key={node.id} node={node} version={map.selected_game_version} />)}</ol>
                </div>
              ))}
            </section>
          )) : chapters.length ? chapters.map((chapter, index) => (
            <section key={chapter.id} className="story-chapter" aria-labelledby={`story-chapter-${chapter.id}`}>
              <div className="story-chapter-heading">
                <span>{String(index + 1).padStart(2, "0")}</span>
                <div>
                  <small>QUEST TREE CHAPTER {chapter.id}</small>
                  <h2 id={`story-chapter-${chapter.id}`}>{chapter.title || `Chapter ${chapter.id}`}</h2>
                </div>
                <b>{chapter.nodes.length} paths</b>
              </div>
              <ol className="story-steps">{chapter.nodes.map((node) => <StoryStep key={node.id} node={node} version={map.selected_game_version} />)}</ol>
            </section>
          )) : <div className="story-map-empty"><h2>No matching quests</h2><p>Try another name or story path.</p></div>}
        </>
      )}
    </div>
  );
}
