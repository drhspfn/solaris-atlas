import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const cache = new Map();
function load(path) {
  if (cache.has(path)) return cache.get(path);
  const exports = {};
  cache.set(path, exports);
  const source = readFileSync(
    new URL(`../src/components/${path}.ts`, import.meta.url),
    "utf8",
  );
  const code = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText;
  vm.runInNewContext(code, {
    exports,
    URL,
    require: (name) =>
      load(
        name.startsWith("../ui/")
          ? `ui/${name.slice(6)}`
          : `story/${name.replace("./", "")}`,
      ),
  });
  return exports;
}
const {
  sequenceFrameForLine,
  readingDelay,
  enrichSequenceLines,
  sequenceBackgroundMusic,
} = load("story/sequenceReader");
const { illustratedRuns } = load("story/questReading");
const line = { text: { key: "dialogue.key" } };
const frame = (scene, time, id) => ({
  kind: "illustration",
  status: "published",
  scene_source: scene,
  time_seconds: time,
  file_id: id,
  url: `/${id}.png`,
});
const cue = (scene, time, key = "dialogue.key") => ({
  kind: "dialogue",
  text_key: key,
  scene_source: scene,
  time_seconds: time,
});
const media = (...sections) => ({
  schema_version: 1,
  complete_video: false,
  sections,
});

test("chooses latest published frame in the cue’s own scene", () => {
  const data = media(
    frame("a", 0, 1),
    frame("a", 2, 2),
    frame("a", 5, 3),
    frame("b", 3, 4),
    cue("a", 3),
  );
  assert.equal(sequenceFrameForLine(data, line).file_id, 2);
  assert.equal(
    sequenceFrameForLine(data, { text: { key: "other" } }),
    undefined,
  );
});

test("uses authored frame range and rejects ambiguous or unscoped cues", () => {
  const timed = {
    kind: "dialogue",
    text_key: "dialogue.key",
    scene_source: "a",
    clock: { numerator: 30, denominator: 1 },
    range: {
      Value: {
        LowerBound: { Type: 1, Value: { Value: 90 } },
        UpperBound: { Type: 0, Value: { Value: 120 } },
      },
    },
  };
  assert.equal(
    sequenceFrameForLine(media(frame("a", 2, 1), timed), line).file_id,
    1,
  );
  assert.equal(
    sequenceFrameForLine(media(frame("a", 2, 1), cue(null, 3)), line),
    undefined,
  );
  assert.equal(
    sequenceFrameForLine(
      media(frame("a", 2, 1), cue("a", 3), cue("b", 3)),
      line,
    ),
    undefined,
  );
  assert.equal(
    sequenceFrameForLine(
      media(frame("a", 2, 1), frame("a", 2, 2), cue("a", 3)),
      line,
    ),
    undefined,
  );
  assert.equal(
    sequenceFrameForLine(media(frame("a", 4, 1), cue("a", 3)), line),
    undefined,
  );
});

test("voice key matching is exact and reading delay is bounded", () => {
  assert.equal(
    sequenceFrameForLine(media(frame("a", 0, 1), cue("a", 1, "voice.key")), {
      media: { voice_references: [{ plot_audio_id: "voice.key" }] },
    }).file_id,
    1,
  );
  assert.equal(
    sequenceFrameForLine(media(frame("a", 0, 1), cue("a", 1, "voice.key")), {
      media: { voice_references: [{ plot_audio_id: "VOICE.KEY" }] },
    }),
    undefined,
  );
  assert.equal(readingDelay("short"), 2500);
  assert.equal(readingDelay("x".repeat(1000)), 15000);
  assert.equal(readingDelay("x".repeat(100), 70), 7000);
  assert.equal(readingDelay("x".repeat(100), -1), 5500);
});

