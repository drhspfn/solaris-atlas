import React, { useState } from "react";
import { Link } from "react-router-dom";
import { Search } from "lucide-react";
import { api } from "../../api/client";
import { useLocale } from "../../hooks/useLocale";
import { PlayerText, type PlayerDisplay } from "../dialogue/PlayerText";

export function InlineDialogueSearch({
  character,
  quests = [],
  playerDisplay,
}: {
  character: string;
  quests?: any[];
  playerDisplay: PlayerDisplay;
}) {
  const locale = useLocale();
  const [q, setQ] = useState("");
  const [results, setResults] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);
  const [questId, setQuestId] = useState("");
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!q.trim()) return;
    setBusy(true);
    try {
      const params = new URLSearchParams({
        q,
        scope: "dialogue",
        character,
        locale,
        limit: "8",
      });
      if (questId) params.set("quest_id", questId);
      const data = await api<any>(`/search?${params}`);
      setResults(data.results);
    } catch {
      setResults([]);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <form className="dialogue-search" onSubmit={submit}>
        <Search size={16} />
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search within this character’s lines"
        />
        {quests.length > 0 && (
          <select
            value={questId}
            onChange={(event) => setQuestId(event.target.value)}
            aria-label="Limit dialogue search to a quest"
          >
            <option value="">All appearances</option>
            {quests.map((entry: any) => {
              const quest = entry.quest || entry;
              return (
                <option key={quest.game_quest_id} value={quest.game_quest_id}>
                  {quest.name?.content || `Quest ${quest.game_quest_id}`}
                </option>
              );
            })}
          </select>
        )}
        <button>{busy ? "Searching" : "Find lines"}</button>
      </form>
      {results.map((r, i) => (
        <div className="quote-result" key={i}>
          <p>
            “<PlayerText display={playerDisplay} value={r.text} fallback={r.text?.inline_text || "Text unavailable"} />”
          </p>
          <small>
            {r.flow_state || "Dialogue"} · {r.action?.name || "Talk"} ·{" "}
            {questId ? (
              <Link to={`/quests/${questId}`}>Open quest transcript</Link>
            ) : (
              <span>Select a quest to open its transcript</span>
            )}
          </small>
        </div>
      ))}
    </>
  );
}
