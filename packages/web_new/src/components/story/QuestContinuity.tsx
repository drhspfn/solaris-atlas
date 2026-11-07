'use client';

import { ArrowLeft, ArrowRight, GitBranch } from 'lucide-react';
import { Link } from '@/lib/router';
import { questPath } from '@/lib/routeKey';
import { PlayerText, type PlayerDisplay } from '@/components/dialogue/PlayerText';

import type { QuestContinuity as Continuity } from '../../data/story';

function NeighborLinks({
  entries,
  version,
  direction,
  display,
}: {
  entries: Continuity['quest_tree_nodes'][number]['neighbors'];
  version: string;
  direction: 'previous' | 'next';
  display?: PlayerDisplay;
}) {
  const seen = new Set<number>();
  const quests = entries
    .flatMap((entry) =>
      (entry.quests ?? entry.quest_ids.map((id) => ({ game_quest_id: id }))).map((quest) => ({
        ...quest,
        id: quest.game_quest_id,
        title: entry.name?.content,
        source: entry.source,
      })),
    )
    .filter((entry) => {
      if (seen.has(entry.id)) return false;
      seen.add(entry.id);
      return true;
    });
  return (
    <div className="quest-continuity-side">
      <small>{direction === 'previous' ? 'PREVIOUS PATH' : 'NEXT PATH'}</small>
      {quests.length ? (
        quests.map((quest) => (
          <Link
            key={quest.id}
            to={`${questPath(quest)}?game_version=${encodeURIComponent(version)}`}
            title={`${quest.source.file} ${quest.source.raw_path}`}
          >
            {direction === 'previous' && <ArrowLeft size={16} aria-hidden="true" />}
            <span>
              <PlayerText value={quest.title} fallback={`Quest ${quest.id}`} display={display} />
              <small>Quest {quest.id}</small>
            </span>
            {direction === 'next' && <ArrowRight size={16} aria-hidden="true" />}
          </Link>
        ))
      ) : (
        <p>{entries.length ? 'Linked node has no quest record' : 'No authored link recorded'}</p>
      )}
    </div>
  );
}

export function QuestContinuity({ continuity, title, display }: { continuity: Continuity; title: string; display?: PlayerDisplay }) {
  const version = continuity.selected_game_version;
  const previous = continuity.quest_tree_nodes.flatMap((node) =>
    node.neighbors.filter((neighbor) => neighbor.relation === 'quest_tree_predecessor'),
  );
  const next = continuity.quest_tree_nodes.flatMap((node) =>
    node.neighbors.filter((neighbor) => neighbor.relation === 'quest_tree_next'),
  );
  const chapter =
    continuity.quest_tree_nodes[0]?.tree_chapter_name?.content ||
    continuity.quest_chapter.chapter_name?.content;
  return (
    <section className="quest-continuity" aria-labelledby="quest-continuity-title">
      <div className="quest-continuity-heading">
        <div>
          <span className="eyebrow left">STORY PATH / SNAPSHOT {version}</span>
          <h2 id="quest-continuity-title">Where this quest belongs</h2>
          <p>
            <PlayerText value={chapter} fallback="Chapter not recorded" display={display} />
            {continuity.quest_type.name?.content && <> · <PlayerText value={continuity.quest_type.name} display={display} /></>}
          </p>
        </div>
        <Link to={`/story-map?game_version=${encodeURIComponent(version)}`}>
          <GitBranch size={16} aria-hidden="true" /> Open story map{' '}
          <ArrowRight size={15} aria-hidden="true" />
        </Link>
      </div>
      {continuity.quest_tree_nodes.length ? (
        <div className="quest-continuity-grid">
          <NeighborLinks entries={previous} version={version} direction="previous" display={display} />
          <div className="quest-continuity-current">
            <small>CURRENT TREE NODE</small>
            <strong><PlayerText value={continuity.quest_tree_nodes[0].name} fallback={continuity.quest} display={display} /></strong>
            <span>{continuity.quest_tree_nodes[0].canonical_key}</span>
          </div>
          <NeighborLinks entries={next} version={version} direction="next" display={display} />
        </div>
      ) : continuity.prerequisites.length || continuity.required_by.length ? (
        <div className="quest-continuity-grid">
          <div className="quest-continuity-side">
            <small>REQUIRED BEFORE THIS QUEST</small>
            {continuity.prerequisites.map((entry) => (
              <Link
                key={entry.quest}
                to={`${questPath(entry)}?game_version=${encodeURIComponent(version)}`}
                title={`${entry.source.file} ${entry.source.raw_path}`}
              >
                <ArrowLeft size={16} aria-hidden="true" />
                <span>
                  <PlayerText value={entry.name} fallback={entry.quest} display={display} />
                  <small>Prerequisite</small>
                </span>
              </Link>
            ))}
          </div>
          <div className="quest-continuity-current">
            <small>CURRENT QUEST</small>
            <strong><PlayerText value={title} display={display} /></strong>
            <span>{continuity.quest}</span>
          </div>
          <div className="quest-continuity-side">
            <small>QUESTS REQUIRING THIS</small>
            {continuity.required_by.map((entry) => (
              <Link
                key={entry.quest}
                to={`${questPath(entry)}?game_version=${encodeURIComponent(version)}`}
                title={`${entry.source.file} ${entry.source.raw_path}`}
              >
                <span>
                  <PlayerText value={entry.name} fallback={entry.quest} display={display} />
                  <small>Requires this quest</small>
                </span>
                <ArrowRight size={16} aria-hidden="true" />
              </Link>
            ))}
          </div>
        </div>
      ) : (
        <p className="quest-continuity-unmapped">
          No QuestTree position or explicit prerequisite link is recorded for this quest in the
          selected snapshot.
        </p>
      )}
      <p className="quest-continuity-note">
        {continuity.quest_tree_nodes.length
          ? 'These are authored QuestTree links. They do not guarantee one playthrough or prove immediate chronology.'
          : 'These links come from QuestData prerequisites. They identify requirements, not an immediate next story event.'}
      </p>
    </section>
  );
}