test("fourteen source-matched lines without original illustrations form one reader", () => {
  const cues = Array.from({ length: 14 }, (_, index) =>
    cue("scene", index, `line.${index}`),
  );
  const first = { ...frame("scene", 0, 1), sample_time_seconds: 1.033 };
  const data = media(first, frame("scene", 8, 2), ...cues);
  const lines = cues.map((item, index) => ({
    id: `id.${index}`,
    flow_state: "fox",
    text: { key: item.text_key },
  }));
  const result = enrichSequenceLines(lines, [
    { flow_state: "fox", sequence_media: data },
  ]);
  assert.equal(illustratedRuns(result).length, 1);
  assert.equal(illustratedRuns(result)[0].length, 14);
  assert.equal(result[0].illustration.images[0].url, "/1.png");
  assert.equal(sequenceFrameForLine(data, result[8]).file_id, 2);
  assert.equal(lines[0].illustration, undefined);
});

test("no enrichment for wrong flow, missing cues, future frames, or ambiguous sources", () => {
  const line = { id: "one", flow_state: "fox", text: { key: "dialogue.key" } };
  const good = media(frame("scene", 0, 1), cue("scene", 0));
  for (const sources of [
    [{ flow_state: "other", sequence_media: good }],
    [
      {
        flow_state: "fox",
        sequence_media: media(frame("scene", 1, 1), cue("scene", 0)),
      },
    ],
    [
      {
        flow_state: "fox",
        sequence_media: media(frame("scene", 0, 1), cue(null, 0)),
      },
    ],
    [
      { flow_state: "fox", sequence_media: good },
      { flow_state: "fox", sequence_media: good },
    ],
  ])
    assert.equal(
      enrichSequenceLines([line], sources)[0].illustration,
      undefined,
    );
});

test("background music must be uniquely published and explicitly marked music", () => {
  const track = (role, url) => ({ role, url, status: "published" });
  const section = (...tracks) => ({ kind: "audio_event", tracks });
  assert.equal(
    sequenceBackgroundMusic(media(section(track("mixed", "/mixed.ogg")))),
    undefined,
  );
  const once = sequenceBackgroundMusic(media(section(track("music", "/music.ogg"))));
  assert.equal(once.url, "/music.ogg");
  assert.equal(once.loop, false);
  const loop = sequenceBackgroundMusic(media(section({ ...track("music", "/music.ogg"), loop: true })));
  assert.equal(loop.loop, true);
  assert.equal(sequenceBackgroundMusic(media(section(track("music", "/music.ogg"), { ...track("music", "/music.ogg"), loop: true }))), undefined);
  assert.equal(
    sequenceBackgroundMusic(
      media(section(track("music", "/a.ogg"), track("music", "/b.ogg"))),
    ),
    undefined,
  );
});

test("quest and encounter paths enrich lines before creating reader groups", () => {
  const page = readFileSync(
    new URL("../src/components/quest/QuestPageClient.tsx", import.meta.url),
    "utf8",
  );
  assert.match(page, /illustratedRuns\(\s*enrichSequenceLines\(shown\.filter/);
  assert.match(page, /illustratedRuns\(\s*enrichSequenceLines\(selected\.map/);
  assert.match(page, /flow\.sequence_media \? \[\{ flow_state: flow\.state/);
  assert.match(
    page,
    /backgroundMusic=\{sequenceBackgroundMusic\(sequenceForRun\(run\)\)\}/,
  );
});

test("music starts on reader gestures and is independent of line changes", () => {
  const scene = readFileSync(
    new URL(
      "../src/components/story/QuestIllustratedScene.tsx",
      import.meta.url,
    ),
    "utf8",
  );
  assert.match(scene, /if \(userGesture\) \{\s*startMusic\(\)/);
  assert.match(scene, /if \(!auto\) \{\s*startMusic\(\)/);
  assert.match(
    scene,
    /audio\.play\(\)\.catch\(\(\) => setMusicError\(true\)\)/,
  );
  assert.match(
    scene,
    /ref=\{music\}\s+data-scene-background-music\s+controls\s+loop=\{backgroundMusic\.loop\}/,
  );
  assert.match(
    scene,
    /querySelectorAll<HTMLMediaElement>\(\s*["']\.quest-scene-dialogue audio, \.quest-scene-dialogue video["'],?\s*\)/,
  );
  assert.doesNotMatch(scene, /<audio[^>]*key=\{(?:current|index)/);
});
