'use client';

import { BookOpen, ChevronLeft, ChevronRight } from 'lucide-react';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { QuestIllustration, type Illustration } from './QuestIllustration';
import type { SequenceMedia } from './encounterTransitions';
import { readingDelay, sequenceFrameForLine } from './sequenceReader';
import { mediaViewerUrl } from '../ui/mediaViewerUtils';

export function QuestIllustratedScene({
  lines,
  renderLine,
  anchors,
  focusLine,
  sequenceMedia,
  backgroundMusic,
  readingMillisecondsPerCharacter = 55,
}: {
  lines: Array<{ id: string; illustration: Illustration; text?: { key?: string | null; content?: string | null; inline_text?: string | null }; media?: { voice_references?: Array<{ plot_audio_id?: string | null; file_name?: string | null }> } }>;
  renderLine: (line: any) => ReactNode;
  anchors: Map<string, string>;
  focusLine?: string | null;
  sequenceMedia?: SequenceMedia;
  backgroundMusic?: string | null;
  readingMillisecondsPerCharacter?: number;
}) {
  const [index, setIndex] = useState(0);
  const [transcript, setTranscript] = useState(false);
  const [auto, setAuto] = useState(false);
  const [autoError, setAutoError] = useState(false);
  const [musicError, setMusicError] = useState(false);
  const root = useRef<HTMLElement>(null);
  const music = useRef<HTMLAudioElement>(null);
  const active = useRef(0);
  const current = Math.min(index, lines.length - 1);
  const stopAudio = () =>
    root.current
      ?.querySelectorAll<HTMLMediaElement>('.quest-scene-dialogue audio, .quest-scene-dialogue video')
      .forEach((media) => {
        if (!media.closest('.quest-illustration')) media.pause();
      });
  const startMusic = () => {
    const audio = music.current;
    if (!audio || !audio.paused) return;
    setMusicError(false);
    void audio.play().catch(() => setMusicError(true));
  };
  const select = (next: number, userGesture = false) => {
    if (userGesture) startMusic();
    stopAudio();
    active.current = next;
    setIndex(next);
  };
  useEffect(() => {
    if (!auto || transcript || !lines.length) return;
    const container = root.current?.querySelector<HTMLElement>('[data-scene-active="true"]');
    const audio = container?.querySelector<HTMLAudioElement>('.dialogue-voice audio');
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const advance = () => {
      if (cancelled) return;
      if (current < lines.length - 1) select(current + 1);
      else setAuto(false);
    };
    if (audio) {
      audio.addEventListener('ended', advance);
      const fail = () => { if (!cancelled) { setAuto(false); setAutoError(true); } };
      audio.addEventListener('error', fail);
      void audio.play().catch(fail);
      return () => { cancelled = true; audio.removeEventListener('ended', advance); audio.removeEventListener('error', fail); };
    }
    const text = lines[current].text?.content || lines[current].text?.inline_text || '';
    timer = setTimeout(advance, readingDelay(text, readingMillisecondsPerCharacter));
    return () => { cancelled = true; clearTimeout(timer); };
  }, [auto, transcript, current, lines[current]?.id, readingMillisecondsPerCharacter]);
  useEffect(() => {
    let frame: number | undefined;
    const reveal = () => {
      let id: string;
      try {
        id = decodeURIComponent(location.hash.slice(1));
      } catch {
        return;
      }
      const next = lines.findIndex((line) => anchors.get(line.id) === id || line.id === focusLine);
      if (next >= 0) {
        if (next !== active.current) select(next);
        setTranscript(false);
        if (frame !== undefined) cancelAnimationFrame(frame);
        frame = requestAnimationFrame(() => {
          const target = document.getElementById(anchors.get(lines[next].id) || '');
          if (target && root.current?.contains(target)) target.scrollIntoView({ block: 'center' });
        });
      }
    };
    reveal();
    window.addEventListener('hashchange', reveal);
    return () => {
      window.removeEventListener('hashchange', reveal);
      if (frame !== undefined) cancelAnimationFrame(frame);
    };
  }, [lines, anchors, focusLine]);
  if (!lines.length) return null;
  const frame = sequenceFrameForLine(sequenceMedia, lines[current]);
  const backdrop: Illustration = frame ? { id: `sequence:${frame.file_id}`, kind: 'image', images: [{ rover: null, engine_path: '', url: frame.url! }] } : lines[current].illustration;
  return (
    <section
      className={`quest-illustrated-scene${transcript ? ' is-transcript' : ''}`}
      ref={root}
      aria-label="Illustrated dialogue"
      tabIndex={0}
      onKeyDown={(event) => {
        if (transcript || event.target !== event.currentTarget) return;
        if (event.key === 'ArrowRight' || event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          if (current < lines.length - 1) select(current + 1, true);
        } else if (event.key === 'ArrowLeft') {
          event.preventDefault();
          if (current > 0) select(current - 1, true);
        }
      }}
    >
      <noscript>
        <style>
          {'.quest-illustrated-scene [hidden]{display:block}.quest-scene-transport{display:none}'}
        </style>
      </noscript>
      <QuestIllustration illustration={backdrop} presentation="background" />
      <div className="quest-scene-dialogue" aria-live={transcript ? 'off' : 'polite'}
        onClick={(event) => {
          if (transcript || current >= lines.length - 1 ||
              (event.target as HTMLElement).closest('button, a, input, select, textarea, summary, audio, video')) return;
          select(current + 1, true);
        }}>
        {lines.map((line, position) => (
          <div key={line.id} data-scene-active={!transcript && position === current} hidden={!transcript && position !== current}>
            {renderLine(line)}
          </div>
        ))}
      </div>
      <footer className="quest-scene-transport">
        <div>
          <button
            type="button"
            aria-label="Previous dialogue line"
            disabled={!current}
            onClick={() => select(current - 1, true)}
          >
            <ChevronLeft size={18} />
          </button>
          <span>
            {current + 1} / {lines.length}
          </span>
          <button
            type="button"
            aria-label="Next dialogue line"
            disabled={current >= lines.length - 1}
            onClick={() => select(current + 1, true)}
          >
            <ChevronRight size={18} />
          </button>
        </div>
        <button
          type="button"
          aria-pressed={auto}
          disabled={transcript}
          onClick={() => { setAutoError(false); if (!auto) startMusic(); setAuto(value => !value); }}
        >{auto ? 'Auto on' : 'Auto off'}</button>
        <button
          type="button"
          aria-pressed={transcript}
          onClick={() => {
            stopAudio();
            setAuto(false);
            setTranscript(!transcript);
          }}
        >
          <BookOpen size={16} />
          {transcript ? 'Scene view' : 'Read transcript'}
        </button>
      </footer>
      {autoError && <p role="status">Voice could not be played. Continue manually or try auto again.</p>}
      {musicError && <p role="status">Background music could not start. Use the music controls to retry.</p>}
      {backgroundMusic && mediaViewerUrl(backgroundMusic) && <audio ref={music} data-scene-background-music controls loop preload="none" src={backgroundMusic} aria-label="Scene background music" onPlaying={() => setMusicError(false)} onError={() => setMusicError(true)} />}
    </section>
  );
}
