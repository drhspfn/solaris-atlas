"use client";

import { useState } from "react";
import { AudioPlayer } from "../ui/AudioPlayer";
import { Select } from "../ui/Select";
import { mediaViewerUrl } from "../ui/mediaViewerUtils";
import type { SequenceMedia } from "./encounterTransitions";
import styles from "./SequenceComponents.module.css";

type AudioTrack = {
  file_id: number;
  url: string;
  language: string | null;
  rover?: "male" | "female" | null;
  status: "published";
  file: string;
};
type Section = SequenceMedia["sections"][number];

function publishedTracks(section: Section): AudioTrack[] {
  if (!Array.isArray(section.tracks)) return [];
  return section.tracks.filter((track): track is AudioTrack =>
    !!track && track.status === "published" && Number.isSafeInteger(track.file_id) &&
    typeof track.url === "string" && !!mediaViewerUrl(track.url) &&
    (track.language === null || (typeof track.language === "string" && !!track.language)) &&
    (track.rover == null || track.rover === "male" || track.rover === "female") &&
    typeof track.file === "string" && track.file.startsWith("audio/") && track.file.endsWith(".ogg") &&
    !track.file.split("/").includes("..") && !track.file.includes("\\"));
}

export function playableSequenceAudio(media?: SequenceMedia) {
  if (!media || media.schema_version !== 1 || media.complete_video !== false) return [];
  return media.sections.filter((section) => section.kind === "audio_event" && publishedTracks(section).length > 0);
}

function SceneSoundtrack({ section }: { section: Section }) {
  const tracks = publishedTracks(section);
  const [languageChoice, setLanguage] = useState<string | null>(null);
  const [roverChoice, setRover] = useState("");
  const languages = [...new Set(tracks.map((track) => track.language ?? ""))];
  const language = languageChoice !== null && languages.includes(languageChoice) ? languageChoice : languages.includes("en") ? "en" : languages[0];
  const localized = tracks.filter((track) => (track.language ?? "") === language);
  const rovers: string[] = [...new Set(localized.map((track) => track.rover || ""))];
  const rover = rovers.includes(roverChoice) ? roverChoice : rovers[0];
  const track = localized.find((track) => (track.rover || "") === rover);
  const labels: Record<string, string> = { "": "Shared", en: "English", ja: "日本語", ko: "한국어", zh: "中文" };
  return (
    <section className={styles.components} aria-label="Scene soundtrack">
      <h4>Scene soundtrack</h4>
      {track?.language === null && <small>Shared soundtrack</small>}
      <AudioPlayer src={track?.url} label="Scene soundtrack" settings={
        <>
          {languages.length > 1 && <Select label="Soundtrack language" value={language} onChange={setLanguage}
            options={languages.map((value) => ({ value, label: labels[value] || value }))} />}
          {rovers.length > 1 && <Select label="Soundtrack Rover" value={rover} onChange={setRover}
            options={rovers.map((value) => ({ value, label: value === "male" ? "Male Rover" : value === "female" ? "Female Rover" : "Shared soundtrack" }))} />}
        </>
      } />
      <details>
        <summary>Source</summary>
        <p>Whole Wwise event · mixed scene soundtrack</p>
        <pre>{JSON.stringify(section, null, 2)}</pre>
      </details>
    </section>
  );
}

export function QuestSequenceAudio({ media }: { media?: SequenceMedia }) {
  return <>{playableSequenceAudio(media).map((section, index) =>
    <SceneSoundtrack key={`${String(section.source)}-${index}`} section={section} />)}</>;
}
