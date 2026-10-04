import type { QuestMediaEvent } from './QuestMediaReferences';

type Line = { id: string; flow_state: string; action?: { index: number } | null };

// Preserve the transcript's authored-state ordering and insert within action order.
// Exact subtitle ownership takes precedence over a separate PlayMovie wrapper state.
export function cutsceneSlots(lines: Line[], events: QuestMediaEvent[], partial = false) {
  const slots = new Map<number, QuestMediaEvent[]>();
  for (const event of events.filter((entry) => entry.kind === 'cutscene')) {
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
      a.flow_state < b.flow_state
        ? -1
        : a.flow_state > b.flow_state
          ? 1
          : a.action_index - b.action_index,
    );
  return slots;
}
