import type { QuestMediaEvent } from "./QuestMediaReferences";
import type { EncounterCinematic, EncounterFlow } from "./encounterTransitions";

export function sequenceVideoUrls(video: QuestMediaEvent["youtube_fallback"]) {
  if (!video || !/^[A-Za-z0-9_-]{11}$/.test(video.video_id) ||
      !Number.isSafeInteger(video.start_seconds) || video.start_seconds < 0 ||
      (video.end_seconds !== null && (!Number.isSafeInteger(video.end_seconds) ||
        video.end_seconds <= video.start_seconds))) return null;
  const query = new URLSearchParams({ start: String(video.start_seconds) });
  if (video.end_seconds !== null) query.set("end", String(video.end_seconds));
  return {
    embed: `https://www.youtube-nocookie.com/embed/${video.video_id}?${query}`,
    watch: `https://www.youtube.com/watch?v=${video.video_id}&t=${video.start_seconds}s`,
  };
}

export function encounterSequenceRecordings(
  events: QuestMediaEvent[], flow: EncounterFlow, part: EncounterCinematic[],
) {
  return events.filter((event) => {
    if (event.kind !== "sequence" || !sequenceVideoUrls(event.youtube_fallback) ||
        event.flow_state !== flow.state) return false;
    const matches = flow.cinematics.filter((scene) =>
      scene.kind === "sequence" && scene.reference === event.reference &&
      scene.start.action_index === event.action_index);
    const peers = events.filter((other) =>
      other.kind === "sequence" && other.flow_state === flow.state &&
      other.reference === event.reference && other.action_index === event.action_index);
    return matches.length === 1 && peers.length === 1 && part.includes(matches[0]);
  });
}
