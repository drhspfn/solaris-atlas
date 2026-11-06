import assert from "node:assert/strict";
import test from "node:test";

import {
  cutsceneSlots,
  visibleCutsceneEntries,
} from "../src/components/story/cutscenePlacement.ts";

const movie = {
  kind: "cutscene",
  reference: "cutscene:shared",
  action: "first",
  flow_state: "first",
  action_index: 1,
  story_position: [1],
  source: {},
  playback: {
    entry: "choice",
    nodes: [{ kind: "choice", options: ["male", "female"] }],
  },
};
const alternate = {
  ...movie,
  action: "second",
  flow_state: "second",
  story_position: [5],
};

test("partial window selects alternate entry and retains graph and unique old anchors", () => {
  const grouped = { ...movie, entry_points: [movie, alternate] };
  const before = structuredClone(grouped);
  const lines = [
    { id: "a", flow_state: "second", story_position: [4] },
    { id: "b", flow_state: "second", story_position: [6] },
  ];
  const placed = [...cutsceneSlots(lines, [grouped], true).values()].flat();
  assert.equal(placed.length, 1);
  assert.equal(placed[0].flow_state, "second");
  assert.equal(placed[0].playback, grouped.playback);
  assert.deepEqual(
    placed[0].entry_points.map((p) => p.flow_state),
    ["first", "second"],
  );
  assert.deepEqual(grouped, before);
  assert.equal(
    cutsceneSlots(
      [{ id: "c", flow_state: "other", story_position: [8] }],
      [grouped],
      true,
    ).size,
    0,
  );
});

test("visible interaction replaces absent owner without inheriting primary placement fields", () => {
  const grouped = {
    ...movie,
    interaction_id: "absent:inspect",
    story_block_id: "absent",
    entry_points: [
      {
        ...alternate,
        interaction_id: "visible:inspect",
        story_block_id: "visible",
      },
    ],
  };
  const [selected] = visibleCutsceneEntries(
    [],
    [grouped],
    true,
    new Set(["visible:inspect"]),
  );
  assert.equal(selected.interaction_id, "visible:inspect");
  assert.equal(selected.story_block_id, "visible");
  assert.equal(selected.playback, grouped.playback);
  assert.equal(selected.entry_points.length, 2);
  const [flat] = visibleCutsceneEntries(
    [{ id: "b", flow_state: "second" }],
    [
      {
        ...grouped,
        entry_points: [{ ...alternate, story_position: undefined }],
      },
    ],
    true,
  );
  assert.equal(flat.interaction_id, undefined);
  assert.equal(flat.story_block_id, undefined);
  assert.equal(flat.story_position, undefined);
});

test("multiple visible invocations have one page player per cinematic reference", () => {
  const grouped = { ...movie, entry_points: [movie, alternate] };
  const lines = [
    { id: "a", flow_state: "first", story_position: [0] },
    { id: "b", flow_state: "second", story_position: [9] },
  ];
  assert.equal(
    [...cutsceneSlots(lines, [grouped, grouped], true).values()].flat().length,
    1,
  );
  assert.equal(
    visibleCutsceneEntries(
      [],
      [{ ...grouped, interaction_id: "a" }],
      true,
      new Set(["a", "b"]),
    ).length,
    1,
  );
});

test("explicit transition position beats lexical flow name and does not repeat on a later page", () => {
  const lines = [
    { id: "a", flow_state: "z", story_position: [1, 0, 0] },
    { id: "b", flow_state: "a", story_position: [3, 0, 0] },
  ];
  const event = {
    kind: "cutscene",
    reference: "cutscene:M0355_Loading",
    flow_state: "",
    action_index: 0,
    story_position: [2, 100001, 2],
  };
  assert.deepEqual(cutsceneSlots(lines, [event]).get(1), [event]);
  assert.equal(cutsceneSlots(lines.slice(1), [event], true).size, 0);
});

test("mapped scene appears before its caption lines; unmapped scene stays separate", () => {
  const lines = [
    { id: "a", flow_state: "earlier" },
    { id: "b", flow_state: "caption" },
  ];
  const mapped = {
    kind: "cutscene",
    reference: "cutscene:M0366",
    flow_state: "movie",
    action_index: 1,
    context_inferred: true,
    story_order_mapped: true,
    transcript_states: ["caption"],
  };
  const unknown = {
    ...mapped,
    reference: "cutscene:unknown",
    story_order_mapped: false,
  };
  const result = cutsceneSlots(lines, [mapped, unknown]);
  assert.deepEqual(result.get(1), [mapped]);
  assert.deepEqual(result.get(2), [unknown]);
});
