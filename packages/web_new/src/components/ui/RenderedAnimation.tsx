'use client';

import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Pause, Play } from 'lucide-react';
import { animationPreviewUrl, type AnimationClip } from './animationPreview';
import '@/styles/rendered-animation.css';

export function RenderedAnimation({
  preview,
  label,
  fallback,
  loop = true,
  compact = false,
  decorative = false,
}: {
  preview?: AnimationClip | null;
  label: string;
  fallback: ReactNode;
  loop?: boolean;
  compact?: boolean;
  decorative?: boolean;
}) {
  const url = animationPreviewUrl(preview);
  if (!url) return fallback;
  return (
    <WebMPlayback
      key={url}
      url={url}
      label={label}
      fallback={fallback}
      compact={compact}
      decorative={decorative}
      aspectRatio={preview?.width && preview?.height ? `${preview.width} / ${preview.height}` : undefined}
      loop={loop}
    />
  );
}

function WebMPlayback({ url, label, fallback, loop, compact, decorative, aspectRatio }: {
  url: string;
  label: string;
  fallback: ReactNode;
  loop: boolean;
  compact: boolean;
  decorative: boolean;
  aspectRatio?: string;
}) {
  const root = useRef<HTMLDivElement | HTMLSpanElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const [visible, setVisible] = useState(false);
  const [activated, setActivated] = useState(false);
  const [ready, setReady] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        const intersects = entries.some((entry) => entry.isIntersecting);
        setVisible(intersects);
        if (intersects) setActivated(true);
      },
      { threshold: 0.01 },
    );
    if (root.current) observer.observe(root.current);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const element = video.current;
    if (!element || failed) return;
    const motion = matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => {
      if (!visible || document.hidden || motion.matches) element.pause();
      else if (!element.ended) void element.play().catch(() => { /* Native controls allow explicit playback. */ });
    };
    update();
    document.addEventListener('visibilitychange', update);
    motion.addEventListener('change', update);
    return () => {
      document.removeEventListener('visibilitychange', update);
      motion.removeEventListener('change', update);
      element.pause();
    };
  }, [visible, failed, attempt]);

  const Container = decorative ? 'span' : 'div';
  return (
    <Container className="rendered-animation" ref={(element) => { root.current = element; }} style={{ aspectRatio }}>
      {!ready && <Container className="rendered-animation-fallback">{fallback}</Container>}
      {!failed && (
        <video
          ref={video}
          key={attempt}
          src={activated ? url : undefined}
          aria-label={decorative ? undefined : label}
          aria-hidden={decorative || undefined}
          style={{ opacity: ready ? 1 : 0 }}
          controls={!compact && !decorative}
          muted
          playsInline
          loop={loop}
          preload={activated ? 'auto' : 'none'}
          onLoadedData={() => setReady(true)}
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onEnded={() => setPlaying(false)}
          onError={() => { setReady(false); setFailed(true); }}
        />
      )}
      {compact && !decorative && !failed && (
        <button className="rendered-animation-toggle" type="button" disabled={!ready}
          aria-label={`${playing ? 'Pause' : 'Play'} ${label}`}
          onClick={() => {
            if (playing) video.current?.pause();
            else void video.current?.play().catch(() => {});
          }}>
          {playing ? <Pause size={16} aria-hidden="true" /> : <Play size={16} aria-hidden="true" />}
          {playing ? 'Pause animation' : 'Play animation'}
        </button>
      )}
      {failed && !decorative && (
        <div className="rendered-animation-error" role="status">
          <span>Animation unavailable.</span>
          <button type="button" onClick={() => {
            setFailed(false);
            setAttempt((value) => value + 1);
          }}>Retry animation</button>
        </div>
      )}
    </Container>
  );
}
