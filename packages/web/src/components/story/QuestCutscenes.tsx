import { Film } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { APP_SETTINGS } from '../../config/settings';
import { usePlayerDisplay } from '../../hooks/usePlayerDisplay';
import { useNarrativePreferences } from '../../preferences/NarrativePreferences';
import { PlayerText } from '../dialogue/PlayerText';
import { AnalysisActions } from './AnalysisActions';
import { CutsceneControls } from './CutsceneControls';
import { CutsceneSound } from './CutsceneSound';
import { chapterPosition, cutsceneTimeline, timelineTarget } from './cutsceneTimeline';
import { preferredRoverTarget } from './preferredRover';
import {
  type CutsceneFlow,
  type CutsceneNode,
  type QuestMediaEvent,
  type QuestMediaManifest,
} from './QuestMediaReferences';

function fallbackFlow(event: QuestMediaEvent): CutsceneFlow | null {
  const variants = (event.resources || []).flatMap((resource) =>
    resource.kind === 'has_variant'
      ? resource.assets
          .filter((asset) => asset.video)
          .map((asset) => ({ asset, gender: resource.variant?.girl_or_boy }))
      : [],
  );
  if (!variants.length) return null;
  const clips: CutsceneNode[] = variants.map(({ asset }, index) => ({
    id: `variant-${index}`,
    kind: 'clip',
    asset: asset.reference,
    start: 0,
    end: null,
    next: null,
  }));
  return {
    version: 1,
    entry: variants.length > 1 ? 'variant-choice' : clips[0].id,
    nodes:
      variants.length > 1
        ? [
            {
              id: 'variant-choice',
              kind: 'choice',
              prompt: 'Choose a variant',
              options: variants.map(({ gender }, index) => ({
                rover: gender === 1 ? 'male' : gender === 0 ? 'female' : null,
                label:
                  gender === 1
                    ? 'Male Rover'
                    : gender === 0
                      ? 'Female Rover'
                      : `Variant ${index + 1}`,
                next: clips[index].id,
              })),
            },
            ...clips,
          ]
        : clips,
    media: Object.fromEntries(variants.map(({ asset }) => [asset.reference, asset.video!])),
    asset_version: variants[0].asset.video!.asset_version,
    evidence: 'Authored video variants; no shared prefix inferred.',
  };
}

