'use client';
import { useEffect, useRef, useState } from 'react';
import { Pause, Play } from 'lucide-react';
import type { createSceneRenderer, OpenScene } from './openSceneWebGL';
export type OpenPlaybackVariant = {
  rover: 'male' | 'female' | null;
  animation: string;
  scene_url: string;
  textures: Record<string, string>;
};
export type OpenPlayback = {
  format: string;
  variants: OpenPlaybackVariant[];
  unsupported: Array<{ prefab: string; reason: string }>;
};

async function fetchPart(url: string, signal: AbortSignal, limit: number) {
  if (!/^https?:\/\//.test(url)) throw new Error('Invalid scene resource address');
  const response = await fetch(url, { signal, credentials: 'omit' });
  if (!response.ok) throw new Error(`Scene resource request failed (${response.status})`);
  if (Number(response.headers.get('Content-Length') || 0) > limit)
    throw new Error('Scene resource exceeds its size limit');
  const blob = await response.blob();
  if (blob.size > limit) throw new Error('Scene resource exceeds its size limit');
  return blob;
}
export function QuestAnimatedBackdrop({
  variant,
  loop,
}: {
  variant: OpenPlaybackVariant;
  loop: boolean;
}) {
  const canvas = useRef<HTMLCanvasElement>(null),
    root = useRef<HTMLDivElement>(null);
  const engine = useRef<ReturnType<typeof createSceneRenderer> | null>(null),
    cursor = useRef(0);
  const [visible, setVisible] = useState(false),
    [playing, setPlaying] = useState(false),
    [ready, setReady] = useState(false),
    [error, setError] = useState(''),
    [retry, setRetry] = useState(0);
  useEffect(() => {
    const motion = matchMedia('(prefers-reduced-motion: reduce)');
    setPlaying(!motion.matches);
    const change = () => setPlaying(!motion.matches);
    motion.addEventListener('change', change);
    const observer = new IntersectionObserver(
      (entries) => setVisible(entries.some((entry) => entry.isIntersecting)),
      { threshold: 0.01 },
    );
    if (root.current) observer.observe(root.current);
    return () => {
      observer.disconnect();
      motion.removeEventListener('change', change);
    };
  }, []);
  const textureKey = JSON.stringify(variant.textures);
  useEffect(() => {
    if (!visible || !canvas.current) return;
    const controller = new AbortController(),
      bitmaps: ImageBitmap[] = [];
    const element = canvas.current;
    let cancelled = false;
    let localEngine: ReturnType<typeof createSceneRenderer> | null = null;
    const lost = (event: Event) => {
      event.preventDefault();
      engine.current?.setPlaying(false);
      setError('The graphics context was lost. Retry the animated scene.');
      setReady(false);
    };
    element.addEventListener('webglcontextlost', lost);
    setReady(false);
    setError('');
    void (async () => {
      try {
        const [module, blob] = await Promise.all([
          import('./openSceneWebGL'),
          fetchPart(variant.scene_url, controller.signal, 16 * 1024 * 1024),
        ]);
        const data: OpenScene = JSON.parse(await blob.text());
        if (data.format !== 'solaris.skeletal-scene.v1')
          throw new Error('Unsupported animated scene format');
        let pixels = 0;
        const entries = await Promise.all(
          Object.entries(variant.textures).map(async ([name, url]) => {
            const bitmap = await createImageBitmap(
              await fetchPart(url, controller.signal, 64 * 1024 * 1024),
              { premultiplyAlpha: 'none', colorSpaceConversion: 'none' },
            );
            if (cancelled || controller.signal.aborted) {
              bitmap.close();
              throw new DOMException('Scene closed', 'AbortError');
            }
            bitmaps.push(bitmap);
            pixels += bitmap.width * bitmap.height;
            if (pixels > 48 * 1024 * 1024)
              throw new Error('Animated scene textures exceed the decoded size limit');
            return [name, bitmap] as const;
          }),
        );
        if (cancelled || !canvas.current) return;
        localEngine = module.createSceneRenderer(
          canvas.current,
          data,
          Object.fromEntries(entries),
          variant.animation,
          loop,
          () => setPlaying(false),
          (reason) => {
            setError(reason instanceof Error ? reason.message : 'Animation evaluation failed');
            setReady(false);
          },
        );
        engine.current = localEngine;
        localEngine.drawAt(cursor.current);
        setReady(true);
      } catch (reason) {
        controller.abort();
        for (const bitmap of bitmaps) bitmap.close();
        localEngine?.dispose();
        if (engine.current === localEngine) engine.current = null;
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : 'Animated scene could not load');
          setReady(false);
        }
      }
    })();
    return () => {
      cancelled = true;
      element.removeEventListener('webglcontextlost', lost);
      controller.abort();
      if (localEngine) {
        cursor.current = localEngine.getTime();
        localEngine.dispose();
        if (engine.current === localEngine) engine.current = null;
      }
      for (const bitmap of bitmaps) bitmap.close();
    };
  }, [visible, variant.scene_url, variant.animation, textureKey, loop, retry]);
  useEffect(() => {
    const update = () => engine.current?.setPlaying(ready && playing && !document.hidden);
    update();
    document.addEventListener('visibilitychange', update);
    return () => document.removeEventListener('visibilitychange', update);
  }, [playing, ready]);
  return (
    <div className="quest-animated-backdrop" ref={root}>
      <canvas
        key={`${visible}:${retry}`}
        ref={canvas}
        role="img"
        aria-label="Animated story illustration"
      />
      {!ready && !error && (
        <p role="status" className="quest-animation-message">
          Loading animated scene…
        </p>
      )}
      {error && (
        <div className="quest-animation-message" role="alert">
          <p>Animated scene could not load.</p>
          <details>
            <summary>Details</summary>
            {error}
          </details>
          <button type="button" onClick={() => setRetry(retry + 1)}>
            Retry animated scene
          </button>
        </div>
      )}
      {ready && (
        <button
          type="button"
          className="quest-animation-toggle"
          aria-pressed={!playing}
          onClick={() => setPlaying(!playing)}
        >
          {playing ? <Pause size={14} /> : <Play size={14} />}{' '}
          {playing ? 'Pause animation' : 'Play animation'}
        </button>
      )}
    </div>
  );
}
