import type { CutsceneAudioTrack, CutsceneFlow } from './QuestMediaReferences';

export function clipRover(flow: CutsceneFlow, clipId: string, preference: 'male' | 'female' | 'ask') {
  const clip = flow.nodes.find(node => node.id === clipId);
  if (clip?.kind === 'clip' && clip.rover) return clip.rover;
  // Older published flows keep the selector on the incoming authored option.
  const selectors = new Set(flow.nodes.flatMap(node => node.kind === 'choice'
    ? node.options.filter(option => option.next === clipId && option.rover).map(option => option.rover!)
    : []));
  return selectors.size === 1 ? [...selectors][0] : preference;
}

/** Shared movies retain independent authored voice/effect variants. */
export function selectCutsceneTracks(
  tracks: CutsceneAudioTrack[],
  language: string,
  rover: 'male' | 'female' | 'ask' | null,
) {
  return tracks.filter(
    (track) =>
      (!track.rover || track.rover === rover) &&
      (track.role !== 'voice' || track.language === language),
  );
}