function FlowPlayer({
  flow,
  title,
  anchor,
  captions,
}: {
  flow: CutsceneFlow;
  title: string;
  anchor?: string;
  captions?: QuestMediaEvent['captions'];
}) {
  const { preferredRover, voiceLanguage, setVoiceLanguage } = useNarrativePreferences();
  const playerDisplay = usePlayerDisplay();
  const [musicVolume, setMusicVolume] = useState<number>(APP_SETTINGS.cutscene.musicLevel);
  const [playing, setPlaying] = useState(false);
  const [choices, setChoices] = useState<Record<string, string>>({});
  const [durations, setDurations] = useState<Record<string, number>>({});
  const pendingTime = useRef<number | null>(null);
  const pendingSeek = useRef<number | null>(null);
  const [subtitles, setSubtitles] = useState(true);
  const [volume, setVolume] = useState<number>(APP_SETTINGS.cutscene.masterVolume);
  const [time, setTime] = useState(0);
  const entry =
    preferredRoverTarget(
      flow.nodes.find((node) => node.id === flow.entry),
      preferredRover,
    ) || flow.entry;
  const [step, setStep] = useState<string | null>(entry);
  const [clipId, setClipId] = useState(
    (flow.nodes.find((node) => node.id === entry && node.kind === 'clip') ||
      flow.nodes.find((node) => node.kind === 'clip'))!.id,
  );
  const [failed, setFailed] = useState(false);
  const [fullscreenFailed, setFullscreenFailed] = useState(false);
  const stageRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const choiceRef = useRef<HTMLDivElement>(null);
  const continuePlaying = useRef(false);
  const transitioning = useRef(false);
  const node = flow.nodes.find((entry) => entry.id === step);
  const automaticTarget = preferredRoverTarget(node, preferredRover);
  const clip = flow.nodes.find((entry) => entry.id === clipId);
  const activeClip = clip?.kind === 'clip' ? clip : null;
  const source = activeClip ? flow.media[activeClip.segment || activeClip.asset] : null;
  const tracks = source?.audio_tracks;
  const languages =
    tracks?.filter((track) => track.role === 'voice').map((track) => track.language) || [];
  const selectedTracks =
    tracks?.filter((track) => track.role !== 'voice' || track.language === voiceLanguage) || [];
  const timeline = cutsceneTimeline(flow, choices, preferredRover, durations);
  const timelineClip = timeline.clips.find((entry) => entry.id === clipId);
  const position =
    node?.kind === 'choice'
      ? timeline.decisions.find((entry) => entry.id === node.id)?.time || 0
      : !node
        ? timeline.total
        : (timelineClip?.start || 0) + Math.max(0, time - (activeClip?.start || 0));
  const cues = captions?.[voiceLanguage] || [];
  const absoluteTime = time + (source?.timeline_offset || 0);

  useEffect(() => {
    if (node?.kind === 'choice' && !automaticTarget && continuePlaying.current)
      choiceRef.current?.querySelector('button')?.focus();
  }, [node, automaticTarget]);

  const advance = useCallback(
    (next: string | null, play = true) => {
      if (transitioning.current) return;
      transitioning.current = true;
      videoRef.current?.pause();
      continuePlaying.current = play;
      const requested = flow.nodes.find((entry) => entry.id === next);
      next =
        (requested?.kind === 'choice' ? choices[requested.id] : null) ||
        preferredRoverTarget(requested, preferredRover) ||
        next;
      const target = flow.nodes.find((entry) => entry.id === next);
      if (target?.kind === 'clip') {
        setClipId(target.id);
        if (target.id === clipId && videoRef.current && videoRef.current.readyState >= 1) {
          videoRef.current.currentTime = pendingTime.current ?? target.start;
          pendingTime.current = null;
          setTime(videoRef.current.currentTime);
          if (play) void videoRef.current.play().catch(() => setFailed(true));
          transitioning.current = false;
        }
      }
      setStep(next);
      setFailed(false);
      if (target?.kind !== 'clip') transitioning.current = false;
    },
    [clipId, flow.nodes, preferredRover, choices],
  );

  useEffect(() => {
    if (automaticTarget) advance(automaticTarget, continuePlaying.current);
  }, [automaticTarget, advance]);

  useEffect(() => {
    const video = videoRef.current;
    if (
      !video ||
      node?.kind !== 'clip' ||
      node.end === null ||
      typeof video.requestVideoFrameCallback !== 'function'
    )
      return;
    let callbackId = 0;
    function check(_now: number, frame: VideoFrameCallbackMetadata) {
      if (node?.kind !== 'clip' || node.end === null) return;
      if (frame.mediaTime >= node.end) {
        video!.pause();
        video!.currentTime = node.end;
        advance(node.next);
      } else callbackId = video!.requestVideoFrameCallback(check);
    }
    callbackId = video.requestVideoFrameCallback(check);
    return () => video.cancelVideoFrameCallback(callbackId);
  }, [node, advance]);

  const seek = (position: number) => {
    const target = timelineTarget(timeline, position);
    if (!target) return;
    if (position >= timeline.total && target.time !== null) {
      transitioning.current = false;
      pendingTime.current = null;
      pendingSeek.current = null;
      advance(null, false);
      return;
    }
    pendingSeek.current = target.time === null ? position : null;
    pendingTime.current = target.time;
    transitioning.current = false;
    advance(target.id, !videoRef.current?.paused);
  };
  const togglePlay = () => {
    const video = videoRef.current;
    if (!video) return;
    if (!node) {
      transitioning.current = false;
      advance(flow.entry);
    } else if (node.kind === 'clip') {
      if (video.paused) void video.play().catch(() => setFailed(true));
      else {
        continuePlaying.current = false;
        video.pause();
      }
    } else choiceRef.current?.querySelector('button')?.focus();
  };
  const fullscreen = () => {
    setFullscreenFailed(false);
    const operation = document.fullscreenElement
      ? document.exitFullscreen()
      : stageRef.current?.requestFullscreen();
    void operation?.catch(() => setFullscreenFailed(true));
  };
  useEffect(() => {
    if (videoRef.current) videoRef.current.volume = volume;
  }, [volume, clipId]);

  if (!source || !activeClip) return null;
  const interactive = (node?.kind === 'choice' && !automaticTarget) || !node;
  return (
    <section className="quest-cutscene" aria-label={`Cutscene ${title}`}>
      <header>
        <h3>
          <Film size={18} /> Cutscene · {title}
        </h3>
        <AnalysisActions
          target={{
            kind: 'cutscene',
            version: flow.asset_version,
            assetIds: [
              ...new Set(
                Object.values(flow.media).flatMap((media) =>
                  media.asset_node_id ? [media.asset_node_id] : [],
                ),
              ),
            ],
          }}
        />
      </header>
      {tracks && languages.length > 0 && !languages.includes(voiceLanguage) && (
        <p role="status">Selected voice language is unavailable for this scene.</p>
      )}
      <div
        className="cutscene-stage"
        ref={stageRef}
        tabIndex={0}
        aria-label={`Player for ${title}`}
        onKeyDown={(event) => {
          if (event.target !== event.currentTarget && event.target !== videoRef.current) return;
          const key = event.key.toLowerCase();
          if ([' ', 'k', 'm', 'f', 'c', 'arrowleft', 'arrowright'].includes(key))
            event.preventDefault();
          if (key === ' ' || key === 'k') togglePlay();
          if (key === 'm') setVolume((value) => (value ? 0 : APP_SETTINGS.cutscene.masterVolume));
          if (key === 'f') fullscreen();
          if (key === 'c' && cues.length) setSubtitles((value) => !value);
          if (key === 'arrowleft' || key === 'arrowright')
            seek(position + (key === 'arrowleft' ? -1 : 1) * APP_SETTINGS.cutscene.seekSeconds);
        }}
      >
        <video
          key={`video:${clipId}`}
          ref={videoRef}
          controls={false}
          disablePictureInPicture
          playsInline
          preload="metadata"
          muted={Boolean(tracks)}
          src={source.url}
          aria-label="Cutscene video"
          onLoadedMetadata={(event) => {
            if (event.currentTarget !== videoRef.current) return;
            event.currentTarget.currentTime = pendingTime.current ?? activeClip.start;
            pendingTime.current = null;
            setTime(event.currentTarget.currentTime);
            const duration = event.currentTarget.duration;
            if (Number.isFinite(duration))
              setDurations((values) => ({
                ...values,
                [activeClip.segment || activeClip.asset]: duration,
              }));
            transitioning.current = false;
            if (node?.kind === 'clip' && continuePlaying.current)
              void event.currentTarget.play().catch(() => {});
          }}
          onPlay={(event) => {
            if (interactive) {
              event.currentTarget.pause();
              return;
            }
            setPlaying(true);
            continuePlaying.current = true;
            document.querySelectorAll<HTMLMediaElement>('audio, video').forEach((other) => {
              if (
                other !== event.currentTarget &&
                other.closest('.quest-cutscene') !== event.currentTarget.closest('.quest-cutscene')
              )
                other.pause();
            });
          }}
          onPause={() => setPlaying(false)}
          onClick={() => {
            if (!interactive) {
              const video = videoRef.current!;
              if (video.paused) void video.play().catch(() => setFailed(true));
              else video.pause();
            }
          }}
          onTimeUpdate={(event) => {
            setTime(event.currentTarget.currentTime);
            if (node?.kind !== 'clip' || transitioning.current) return;
            if (event.currentTarget.currentTime < node.start)
              event.currentTarget.currentTime = node.start;
            if (node.end !== null && event.currentTarget.currentTime >= node.end) {
              event.currentTarget.currentTime = node.end;
              advance(node.next);
            }
          }}
          onEnded={() => {
            if (node?.kind === 'clip') advance(node.next);
          }}
          onError={() => setFailed(true)}
        />
        {subtitles && !interactive && (
          <div className="cutscene-captions" aria-label="Subtitles">
            {cues
              .filter((cue) => absoluteTime >= cue.start && absoluteTime < cue.end)
              .map((cue) => (
                <p key={`${cue.key}-${cue.start}`}>
                  <PlayerText display={playerDisplay} value={{ content: cue.text }} />
                </p>
              ))}
          </div>
        )}
        {interactive && (
          <div
            className="cutscene-choice"
            ref={choiceRef}
            role="group"
            aria-label={node?.kind === 'choice' ? node.prompt : 'Cutscene complete'}
          >
            <h4>{node?.kind === 'choice' ? node.prompt : 'Cutscene complete'}</h4>
            {node?.kind === 'choice' ? (
              node.options.map((option) => (
                <button
                  key={option.next + option.label}
                  type="button"
                  onClick={() => {
                    transitioning.current = false;
                    setChoices((values) => ({ ...values, [node.id]: option.next }));
                    const desired = pendingSeek.current;
                    pendingSeek.current = null;
                    if (desired !== null) {
                      const path = cutsceneTimeline(
                        flow,
                        { ...choices, [node.id]: option.next },
                        preferredRover,
                        durations,
                      );
                      const target = timelineTarget(path, desired);
                      if (desired >= path.total && target?.time !== null) {
                        pendingTime.current = null;
                        advance(null, false);
                        return;
                      }
                      pendingTime.current = target?.time ?? null;
                      advance(target?.id || option.next);
                    } else advance(option.next);
                  }}
                >
                  {option.label}
                </button>
              ))
            ) : (
              <button
                type="button"
                onClick={() => {
                  transitioning.current = false;
                  advance(flow.entry);
                }}
              >
                Watch again
              </button>
            )}
          </div>
        )}
        <CutsceneControls
          timeline={timeline}
          position={position}
          playing={playing}
          volume={volume}
          musicVolume={musicVolume}
          subtitles={subtitles}
          hasCaptions={!!cues.length}
          hasMusic={!!tracks?.some((track) => track.role === 'music')}
          languages={languages}
          language={voiceLanguage}
          onLanguage={setVoiceLanguage}
          onVolume={setVolume}
          onMusicVolume={setMusicVolume}
          onSubtitles={() => setSubtitles((value) => !value)}
          onSeek={seek}
          onPlay={togglePlay}
          onRestart={() => {
            transitioning.current = false;
            pendingTime.current = null;
            pendingSeek.current = null;
            advance(flow.entry, false);
          }}
          onFullscreen={fullscreen}
          onChoice={(id) => {
            transitioning.current = false;
            setChoices((values) => {
              const updated = { ...values };
              delete updated[id];
              return updated;
            });
            videoRef.current?.pause();
            setStep(id);
          }}
        />
        {tracks && (
          <CutsceneSound
            key={`audio:${clipId}`}
            videoRef={videoRef}
            tracks={selectedTracks}
            offset={source.timeline_offset || 0}
            volume={volume}
            musicVolume={musicVolume}
          />
        )}
      </div>
      {Object.entries(flow.media)
        .filter(
          ([key]) =>
            !Object.hasOwn(durations, key) &&
            flow.nodes.some(
              (entry) =>
                entry.kind === 'clip' &&
                (entry.segment || entry.asset) === key &&
                entry.end === null,
            ),
        )
        .map(([key, media]) => (
          <video
            hidden
            key={key}
            src={media.url}
            preload="metadata"
            onLoadedMetadata={(event) => {
              const duration = event.currentTarget.duration;
              if (Number.isFinite(duration))
                setDurations((values) => ({ ...values, [key]: duration }));
            }}
          />
        ))}
      {fullscreenFailed && <p role="status">Fullscreen could not open. Use the inline player.</p>}
      {failed && <p role="alert">Video could not load. Refresh the page to retry.</p>}
      <footer>
        {source.description && (
          <details className="cutscene-description">
            <summary>{source.description.title} · AI description</summary>
            <p>{source.description.text}</p>
            {source.description.chapters.map((chapter) => (
              <section key={chapter.start}>
                <button
                  type="button"
                  onClick={() => {
                    const position = chapterPosition(
                      flow,
                      timeline,
                      source.asset_node_id,
                      chapter.start,
                    );
                    if (position !== null) seek(position);
                  }}
                >
                  {Math.floor(chapter.start / 60)}:
                  {String(Math.floor(chapter.start % 60)).padStart(2, '0')} · {chapter.title}
                </button>
                <p>{chapter.text}</p>
              </section>
            ))}
            <small>
              Based on sampled visual observations and quest context. This description follows the
              selected video variant.
            </small>
          </details>
        )}
        <span>
          {tracks
            ? 'Separate audio tracks'
            : source.has_audio
              ? 'Sound included'
              : 'No audio track'}
          {!cues.length && ' · Subtitles unavailable for this scene'}
          {tracks?.some((track) => track.role === 'mixed') &&
            ' · Music is part of the original mix'}
        </span>
        {anchor && <a href={`#${anchor}`}>Continue to dialogue ↗</a>}
        <details>
          <summary>Source details</summary>
          <small>
            Video assets {flow.asset_version}; this does not confirm an identical recording in
            earlier releases.
          </small>
          <small>{flow.evidence}</small>
          <code>{activeClip.asset}</code>
        </details>
      </footer>
    </section>
  );
}

export function Cutscene({ event, anchor }: { event: QuestMediaEvent; anchor?: string }) {
  const flow = useMemo(() => event.playback || fallbackFlow(event), [event]);
  return flow ? (
    <FlowPlayer
      key={`${flow.asset_version}-${flow.entry}`}
      flow={flow}
      title={event.reference.replace('cutscene:', '')}
      anchor={anchor}
      captions={event.captions}
    />
  ) : null;
}

export function QuestCutscenes({
  manifest,
  stateAnchors,
}: {
  manifest: QuestMediaManifest | null;
  stateAnchors: Map<string, string>;
}) {
  return (
    <>
      {manifest?.events
        .filter((event) => event.kind === 'cutscene')
        .map((event) => (
          <Cutscene
            key={`${event.action}-${event.reference}`}
            event={event}
            anchor={stateAnchors.get(event.flow_state)}
          />
        ))}
    </>
  );
}
