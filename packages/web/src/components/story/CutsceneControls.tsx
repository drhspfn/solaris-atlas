import {
  Captions,
  GitBranch,
  Maximize,
  Pause,
  Play,
  RotateCcw,
  Settings2,
  Volume2,
  VolumeX,
} from 'lucide-react';
import { useState } from 'react';

import { APP_SETTINGS } from '../../config/settings';
import type { cutsceneTimeline } from './cutsceneTimeline';

function clock(seconds: number) {
  const value = Math.floor(Math.max(0, seconds));
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`;
}

type Language = 'en' | 'ja' | 'ko' | 'zh';
export function CutsceneControls({
  timeline,
  position,
  playing,
  volume,
  musicVolume,
  subtitles,
  hasCaptions,
  hasMusic,
  languages,
  language,
  onLanguage,
  onVolume,
  onMusicVolume,
  onSubtitles,
  onSeek,
  onPlay,
  onRestart,
  onFullscreen,
  onChoice,
}: {
  timeline: ReturnType<typeof cutsceneTimeline>;
  position: number;
  playing: boolean;
  volume: number;
  musicVolume: number;
  subtitles: boolean;
  hasCaptions: boolean;
  hasMusic: boolean;
  languages: (Language | null)[];
  language: Language;
  onLanguage: (language: Language) => void;
  onVolume: (volume: number) => void;
  onMusicVolume: (volume: number) => void;
  onSubtitles: () => void;
  onSeek: (time: number) => void;
  onPlay: () => void;
  onRestart: () => void;
  onFullscreen: () => void;
  onChoice: (id: string) => void;
}) {
  const [settings, setSettings] = useState(false);
  const [unmuted, setUnmuted] = useState(volume || APP_SETTINGS.cutscene.masterVolume);
  const provisional = timeline.decisions.some((entry) => entry.unresolved);
  return (
    <div
      className="cutscene-controls"
      onKeyDown={(event) => {
        if (event.key === 'Escape') {
          setSettings(false);
          event.stopPropagation();
        }
      }}
    >
      <div className="cutscene-seek">
        <div className="cutscene-segments" aria-hidden="true">
          {timeline.clips.map((clip, index) => (
            <span
              key={clip.id}
              title={`Part ${index + 1}`}
              style={{
                flexGrow: clip.duration,
                background: `linear-gradient(to right, var(--mint) ${Math.max(0, Math.min(100, ((position - clip.start) / clip.duration) * 100))}%, rgb(255 255 255 / 28%) 0)`,
              }}
            />
          ))}
        </div>
        <input
          aria-label="Cutscene timeline"
          aria-valuetext={`${clock(position)} of ${clock(timeline.total)}${provisional ? ', duration depends on choice' : ''}`}
          type="range"
          min="0"
          max={timeline.total || 1}
          step="0.1"
          value={Math.min(position, timeline.total)}
          disabled={!timeline.complete || !timeline.total}
          onChange={(event) => onSeek(Number(event.target.value))}
        />
        {timeline.decisions.map((entry) => (
          <button
            key={entry.id}
            className="cutscene-decision"
            style={{ left: `${timeline.total ? (entry.time / timeline.total) * 100 : 0}%` }}
            aria-label={entry.unresolved ? 'Choose a branch' : 'Change branch'}
            title={entry.unresolved ? 'Choose a branch' : 'Change branch'}
            onClick={() => onChoice(entry.id)}
          >
            <GitBranch size={12} />
          </button>
        ))}
      </div>
      <div className="cutscene-control-row">
        <button
          type="button"
          aria-label={playing ? 'Pause cutscene' : 'Play cutscene'}
          title={playing ? 'Pause (K)' : 'Play (K)'}
          onClick={onPlay}
        >
          {playing ? <Pause size={20} /> : <Play size={20} />}
        </button>
        <button
          type="button"
          aria-label={volume ? 'Mute cutscene' : 'Unmute cutscene'}
          title="Mute (M)"
          onClick={() => {
            if (volume) {
              setUnmuted(volume);
              onVolume(0);
            } else onVolume(unmuted);
          }}
        >
          {volume ? <Volume2 size={19} /> : <VolumeX size={19} />}
        </button>
        <input
          className="cutscene-master-volume"
          aria-label="Cutscene volume"
          aria-valuetext={`${Math.round(volume * 100)} percent`}
          type="range"
          min="0"
          max="1"
          step="0.01"
          value={volume}
          onChange={(event) => onVolume(Number(event.target.value))}
        />
        <span className="cutscene-time">
          {clock(position)} / {timeline.complete ? clock(timeline.total) : '…'}
          {provisional && (
            <small title="Total duration follows the preview branch until you choose"> ≈</small>
          )}
        </span>
        <span className="cutscene-control-spacer" />
        <button
          type="button"
          aria-label="Subtitles"
          aria-pressed={subtitles && hasCaptions}
          disabled={!hasCaptions}
          title={hasCaptions ? 'Subtitles (C)' : 'Subtitles unavailable'}
          onClick={onSubtitles}
        >
          <Captions size={20} />
        </button>
        <button
          type="button"
          aria-label="Playback settings"
          aria-expanded={settings}
          title="Playback settings"
          onClick={() => setSettings(!settings)}
        >
          <Settings2 size={19} />
        </button>
        <button
          type="button"
          aria-label="Restart cutscene"
          title="Restart cutscene"
          onClick={onRestart}
        >
          <RotateCcw size={18} />
        </button>
        <button
          type="button"
          aria-label="Toggle fullscreen"
          title="Fullscreen (F)"
          onClick={onFullscreen}
        >
          <Maximize size={19} />
        </button>
      </div>
      {settings && (
        <div className="cutscene-options" role="group" aria-label="Playback settings">
          <header>
            <strong>Playback</strong>
            <button type="button" onClick={() => setSettings(false)}>
              Done
            </button>
          </header>
          {!!languages.length && (
            <label>
              Voice
              <select
                aria-label="Cutscene voice language"
                value={language}
                onChange={(event) => onLanguage(event.target.value as Language)}
              >
                {(
                  [
                    ['en', 'English'],
                    ['ja', 'Japanese'],
                    ['ko', 'Korean'],
                    ['zh', 'Chinese'],
                  ] as const
                ).map(([code, label]) => (
                  <option key={code} value={code} disabled={!languages.includes(code)}>
                    {label}
                    {!languages.includes(code) ? ' · unavailable' : ''}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label>
            Overall volume <output>{Math.round(volume * 100)}%</output>
            <input
              aria-label="Overall volume"
              type="range"
              min="0"
              max="1"
              step="0.01"
              value={volume}
              onChange={(event) => onVolume(Number(event.target.value))}
            />
          </label>
          {hasMusic ? (
            <label>
              Music level <output>{Math.round(musicVolume * 100)}%</output>
              <input
                aria-label="Background music level"
                type="range"
                min="0"
                max="1"
                step="0.01"
                value={musicVolume}
                onChange={(event) => onMusicVolume(Number(event.target.value))}
              />
              <small>Relative to overall volume. Set to 0 to turn music off.</small>
            </label>
          ) : (
            <p>Music is part of the original mix, or no separate music track is available.</p>
          )}
        </div>
      )}
    </div>
  );
}
