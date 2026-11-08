import type { SequenceMedia } from "./encounterTransitions";
import { sequenceInterval } from "./sequenceComponents";
import { illustrationSections } from "./sequenceIllustrations";
import { mediaViewerUrl } from "../ui/mediaViewerUtils";

type Line = {
  id?: string;
  flow_state?: string;
  illustration?: { id: string; kind: string };
  text?: {
    key?: string | null;
    content?: string | null;
    inline_text?: string | null;
  };
  media?: {
    voice_references?: Array<{
      plot_audio_id?: string | null;
      file_name?: string | null;
    }>;
  };
};

export function sequenceFrameForLine(
  media: SequenceMedia | undefined,
  line: Line,
) {
  if (!media) return undefined;
  const keys = new Set(
    [
      line.text?.key,
      ...(line.media?.voice_references || []).flatMap((voice) => [
        voice.plot_audio_id,
        voice.file_name,
      ]),
    ].filter((key): key is string => typeof key === "string" && !!key),
  );
  const cues = media.sections.flatMap((section) => {
    if (section.kind !== "dialogue" && section.kind !== "flow_dialogue")
      return [];
    const key =
      typeof section.text_key === "string"
        ? section.text_key
        : section.reference;
    const interval = sequenceInterval(section);
    const time =
      typeof section.time_seconds === "number"
        ? section.time_seconds
        : interval?.start;
    if (
      !key ||
      !keys.has(key) ||
      typeof section.scene_source !== "string" ||
      !section.scene_source ||
      typeof time !== "number" ||
      !Number.isFinite(time) ||
      time < 0
    )
      return [];
    return [{ scene: section.scene_source, time }];
  });
  const unique = new Map(
    cues.map((cue) => [`${cue.scene}\u0000${cue.time}`, cue]),
  );
  if (unique.size !== 1) return undefined;
  const cue = [...unique.values()][0];
  const frames = illustrationSections(media).filter(
    (frame) =>
      frame.scene_source === cue.scene &&
      typeof frame.time_seconds === "number" &&
      Number.isFinite(frame.time_seconds) &&
      frame.time_seconds >= 0 &&
      frame.time_seconds <= cue.time &&
      !!mediaViewerUrl(frame.url!),
  );
  const latest = Math.max(...frames.map(frame => frame.time_seconds as number));
  const matches = frames.filter((frame) => frame.time_seconds === latest);
  return matches.length &&
    matches.every(
      (frame) =>
        frame.file_id === matches[0].file_id && frame.url === matches[0].url,
    )
    ? matches[0]
    : undefined;
}

type SequenceSource = { flow_state: string; sequence_media?: SequenceMedia | null };

// Enrich only an exact, unambiguous flow/cue/frame match; keep transcript data intact.
export function enrichSequenceLines<T extends Line>(lines: T[], sources: SequenceSource[]) {
  return lines.map(line => {
    if (!line.flow_state || line.illustration) return line;
    const matches = sources.flatMap(source => {
      if (source.flow_state !== line.flow_state || !source.sequence_media) return [];
      const frame = sequenceFrameForLine(source.sequence_media, line);
      return frame ? [{ frame, media: source.sequence_media }] : [];
    });
    if (matches.length !== 1) return line;
    const { frame, media } = matches[0];
    return { ...line, sequenceMedia: media, illustration: {
      id: `sequence:${frame.file_id}`, kind: 'image' as const,
      images: [{ rover: null, engine_path: '', url: frame.url! }],
    } };
  });
}

export function sequenceBackgroundMusic(media?: SequenceMedia) {
  if (!media || media.schema_version !== 1) return undefined;
  const tracks = new Map<string, { url: string; loop: boolean }>();
  for (const section of media.sections) {
    if (section.kind !== 'audio_event' || !Array.isArray(section.tracks)) continue;
    for (const track of section.tracks) {
      if (track && typeof track === 'object' && track.role === 'music' && track.status === 'published' &&
          typeof track.url === 'string' && mediaViewerUrl(track.url)) {
        const music = { url: track.url, loop: track.loop === true };
        tracks.set(JSON.stringify(music), music);
      }
    }
  }
  return tracks.size === 1 ? [...tracks.values()][0] : undefined;
}

// Flow music is separate from scene cues; every displayed line must be covered.
export function flowBackgroundMusic(lines: Line[], events: unknown[]) {
  if (!lines.length || !lines[0].flow_state || lines.some(line =>
    line.flow_state !== lines[0].flow_state || typeof line.text?.key !== "string" || !line.text.key)) return undefined;
  const matches = events.flatMap((value) => {
    if (!value || typeof value !== "object") return [];
    const event = value as { kind?: string; flow_state?: string; covered_text_keys?: unknown; sequence_media?: SequenceMedia };
    if (event.kind !== "flow_music" || event.flow_state !== lines[0].flow_state ||
        !Array.isArray(event.covered_text_keys) || event.covered_text_keys.some(key => typeof key !== "string" || !key)) return [];
    const keys: string[] = event.covered_text_keys;
    if (!lines.every(line => keys.includes(line.text!.key!))) return [];
    const media = event.sequence_media;
    if (!media || media.schema_version !== 1 || media.complete_video !== false || !Array.isArray(media.sections)) return [];
    const sections = media.sections.filter(section => {
      if (section.kind !== "audio_event" || section.scope !== "flow" || section.reconstruction_kind !== "flow_authored_music") return false;
      const proof = section.source_proof as Record<string, unknown> | undefined;
      if (!proof || proof.flow_state !== event.flow_state || !Array.isArray(proof.talk_text_keys) ||
          proof.talk_text_keys.some(key => typeof key !== "string" || !key) ||
          !["presentation_sha256", "asset_version", "download_id", "play_path", "talk_path", "stop_path"].every(key => typeof proof[key] === "string" && !!proof[key]) ||
          !Number.isSafeInteger(proof.flow_owner) || !Number.isSafeInteger(proof.release_id)) return false;
      const covered = new Set(event.covered_text_keys as string[]);
      const proven = new Set(proof.talk_text_keys as string[]);
      return covered.size === proven.size && [...covered].every(key => proven.has(key));
    });
    if (sections.length !== 1) return [];
    const music = sequenceBackgroundMusic({ ...media, sections });
    return music ? [music] : [];
  });
  return matches.length === 1 ? matches[0] : undefined;
}

// A reading aid, not an authored scene clock.
export function readingDelay(text: string, millisecondsPerCharacter = 55) {
  const rate =
    Number.isFinite(millisecondsPerCharacter) && millisecondsPerCharacter > 0
      ? millisecondsPerCharacter
      : 55;
  return Math.max(2500, Math.min(15000, Array.from(text.trim()).length * rate));
}
