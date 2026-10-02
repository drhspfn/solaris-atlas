import { Film, Info, Pause, Play, Volume2, VolumeX } from 'lucide-react';
import { type ReactNode, useRef, useState } from 'react';

import { useNarrativePreferences } from '../../preferences/NarrativePreferences';

export interface MediaSource {
  file: string | null;
  raw_path: string | null;
}

export interface MediaAssetReference {
  reference: string;
  engine_path: string | null;
  source: MediaSource;
  video?: {
    url: string;
    asset_version: string;
    has_audio: boolean;
    soundtrack: string;
    subtitles_included: boolean;
  } | null;
}

export type CutsceneNode =
  | {
      id: string;
      kind: 'clip';
      asset: string;
      segment?: string | null;
      start: number;
      end: number | null;
      next: string | null;
    }
  | { id: string; kind: 'choice'; prompt: string; options: Array<{ label: string; next: string }> };
export interface CutsceneFlow {
  version: 1;
  entry: string;
  nodes: CutsceneNode[];
  evidence: string;
  asset_version: string;
  media: Record<string, NonNullable<MediaAssetReference['video']>>;
}

export interface QuestMediaEvent {
  kind: 'cutscene' | 'sequence' | 'audio_event';
  playback?: CutsceneFlow | null;
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
    variant?: {
      cg_id: number | null;
      girl_or_boy: number | null;
      belong_branch: string | null;
    } | null;
    caption?: {
      localization_key: string | null;
      show_moment: number | null;
      duration: number | null;
      timing_unit: string;
    } | null;
  }>;
}

export interface QuestMediaManifest {
  availability: 'references_only' | 'partial';
  events: QuestMediaEvent[];
  video_packages: Array<{ reference: string; packages: Array<{ reference: string }> }>;
}

function SourcePath({ source }: { source: MediaSource }) {
  return (
    <small title={source.raw_path || undefined}>
      {source.file?.split('/').at(-1)} · {source.raw_path}
    </small>
  );
}

export function QuestMediaReferences({
  manifest,
  stateAnchors,
}: {
  manifest: QuestMediaManifest | null;
  stateAnchors: Map<string, string>;
}) {
  if (!manifest) return null;
  const visualEvents = manifest.events.filter((event) => event.kind !== 'audio_event');
  const audioEvents = manifest.events.filter((event) => event.kind === 'audio_event');
  if (!visualEvents.length && !audioEvents.length && !manifest.video_packages.length) return null;
  return (
    <section className="quest-media-panel" aria-label="Quest media references">
      <h4>
        Media references <span>{manifest.events.length}</span>
      </h4>
      <p>
        {manifest.availability === 'partial'
          ? 'Available cutscenes can be watched beside the transcript. Other entries are source references.'
          : 'Game resource paths are recorded. Files have not been exported yet.'}
      </p>
      {visualEvents.map((event, index) => (
        <details className="quest-media-event" key={`${event.action}-${event.reference}-${index}`}>
          <summary>
            <Film size={13} />
            <span>
              {event.kind === 'cutscene'
                ? `Cutscene · ${event.reference.replace('cutscene:', '')}`
                : `Sequence · ${event.engine_path?.split('/').at(-1) || 'asset'}`}
              <small>{event.flow_state}</small>
            </span>
          </summary>
          {event.engine_path && <code>{event.engine_path}</code>}
          {stateAnchors.has(event.flow_state) && (
            <a className="quest-media-jump" href={`#${stateAnchors.get(event.flow_state)}`}>
              Read dialogue in this flow state ↗
            </a>
          )}
          {event.resources?.map((resource, resourceIndex) => (
            <div className="quest-media-resource" key={`${resource.reference}-${resourceIndex}`}>
              <span>{resource.kind.replaceAll('_', ' ')}</span>
              {resource.variant && (
                <small>
                  CG {resource.variant.cg_id} · GirlOrBoy{' '}
                  {resource.variant.girl_or_boy ?? 'unspecified'}
                  {resource.variant.belong_branch ? ` · ${resource.variant.belong_branch}` : ''}
                </small>
              )}
              {resource.caption && (
                <small>
                  Caption {resource.caption.localization_key} · timing{' '}
                  {resource.caption.show_moment} / {resource.caption.duration} (unit unverified)
                </small>
              )}
              {resource.assets.map((asset, assetIndex) => (
                <code key={assetIndex}>{asset.engine_path || asset.reference}</code>
              ))}
              <SourcePath source={resource.source} />
            </div>
          ))}
          <SourcePath source={event.source} />
        </details>
      ))}
      {audioEvents.length > 0 && (
        <details className="quest-media-event">
          <summary>
            <Volume2 size={13} />
            <span>
              Audio events<small>{audioEvents.length} source references</small>
            </span>
          </summary>
          {audioEvents.map((event, index) => (
            <div className="quest-media-resource" key={`${event.action}-${index}`}>
              <code>{event.engine_path || event.reference}</code>
              <SourcePath source={event.source} />
            </div>
          ))}
        </details>
      )}
      {manifest.video_packages.length > 0 && (
        <details className="quest-media-event">
          <summary>
            <Film size={13} />
            <span>
              Video packages<small>{manifest.video_packages.length} source references</small>
            </span>
          </summary>
          {manifest.video_packages
            .flatMap((entry) => entry.packages)
            .map((entry, index) => (
              <code key={index}>{entry.reference.replace('asset:video_package:', '')}</code>
            ))}
        </details>
      )}
    </section>
  );
}

