import type { QuestMediaEvent } from './QuestMediaReferences';

export function cutsceneAnchor(event: Pick<QuestMediaEvent, 'flow_state' | 'action_index' | 'reference'>): string {
  const key = `${event.flow_state}:${event.action_index}:${event.reference}`;
  return `cutscene-${Array.from(key, (char) => char.codePointAt(0)!.toString(16)).join('-')}`;
}

export function packageCutsceneAnchor(name: string): string {
  return cutsceneAnchor({ flow_state: '', action_index: 0, reference: `cutscene:${name}` });
}
