"use client";

import {
  BookOpen,
  ChevronLeft,
  ChevronRight,
  Maximize,
  Minimize,
  Play,
  SlidersHorizontal,
  Volume2,
  Music2,
} from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { gameTextPlainText, type PlayerDisplay } from "@/data/gameText";
import { GAME_DIALOGUE_UI } from "@/data/dialogueUiAssets";
import { useNarrativePreferences } from "@/preferences/NarrativePreferences";
import { IllustratedDialogueLine } from "./IllustratedDialogueLine";
import { QuestIllustration, type Illustration } from "./QuestIllustration";
import type { SequenceMedia } from "./encounterTransitions";
import { sequenceFrameForLine } from "./sequenceReader";
import { mediaViewerUrl } from "../ui/mediaViewerUtils";

type SceneLine = {
  id: string;
  player_choices?: unknown[];
  illustration: Illustration;
  speaker?: { label?: unknown } | null;
  text?: {
    key?: string | null;
    content?: string | null;
    inline_text?: string | null;
  };
  media?: {
    voice_references?: Array<{
      plot_audio_id?: string | null;
      file_name?: string | null;
      tracks?: Array<{
        language: string;
        url: string;
        rover?: "male" | "female" | null;
      }>;
    }>;
  };
};

export function QuestIllustratedScene({
  lines,
  renderLine,
  anchors,
  focusLine,
  sequenceMedia,
  backgroundMusic,
  display,
}: {
  lines: SceneLine[];
  renderLine: (line: any) => ReactNode;
  anchors: Map<string, string>;
  focusLine?: string | null;
  sequenceMedia?: SequenceMedia;
  backgroundMusic?: { url: string; loop: boolean } | null;
  display: PlayerDisplay;
}) {
  const { dialogueStyle, voiceLanguage, preferredRover } =
    useNarrativePreferences();
  const [index, setIndex] = useState(0);
  const [transcript, setTranscript] = useState(false);
  const [started, setStarted] = useState(false);
  const [auto, setAuto] = useState(false);
  const [voiceDone, setVoiceDone] = useState(false);
  const [textDone, setTextDone] = useState(false);
  const [revealed, setRevealed] = useState(false);
  const [canAdvance, setCanAdvance] = useState(false);
  const [voiceError, setVoiceError] = useState(false);
  const [musicError, setMusicError] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [fullscreenError, setFullscreenError] = useState(false);
  const [voiceVolume, setVoiceVolume] = useState(1);
  const [musicVolume, setMusicVolume] = useState(0.35);
  const root = useRef<HTMLElement>(null);
  const music = useRef<HTMLAudioElement>(null);
  const voice = useRef<HTMLAudioElement>(null);
  const active = useRef(0);
  const current = Math.max(0, Math.min(index, lines.length - 1));
  const line = lines[current];
  const tracks =
    line?.media?.voice_references?.flatMap((item) => item.tracks || []) || [];
  const track =
    tracks.find(
      (item) =>
        item.language === voiceLanguage && item.rover === preferredRover,
    ) || tracks.find((item) => item.language === voiceLanguage && !item.rover);
  const voiceUrl = track && mediaViewerUrl(track.url) ? track.url : undefined;
  const text = gameTextPlainText(
    line?.text,
    display,
    "Text unavailable in this locale",
  );
  const revealDuration = Math.max(Array.from(text).length * 25, 25);
  const isRevealing = started && !revealed;
  const showingChoices = Boolean(
    line?.player_choices?.length && revealed && canAdvance,
  );
  const indicatorVisible = started && canAdvance && !showingChoices;

  const startMusic = () => {
    const audio = music.current;
    if (!audio || !audio.paused) return;
    setMusicError(false);
    audio.volume = musicVolume;
    void audio.play().catch(() => setMusicError(true));
  };
  const select = (next: number, userGesture = false) => {
    if (next < 0 || next >= lines.length) return;
    if (userGesture) {
      startMusic();
      setStarted(true);
    }
    voice.current?.pause();
    setVoiceDone(false);
    setTextDone(false);
    setRevealed(false);
    setCanAdvance(false);
    setVoiceError(false);
    active.current = next;
    setIndex(next);
  };
  const begin = () => {
    startMusic();
    if (current === 0 && voice.current) {
      voice.current.volume = voiceVolume;
      void voice.current.play().catch(() => {
        setVoiceError(true);
        setVoiceDone(true);
      });
    }
    if (current !== 0) select(0);
    setTranscript(false);
    setStarted(true);
    setAuto(true);
  };

  useEffect(() => {
    if (!started || transcript || !line) return;
    setTextDone(false);
    setRevealed(false);
    setCanAdvance(false);
    const clickTimer = window.setTimeout(() => setCanAdvance(true), 300);
    const revealTimer = window.setTimeout(
      () => setRevealed(true),
      revealDuration,
    );
    const readingTimer = window.setTimeout(
      () => setTextDone(true),
      revealDuration + 700,
    );
    return () => {
      window.clearTimeout(clickTimer);
      window.clearTimeout(revealTimer);
      window.clearTimeout(readingTimer);
    };
  }, [started, transcript, current, line?.id, revealDuration]);

  useEffect(() => {
    if (!started || transcript || !line) return;
    const audio = voice.current;
    setVoiceDone(!audio);
    setVoiceError(false);
    if (!audio) return;
    let cancelled = false;
    audio.volume = voiceVolume;
    void audio.play().catch((error: DOMException) => {
      if (!cancelled && error.name !== "AbortError") {
        setVoiceError(true);
        setVoiceDone(true);
      }
    });
    return () => {
      cancelled = true;
      audio.pause();
    };
  }, [started, transcript, current, line?.id, voiceUrl]);

  useEffect(() => {
    if (voice.current) voice.current.volume = voiceVolume;
  }, [voiceVolume, voiceUrl, line?.id]);
  useEffect(() => {
    if (music.current) music.current.volume = musicVolume;
  }, [musicVolume, backgroundMusic?.url]);

  useEffect(() => {
    if (!started || transcript || !textDone || !voiceDone || showingChoices)
      return;
    if (current === lines.length - 1) {
      voice.current?.pause();
      music.current?.pause();
      setAuto(false);
    } else if (auto) select(current + 1);
  }, [
    started,
    auto,
    transcript,
    textDone,
    voiceDone,
    current,
    lines.length,
    showingChoices,
  ]);

  useEffect(() => {
    const update = () =>
      setFullscreen(document.fullscreenElement === root.current);
    document.addEventListener("fullscreenchange", update);
    return () => document.removeEventListener("fullscreenchange", update);
  }, []);
  useEffect(() => {
    let frame: number | undefined;
    const reveal = () => {
      let id = "";
      try {
        id = decodeURIComponent(location.hash.slice(1));
      } catch {
        return;
      }
      const next = lines.findIndex(
        (item) => (id && anchors.get(item.id) === id) || item.id === focusLine,
      );
      if (next >= 0) {
        if (next !== active.current) select(next);
        setTranscript(false);
        if (id)
          frame = requestAnimationFrame(() =>
            root.current?.scrollIntoView({ block: "center" }),
          );
      }
    };
    reveal();
    window.addEventListener("hashchange", reveal);
    return () => {
      window.removeEventListener("hashchange", reveal);
      if (frame !== undefined) cancelAnimationFrame(frame);
    };
  }, [lines, anchors, focusLine]);
  useEffect(
    () => () => {
      music.current?.pause();
      voice.current?.pause();
    },
    [],
  );

  if (!line) return null;
  const advance = () => {
    if (!started) {
      begin();
      return;
    }
    if (!canAdvance || showingChoices) return;
    if (isRevealing) {
      setRevealed(true);
      return;
    }
    if (current < lines.length - 1) select(current + 1, true);
    else {
      voice.current?.pause();
      music.current?.pause();
      setAuto(false);
    }
  };
  const frame = sequenceFrameForLine(sequenceMedia, line);
  const backdrop: Illustration = frame
    ? {
        id: `sequence:${frame.file_id}`,
        kind: "image",
        images: [{ rover: null, engine_path: "", url: frame.url! }],
      }
    : line.illustration;
  const toggleFullscreen = async () => {
    try {
      setFullscreenError(false);
      if (document.fullscreenElement === root.current)
        await document.exitFullscreen();
      else await root.current?.requestFullscreen();
    } catch {
      setFullscreenError(true);
    }
  };
  return (
    <section
      className={`quest-illustrated-scene scene-${dialogueStyle || "game"}${transcript ? " is-transcript" : ""}`}
      ref={root}
      aria-label="Illustrated dialogue"
      tabIndex={0}
      onClick={(event) => {
        if (
          transcript ||
          (event.target as HTMLElement).closest(
            'button, a, input, select, textarea, summary, audio, [role="button"], .quest-scene-transport',
          )
        )
          return;
        advance();
      }}
      onDragStart={(event) => {
        if (!transcript) event.preventDefault();
      }}
      onContextMenu={(event) => {
        if (!transcript) event.preventDefault();
      }}
      onKeyDown={(event) => {
        if (transcript || event.target !== event.currentTarget) return;
        if (
          event.key === "ArrowRight" ||
          event.key === "Enter" ||
          event.key === " "
        ) {
          event.preventDefault();
          advance();
        } else if (event.key === "ArrowLeft") {
          event.preventDefault();
          select(current - 1, true);
        }
      }}
    >
      <noscript>
        <style>
          {
            ".quest-illustrated-scene [hidden]{display:block}.quest-scene-transport{display:none}"
          }
        </style>
      </noscript>
      <QuestIllustration illustration={backdrop} presentation="background" />
      <div className="quest-scene-dialogue">
        {!transcript && !showingChoices && (
          <div aria-live="polite" key={`${line.id}:${started}`}>
            <IllustratedDialogueLine
              line={line}
              display={display}
              duration={revealDuration}
              animate={isRevealing}
              showContinue={indicatorVisible}
            />
          </div>
        )}
        {lines.map((item, position) => (
          <div
            key={item.id}
            data-scene-active={!transcript && position === current}
            hidden={!transcript && !(showingChoices && position === current)}
          >
            {renderLine(item)}
          </div>
        ))}
      </div>
      <footer className="quest-scene-transport">
        <div className="quest-scene-navigation">
          <button
            type="button"
            aria-label="Previous dialogue line"
            disabled={!current || transcript}
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
            disabled={
              transcript ||
              showingChoices ||
              (started && !canAdvance) ||
              (!started && current >= lines.length - 1)
            }
            onClick={() => {
              if (!started) select(current + 1, true);
              else advance();
            }}
          >
            <ChevronRight size={18} />
          </button>
        </div>
        <div className="quest-scene-actions">
          <details className="quest-scene-audio-settings">
            <summary aria-label="Audio settings" title="Audio settings">
              <SlidersHorizontal size={16} />
              <span>Audio</span>
            </summary>
            <div className="quest-scene-audio-panel">
              <strong>Audio settings</strong>
              <label>
                <span>
                  <Volume2 size={16} /> Voice{" "}
                  <output>{Math.round(voiceVolume * 100)}%</output>
                </span>
                <input
                  type="range"
                  min="0"
                  max="1"
                  step="0.01"
                  aria-label="Voice volume"
                  aria-valuetext={`${Math.round(voiceVolume * 100)}%`}
                  value={voiceVolume}
                  onChange={(event) =>
                    setVoiceVolume(Number(event.target.value))
                  }
                />
              </label>
              {backgroundMusic && mediaViewerUrl(backgroundMusic.url) && (
                <label>
                  <span>
                    <Music2 size={16} /> Music{" "}
                    <output>{Math.round(musicVolume * 100)}%</output>
                  </span>
                  <input
                    type="range"
                    min="0"
                    max="1"
                    step="0.01"
                    aria-label="Music volume"
                    aria-valuetext={`${Math.round(musicVolume * 100)}%`}
                    value={musicVolume}
                    onChange={(event) =>
                      setMusicVolume(Number(event.target.value))
                    }
                  />
                </label>
              )}
            </div>
          </details>
          {!started && (
            <button type="button" onClick={begin}>
              <Play size={16} /> Start scene
            </button>
          )}
          <button
            type="button"
            className="quest-scene-auto"
            aria-label={auto ? "Disable auto advance" : "Enable auto advance"}
            aria-pressed={auto}
            disabled={transcript}
            onClick={() => {
              if (!started) {
                begin();
                return;
              }
              setAuto((value) => !value);
            }}
          >
            <img
              src={auto ? GAME_DIALOGUE_UI.auto : GAME_DIALOGUE_UI.manual}
              alt=""
            />
            <span>{auto ? "Auto on" : "Auto off"}</span>
          </button>
          <button
            type="button"
            aria-pressed={transcript}
            onClick={() => {
              if (!transcript) {
                voice.current?.pause();
                music.current?.pause();
                setAuto(false);
              } else if (started) startMusic();
              setTranscript(!transcript);
            }}
          >
            <BookOpen size={16} />
            {transcript ? "Scene view" : "Read transcript"}
          </button>
          <button
            type="button"
            aria-label={fullscreen ? "Exit fullscreen" : "Enter fullscreen"}
            onClick={() => void toggleFullscreen()}
          >
            {fullscreen ? <Minimize size={16} /> : <Maximize size={16} />}
          </button>
        </div>
      </footer>
      {voiceError && (
        <p className="quest-scene-feedback" role="status">
          Voice could not play. The scene will continue without it.
        </p>
      )}
      {musicError && (
        <p className="quest-scene-feedback" role="status">
          Background music could not play.
        </p>
      )}
      {fullscreenError && (
        <p className="quest-scene-feedback" role="status">
          Fullscreen is unavailable in this browser.
        </p>
      )}
      {voiceUrl && (
        <audio
          key={line.id}
          ref={voice}
          preload="none"
          src={voiceUrl}
          onEnded={() => setVoiceDone(true)}
          onError={() => {
            setVoiceError(true);
            setVoiceDone(true);
          }}
        />
      )}
      {backgroundMusic && mediaViewerUrl(backgroundMusic.url) && (
        <audio
          ref={music}
          data-scene-background-music
          loop={backgroundMusic.loop}
          preload="none"
          src={backgroundMusic.url}
          onError={() => setMusicError(true)}
        />
      )}
    </section>
  );
}
