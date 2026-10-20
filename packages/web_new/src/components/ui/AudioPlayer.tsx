'use client';

import { Pause, Play, Volume2, VolumeX } from 'lucide-react';
import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import '@/styles/audio-player.css';

function time(seconds: number) {
  const whole = Number.isFinite(seconds) ? Math.max(0, Math.floor(seconds)) : 0;
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
}

export function AudioPlayer({
  src,
  label = 'Audio',
  settings,
}: {
  src?: string | null;
  label?: string;
  settings?: ReactNode;
}) {
  const audio = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [duration, setDuration] = useState(0);
  const [error, setError] = useState(false);
  const [muted, setMuted] = useState(false);
  const [volume, setVolume] = useState(1);
  useEffect(() => {
    if (audio.current) audio.current.volume = volume;
  }, [volume, src]);
  useEffect(() => {
    setPlaying(false);
    setElapsed(0);
    setDuration(0);
    setError(false);
  }, [src]);
  return (
    <div className="audio-player" role="group" aria-label={`Audio: ${label}`}>
      <div className="audio-player-controls">
        <button
          type="button"
          disabled={!src || error}
          aria-label={`${playing ? 'Pause' : 'Play'} ${label}`}
          onClick={async () => {
            if (!audio.current) return;
            if (playing) audio.current.pause();
            else {
              try {
                await audio.current.play();
              } catch {
                setError(true);
              }
            }
          }}
        >
          {playing ? <Pause size={18} /> : <Play size={18} />}
        </button>
        <input
          className="audio-player-progress"
          type="range"
          min="0"
          max={duration || 1}
          step="0.1"
          value={Math.min(elapsed, duration || 0)}
          disabled={!src || !duration || error}
          aria-label={`Seek: ${label}`}
          aria-valuetext={`${time(elapsed)} of ${time(duration)}`}
          style={
            { '--audio-progress': `${duration ? (elapsed / duration) * 100 : 0}%` } as CSSProperties
          }
          onChange={(event) => {
            if (!audio.current) return;
            audio.current.currentTime = Number(event.target.value);
            setElapsed(audio.current.currentTime);
          }}
        />
        <small className="audio-player-time">
          {time(elapsed)} / {time(duration)}
        </small>
        <div className="audio-player-volume">
          <button
            type="button"
            aria-label={`${muted || !volume ? 'Unmute' : 'Mute'} ${label}`}
            aria-pressed={muted || !volume}
            onClick={() => {
              if (muted || !volume) {
                setMuted(false);
                if (!volume) setVolume(1);
              } else setMuted(true);
            }}
          >
            {muted || !volume ? <VolumeX size={16} /> : <Volume2 size={16} />}
          </button>
          <div className="audio-player-volume-popup">
            <input
              type="range"
              min="0"
              max="1"
              step="0.01"
              value={volume}
              aria-label={`Volume: ${label}`}
              aria-valuetext={`${Math.round(volume * 100)}%`}
              onChange={(event) => {
                setVolume(Number(event.target.value));
                setMuted(false);
              }}
            />
          </div>
        </div>
        {settings && <div className="audio-player-settings">{settings}</div>}
      </div>
      {src && (
        <audio
          key={src}
          ref={audio}
          src={src}
          preload="none"
          muted={muted}
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onEnded={() => setPlaying(false)}
          onTimeUpdate={(event) => setElapsed(event.currentTarget.currentTime)}
          onLoadedMetadata={(event) =>
            setDuration(
              Number.isFinite(event.currentTarget.duration) ? event.currentTarget.duration : 0,
            )
          }
          onError={() => {
            setError(true);
            setPlaying(false);
          }}
        />
      )}
      {!src && <small role="status">Audio unavailable.</small>}
      {error && (
        <div>
          <small role="alert">Audio could not be played.</small>
          <button
            type="button"
            onClick={() => {
              setError(false);
              audio.current?.load();
            }}
          >
            Retry audio
          </button>
        </div>
      )}
    </div>
  );
}