export function DialogueAudioReference({
  media,
  children,
}: {
  children?: ReactNode;
  media?: {
    voice_references?: Array<{
      tracks?: Array<{ language: string; url: string; asset_version: string }>;
      file_name: string | null;
      plot_audio_id: string | null;
      source: MediaSource;
    }>;
    audio_event_paths?: Array<{ engine_path: string; source: MediaSource }>;
  };
}) {
  const { voiceLanguage } = useNarrativePreferences();
  const audioRef = useRef<HTMLAudioElement>(null);
  const [playingUrl, setPlayingUrl] = useState<string | null>(null);
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const voices = media?.voice_references || [];
  const events = media?.audio_event_paths || [];
  if (!voices.length && !events.length)
    return (
      <div className="dialogue-voice">
        <div className="dialogue-spoken-text">
          <span
            className="dialogue-play unavailable"
            role="img"
            aria-label="Audio not available yet"
            title="Audio not available yet"
            tabIndex={0}
          >
            <VolumeX size={16} />
          </span>
          {children}
        </div>
      </div>
    );
  const tracks = voices.flatMap((voice) => voice.tracks || []);
  const track = tracks.find((entry) => entry.language === voiceLanguage);
  return (
    <div className="dialogue-voice">
      <div className="dialogue-spoken-text">
        {track && (
          <>
            <button
              type="button"
              className="dialogue-play"
              aria-label={playingUrl === track.url ? 'Pause voice' : 'Play voice'}
              title={playingUrl === track.url ? 'Pause voice' : 'Play voice'}
              onClick={() => {
                const audio = audioRef.current;
                if (!audio) return;
                if (!audio.paused) {
                  audio.pause();
                  return;
                }
                document.querySelectorAll<HTMLMediaElement>('audio, video').forEach((other) => {
                  if (other !== audio) other.pause();
                });
                void audio.play().catch(() => setFailedUrl(track.url));
              }}
            >
              {playingUrl === track.url ? <Pause size={16} /> : <Play size={16} />}
            </button>
            <audio
              ref={audioRef}
              key={track.url}
              preload="none"
              src={track.url}
              onPlaying={() => {
                setPlayingUrl(track.url);
                setFailedUrl(null);
              }}
              onPause={() => setPlayingUrl(null)}
              onEnded={() => setPlayingUrl(null)}
              onError={() => {
                setPlayingUrl(null);
                setFailedUrl(track.url);
              }}
            />
          </>
        )}
        {!track && (
          <span
            className="dialogue-play unavailable"
            role="img"
            aria-label="Audio not available yet"
            title={
              tracks.length
                ? 'This voice language is not available yet.'
                : 'Audio not available yet.'
            }
            tabIndex={0}
          >
            <VolumeX size={16} />
          </span>
        )}
        {children}
      </div>
      {failedUrl === track?.url && (
        <small role="alert">Audio could not load. Try playing again or refresh the page.</small>
      )}
      <details className="dialogue-audio-ref dialogue-source-info">
        <summary
          aria-label="Audio source details"
          title={voices
            .map((voice) =>
              [voice.file_name || voice.plot_audio_id, voice.source.file, voice.source.raw_path]
                .filter(Boolean)
                .join('\n'),
            )
            .concat(events.map((event) => event.engine_path))
            .join('\n\n')}
        >
          <Info size={14} />
        </summary>
        <div className="dialogue-source-popover">
          {voices.map((voice, index) => (
            <div key={`${voice.plot_audio_id}-${index}`}>
              <code>{voice.file_name || voice.plot_audio_id}</code>
              <small>PlotAudio filename · no confirmed Unreal asset path</small>
              <SourcePath source={voice.source} />
            </div>
          ))}
          {events.map((event, index) => (
            <div key={`${event.engine_path}-${index}`}>
              <code>{event.engine_path}</code>
              <small>Unreal audio event path</small>
              <SourcePath source={event.source} />
            </div>
          ))}
        </div>
      </details>
    </div>
  );
}
