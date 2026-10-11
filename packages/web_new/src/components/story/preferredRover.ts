import type { CutsceneNode } from './QuestMediaReferences';

/** Only a complete, explicitly tagged Rover pair can skip a choice. */
export function preferredRoverTarget(
  node: CutsceneNode | undefined,
  preference: 'ask' | 'male' | 'female',
): string | null {
  if (preference === 'ask' || node?.kind !== 'choice' || node.options.length !== 2) return null;
  if (
    !node.options.some((option) => option.rover === 'male') ||
    !node.options.some((option) => option.rover === 'female')
  )
    return null;
  return node.options.find((option) => option.rover === preference)?.next ?? null;
}
