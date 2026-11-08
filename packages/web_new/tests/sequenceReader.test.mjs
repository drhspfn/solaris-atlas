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
  flowBackgroundMusic,
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
  const once = sequenceBackgroundMusic(
    media(section(track("music", "/music.ogg"))),
  );
  assert.equal(once.url, "/music.ogg");
  assert.equal(once.loop, false);
  const loop = sequenceBackgroundMusic(
    media(section({ ...track("music", "/music.ogg"), loop: true })),
  );
  assert.equal(loop.loop, true);
  assert.equal(
    sequenceBackgroundMusic(
      media(
        section(track("music", "/music.ogg"), {
          ...track("music", "/music.ogg"),
          loop: true,
        }),
      ),
    ),
    undefined,
  );
  assert.equal(
    sequenceBackgroundMusic(
      media(section(track("music", "/a.ogg"), track("music", "/b.ogg"))),
    ),
    undefined,
  );
});

test("flow music requires exact line coverage and a unique source-proven published track", () => {
  const lines = [{ flow_state: "denia", text: { key: "one" } }, { flow_state: "denia", text: { key: "two" } }];
  const proof = { flow_owner: 10, release_id: 21, flow_state: "denia", presentation_sha256: "sha", asset_version: "3.3", download_id: "id", play_path: "play", talk_path: "talk", stop_path: "stop", talk_text_keys: ["one", "two"] };
  const section = { kind: "audio_event", scope: "flow", reconstruction_kind: "flow_authored_music", source_proof: proof, tracks: [{ url: "/denia.ogg", role: "music", status: "published", loop: true }] };
  const event = { kind: "flow_music", flow_state: "denia", covered_text_keys: ["one", "two"], sequence_media: media(section) };
  const result = flowBackgroundMusic(lines, [event]);
  assert.equal(result.url, "/denia.ogg");
  assert.equal(result.loop, true);
  const reconstructed = { ...section, reconstruction_kind: "state_consumer_fixed_start", source_proof: {
    ...proof, confidence: "reconstructed", runtime_start_confirmed: false, state_event: "state", consumer_event: "consumer" } };
  assert.equal(flowBackgroundMusic(lines, [{ ...event, sequence_media: media(reconstructed) }]).url, "/denia.ogg");
  assert.equal(flowBackgroundMusic(lines, [{ ...event, sequence_media: media({ ...reconstructed, source_proof: { ...reconstructed.source_proof, runtime_start_confirmed: true } }) }]), undefined);
  assert.equal(flowBackgroundMusic(lines, [{ ...event, covered_text_keys: ["one"] }]), undefined);
  assert.equal(flowBackgroundMusic(lines, [{ ...event, flow_state: "other" }]), undefined);
  assert.equal(flowBackgroundMusic(lines, [event, event]), undefined);
  assert.equal(flowBackgroundMusic(lines, [{ ...event, sequence_media: media({ ...section, source_proof: { ...proof, talk_text_keys: ["one"] } }) }]), undefined);
  assert.equal(flowBackgroundMusic(lines, [{ ...event, sequence_media: media({ ...section, tracks: [] }) }]), undefined);
  assert.equal(flowBackgroundMusic(lines, [{ ...event, sequence_media: media({ ...section, tracks: [{ ...section.tracks[0], url: "javascript:alert(1)" }] }) }]), undefined);
});

test("quest and encounter paths enrich lines before creating reader groups", () => {
  const page = readFileSync(
    new URL("../src/components/quest/QuestPageClient.tsx", import.meta.url),
    "utf8",
  );
  assert.match(
    page,
    /illustratedRuns\(\s*enrichSequenceLines\(\s*shown\.filter/,
  );
  assert.match(
    page,
    /illustratedRuns\(\s*enrichSequenceLines\(\s*selected\.map/,
  );
  assert.match(
    page,
    /flow\.sequence_media\s*\?\s*\[\s*\{\s*flow_state:\s*flow\.state/,
  );
  assert.match(
    page,
    /backgroundMusic=\{sequenceBackgroundMusic\(sequenceForRun\(run\)\)\s*\?\?\s*flowBackgroundMusic/,
  );
  assert.match(page, /events:\s*media\.events\.filter\(event => String\(event\.kind\) !== "flow_music"\)/);
});

test("background music starts with a gesture and has no native controls", () => {
  const scene = readFileSync(
    new URL(
      "../src/components/story/QuestIllustratedScene.tsx",
      import.meta.url,
    ),
    "utf8",
  );
  assert.match(scene, /const startMusic =/);
  assert.match(scene, /data-scene-background-music/);
  assert.doesNotMatch(scene, /<audio[^>]*controls/);
});
