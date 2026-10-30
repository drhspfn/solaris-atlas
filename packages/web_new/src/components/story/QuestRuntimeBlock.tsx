import { PlayerText, type PlayerDisplay } from '../dialogue/PlayerText';
import { compareStoryPosition } from './cutscenePlacement';

export type RuntimeBlock = {
  id: string;
  title: string;
  condition_type?: string;
  position: number[];
  branches: Array<{ id: string; type: string }>;
  flow_states: Array<{ state: string; position: number[] }>;
  source: { file: string; key: string; raw_path: string };
};

export const runtimeBlockAnchor = (id: string) =>
  `activity-${Array.from(id, (char) => char.codePointAt(0)!.toString(16)).join('-')}`;

export function runtimeBlockSlots(lines: Array<{ story_position?: number[] }>, blocks: RuntimeBlock[], partial: boolean) {
  const slots = new Map<number, RuntimeBlock[]>();
  const positioned = lines.filter((line) => line.story_position);
  for (const block of blocks) {
    if (partial && (!positioned.length || compareStoryPosition(block.position, positioned[0].story_position!) < 0 ||
        compareStoryPosition(block.position, positioned.at(-1)!.story_position!) > 0)) continue;
    let slot = lines.findIndex((line) => line.story_position && compareStoryPosition(line.story_position, block.position) >= 0);
    if (slot < 0) slot = positioned.length ? lines.findLastIndex((line) => !!line.story_position) + 1 : lines.length;
    slots.set(slot, [...(slots.get(slot) || []), block]);
  }
  return slots;
}

export function QuestRuntimeBlock({ block, display, stateAnchors }: {
  block: RuntimeBlock;
  display: PlayerDisplay;
  stateAnchors: Map<string, string>;
}) {
  return (
    <section className="quest-runtime-block" id={runtimeBlockAnchor(block.id)}>
      <span className="eyebrow left">EXPLORATION / OBJECTIVE</span>
      <h3><PlayerText display={display} value={block.title} fallback="Story activity" /></h3>
      {block.branches.length > 0 && <p className="quest-runtime-branch">
        Conditional / parallel branch · may depend on where you go or what you interact with.
      </p>}
      {block.flow_states.some((flow) => stateAnchors.has(flow.state)) && (
        <nav aria-label="Dialogue for this activity">
          {block.flow_states.filter((flow) => stateAnchors.has(flow.state)).map((flow, index) => (
            <a key={`${flow.state}:${index}`} href={`#${stateAnchors.get(flow.state)}`}>Read dialogue{index ? ` ${index + 1}` : ''} ↗</a>
          ))}
        </nav>
      )}
      <details><summary>Source &amp; conditions</summary>
        <p>{block.condition_type || 'Scripted activity'}</p>
        <small>{block.source.file} · {block.source.key}</small>
        {block.branches.map((branch) => <small key={branch.id}>{branch.type} · {branch.id}</small>)}
      </details>
    </section>
  );
}
