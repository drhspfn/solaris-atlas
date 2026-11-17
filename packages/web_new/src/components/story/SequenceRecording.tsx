import type { QuestMediaEvent } from "./QuestMediaReferences";
import { sequenceVideoUrls } from "./sequenceVideo";

export function SequenceRecording({ event }: { event: QuestMediaEvent }) {
  const urls = sequenceVideoUrls(event.youtube_fallback);
  if (!urls) return null;
  return <div>
    <p>Scene playback is unavailable here. Watch the recording instead.</p>
    <iframe title="In-game scene recording" src={urls.embed} loading="lazy"
      allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
      allowFullScreen style={{ width: "100%", aspectRatio: "16 / 9", border: 0 }} />
    <p><a href={urls.watch}>Watch on YouTube ↗</a></p>
  </div>;
}
