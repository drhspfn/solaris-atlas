import { Film, Maximize, Minimize, RotateCcw } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { useNarrativePreferences } from '../../preferences/NarrativePreferences';
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
}: {
  flow: CutsceneFlow;
  title: string;
  anchor?: string;
}) {
  const { preferredRover } = useNarrativePreferences();
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
      next = preferredRoverTarget(requested, preferredRover) || next;
      const target = flow.nodes.find((entry) => entry.id === next);
      if (target?.kind === 'clip') {
        setClipId(target.id);
        if (target.id === clipId && videoRef.current) {
          videoRef.current.currentTime = target.start;
          transitioning.current = false;
        }
      }
      setStep(next);
      setFailed(false);
      if (target?.kind !== 'clip') transitioning.current = false;
    },
    [clipId, flow.nodes, preferredRover],
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

  useEffect(() => {
    const video = videoRef.current;
    if (node?.kind !== 'clip' || !video || video.readyState < 1) return;
    video.currentTime = node.start;
    if (continuePlaying.current) void video.play().catch(() => {});
  }, [node]);

  if (!source || !activeClip) return null;
  const interactive = (node?.kind === 'choice' && !automaticTarget) || !node;
  return (
    <section className="quest-cutscene" aria-label={`Cutscene ${title}`}>
      <header>
        <h3>
          <Film size={18} /> Cutscene · {title}
        </h3>
        <div className="cutscene-actions">
          <button
            className="cutscene-restart"
            type="button"
            title="Fullscreen"
            aria-label="Fullscreen"
            onClick={() => {
              setFullscreenFailed(false);
              const operation = document.fullscreenElement
                ? document.exitFullscreen()
                : stageRef.current?.requestFullscreen();
              void operation?.catch(() => setFullscreenFailed(true));
            }}
          >
            <Maximize size={16} />
          </button>
          <button
            className="cutscene-restart"
            type="button"
            title="Restart cutscene"
            aria-label="Restart cutscene"
            onClick={() => {
              transitioning.current = false;
              advance(flow.entry, false);
            }}
          >
            <RotateCcw size={16} />
          </button>
        </div>
      </header>
      <div className="cutscene-stage" ref={stageRef}>
        <button
          className="cutscene-exit-fullscreen cutscene-restart"
          type="button"
          title="Exit fullscreen"
          aria-label="Exit fullscreen"
          onClick={() => void document.exitFullscreen().catch(() => setFullscreenFailed(true))}
        >
          <Minimize size={16} />
        </button>
        <video
          key={clipId}
          ref={videoRef}
          controls={!interactive}
          controlsList="nofullscreen"
          disablePictureInPicture
          playsInline
          preload="metadata"
          src={source.url}
          aria-label="Cutscene video"
          onLoadedMetadata={(event) => {
            event.currentTarget.currentTime = activeClip.start;
            transitioning.current = false;
            if (node?.kind === 'clip' && continuePlaying.current)
              void event.currentTarget.play().catch(() => {});
          }}
          onPlay={(event) => {
            if (interactive) {
              event.currentTarget.pause();
              return;
            }
            continuePlaying.current = true;
            document.querySelectorAll<HTMLMediaElement>('audio, video').forEach((other) => {
              if (other !== event.currentTarget) other.pause();
            });
          }}
          onTimeUpdate={(event) => {
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
                    advance(option.next);
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
      </div>
      {fullscreenFailed && <p role="status">Fullscreen could not open. Use the inline player.</p>}
      {failed && <p role="alert">Video could not load. Refresh the page to retry.</p>}
      <footer>
        <span>
          {source.has_audio ? 'Sound included' : 'No audio track'} ·{' '}
          {source.subtitles_included ? 'Subtitles included' : 'Subtitles not available yet'}
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

function Cutscene({ event, anchor }: { event: QuestMediaEvent; anchor?: string }) {
  const flow = useMemo(() => event.playback || fallbackFlow(event), [event]);
  return flow ? (
    <FlowPlayer
      key={`${flow.asset_version}-${flow.entry}`}
      flow={flow}
      title={event.reference.replace('cutscene:', '')}
      anchor={anchor}
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
