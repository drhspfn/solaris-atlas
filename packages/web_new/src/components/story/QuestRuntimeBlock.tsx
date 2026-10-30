import { PlayerText, type PlayerDisplay } from '../dialogue/PlayerText';
import type { ReactNode } from 'react';
import { Compass, MapPin, MessageCircle, Swords } from 'lucide-react';
import { compareStoryPosition } from './cutscenePlacement';

export type RuntimeBlock = {
  id: string;
  title: string;
  condition_type?: string;
  position: number[];
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
    if (
      partial &&
      (!positioned.length ||
        compareStoryPosition(block.position, positioned[0].story_position!) < 0 ||
        compareStoryPosition(block.position, positioned.at(-1)!.story_position!) > 0)
    )
      continue;
    let slot = lines.findIndex(
      (line) =>
        line.story_position && compareStoryPosition(line.story_position, block.position) >= 0,
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
}: {
  block: RuntimeBlock;
  display: PlayerDisplay;
  stateAnchors: Map<string, string>;
  renderInteraction?: (states: string[], owner: string) => ReactNode[];
}) {
  const Icon =
    block.condition_type === 'PlayFlow'
      ? MessageCircle
      : block.condition_type === 'WaitBattleCondition'
        ? Swords
        : ['ReachArea', 'EnterDungeon', 'LeaveDungeon'].includes(block.condition_type || '')
          ? MapPin
          : Compass;
  const label =
    block.condition_type === 'PlayFlow'
      ? 'Conversation'
      : block.condition_type === 'WaitBattleCondition'
        ? 'Encounter'
        : block.condition_type === 'DoInteract'
          ? 'Interaction'
          : 'Exploration';
  return (
    <section className="quest-runtime-block" id={runtimeBlockAnchor(block.id)}>
      <span className="quest-activity-kind">
        <Icon size={16} />
        {label}
      </span>
      <h3>
        <PlayerText display={display} value={block.title} fallback="Story activity" />
      </h3>
      {block.branches.length > 0 && (
        <p className="quest-runtime-branch">
          Conditional / parallel branch · may depend on where you go or what you interact with.
        </p>
      )}
      {block.flow_states.some((flow) => stateAnchors.has(flow.state)) && (
        <nav aria-label="Dialogue for this activity">
          {block.flow_states
            .filter((flow) => stateAnchors.has(flow.state))
            .map((flow, index) => (
              <a key={`${flow.state}:${index}`} href={`#${stateAnchors.get(flow.state)}`}>
                Read dialogue{index ? ` ${index + 1}` : ''} ↗
              </a>
            ))}
        </nav>
      )}
      {!!block.interactions?.length && (
        <div className="quest-runtime-interactions">
          <p>Optional interactions in this area · explore in your own order.</p>
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
                <small>
                  {item.source.file} · {item.source.key}
                </small>
              </details>
            );
          })}
        </div>
      )}
      <details>
        <summary>Source &amp; conditions</summary>
        <p>{block.condition_type || 'Scripted activity'}</p>
        <small>
          {block.source.file} · {block.source.key}
        </small>
        {block.branches.map((branch) => (
          <small key={branch.id}>
            {branch.type} · {branch.id}
          </small>
        ))}
      </details>
    </section>
  );
}
