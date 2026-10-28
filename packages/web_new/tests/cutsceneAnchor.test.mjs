import assert from "node:assert/strict";
import test from "node:test";
import { cutsceneAnchor } from "../src/components/story/cutsceneAnchor.ts";

test("catalog quest links target the same exact scene anchor, including Chinese source keys", () => {
  const source = {
    flow_state: "剧情_2_7:43",
    action_index: 1,
    reference: "cutscene:M0311",
  };
  const anchor = cutsceneAnchor(source);
  assert.equal(
    anchor,
    cutsceneAnchor({ ...source, kind: "cutscene", playback: {} }),
  );
  assert.match(anchor, /^cutscene-[a-f0-9-]+$/);
  assert.notEqual(anchor, cutsceneAnchor({ ...source, action_index: 2 }));
});
