import { localizedText } from '../../data/localized';

export type DialogueSpeaker = {
  label?: unknown;
  canonical_key?: string | null;
  name_hidden?: boolean;
};

/** An authored empty name is narration, not a missing character identity. */
export function dialogueSpeakerName(speaker?: DialogueSpeaker | null): unknown | null {
  if (speaker?.name_hidden) return null;
  const label = localizedText(speaker?.label);
  if (!label || /^speaker:\d+$/.test(label)) return 'Unknown speaker';
  return speaker!.label;
}
