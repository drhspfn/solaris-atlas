'use client';

import { BookOpen, ChevronLeft, ChevronRight } from 'lucide-react';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { QuestIllustration, type Illustration } from './QuestIllustration';

export function QuestIllustratedScene({
  lines,
  renderLine,
  anchors,
  focusLine,
}: {
  lines: Array<{ id: string; illustration: Illustration }>;
  renderLine: (line: any) => ReactNode;
  anchors: Map<string, string>;
  focusLine?: string | null;
}) {
  const [index, setIndex] = useState(0);
  const [transcript, setTranscript] = useState(false);
  const root = useRef<HTMLElement>(null);
  const active = useRef(0);
  const current = Math.min(index, lines.length - 1);
  const stopAudio = () =>
    root.current
      ?.querySelectorAll<HTMLMediaElement>('audio,video')
      .forEach((media) => media.pause());
  const select = (next: number) => {
    stopAudio();
    active.current = next;
    setIndex(next);
  };
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
  return (
    <section
      className={`quest-illustrated-scene${transcript ? ' is-transcript' : ''}`}
      ref={root}
      aria-label="Illustrated dialogue"
    >
      <noscript>
        <style>
          {'.quest-illustrated-scene [hidden]{display:block}.quest-scene-transport{display:none}'}
        </style>
      </noscript>
      <QuestIllustration illustration={lines[current].illustration} presentation="background" />
      <div className="quest-scene-dialogue" aria-live={transcript ? 'off' : 'polite'}>
        {lines.map((line, position) => (
          <div key={line.id} hidden={!transcript && position !== current}>
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
            onClick={() => select(current - 1)}
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
            onClick={() => select(current + 1)}
          >
            <ChevronRight size={18} />
          </button>
        </div>
        <button
          type="button"
          aria-pressed={transcript}
          onClick={() => {
            stopAudio();
            setTranscript(!transcript);
          }}
        >
          <BookOpen size={16} />
          {transcript ? 'Scene view' : 'Read transcript'}
        </button>
      </footer>
    </section>
  );
}
