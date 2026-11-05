import type { CutsceneAudioTrack } from './QuestMediaReferences';

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
