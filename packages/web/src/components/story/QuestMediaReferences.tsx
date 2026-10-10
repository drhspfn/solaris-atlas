import { Film, Info, Pause, Play, VolumeX } from 'lucide-react';
import { type ReactNode, useRef, useState } from 'react';

import { useNarrativePreferences } from '../../preferences/NarrativePreferences';
import { cutsceneAnchor } from './cutsceneAnchor';

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
    timeline_offset?: number;
    audio_tracks?: CutsceneAudioTrack[] | null;
    asset_node_id?: number;
    description?: {
      title: string;
      text: string;
      chapters: { start: number; end: number; title: string; text: string }[];
    };
  } | null;
}

export interface CutsceneAudioTrack {
  role: 'voice' | 'music' | 'effects' | 'mixed';
  language: 'en' | 'ja' | 'ko' | 'zh' | null;
  url: string;
}

export interface CutsceneCaption {
  start: number;
  end: number;
  text: string;
  key: string;
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
  | {
      id: string;
      kind: 'choice';
      prompt: string;
      options: Array<{ label: string; next: string; rover?: 'male' | 'female' | null }>;
    };
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
  action_index: number;
  transcript_states?: string[];
  captions?: Record<string, CutsceneCaption[]>;
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
  visibleCutscenes = manifest?.events || [],
}: {
  manifest: QuestMediaManifest | null;
  stateAnchors: Map<string, string>;
  visibleCutscenes?: QuestMediaEvent[];
}) {
  if (!manifest) return null;
  const visible = new Set(visibleCutscenes.map(cutsceneAnchor));
  const links = manifest.events.flatMap((event) => {
    const playable =
      event.kind === 'cutscene' &&
      (event.playback ||
        event.resources?.some(
          (resource) =>
            resource.kind === 'has_variant' && resource.assets.some((asset) => asset.video),
        ));
    const anchor =
      playable && visible.has(cutsceneAnchor(event))
        ? cutsceneAnchor(event)
        : stateAnchors.get(event.flow_state);
    if (!anchor || event.kind === 'audio_event') return [];
    return [{ event, anchor }];
  });
  if (!links.length) return null;
  return (
    <nav className="quest-media-panel" aria-label="Quest scenes">
      <h4>
        Scenes <span>{links.length}</span>
      </h4>
      {links.map(({ event, anchor }) => (
        <a
          className="quest-media-jump quest-media-event"
          key={cutsceneAnchor(event)}
          href={`#${anchor}`}
        >
          <Film size={13} />
          <span>
            {event.kind === 'cutscene'
              ? `Cutscene � ${event.reference.replace('cutscene:', '')}`
              : 'Story scene'}
          </span>
        </a>
      ))}
    </nav>
  );
}

export function DialogueAudioReference({
  media,
  children,
}: {
  children?: ReactNode;
  media?: {
    voice_references?: Array<{
      tracks?: Array<{
        language: string;
        url: string;
        asset_version: string;
        rover?: 'male' | 'female' | null;
      }>;
      file_name: string | null;
      plot_audio_id: string | null;
      source: MediaSource;
    }>;
    audio_event_paths?: Array<{ engine_path: string; source: MediaSource }>;
  };
}) {
  const { voiceLanguage, preferredRover } = useNarrativePreferences();
  const [voiceRover, setVoiceRover] = useState<'male' | 'female' | 'ask'>('ask');
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
  const rover = preferredRover === 'ask' ? voiceRover : preferredRover;
  const track =
    tracks.find((entry) => entry.language === voiceLanguage && entry.rover === rover) ||
    tracks.find((entry) => entry.language === voiceLanguage && !entry.rover);
  const roverChoices = tracks.filter((entry) => entry.language === voiceLanguage && entry.rover);

  return (
    <div className="dialogue-voice">
      {preferredRover === 'ask' && roverChoices.length > 0 && (
        <label className="voice-language">
          Rover voice
          <select
            aria-label="Rover voice"
            value={voiceRover}
            onChange={(event) => setVoiceRover(event.target.value as typeof voiceRover)}
          >
            <option value="ask">Choose Rover</option>
            {['female', 'male']
              .filter((value) => roverChoices.some((entry) => entry.rover === value))
              .map((value) => (
                <option key={value} value={value}>
                  {value === 'female' ? 'Female Rover' : 'Male Rover'}
                </option>
              ))}
          </select>
        </label>
      )}
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
