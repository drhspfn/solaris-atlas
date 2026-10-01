import { Film, Volume2 } from "lucide-react";

export interface MediaSource {
  file: string | null;
  raw_path: string | null;
}

export interface MediaAssetReference {
  reference: string;
  engine_path: string | null;
  source: MediaSource;
}

export interface QuestMediaEvent {
  kind: "cutscene" | "sequence" | "audio_event";
  action: string;
  flow_state: string;
  reference: string;
  engine_path: string | null;
  source: MediaSource;
  resources?: Array<{
    kind: string;
    reference: string;
    source: MediaSource;
    assets: MediaAssetReference[];
    variant?: { cg_id: number | null; girl_or_boy: number | null; belong_branch: string | null } | null;
    caption?: { localization_key: string | null; show_moment: number | null; duration: number | null; timing_unit: string } | null;
  }>;
}

export interface QuestMediaManifest {
  availability: "references_only";
  events: QuestMediaEvent[];
  video_packages: Array<{ reference: string; packages: Array<{ reference: string }> }>;
}

function SourcePath({ source }: { source: MediaSource }) {
  return <small title={source.raw_path || undefined}>{source.file?.split("/").at(-1)} · {source.raw_path}</small>;
}

export function QuestMediaReferences({ manifest, stateAnchors }: {
  manifest: QuestMediaManifest | null;
  stateAnchors: Map<string, string>;
}) {
  if (!manifest) return null;
  const visualEvents = manifest.events.filter((event) => event.kind !== "audio_event");
  const audioEvents = manifest.events.filter((event) => event.kind === "audio_event");
  if (!visualEvents.length && !audioEvents.length && !manifest.video_packages.length) return null;
  return (
    <section className="quest-media-panel" aria-label="Quest media references">
      <h4>Media references <span>{manifest.events.length}</span></h4>
      <p>Game resource paths are recorded. Files have not been exported yet.</p>
      {visualEvents.map((event, index) => (
        <details className="quest-media-event" key={`${event.action}-${event.reference}-${index}`}>
          <summary>
            <Film size={13} />
            <span>{event.kind === "cutscene" ? `Cutscene · ${event.reference.replace("cutscene:", "")}` : `Sequence · ${event.engine_path?.split("/").at(-1) || "asset"}`}<small>{event.flow_state}</small></span>
          </summary>
          {event.engine_path && <code>{event.engine_path}</code>}
          {stateAnchors.has(event.flow_state) && <a className="quest-media-jump" href={`#${stateAnchors.get(event.flow_state)}`}>Read dialogue in this flow state ↗</a>}
          {event.resources?.map((resource, resourceIndex) => (
            <div className="quest-media-resource" key={`${resource.reference}-${resourceIndex}`}>
              <span>{resource.kind.replaceAll("_", " ")}</span>
              {resource.variant && <small>CG {resource.variant.cg_id} · GirlOrBoy {resource.variant.girl_or_boy ?? "unspecified"}{resource.variant.belong_branch ? ` · ${resource.variant.belong_branch}` : ""}</small>}
              {resource.caption && <small>Caption {resource.caption.localization_key} · timing {resource.caption.show_moment} / {resource.caption.duration} (unit unverified)</small>}
              {resource.assets.map((asset, assetIndex) => <code key={assetIndex}>{asset.engine_path || asset.reference}</code>)}
              <SourcePath source={resource.source} />
            </div>
          ))}
          <SourcePath source={event.source} />
        </details>
      ))}
      {audioEvents.length > 0 && (
        <details className="quest-media-event">
          <summary><Volume2 size={13} /><span>Audio events<small>{audioEvents.length} source references</small></span></summary>
          {audioEvents.map((event, index) => <div className="quest-media-resource" key={`${event.action}-${index}`}>
            <code>{event.engine_path || event.reference}</code>
            <SourcePath source={event.source} />
          </div>)}
        </details>
      )}
      {manifest.video_packages.length > 0 && (
        <details className="quest-media-event">
          <summary><Film size={13} /><span>Video packages<small>{manifest.video_packages.length} source references</small></span></summary>
          {manifest.video_packages.flatMap((entry) => entry.packages).map((entry, index) =>
            <code key={index}>{entry.reference.replace("asset:video_package:", "")}</code>)}
        </details>
      )}
    </section>
  );
}

export function DialogueAudioReference({ media }: {
  media?: { voice_references?: Array<{ file_name: string | null; plot_audio_id: string | null; source: MediaSource }>;
            audio_event_paths?: Array<{ engine_path: string; source: MediaSource }> };
}) {
  const voices = media?.voice_references || [];
  const events = media?.audio_event_paths || [];
  if (!voices.length && !events.length) return null;
  return <details className="dialogue-audio-ref">
    <summary><Volume2 size={13} /> Voice source · awaiting file export</summary>
    {voices.map((voice, index) => <div key={`${voice.plot_audio_id}-${index}`}>
      <code>{voice.file_name || voice.plot_audio_id}</code>
      <small>PlotAudio filename · no confirmed Unreal asset path</small>
      <SourcePath source={voice.source} />
    </div>)}
    {events.map((event, index) => <div key={`${event.engine_path}-${index}`}>
      <code>{event.engine_path}</code>
      <small>Unreal audio event path</small>
      <SourcePath source={event.source} />
    </div>)}
  </details>;
}
