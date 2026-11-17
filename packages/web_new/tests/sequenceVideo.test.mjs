import assert from "node:assert/strict";
import test from "node:test";
import { encounterSequenceRecordings, sequenceVideoUrls } from "../src/components/story/sequenceVideo.ts";

test("builds a bounded YouTube embed from a validated video ID", () => {
  assert.deepEqual(sequenceVideoUrls({ video_id: "abcdefghijk", start_seconds: 12, end_seconds: 40 }), {
    embed: "https://www.youtube-nocookie.com/embed/abcdefghijk?start=12&end=40",
    watch: "https://www.youtube.com/watch?v=abcdefghijk&t=12s",
  });
  assert.equal(sequenceVideoUrls({ video_id: "evil.test/x", start_seconds: 0, end_seconds: null }), null);
  assert.equal(sequenceVideoUrls({ video_id: "abcdefghijk", start_seconds: 12, end_seconds: 12 }), null);
});

test("encounter recording requires one matching action occurrence", () => {
  const scene = { kind: "sequence", reference: "shared", start: { action_index: 2 } };
  const flow = { state: "flow", cinematics: [scene] };
  const video = { video_id: "abcdefghijk", start_seconds: 0, end_seconds: null };
  const event = { kind: "sequence", flow_state: "flow", reference: "shared", action_index: 2, youtube_fallback: video };
  assert.deepEqual(encounterSequenceRecordings([event], flow, [scene]), [event]);
  assert.deepEqual(encounterSequenceRecordings([event, { ...event }], flow, [scene]), []);
  assert.deepEqual(encounterSequenceRecordings([event], { ...flow, cinematics: [scene, { ...scene }] }, [scene]), []);
  assert.deepEqual(encounterSequenceRecordings([event], flow, []), []);
  assert.deepEqual(encounterSequenceRecordings([{ ...event, action_index: 3 }], flow, [scene]), []);
});
