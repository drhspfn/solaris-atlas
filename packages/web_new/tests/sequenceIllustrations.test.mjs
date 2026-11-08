import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const exports = {};
vm.runInNewContext(
  ts.transpileModule(
    readFileSync(
      new URL(
        "../src/components/story/sequenceIllustrations.ts",
        import.meta.url,
      ),
      "utf8",
    ),
    {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2022,
      },
    },
  ).outputText,
  { exports },
);
const media = (composition, sections = []) => ({
  schema_version: 1,
  complete_video: false,
  composition,
  sections,
});
const composition = (state, reasons = []) => ({
  schema_version: 1,
  state,
  reasons,
});

test("only published illustrations with a URL and valid file ID are shown", () => {
  const sections = [
    {
      kind: "illustration",
      status: "published",
      url: "/one.png",
      file_id: 1,
      time_seconds: 1.5,
    },
    { kind: "illustration", status: "missing", url: "/two.png", file_id: 2 },
    { kind: "illustration", status: "published", file_id: 3 },
    { kind: "movie_fragment", status: "published", url: "/movie", file_id: 4 },
  ];
  assert.equal(
    exports.illustrationSections(media(composition("ready"), sections)).length,
    1,
  );
  assert.equal(
    exports.illustrationSections(media(composition("ready"), sections))[0]
      .time_seconds,
    1.5,
  );
});

test("missing assets, failed render, unsupported and unrendered are distinct", () => {
  assert.match(
    exports.illustrationStatus(
      media(composition("failed", [{ code: "missing_assets" }])),
    ),
    /assets are missing/,
  );
  assert.match(
    exports.illustrationStatus(
      media(composition("failed", [{ code: "render_failed" }])),
    ),
    /rendering failed/,
  );
  assert.match(
    exports.illustrationStatus(media(composition("unsupported"))),
    /not supported/,
  );
  assert.match(exports.illustrationStatus(), /not been rendered/);
  assert.match(
    exports.illustrationStatus(media(composition("ready"))),
    /not available/,
  );
});

test("both sequence paths use the reusable illustration display and keep audio and video", () => {
  const read = (path) =>
    readFileSync(
      new URL(`../src/components/story/${path}`, import.meta.url),
      "utf8",
    );
  assert.match(read("QuestCutscenes.tsx"), /QuestSceneDetails event=\{event\}/);
  assert.match(
    read("QuestSceneDetails.tsx"),
    /QuestSequenceFragments media=\{event.sequence_media\}/,
  );
  const fragments = read("QuestSequenceFragments.tsx");
  assert.match(fragments, /QuestSequenceIllustrations media=\{media\}/);
  assert.match(fragments, /FragmentList items=\{items\}/);
  assert.match(fragments, /QuestSequenceAudio media=\{media\}/);
  assert.doesNotMatch(
    read("QuestEncounter.tsx"),
    /rendered by the game engine/,
  );
});

test("illustrated dialogue supports keyboard and click advancement without hijacking controls", () => {
  const scene = readFileSync(
    new URL(
      "../src/components/story/QuestIllustratedScene.tsx",
      import.meta.url,
    ),
    "utf8",
  );
  assert.match(scene, /event\.key === ["']ArrowRight["']/);
  assert.match(scene, /event\.key === ["']ArrowLeft["']/);
  assert.match(scene, /event\.target !== event\.currentTarget/);
  assert.match(
    scene,
    /closest\(\s*'button, a, input, select, textarea, summary, audio, \[role="button"\], \.quest-scene-transport',?\s*\)/,
  );
  assert.match(scene, /if \(transcript \|\|/);
});
