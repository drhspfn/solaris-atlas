import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const exports = {};
vm.runInNewContext(
  ts.transpileModule(
    readFileSync(
      new URL("../src/components/story/sequenceComponents.ts", import.meta.url),
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
const {
  sequenceInterval,
  sequenceTime,
  orderComponents,
  componentOccurrences,
} = exports;
const section = (start, end, source = "scene-a") => ({
  kind: "movie_fragment",
  status: "published",
  file_id: 7,
  source: `section-${start}`,
  scene_source: source,
  clock: { numerator: 30, denominator: 1 },
  range: {
    Value: {
      LowerBound: { Type: 1, Value: { Value: start } },
      UpperBound: { Type: 0, Value: { Value: end } },
    },
  },
});

test("decoded Unreal bounds retain their owning clock and source offset is not a crop", () => {
  const s = section(402, 458);
  s.parameters = { StartFrameOffset: { Value: 102 } };
  const range = sequenceInterval(s);
  assert.equal(range.start, 13.4);
  assert.equal(range.end, 458 / 30);
  assert.equal(range.scene, "scene-a");
  assert.equal(sequenceTime(range.start), "0:13.40");
});

test("invalid, open and reversed bounds remain unknown", () => {
  for (const s of [
    section(8, 4),
    { ...section(0, 30), clock: { numerator: 0, denominator: 1 } },
    {
      ...section(0, 30),
      range: { Value: { LowerBound: { Type: 2 }, UpperBound: { Type: 2 } } },
    },
  ])
    assert.equal(sequenceInterval(s), null);
});

test("exclusive starts and inclusive ends use frame boundaries", () => {
  const s = section(0, 29);
  s.range.Value.LowerBound.Type = 0;
  s.range.Value.UpperBound.Type = 1;
  assert.equal(sequenceInterval(s).start, 1 / 30);
  assert.equal(sequenceInterval(s).end, 1);
});

test("components sort within each scene, never across unrelated clocks", () => {
  const a = section(400, 450),
    b = section(0, 60, "scene-b"),
    c = section(100, 200);
  assert.deepEqual(
    Array.from(orderComponents([a, b, c]), (s) => s.source),
    [c.source, a.source, b.source],
  );
});

test("reused files keep all authored occurrences", () => {
  const first = section(150, 370),
    second = section(1626, 1785);
  const uses = componentOccurrences(
    { schema_version: 1, complete_video: false, sections: [first, second] },
    7,
  );
  assert.equal(uses.length, 2);
  assert.equal(uses[0].interval.start, 5);
  assert.equal(uses[1].interval.start, 54.2);
});
