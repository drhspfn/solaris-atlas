import type { QuestMediaEvent } from './QuestMediaReferences';

type Line = { id: string; flow_state: string; action?: { index: number } | null; story_position?: number[] };

export function compareStoryPosition(a: number[], b: number[]) {
  for (let i = 0; i < Math.min(a.length, b.length); i++)
    if (a[i] !== b[i]) return a[i] - b[i];
  return a.length - b.length;
}

// Preserve the transcript's authored-state ordering and insert within action order.
// Exact subtitle ownership takes precedence over a separate PlayMovie wrapper state.
export function cutsceneSlots(lines: Line[], events: QuestMediaEvent[], partial = false,
    kinds: QuestMediaEvent['kind'][] = ['cutscene']) {
  const slots = new Map<number, QuestMediaEvent[]>();
  const positioned = lines.filter((line) => line.story_position);
  for (const event of events.filter((entry) => kinds.includes(entry.kind))) {
    if (event.story_position && positioned.length) {
      if (partial && (compareStoryPosition(event.story_position, positioned[0].story_position!) < 0 ||
          compareStoryPosition(event.story_position, positioned.at(-1)!.story_position!) > 0)) continue;
      let slot = lines.findIndex((line) => line.story_position &&
        compareStoryPosition(line.story_position, event.story_position!) >= 0);
      if (slot < 0) {
        const last = lines.findLastIndex((line) => !!line.story_position);
        slot = last + 1;
      }
      slots.set(slot, [...(slots.get(slot) || []), event]);
      continue;
    }
    if (event.context_inferred && (!event.story_order_mapped ||
        !lines.some((line) => event.transcript_states?.includes(line.flow_state)))) {
      if (!partial) slots.set(lines.length, [...(slots.get(lines.length) || []), event]);
      continue;
    }
    const transcript = lines.findIndex((line) =>
      event.transcript_states?.includes(line.flow_state),
    );
    if (partial && transcript < 0 && !lines.some((line) => line.flow_state === event.flow_state))
      continue;
    let slot = transcript;
    if (slot < 0) {
      const sameState = lines
        .map((line, index) => ({ line, index }))
        .filter(({ line }) => line.flow_state === event.flow_state);
      if (sameState.length) {
        slot =
          sameState.find(({ line }) => (line.action?.index ?? 0) > event.action_index)?.index ??
          sameState.at(-1)!.index + 1;
      } else {
        slot = lines.findIndex((line) => line.flow_state > event.flow_state);
        if (slot < 0) slot = lines.length;
      }
    }
    slots.set(slot, [...(slots.get(slot) || []), event]);
  }
  for (const entries of slots.values())
    entries.sort((a, b) =>
      a.story_position && b.story_position ? compareStoryPosition(a.story_position, b.story_position) :
      a.flow_state < b.flow_state
        ? -1
        : a.flow_state > b.flow_state
          ? 1
          : a.action_index - b.action_index,
    );
  return slots;
}
