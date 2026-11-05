import { PlayerText, type PlayerDisplay } from '../dialogue/PlayerText';
import type { ReactNode } from 'react';
import { Compass, MapPin, MessageCircle, Swords } from 'lucide-react';
import { compareStoryPosition } from './cutscenePlacement';
import { QuestEncounter, type RenderEncounterFlow } from './QuestEncounter';
import type { Encounter } from './encounterTransitions';
import styles from './QuestRuntimeBlock.module.css';
export type { EncounterFlow } from './encounterTransitions';
export { EncounterCinematicBoundary } from './QuestEncounter';
export { encounterCinematicParts, encounterFlowOwners, encounterOwnsMedia } from './encounterTransitions';

export type RuntimeBlock = {
  id: string;
  title: string;
  condition_type?: string;
  encounter?: Encounter;
  transition?: {
    kind: 'dungeon_exit';
    dungeon_id: number;
    map_config_id: number;
    is_exit_dungeon: true;
    boundary_semantics: 'authored_exit_requirement';
  };
  position: number[] | null;
  ordering?: string;
  branches: Array<{ id: string; type: string }>;
  flow_states: Array<{ state: string; position: number[] }>;
  interactions?: Array<{
    id: string;
    title: string;
    flow_states: Array<{ state: string }>;
    source: { file: string; key: string; raw_path: string };
  }>;
  source: { file: string; key: string; raw_path: string };
};

export const runtimeBlockAnchor = (id: string) =>
  `activity-${Array.from(id, (char) => char.codePointAt(0)!.toString(16)).join('-')}`;

export function runtimeBlockSlots(
  lines: Array<{ story_position?: number[] }>,
  blocks: RuntimeBlock[],
  partial: boolean,
) {
  const slots = new Map<number, RuntimeBlock[]>();
  const positioned = lines.filter((line) => line.story_position);
  for (const block of blocks) {
    // Related source context has no chronological slot. Keep it separately
    // addressable instead of comparing null or inventing a transcript position.
    if (!block.position || block.ordering === 'related') {
      slots.set(-1, [...(slots.get(-1) || []), block]);
      continue;
    }
    const position = block.position;
    if (
      partial &&
      (!positioned.length ||
        compareStoryPosition(block.position, positioned[0].story_position!) < 0 ||
        compareStoryPosition(block.position, positioned.at(-1)!.story_position!) > 0)
    )
      continue;
    let slot = lines.findIndex(
      (line) =>
        line.story_position && compareStoryPosition(line.story_position, position) >= 0,
    );
    if (slot < 0)
      slot = positioned.length
        ? lines.findLastIndex((line) => !!line.story_position) + 1
        : lines.length;
    slots.set(slot, [...(slots.get(slot) || []), block]);
  }
  return slots;
}

export function QuestRuntimeBlock({
  block,
  display,
  stateAnchors,
  renderInteraction,
  renderEncounterFlow,
}: {
  block: RuntimeBlock;
  display: PlayerDisplay;
  stateAnchors: Map<string, string>;
  renderInteraction?: (states: string[], owner: string) => ReactNode[];
  renderEncounterFlow?: RenderEncounterFlow;
}) {
  if (block.encounter) return <div id={runtimeBlockAnchor(block.id)}>
    <QuestEncounter encounter={block.encounter} display={display} stateAnchors={stateAnchors} renderFlow={renderEncounterFlow} />
  </div>;
  const isExit = block.transition?.kind === 'dungeon_exit';
  const genericTitle = !block.title || ['Story activity', 'Continue the story'].includes(block.title);
  const dialogue = block.flow_states.filter((flow) => stateAnchors.has(flow.state));
  const sourceContext = <>
    <p>{block.condition_type || 'Scripted activity'}</p>
    {isExit && <p>Authored exit requirement; completion is not established.</p>}
    {!!block.branches.length && <p>Conditional or parallel branch; order is not established.</p>}
    <small>{block.source.file} · {block.source.key}</small>
    {block.branches.map((branch) => <small key={branch.id}>{branch.type} · {branch.id}</small>)}
  </>;
  if (genericTitle && !isExit && !block.flow_states.length && !block.interactions?.length)
    return <details className={styles.sourceOnly} id={runtimeBlockAnchor(block.id)}>
      <summary>Source &amp; conditions</summary>
      {sourceContext}
    </details>;
  const Icon = isExit ? MapPin :
    block.condition_type === 'PlayFlow'
      ? MessageCircle
      : block.condition_type === 'WaitBattleCondition'
        ? Swords
        : ['ReachArea', 'EnterDungeon', 'LeaveDungeon'].includes(block.condition_type || '')
          ? MapPin
          : Compass;
  const label = isExit ? 'Area exit' :
    block.condition_type === 'PlayFlow'
      ? 'Conversation'
      : block.condition_type === 'WaitBattleCondition'
        ? 'Encounter'
        : block.condition_type === 'DoInteract'
          ? 'Interaction'
          : 'Exploration';
  return (
    <section className={`quest-runtime-block ${styles.block}`} id={runtimeBlockAnchor(block.id)}>
      {!isExit && <span className="quest-activity-kind">
        <Icon size={16} aria-hidden="true" />
        {label}
      </span>}
      {(!genericTitle || isExit) && <h3>
        <PlayerText display={display} value={isExit && genericTitle ? 'Leave the area' : block.title} fallback="Story activity" />
      </h3>}
      {dialogue.length > 0 && (
        <nav aria-label="Dialogue for this activity">
          {dialogue
            .map((flow, index) => (
              <a key={`${flow.state}:${index}`} href={`#${stateAnchors.get(flow.state)}`}>
                Read dialogue{index ? ` ${index + 1}` : ''} ↗
              </a>
            ))}
        </nav>
      )}
      {!!block.flow_states.length && !dialogue.length && <p>Dialogue is outside this transcript window.</p>}
      {!!block.interactions?.length && (
        <div className="quest-runtime-interactions">
          <p>Optional interactions</p>
          {block.interactions.map((item) => {
            const content =
              renderInteraction?.(
                item.flow_states.map((flow) => flow.state),
                `${block.id}:${item.id}`,
              ) || [];
            return (
              <details key={item.id}>
                <summary>
                  <PlayerText
                    display={display}
                    value={item.title}
                    fallback="Inspect the surroundings"
                  />
                </summary>
                {content.length
                  ? content
                  : item.flow_states
                      .filter((flow) => stateAnchors.has(flow.state))
                      .map((flow) => (
                        <a key={flow.state} href={`#${stateAnchors.get(flow.state)}`}>
                          Read this interaction ↗
                        </a>
                      ))}
                {!content.length &&
                  !item.flow_states.some((flow) => stateAnchors.has(flow.state)) && (
                    <p>
                      {item.flow_states.length
                        ? 'Dialogue is outside this transcript window.'
                        : 'This interaction has no authored dialogue in the source.'}
                    </p>
                  )}
                <details><summary>Source reference</summary><small>{item.source.file} · {item.source.key}</small></details>
              </details>
            );
          })}
        </div>
      )}
      <details>
        <summary>Source &amp; conditions</summary>
        {sourceContext}
      </details>
    </section>
  );
}
