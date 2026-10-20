'use client';

import { Pause, Play, Volume2, VolumeX } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import '@/styles/audio-player.css';

function time(seconds: number) {
  const whole = Number.isFinite(seconds) ? Math.max(0, Math.floor(seconds)) : 0;
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
}

export function AudioPlayer({ src, label = 'Audio' }: { src?: string | null; label?: string }) {
  const audio = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [duration, setDuration] = useState(0);
  const [error, setError] = useState(false);
  const [muted, setMuted] = useState(false);
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
        <div className="audio-player-timeline">
          <input
            type="range"
            min="0"
            max={duration || 1}
            step="0.1"
            value={Math.min(elapsed, duration || 0)}
            disabled={!src || !duration || error}
            aria-label={`Seek: ${label}`}
            aria-valuetext={`${time(elapsed)} of ${time(duration)}`}
            onChange={(event) => {
              if (!audio.current) return;
              audio.current.currentTime = Number(event.target.value);
              setElapsed(audio.current.currentTime);
            }}
          />
          <small>
            {time(elapsed)} / {time(duration)}
          </small>
        </div>
        <button
          type="button"
          aria-label={`${muted ? 'Unmute' : 'Mute'} ${label}`}
          aria-pressed={muted}
          onClick={() => setMuted(!muted)}
        >
          {muted ? <VolumeX size={18} /> : <Volume2 size={18} />}
        </button>
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
