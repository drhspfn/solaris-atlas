import { type RefObject, useEffect, useRef, useState } from 'react';

import { playbackVolume } from './cutsceneTimeline';
import type { CutsceneAudioTrack } from './QuestMediaReferences';

/** Video is the clock; all stems use the uncut movie timeline. */
export function CutsceneSound({
  videoRef,
  tracks,
  offset,
  volume,
  musicVolume,
}: {
  videoRef: RefObject<HTMLVideoElement | null>;
  tracks: CutsceneAudioTrack[];
  offset: number;
  volume: number;
  musicVolume: number;
}) {
  const root = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const identity = tracks.map((track) => track.url).join('|');
  useEffect(() => {
    const video = videoRef.current;
    const audio = Array.from(root.current?.querySelectorAll('audio') || []);
    if (!video || !audio.length) return;
    let disposed = false;
    let internalPause = false;
    let waiting = false;
    let wanted = !video.paused;
    const pauseTracks = () => audio.forEach((track) => track.pause());
    const align = (force = false) => {
      for (const track of audio) {
        const time = video.currentTime + offset;
        if (track.readyState >= 1 && (force || Math.abs(track.currentTime - time) > 0.15))
          track.currentTime = Math.min(
            time,
            Number.isFinite(track.duration) ? track.duration : time,
          );
        track.playbackRate = video.playbackRate;
      }
    };
    const fail = () => {
      if (disposed) return;
      wanted = false;
      video.pause();
      pauseTracks();
      setError(identity);
    };
    const playFailed = (reason: unknown) => {
      if (!(reason instanceof DOMException && reason.name === 'AbortError')) fail();
    };
    const play = () => {
      if (video.paused || video.seeking || waiting) return;
      align();
      for (const track of audio) void track.play().catch(playFailed);
    };
    const buffer = () => {
      if (!wanted || disposed) return;
      waiting = true;
      if (!video.paused) {
        internalPause = true;
        video.pause();
      }
      pauseTracks();
    };
    const ready = () => {
      if (disposed) return;
      align();
      if (audio.every((track) => track.readyState >= 3)) {
        waiting = false;
        if (wanted && video.paused) void video.play().catch(playFailed);
        else if (wanted) play();
      }
    };
    const onPlay = () => {
      wanted = true;
      align();
      if (audio.some((track) => track.readyState < 3)) buffer();
      else {
        waiting = false;
        play();
      }
    };
    const onPause = () => {
      if (internalPause) internalPause = false;
      else wanted = false;
      pauseTracks();
    };
    const seek = () => {
      pauseTracks();
      align(true);
    };
    const update = () => align();
    // Also cancel pending buffer resumes: pause() alone emits no event for a
    // video which is already paused waiting for its audio.
    const otherPlayback = (event: Event) => {
      const target = event.target;
      if (
        target instanceof HTMLMediaElement &&
        target.closest('.quest-cutscene') !== video.closest('.quest-cutscene')
      ) {
        wanted = false;
        video.pause();
        pauseTracks();
      }
    };
    document.addEventListener('play', otherPlayback, true);
    const bindings: [string, () => void][] = [
      ['play', onPlay],
      ['playing', play],
      ['pause', onPause],
      ['waiting', pauseTracks],
      ['seeking', seek],
      ['seeked', ready],
      ['timeupdate', update],
      ['ratechange', update],
      ['ended', pauseTracks],
    ];
    bindings.forEach(([event, handler]) => video.addEventListener(event, handler));
    for (const track of audio) {
      track.addEventListener('loadedmetadata', ready);
      track.addEventListener('canplay', ready);
      track.addEventListener('waiting', buffer);
      track.addEventListener('error', fail);
    }
    align(true);
    if (wanted) onPlay();
    return () => {
      disposed = true;
      document.removeEventListener('play', otherPlayback, true);
      bindings.forEach(([event, handler]) => video.removeEventListener(event, handler));
      for (const track of audio) {
        track.pause();
        track.removeEventListener('loadedmetadata', ready);
        track.removeEventListener('canplay', ready);
        track.removeEventListener('waiting', buffer);
        track.removeEventListener('error', fail);
      }
    };
  }, [identity, offset, videoRef]);
  useEffect(() => {
    root.current?.querySelectorAll('audio').forEach((track) => {
      track.volume = playbackVolume(volume, musicVolume, track.dataset.cutsceneStem || '');
    });
  }, [volume, musicVolume, identity]);
  return (
    <div ref={root}>
      {tracks.map((track) => (
        <audio key={track.url} src={track.url} preload="metadata" data-cutscene-stem={track.role} />
      ))}
      {error === identity && (
        <p role="alert">
          Audio could not load.{' '}
          <button
            type="button"
            onClick={() => {
              setError(null);
              root.current?.querySelectorAll('audio').forEach((track) => track.load());
              void videoRef.current?.play().catch(() => setError(identity));
            }}
          >
            Retry audio
          </button>
        </p>
      )}
    </div>
  );
}
